# face-attendance

Portable, smartphone-sized **face-recognition attendance device** (Raspberry Pi 5 16 GB +
Raspberry Pi AI Camera; 5" touch display planned, HDMI monitor for now) with a **FastAPI + PostgreSQL server** and an
**HTMX admin dashboard**. Final-year ECE project, BMS College of Engineering.

**Status: everything that can be built without the hardware is done** (M0, M2–M7, M9):
scaffold and model fetcher, server core, recognition core verified against InsightFace,
consent-gated enrolment, measured calibration, the offline-first device app
([docs/device.md](docs/device.md)), the admin UI with live view, audited edits, reports and
device management ([docs/admin.md](docs/admin.md)), hardening, backups and operations
docs ([docs/operations.md](docs/operations.md)), and the demo script
([docs/demo.md](docs/demo.md)). The Pi-side pieces of M1 and M8 (provisioning script,
deploy tool, autostart, benchmark, SSH docs) are written and tested where possible on
the laptop; they need a run on the real Pi ([docs/setup_pi.md](docs/setup_pi.md),
[docs/wireless_dev.md](docs/wireless_dev.md)).
New teammate? Start with [docs/setup_laptop.md](docs/setup_laptop.md).

## What it does (target)

1. A teacher starts a session on the device with a PIN. This works fully offline.
2. Each student looks at the camera. The device detects the face (`det_500m`), checks
   quality, aligns a 112×112 crop, embeds it (`w600k_mbf`, 512-d) and matches it
   against the section's cached templates. It shows **"Face matched – Name (USN)"** in
   under 2 s.
3. Records are queued in SQLite on the device and synced to the server. A WiFi drop
   loses nothing; the device prefetches everything it needs for all of its sections.
4. Faculty and admins watch sessions live in a browser, correct records with a
   mandatory reason (fully audited) and export reports.

Two privacy rules shape the whole design: the device **never stores raw face images**,
and **nobody is enrolled without a recorded consent**. Match thresholds come from
**measured calibration data**, never a guess.

## Repository layout

```
face-attendance/
├── README.md, CLAUDE.md        # this file; working agreement + hard constraints
├── pyproject.toml              # ruff, pytest, mypy config; package metadata
├── requirements-dev.txt        # laptop tooling (ruff, pytest, mypy)
├── conftest.py                 # pytest tiers: --run-integration, --run-pi
├── .env.example                # server config template (copy to .env)
├── .vscode/                    # extensions, settings, tasks, launch configs
├── common/                     # shared pure-Python code (device + server + tools)
│   ├── face/                   # detector, align, embedder, quality, matcher, liveness   (M3)
│   ├── schemas.py              # pydantic models shared by device client and server     (M2)
│   └── version.py
├── device/
│   ├── requirements.txt        # pip deps for the Pi venv (NO numpy/opencv: apt)
│   ├── requirements-sim.txt    # laptop simulator extras
│   ├── device.example.toml     # device config template (-> ~/.config/attendance/device.toml)
│   ├── app/                    # main, config, camera, pipeline, store, sync, ui/        (M3+)
│   └── deploy/                 # autostart entry, run wrapper, sudoers drop-in           (M1)
├── server/
│   ├── requirements.txt
│   ├── app/                    # main, config, db, models, auth, api/, admin/, services/ (M2+)
│   ├── alembic/                                                                          (M2)
│   └── deploy/                 # docker-compose.yml for Postgres; service scripts        (M2/M9)
├── tools/                      # fetch_models, enroll_bulk, capture_probes, calibrate, reembed,
│                               # bench_pi, seed_demo, deploy, export_report
├── scripts/                    # setup_pi.sh, setup_server.ps1/.sh, pg_portable.py, backup.py
├── docs/
│   ├── setup_laptop.md         # one-page laptop guide
│   ├── setup_pi.md             # Imager, SSH keys, campus WiFi (nmcli), provisioning
│   ├── wireless_dev.md         # SSH config, Tailscale/hotspot fallbacks, VS Code tasks, debugpy
│   ├── api.md, recognition.md  # device API; face pipeline, thresholds, calibration
│   ├── device.md, admin.md     # device screens + offline behaviour; admin UI + roles
│   └── operations.md, demo.md  # running, backups/restore, troubleshooting; demo script
├── models/                     # ONNX models, downloaded, never committed
├── data/                       # enrolment crops and probes, server only, never committed
└── tests/                      # common/, device/, server/, tools/
```

## Quick start (laptop)

Full guide with PostgreSQL and troubleshooting: [docs/setup_laptop.md](docs/setup_laptop.md).

```powershell
py -3.11 -m venv .venv            # or py -3.12; see the guide
.venv\Scripts\activate
python -m pip install -U pip
pip install -e . -r requirements-dev.txt -r server/requirements.txt -r device/requirements-sim.txt
copy .env.example .env
python tools/fetch_models.py      # models/ (git-ignored)
python -m pytest
```

Then open the folder in VS Code, accept the recommended extensions, and run
**Terminal → Run Task → Tests: all**.

## VS Code tasks and debug configurations

| Task | What it does | Live from |
|---|---|---|
| Tests: all | `pytest` (fast unit tests; the default test task) | M0 |
| Tests: integration (needs Postgres) | `pytest --run-integration -m integration` | M2 |
| Lint: ruff check / Lint: ruff format check | lint and formatting | M0 |
| Typecheck: mypy (common + server) | strict type check | M0 |
| Models: fetch | `tools/fetch_models.py` | M0 |
| DB: start Postgres (portable, Windows) | `scripts/pg_portable.py up`, no admin rights needed | M2 |
| DB: start Postgres (docker compose) | Postgres 16 container from `server/deploy/` | M0 |
| DB: migrate | Alembic upgrade head | M2 |
| DB: seed demo data | `tools/seed_demo.py` (admin, faculty, courses, section, students) | M2 |
| Server: run (dev) | uvicorn with reload on port 8000, admin UI at `/admin`, API docs at `/docs` | M2 |
| Device: run simulator (webcam) | device app in a window using a USB webcam | M3 |
| Pi: deploy / deploy (rsync) / restart app / tail logs / open shell / run bench | SSH to the Pi through `tools/deploy.py` (set `attendance.piHost` first) | M1 |

Debug configurations (Run and Debug panel): **Server (local)**, **Device simulator**,
**Attach to Pi device app** (debugpy on `localhost:5678` through the SSH tunnel, M1)
and **Tests: current file**.

The Pi tasks and the attach config read the SSH alias and username from
`.vscode/settings.json` (`attendance.piHost`, `attendance.piUser`). That is the only
place to change them.

## Configuration

| Where | What | Template |
|---|---|---|
| `.env` (server, never committed) | DB URL, secret key, crops/probes dirs, policy knobs | `.env.example` |
| `~/.config/attendance/device.toml` (device, never committed) | server URL, device token, detector size, provisional fallback thresholds | `device/device.example.toml` |
| `.vscode/settings.json` (committed) | Pi SSH alias and user | – |

Calibrated threshold and margin are delivered by the server during sync and always
override the fallbacks in `device.toml`.

## Fill-in values (Section 0 of the build spec)

| Placeholder | Current value | Where it lives |
|---|---|---|
| `<PI_HOST>` | `attendance-pi` (unconfirmed) | `.vscode/settings.json` → `attendance.piHost`; `~/.ssh/config` (M1) |
| `<PI_USER>` | `utkarsh` (unconfirmed) | `.vscode/settings.json` → `attendance.piUser` |
| `<SERVER_HOST>` | laptop for now | `device.toml` → `[server].url` |
| `<SERVER_OS>` | not set | decides systemd vs NSSM in `server/deploy/` (M9) |
| `<LAPTOP_OS>` | Windows 11 | interpreter path in `.vscode/settings.json` |
| `<GIT_REMOTE>` | blank: local repo only | `git remote add origin <url>` when the private repo exists |

## Dependencies

Pinned per component because the three environments differ:

- `requirements-dev.txt`: laptop tooling (ruff, pytest, mypy).
- `server/requirements.txt`: the FastAPI stack. Recognition deps are added in M4.
- `device/requirements.txt`: what pip installs on the Pi. **No numpy or opencv**:
  those come from apt and the venv is created with `--system-site-packages`.
- `device/requirements-sim.txt`: laptop simulator extras. numpy 2.2.4 and OpenCV 4.10
  are pinned on purpose to match the apt versions on the Pi (Raspberry Pi OS Trixie:
  numpy 2.2.4, OpenCV 4.10); PyQt5 comes from pip on the laptop and from apt on the Pi.

## Milestones

| # | Milestone | Status |
|---|---|---|
| M0 | From an empty folder: git init, folder skeleton, pyproject, ruff/pytest, `.vscode/`, `.gitignore`, `.env.example`, `tools/fetch_models.py`, laptop setup guide | **done** |
| M1 | Wireless access: `setup_pi.sh`, SSH docs, `deploy.py`, hello-world PyQt app autostarting on the touch display | scripts and docs written; **verify on the Pi** |
| M2 | Server core: models, Alembic migrations, seed data, admin login, roles, students/courses CRUD | **done** |
| M3 | Recognition core in `common/face/` + simulator device app with webcam | **done** |
| M4 | Enrolment: bulk import with consent CSV, on-device enrol flow, crops on server, `reembed.py` | **done** |
| M5 | Calibration: `capture_probes.py` + `calibrate.py` from scratch; threshold/margin stored on the server and delivered to devices | **done** |
| M6 | Full device app: sessions, live screen, voting/cooldown, outbox, prefetch of all assigned sections, offline catalog and PINs, sync, heartbeat | **done** |
| M7 | Admin UI: dashboard, live view, edit with reason + audit, student history, reports + CSV/XLSX, devices, audit log | **done** |
| M8 | Pi port and performance: `bench_pi.py`, tuning, autostart + crash recovery | bench, autostart and wrapper written; **measure on the Pi** |
| M9 | Hardening and demo: security pass, backups, `docs/operations.md`, demo script | **done** |

## Hard constraints

Listed in [CLAUDE.md](CLAUDE.md), which doubles as the working agreement for AI coding
agents. Highlights: Python 3.13 runtime (Trixie), 3.11-compatible code; numpy/opencv/PyQt5/picamera2 from apt on the
Pi; onnxruntime directly on the Pi (no `insightface`); models never committed; no raw
face images on the device; consent enforced at DB, API and UI level; thresholds from
measured calibration; Jinja2 + HTMX admin UI with vendored assets and no JS build step.
