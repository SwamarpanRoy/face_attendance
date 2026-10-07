# Device app (Raspberry Pi / laptop simulator)

```
python -m device.app.main --sim             # laptop: webcam, half-size window
python -m device.app.main --fullscreen      # Pi: touch display (started by autostart)
python -m device.app.main --selftest --sim  # PASS/FAIL for config, models, camera, DB, server, clock
python -m device.app.main --sync            # pull catalog + templates once, then exit
python -m device.app.main --debug           # also listen for debugpy on 127.0.0.1:5678
```

Config: `~/.config/attendance/device.toml` (template in `device/device.example.toml`).
State: `~/.local/state/attendance/device.db` (SQLite) and `device.log` (rotating).

## Screens

| Screen | Who | What |
|---|---|---|
| **Idle** | anyone | device name, clock, server online/offline, queued records, last sync, WiFi SSID/IP, warnings ("Not calibrated", "Clock not synced"), **Start session**, **Resume session** (after a crash/restart mid-class), Enrol, Settings |
| **Start session** | teacher PIN | course → section → period from the **local catalog**; blocked with "No faces enrolled for this section yet" when no templates are cached for it; if online, a quick template refresh runs first |
| **Live** | – | camera preview with face boxes, banner (green *Face matched – Name (USN)*, amber *Already marked*, red *Not recognised – try again*, neutral gate hints like *Move closer*), present / total, last 5 marked, stage latencies, **End session** (teacher PIN) |
| **Enrol** | teacher PIN | pick student from the roster with the number pad → consent notice → *I consent* → 3 captures → upload → templates re-synced |
| **Settings** | admin PIN | server URL, device id, app and model version, threshold/margin (read-only, flagged when provisional), liveness, cached templates, queue, last sync, network, clock, **Sync now**, **Test camera**, **Restart app** |

PINs are verified **offline** against argon2 hashes synced from the server (5 wrong
tries lock the pad for 60 s). Before the first catalog sync the device asks the
server. PINs never work as dashboard passwords.

## Offline-first behaviour

Everything the device needs is prefetched for **all sections assigned to it** at boot
and every 10 minutes while online: templates (incrementally, with deletions), the
catalog (courses, sections, periods, offerings), rosters, faculty PIN hashes, the
consent text and the active calibration. So:

1. WiFi off from boot → the teacher starts a session from the cached catalog, students
   are recognised against cached templates, every match becomes an **event with a
   client-generated UUID** in the local outbox (`clock_synced=false` if NTP is not
   synced), the session end is queued too.
2. WiFi back → the sync thread sends a heartbeat, then uploads **sessions, then event
   batches (≤ 200), then session ends**. Backoff doubles from 5 s up to 5 min while the
   server is unreachable. Because UUIDs are idempotent on the server, a retry after a
   lost response never duplicates a record; permanently rejected events are logged
   and dropped rather than retried forever.
3. If an admin revokes the device, the next contact returns `401 Device revoked.` and
   the device wipes its cached templates, catalog and PIN hashes.

A session that was never ended (power loss) can be **resumed** from Idle; the
"already marked" cooldown is rebuilt from the outbox so nobody is recorded twice.

## Heartbeat (every 30 s)

Device id and app version, IP and SSID, queue length, CPU temperature
(`/sys/class/thermal/thermal_zone0/temp`), free memory, model version and whether the
clock is NTP-synced (`timedatectl show -p NTPSynchronized --value`). The admin
dashboard uses it for the devices page and sync warnings.

## Privacy

The device stores embeddings only. Enrolment crops live in memory until the upload
finishes (or the dialog is cancelled) and are never written to disk. Logs contain
USNs, scores and timings, never images or embeddings.

## Exit codes

`0` normal, `2` models missing / server not configured, `3` restart requested from
Settings (the Pi run wrapper restarts the app on any exit).
