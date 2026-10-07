# Laptop setup (one page)

Everything here runs on the development laptop. Until the office computer exists, the
server and PostgreSQL run here too. Until the Pi arrives, the device app runs in
simulator mode with a USB webcam. Commands are for Windows 11 PowerShell; macOS/Linux
differences are noted inline.

## 1. Install the basics

| Tool | Windows 11 | macOS / Ubuntu |
|---|---|---|
| Python 3.13 | `winget install Python.Python.3.13`, then check `py -3.13 -V` | installer from python.org (then run *Install Certificates.command*, see Troubleshooting) / `sudo apt install python3.13 python3.13-venv` |
| Git | `winget install Git.Git` | preinstalled / `brew install git` |
| VS Code | `winget install Microsoft.VisualStudioCode` | <https://code.visualstudio.com> |
| PostgreSQL 16 | Docker Desktop (recommended) or a native install, see step 4 | same |

Python 3.13 is the project standard because the Pi (Raspberry Pi OS Trixie) ships it;
the full test suite passes on macOS with 3.13.9. ruff and mypy keep the code
3.11-compatible, so 3.11 and 3.12 also work for laptop development.

## 2. Clone and create the environment

```powershell
git clone <GIT_REMOTE> face-attendance     # or copy the folder if there is no remote yet
cd face-attendance
py -3.11 -m venv .venv                     # or: py -3.12 -m venv .venv
.venv\Scripts\activate                     # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -U pip
pip install -e . -r requirements-dev.txt -r server/requirements.txt -r device/requirements-sim.txt
copy .env.example .env                     # then edit: SECRET_KEY, POSTGRES_PASSWORD, DATABASE_URL
```

macOS/Linux: `python3.11 -m venv .venv && source .venv/bin/activate`, then the same
`pip install` line, and `cp .env.example .env`.

## 3. Open in VS Code

1. File, Open Folder, pick `face-attendance`.
2. Accept the "install recommended extensions" prompt (Remote-SSH, Python, debugpy, Ruff,
   SQLTools + PostgreSQL driver, REST Client).
3. If asked for an interpreter, choose `.venv`. On macOS/Linux also change
   `python.defaultInterpreterPath` in `.vscode/settings.json` to `.venv/bin/python`.
4. Terminal, Run Task, **Tests: all**. It must be green before you change anything.

## 4. PostgreSQL 16 (needed from milestone M2)

**Option A, portable binaries, Windows, no admin rights (what the team laptop uses).**
One command downloads the official EDB binaries into `~\.attendance\pgsql`, creates a
cluster in `~\.attendance\pgdata` owned by the role from `.env`, starts it as a normal
user process and creates the database:

```powershell
python scripts/pg_portable.py up        # VS Code task: DB: start Postgres (portable, Windows)
python scripts/pg_portable.py status    # start | stop | psql also work
```

Run `python scripts/pg_portable.py start` again after a reboot (nothing is installed as a
service). Use `127.0.0.1`, not `localhost`, in `DATABASE_URL`: on Windows `localhost`
tries IPv6 first and can add a 10 s delay per connection.

**Option B, Docker.** Install Docker Desktop
(`winget install Docker.DockerDesktop`, reboot, start it once), then from the repo root:

```powershell
docker compose --env-file .env -f server/deploy/docker-compose.yml up -d
docker compose --env-file .env -f server/deploy/docker-compose.yml ps     # wait for "healthy"
```

The VS Code task **DB: start Postgres (docker compose)** runs the first line. The database
listens on `127.0.0.1:5432` with the user, password and database name from `.env`.

**Option C, native install.** Install PostgreSQL 16 (Windows: the EDB installer; Ubuntu:
`sudo apt install postgresql-16`). Then in `psql` as the superuser:

```sql
CREATE ROLE attendance LOGIN PASSWORD 'change-me';   -- use the password from .env
CREATE DATABASE attendance OWNER attendance;
```

The integration tests create and drop their own `attendance_test` database, so the role
needs `CREATEDB`: `ALTER ROLE attendance CREATEDB;`.

Either way, `DATABASE_URL` in `.env` must match. SQLTools in VS Code already has a
connection named "attendance (local)" that asks for the password.

## 5. Face models

```powershell
python tools/fetch_models.py
```

This downloads `buffalo_s.zip` from the official InsightFace GitHub release, verifies its
SHA-256 and the SHA-256 of each model, and writes `models/det_500m.onnx` and
`models/w600k_mbf.onnx`. The folder is git-ignored. Options: `--pack buffalo_sc` fetches
the 16 MB pack that contains the same two files; `--zip <path>` uses a zip you already
downloaded (for an offline Pi).

## 6. Migrate, seed, verify

```powershell
python -m alembic -c server/alembic.ini upgrade head   # task: DB: migrate
python tools/seed_demo.py                              # task: DB: seed demo data
python -m pytest                                       # server tests use a throwaway attendance_test DB
ruff check . ; ruff format --check .
mypy
```

`seed_demo.py` prints the demo admin and faculty credentials once (set `SEED_*` in `.env`
to choose them). Then run the task **Server: run (dev)** and open
<http://localhost:8000/admin>.

## 7. Day to day

| Want to | Do |
|---|---|
| run the tests | task **Tests: all** (or `python -m pytest`) |
| lint / format | save a file (Ruff formats on save), or tasks **Lint: ruff check** / **Lint: ruff format check** |
| run the server (M2+) | task **Server: run (dev)**, then open <http://localhost:8000/admin> |
| apply DB migrations (M2+) | task **DB: migrate** |
| run the device simulator (M3+) | task **Device: run simulator (webcam)**, or debug config **Device simulator** |
| talk to the Pi (M1) | tasks **Pi: deploy / restart app / tail logs / open shell / run bench** |

## Troubleshooting

- `py` is not recognised: reinstall Python from python.org with "py launcher" ticked, or
  call `python -m venv .venv` with a 3.11 interpreter on PATH.
- `.venv\Scripts\activate` is blocked: run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
- PyQt5 import error on Windows: make sure the venv is 64-bit Python 3.11 to 3.13.
- macOS, `CERTIFICATE_VERIFY_FAILED` when running `tools/fetch_models.py`: Python from
  python.org does not use the macOS certificate store. Double-click
  *Applications → Python 3.13 → Install Certificates.command* once, or run
  `SSL_CERT_FILE=$(python -m certifi) python tools/fetch_models.py`.
- The repo lives in a OneDrive-synced folder: pause syncing while installing packages if
  pip reports "file in use" errors, or move the repo outside OneDrive.
- Docker: `error during connect` means Docker Desktop is not running.
