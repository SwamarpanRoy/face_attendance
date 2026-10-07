# Raspberry Pi setup (first boot by hand, then one script)

Hardware in use: **Raspberry Pi 5 (16 GB)**, **Raspberry Pi AI Camera** (Sony IMX500) on
`CAM/DISP0`, Raspberry Pi OS **Trixie** 64-bit (Python 3.13). For now the Pi is used on an
**HDMI monitor with mouse and keyboard**; the Official Touch Display 2 (5", 720×1280, DSI)
comes later. The original plan (Pi 4 2 GB, Camera Module 3, Bookworm) also works.

Two ways to work: **section 0** (monitor + keyboard, no SSH: start here) or sections
1 to 6 (headless over SSH from VS Code, once you want to unplug the monitor).

## 0. Monitor + keyboard, no SSH (start here)

### 0.1 Connect the AI Camera (power OFF)

1. Shut down (*Menu → Logout → Shutdown*), wait for the green LED to stop, unplug power.
2. Touch grounded metal first (static), hold boards by the edges, never fold the cable.
3. Use the cable with one **narrow** (22-pin) end: narrow end into the Pi, wide end
   (15-pin) into the camera (usually already attached).
4. On the Pi 5, find `CAM/DISP0` (between the micro-HDMI ports and the Ethernet port).
   Pull the flap out gently until it stops, tilt it slightly.
5. Insert the cable straight, **metal contacts facing away from the flap**. Close the flap,
   check with a very light tug. Keep the camera board off any metal. Power on.
6. Use the official 27 W (5 V / 5 A) USB-C supply and the Active Cooler if you have one.

### 0.2 Check the camera (Terminal on the Pi)

```bash
sudo apt update && sudo apt install -y imx500-all     # AI Camera firmware, then reboot
sudo reboot
rpicam-hello --list-cameras                            # must list imx500
rpicam-hello -t 20000                                  # 20 s preview: check focus
```

The AI Camera has a **manual-focus lens**: if a face at 50 to 80 cm looks soft, turn the
lens gently with the supplied focus ring tool. An out-of-focus lens makes the app keep
saying "Hold still". `WARN ... Unsupported V4L2 pixel format Nc30 / Nc12` lines are
normal for the AI Camera (they are its NPU data streams, which this project does not use).

### 0.3 Copy the project onto the Pi

There is no git remote yet, so use a USB stick: copy the `face-attendance` folder from
the laptop to the stick, plug it into the Pi, and in the Pi's file manager copy the
folder into your home folder so that it is `/home/<you>/face-attendance`. Leave out the
laptop's `.venv` folder (it is for macOS, not the Pi) and any `.env` file. Never copy
face photos, `.env`, device tokens or passwords through public paste/file-sharing sites.

### 0.4 Run the setup script (Terminal on the Pi)

```bash
bash ~/face-attendance/scripts/setup_pi.sh --no-autostart
```

It asks for your password once (for `sudo`) and takes a few minutes. `--no-autostart`
keeps the app from taking over the screen at every login while you are still testing.
Without an SSH key it skips SSH hardening (that is expected) and prints a PASS/FAIL
checklist; `server` FAIL is expected until the server runs and `device.toml` is filled in.

### 0.5 Self-test and first run

```bash
cd ~/face-attendance
.venv/bin/python -m device.app.main --selftest        # PASS/FAIL per part
.venv/bin/python -m device.app.main                    # the app in a normal window
```

Close the window to quit. The mouse works as touch. Fullscreen with crash-restart is
`~/.local/bin/run_device.sh`; it restarts the app whenever it exits, so to stop it open a
Terminal (Ctrl+Alt+T) and run `pkill -f run_device.sh; pkill -f device.app.main`.
Re-run the setup script without `--no-autostart` when the device should start the app
at every boot.

## 1. Make an SSH key on the laptop (once)

| OS | Command |
|---|---|
| Windows 11 (PowerShell) | `ssh-keygen -t ed25519 -C "attendance laptop"` → key in `C:\Users\<you>\.ssh\id_ed25519.pub` |
| macOS / Linux | `ssh-keygen -t ed25519 -C "attendance laptop"` → `~/.ssh/id_ed25519.pub` |

Print the public key and keep it in the clipboard: `cat ~/.ssh/id_ed25519.pub`
(PowerShell: `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`).

## 2. Flash the SD card with Raspberry Pi Imager

1. Raspberry Pi Imager → Device **Raspberry Pi 5** (or 4) → OS **Raspberry Pi OS
   (64-bit)**, the default Trixie release with Python 3.13. Bookworm (Python 3.11,
   *Raspberry Pi OS (Legacy, 64-bit)*) also works.
2. Click **Next → Edit settings** (OS customisation):
   - **General**: hostname `<PI_HOST>` (e.g. `attendance-pi`), username `<PI_USER>`
     with a strong password (only used for `sudo`), WiFi SSID + password for a *simple*
     network (home or phone hotspot; campus WPA2-Enterprise is added later), country
     `IN`, locale/timezone `Asia/Kolkata`.
   - **Services**: *Enable SSH* → **Allow public-key authentication only** → paste the
     public key from step 1.
   - **Options**: untick telemetry if you like. Save, write, wait.
3. Insert the card, connect camera and display, power on. First boot takes ~1 min.

## 3. First contact

From the laptop, on the same WiFi:

```
ssh <PI_USER>@<PI_HOST>.local
```

If `.local` does not resolve (campus WiFi blocks mDNS, see docs/wireless_dev.md), read
the IP from your phone hotspot's client list or the router, and use the IP.

## 4. Campus WPA2-Enterprise WiFi (PEAP/MSCHAPv2) with nmcli

Trixie and Bookworm use NetworkManager. On the Pi (over SSH):

```bash
sudo nmcli connection add type wifi con-name campus ifname wlan0 ssid "BMSCE-WiFi" \
  wifi-sec.key-mgmt wpa-eap 802-1x.eap peap 802-1x.phase2-auth mschapv2 \
  802-1x.identity "<college username>" 802-1x.password "<password>" \
  connection.autoconnect yes connection.autoconnect-priority 10
sudo nmcli connection up campus
nmcli -t -f active,ssid dev wifi      # shows which network is active
```

If the campus requires a CA certificate, add `802-1x.ca-cert /path/to/ca.pem`; if it
insists on no certificate check (common, not ideal), add `802-1x.system-ca-certs no`.

### Phone hotspot as fallback

```bash
sudo nmcli connection add type wifi con-name hotspot ifname wlan0 ssid "<phone hotspot>" \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<hotspot password>" \
  connection.autoconnect yes connection.autoconnect-priority 5
```

NetworkManager picks the highest-priority network that is in range, so the Pi joins
the campus network when it can and the hotspot otherwise. Join the laptop to the same
hotspot to work on the Pi when the campus network isolates clients.

## 5. Provision with one script

From the laptop (the script is idempotent; re-run it any time):

```
ssh <PI_HOST> 'REPO_URL=<GIT_REMOTE> bash -s' < scripts/setup_pi.sh
ssh <PI_HOST> 'bash -s -- --tailscale --connect' < scripts/setup_pi.sh    # optional extras
```

It installs the apt packages (picamera2, PyQt5, numpy, OpenCV: **never pip** for
these), clones the repo to `~/face-attendance`, creates the `--system-site-packages`
venv, installs `device/requirements.txt`, verifies `onnxruntime` imports next to the
system numpy, downloads the models, writes `~/.config/attendance/device.toml` from the
example, installs the XDG autostart entry and crash-restart wrapper, the sudoers
drop-in, hardens SSH (`PasswordAuthentication no`, `PermitRootLogin no`) and prints a
PASS/FAIL checklist: camera, display, venv imports, server reachable, NTP synced.

What it found on the OS matters for autostart: Bookworm releases since October 2024
use the **labwc** Wayland compositor (older ones Wayfire); both start XDG autostart
entries from `~/.config/autostart/`, which is what the script installs. The Touch
Display 2 is natively portrait (720×1280); if the desktop comes up landscape, set the
rotation in *Raspberry Pi Configuration → Display* or check `wlr-randr`.

## 6. Configure and test the device

```
ssh <PI_HOST> nano ~/.config/attendance/device.toml     # [server] url + token, [device] id
ssh <PI_HOST> '~/face-attendance/.venv/bin/python -m device.app.main --selftest'
ssh <PI_HOST> '~/face-attendance/.venv/bin/python -m device.app.main --sync'
ssh <PI_HOST> sudo reboot
```

After the reboot the app appears on the touch display within about a minute. From
now on use the VS Code tasks (`Pi: deploy`, `Pi: tail logs`, …), see
docs/wireless_dev.md.

The device token comes from the admin UI: *Devices → Register* (shown once), then
assign the sections the device should cache.
