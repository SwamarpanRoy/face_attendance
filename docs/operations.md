# Operations: running, backups, restarts, troubleshooting

## Running the server

| Where | How | Notes |
|---|---|---|
| Laptop (dev) | `uvicorn server.app.main:app --reload --host 0.0.0.0 --port 8000` (task *Server: run (dev)*) | Postgres via `scripts/pg_portable.py start` after each reboot |
| Office computer, Windows 11 | `.\scripts\setup_server.ps1 -Service` once | registers the scheduled task *FaceAttendanceServer* (starts at logon, restarts on failure, logs in `logs\server.log`); `-Nssm` installs a Windows service instead |
| Office computer, Ubuntu 24.04 | `sudo ./scripts/setup_server.sh` once | `/opt/face-attendance`, system user `attendance`, `face-attendance.service`, nightly backup timer |

Check: `http://<SERVER_HOST>:8000/healthz` returns `{"status":"ok"}`; `/admin` shows the
login; `/docs` the API.

### HTTPS

The admin UI sets the session cookie with `Secure` only when `SESSION_COOKIE_SECURE=true`.
On the campus LAN the simplest path to HTTPS is **Tailscale Serve**
(`tailscale serve --bg 8000`), which gives `https://office-pc.<tailnet>.ts.net` with a
valid certificate and no open ports. Alternatively put Caddy in front
(`reverse_proxy 127.0.0.1:8000`). Then set `SESSION_COOKIE_SECURE=true` and point the
devices' `[server] url` at the HTTPS name.

## Backups

`python scripts/backup.py --keep 30` writes `backups/<timestamp>/attendance.dump`
(`pg_dump -Fc`) and `data.zip` (enrolment crops and probes) and prunes old runs.

- **Windows**: Task Scheduler → Create Basic Task → daily 02:00 → Program
  `C:\...\face-attendance\.venv\Scripts\python.exe`, arguments `scripts\backup.py --keep 30`,
  start in the repo folder.
- **Linux**: installed by `setup_server.sh` (`face-attendance-backup.timer`).

Copy `backups/` to a second place (department NAS, encrypted USB) regularly; it contains
biometric templates, treat it like the database.

### Restore

```
python scripts/backup.py --restore backups/20261006-020000
```

Asks for confirmation, then `pg_restore --clean --if-exists` into `DATABASE_URL` and
unpacks `data.zip` next to `CROPS_DIR`. Stop the server first; afterwards run
`alembic upgrade head` if the code is newer than the dump, and `--sync` on the devices.

## Restarting things

| Component | Command |
|---|---|
| Server (Windows task) | Task Scheduler → *FaceAttendanceServer* → End, Run; or `Stop-ScheduledTask`/`Start-ScheduledTask` |
| Server (NSSM) | `nssm restart FaceAttendance` |
| Server (Linux) | `sudo systemctl restart face-attendance`; logs `journalctl -u face-attendance -f` |
| Postgres (portable) | `python scripts/pg_portable.py stop|start` |
| Device app | *Settings → Restart app* on the device, or task *Pi: restart app*, or `ssh attendance-pi pkill -f device.app.main` (the wrapper restarts it) |
| Whole Pi | `ssh attendance-pi sudo reboot` (allowed without password by the sudoers drop-in) |

## Routine tasks

- New semester: import rosters (Sections → Import CSV), assign course offerings, create
  periods, enrol students (device) or import ID-card photos (`tools/enroll_bulk.py`).
- New device: Devices → Register (token shown once) → assign sections → `device.toml` →
  `--selftest`, `--sync`.
- Model upgrade: put the new ONNX files in a folder, `tools/reembed.py --new-model-version
  <name> --models-dir <folder>`, set `MODEL_VERSION` in `.env` and in every
  `device.toml`, redeploy, `--full-sync` on devices, re-run `tools/calibrate.py`.
- Calibration refresh (after many new enrolments): `tools/capture_probes.py` for a few
  students, `tools/calibrate.py`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Device shows **server: offline** | `curl http://<SERVER_HOST>:8000/healthz` from the Pi; WiFi (`nmcli -t -f active,ssid dev wifi`); firewall on the server for port 8000; token revoked? (Devices page) |
| **DEVICE REVOKED** on idle screen | admin revoked it; *Devices → New token*, update `device.toml`, restart |
| "This section has not been synced" | device not assigned to the section, or no templates yet: Devices → sections, then *Settings → Sync now* |
| Many "Not recognised" | not calibrated (idle screen warning) → run calibration; lighting; `min_face_width_px`/distance; check `device.log` scores |
| Clock warning | Pi has no RTC; it syncs via NTP once online (`timedatectl`); records made before that carry `clock_synced=false` and the server stamps `received_at` |
| App not on the touch screen after boot | `cat ~/.local/state/attendance/wrapper.log`; `ls ~/.config/autostart`; `wlr-randr` for the display; `--selftest` |
| Enrolment refused with 409 | consent missing: the student must tap *I consent* on the device, or appear in the consent CSV |
| Login says "Too many failed attempts" | 5 failures lock email/IP for `LOGIN_LOCKOUT_MINUTES` (15); wait or restart the server |
| Tests skip "PostgreSQL not reachable" | start Postgres (`scripts/pg_portable.py start`), check `TEST_DATABASE_URL` |
| `localhost` is slow on Windows | use `127.0.0.1` in `.env` (IPv6-first resolution adds 10 s per connection) |

## Security notes

- Passwords: argon2id; sessions: signed HttpOnly cookies, 12 h; CSRF token on every form
  (header for HTMX); login throttling per email and IP; security headers (CSP, no
  framing, nosniff); request bodies capped at 5 MB.
- Device tokens: 32 random bytes, stored as SHA-256; shown once; revocation wipes the
  device cache on next contact.
- PINs: argon2 hashes synced to devices, verified offline with lockout; PINs never log
  in to the dashboard.
- Biometric data: embeddings on devices and server, 112×112 crops only on the server
  under `data/crops/<USN>/`; withdrawal deletes both; every edit, deletion and import is
  in `audit_log`.
- Keep `.env` and `device.toml` out of git (they are ignored) and off chat/email.
