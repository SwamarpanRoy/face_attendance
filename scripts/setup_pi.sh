#!/usr/bin/env bash
# Idempotent provisioning for the attendance Raspberry Pi (Pi 5 or Pi 4, Raspberry Pi OS
# 64-bit, Trixie or Bookworm).
#
#   bash ~/face-attendance/scripts/setup_pi.sh                     # on the Pi (Terminal, monitor)
#   bash ~/face-attendance/scripts/setup_pi.sh --no-autostart      # same, app not started at login
#   ssh attendance-pi 'bash -s' < scripts/setup_pi.sh              # from the laptop over SSH
#   ./scripts/setup_pi.sh --tailscale --connect                    # on the Pi, optional extras
#
# What it does (each step is safe to re-run):
#   1. apt packages: picamera2, PyQt5 (+ Wayland plugin), numpy, OpenCV (all from apt, never
#      pip), AI Camera firmware (imx500-all), git, rsync, espeak-ng
#   2. uses the checkout the script runs from (or clones ~/face-attendance) and creates a
#      --system-site-packages venv
#   3. pip installs device/requirements.txt and verifies onnxruntime against the system numpy
#   4. downloads the face models (buffalo_sc: same two files as buffalo_s, 14 MB)
#   5. device.toml, state dir, XDG autostart entry + crash-restart wrapper (--no-autostart skips)
#   6. sudoers drop-in (reboot/shutdown without a password, nothing else)
#   7. SSH hardening (keys only, no root); skipped with a warning if no SSH key is installed
#   8. optional: Tailscale, Raspberry Pi Connect
#   9. prints a PASS/FAIL checklist
set -euo pipefail

REPO_URL="${REPO_URL:-}"            # set to your GIT_REMOTE on first run, e.g. git@github.com:team/face-attendance.git
REPO_DIR="$HOME/face-attendance"
# Run as a file from a copied/cloned checkout (e.g. copied from a USB stick): use that checkout.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -f "$candidate/device/requirements.txt" ]; then REPO_DIR="$candidate"; fi
fi
WITH_TAILSCALE=0
WITH_CONNECT=0
WITH_AUTOSTART=1
for arg in "$@"; do
  case "$arg" in
    --tailscale) WITH_TAILSCALE=1 ;;
    --connect) WITH_CONNECT=1 ;;
    --no-autostart) WITH_AUTOSTART=0 ;;
    --repo=*) REPO_URL="${arg#--repo=}" ;;
    *) echo "unknown option $arg"; exit 2 ;;
  esac
done

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ 0. sanity
if ! grep -Eq "bookworm|trixie" /etc/os-release; then
  echo "WARNING: expected Raspberry Pi OS Trixie (Python 3.13) or Bookworm (3.11); current: $(python3 --version)"
fi
echo "OS: $(. /etc/os-release && echo "$PRETTY_NAME")  Python: $(python3 --version)  Session: ${XDG_SESSION_TYPE:-unknown} ${XDG_CURRENT_DESKTOP:-}"

# ------------------------------------------------------------------ 1. apt
say "apt packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-picamera2 python3-pyqt5 qtwayland5 python3-numpy python3-opencv python3-venv python3-pip \
  imx500-all git rsync espeak-ng libatlas3-base curl

# ------------------------------------------------------------------ 2. repo + venv
say "repository and venv"
if [ ! -f "$REPO_DIR/device/requirements.txt" ]; then
  if [ -z "$REPO_URL" ]; then
    echo "ERROR: no project in $REPO_DIR. Copy the folder there (USB stick) or run: REPO_URL=<git url> $0" >&2
    exit 1
  fi
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
echo "project: $REPO_DIR"
if [ "$REPO_DIR" != "$HOME/face-attendance" ]; then
  echo "WARNING: autostart expects ~/face-attendance; move the folder there or set FACE_ATTENDANCE_DIR"
fi
# A .venv copied from a laptop (USB stick) points at a Python that does not exist here:
# rebuild it from scratch rather than reuse foreign (macOS/Windows) packages.
if ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
  python3 -m venv --clear --system-site-packages .venv
fi
.venv/bin/pip install --quiet --upgrade pip

# ------------------------------------------------------------------ 3. pip (never numpy/opencv)
say "pip install device/requirements.txt"
if grep -Eiq '^(numpy|opencv)' device/requirements.txt; then
  echo "ERROR: device/requirements.txt must not contain numpy or opencv (they come from apt)" >&2
  exit 1
fi
.venv/bin/pip install --quiet -r device/requirements.txt
sha256sum device/requirements.txt | cut -d' ' -f1 > .venv/.requirements.sha256
.venv/bin/python - <<'EOF'
import numpy, cv2, onnxruntime, PyQt5.QtCore, picamera2
print(f"numpy {numpy.__version__} (apt)  cv2 {cv2.__version__} (apt)  onnxruntime {onnxruntime.__version__} (pip)  Qt {PyQt5.QtCore.QT_VERSION_STR}")
import numpy as np
sess_ok = onnxruntime.get_available_providers()
print("onnxruntime providers:", sess_ok)
EOF

# ------------------------------------------------------------------ 4. models
say "face models"
.venv/bin/python tools/fetch_models.py --pack buffalo_sc

# ------------------------------------------------------------------ 5. config, state, autostart
say "config and autostart"
mkdir -p "$HOME/.config/attendance" "$HOME/.local/state/attendance" "$HOME/.config/autostart"
if [ ! -f "$HOME/.config/attendance/device.toml" ]; then
  cp device/device.example.toml "$HOME/.config/attendance/device.toml"
  echo "created ~/.config/attendance/device.toml: set [server] url and token, [device] id"
fi
mkdir -p "$HOME/.local/bin"
install -m 755 device/deploy/autostart/run_device.sh "$HOME/.local/bin/run_device.sh"
if [ "$WITH_AUTOSTART" = 1 ]; then
  sed "s|__HOME__|$HOME|g" device/deploy/autostart/face-attendance.desktop > "$HOME/.config/autostart/face-attendance.desktop"
  echo "autostart: app starts fullscreen at login (undo: rm ~/.config/autostart/face-attendance.desktop)"
else
  rm -f "$HOME/.config/autostart/face-attendance.desktop"
  echo "autostart: off (--no-autostart); start by hand with ~/.local/bin/run_device.sh"
fi
# labwc (Bookworm 2024-10+) and wayfire both honour XDG autostart; nothing else to do.
if [ -f "$HOME/.config/labwc/autostart" ] || [ "${XDG_CURRENT_DESKTOP:-}" = "labwc:wlroots" ]; then
  echo "compositor: labwc (honours XDG autostart)"
fi
# Touch Display 2 is natively portrait (720x1280). If the desktop shows up landscape,
# rotate in Raspberry Pi Configuration > Display, or add to /boot/firmware/cmdline.txt:
#   video=DSI-1:720x1280@60,rotate=0
echo "display rotation: check 'wlr-randr' output; the app expects portrait 720x1280"

# ------------------------------------------------------------------ 6. sudoers
say "sudoers drop-in"
sed "s|__USER__|$USER|g" device/deploy/sudoers.d/attendance | sudo tee /etc/sudoers.d/attendance >/dev/null
sudo chmod 440 /etc/sudoers.d/attendance
sudo visudo -cf /etc/sudoers.d/attendance

# ------------------------------------------------------------------ 7. ssh hardening
say "ssh hardening"
# Never lock out the only way in: without a laptop key, leave SSH as it is.
if [ ! -s "$HOME/.ssh/authorized_keys" ]; then
  echo "SKIPPED: no ~/.ssh/authorized_keys yet (fine when working on a monitor). Re-run after adding a key."
else
  sudo install -d -m 755 /etc/ssh/sshd_config.d
  printf 'PasswordAuthentication no\nPermitRootLogin no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/90-attendance.conf >/dev/null
  if systemctl is-active --quiet ssh; then sudo systemctl reload ssh; fi
  echo "ssh: keys only, no root login"
fi

# ------------------------------------------------------------------ 8. optional extras
if [ "$WITH_TAILSCALE" = 1 ]; then
  say "tailscale"
  if ! command -v tailscale >/dev/null; then curl -fsSL https://tailscale.com/install.sh | sh; fi
  echo "run: sudo tailscale up   (then use the MagicDNS name in ~/.ssh/config)"
fi
if [ "$WITH_CONNECT" = 1 ]; then
  say "raspberry pi connect"
  sudo apt-get install -y -qq rpi-connect && rpi-connect on && rpi-connect signin || true
fi

# ------------------------------------------------------------------ 9. checklist
say "checklist"
pass() { printf 'PASS  %-18s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-18s %s\n' "$1" "$2"; }
cams="$( (command -v rpicam-hello >/dev/null && rpicam-hello --list-cameras 2>/dev/null) || (command -v libcamera-hello >/dev/null && libcamera-hello --list-cameras 2>/dev/null) || true)"
if echo "$cams" | grep -q imx500; then pass camera "AI Camera (imx500) detected"
elif echo "$cams" | grep -q imx708; then pass camera "Camera Module 3 (imx708) detected"
else fail camera "no imx500/imx708 listed (power off, re-seat the ribbon cable, reboot)"; fi
display=""
for status in /sys/class/drm/card*-*/status; do
  # "disconnected" contains "connected": compare the whole line.
  if [ -f "$status" ] && [ "$(cat "$status")" = "connected" ]; then
    display="$display $(basename "$(dirname "$status")" | sed 's/^card[0-9]*-//')"
  fi
done
if [ -n "$display" ]; then pass display "connected:$display"
else fail display "no connected DSI or HDMI display in /sys/class/drm"; fi
if .venv/bin/python -c "import picamera2, PyQt5.QtWidgets, onnxruntime, cv2, numpy" 2>/dev/null; then pass venv "picamera2, PyQt5, onnxruntime, cv2, numpy import"; else fail venv "import error (see above)"; fi
SERVER_URL=$(grep -E '^url' "$HOME/.config/attendance/device.toml" | head -1 | sed 's/.*= *"\(.*\)".*/\1/')
if curl -fsS --max-time 5 "$SERVER_URL/healthz" >/dev/null 2>&1; then pass server "$SERVER_URL reachable"; else fail server "$SERVER_URL not reachable (WiFi? server running? url in device.toml?)"; fi
if [ "$(timedatectl show -p NTPSynchronized --value)" = "yes" ]; then pass ntp "clock synced"; else fail ntp "clock NOT synced (events will be flagged until it is)"; fi
echo
echo "Next: edit ~/.config/attendance/device.toml, then: .venv/bin/python -m device.app.main --selftest"
echo "Try it in a window first: .venv/bin/python -m device.app.main   (close the window to quit)"
echo "Fullscreen with crash-restart: ~/.local/bin/run_device.sh   (autostarts at login unless --no-autostart)"
