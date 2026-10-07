#!/usr/bin/env bash
# Crash-restart wrapper started by the desktop session (XDG autostart).
# Restarts the device app whenever it exits (crash, "Restart app" in Settings, deploy),
# with a short pause so a hard failure cannot spin the CPU. Exit code 3 = restart requested.
REPO="${FACE_ATTENDANCE_DIR:-$HOME/face-attendance}"
LOG="$HOME/.local/state/attendance/wrapper.log"
mkdir -p "$(dirname "$LOG")"
cd "$REPO" || exit 1
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export QT_SCALE_FACTOR="${QT_SCALE_FACTOR:-1}"
while true; do
  echo "$(date -Is) starting device app" >> "$LOG"
  .venv/bin/python -m device.app.main --fullscreen "$@"
  code=$?
  echo "$(date -Is) device app exited with $code" >> "$LOG"
  if [ "$code" = 3 ]; then sleep 1; else sleep 2; fi
done
