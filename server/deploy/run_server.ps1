# Starts the attendance server on a Windows office computer (used by the Task Scheduler
# job or the NSSM service created by scripts/setup_server.ps1). Logs go to logs\server.log.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
New-Item -ItemType Directory -Force -Path "$repo\logs" | Out-Null
# Portable PostgreSQL: start it if it is not running (no-op for Docker/native installs).
if (Test-Path "$env:USERPROFILE\.attendance\pgsql\bin\pg_ctl.exe") {
    & "$repo\.venv\Scripts\python.exe" scripts\pg_portable.py start
}
& "$repo\.venv\Scripts\python.exe" -m alembic -c server\alembic.ini upgrade head
& "$repo\.venv\Scripts\python.exe" -m uvicorn server.app.main:app --host 0.0.0.0 --port 8000 --proxy-headers *>> "$repo\logs\server.log"
