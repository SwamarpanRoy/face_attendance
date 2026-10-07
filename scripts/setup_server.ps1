# One-time setup of the attendance server on a Windows 11 office computer (run in PowerShell
# from the repo root). Idempotent. Needs Python 3.11 (winget install Python.Python.3.11).
#
#   .\scripts\setup_server.ps1                # venv, deps, portable Postgres, migrate, seed, models
#   .\scripts\setup_server.ps1 -Service       # also register a scheduled task that starts the
#                                              # server at logon and restarts it if it stops
#   .\scripts\setup_server.ps1 -Nssm          # alternatively install as a Windows service via NSSM
param([switch]$Service, [switch]$Nssm, [switch]$NoSeed)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3.11 -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e . -r requirements-dev.txt -r server\requirements.txt -r server\requirements-headless.txt
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    $secret = & $py -c "import secrets; print(secrets.token_hex(32))"
    $pw = & $py -c "import secrets; print(secrets.token_urlsafe(18))"
    (Get-Content .env) -replace '^SECRET_KEY=.*', "SECRET_KEY=$secret" -replace 'change-me', $pw | Set-Content -Encoding utf8 .env
    Write-Host "created .env with generated SECRET_KEY and POSTGRES_PASSWORD"
}
& $py scripts\pg_portable.py up
& $py -m alembic -c server\alembic.ini upgrade head
& $py tools\fetch_models.py --pack buffalo_sc
if (-not $NoSeed) { & $py tools\seed_demo.py }

if ($Service) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\server\deploy\run_server.ps1`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName "FaceAttendanceServer" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "FaceAttendanceServer"
    Write-Host "scheduled task FaceAttendanceServer registered and started (http://localhost:8000/admin)"
}
if ($Nssm) {
    if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { winget install -e --id NSSM.NSSM --accept-package-agreements --accept-source-agreements }
    nssm install FaceAttendance "$repo\.venv\Scripts\python.exe" "-m uvicorn server.app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"
    nssm set FaceAttendance AppDirectory $repo
    nssm set FaceAttendance AppStdout "$repo\logs\server.log"
    nssm set FaceAttendance AppStderr "$repo\logs\server.log"
    nssm set FaceAttendance Start SERVICE_AUTO_START
    nssm start FaceAttendance
    Write-Host "NSSM service FaceAttendance installed and started"
}
Write-Host "Server setup done. Dev run: .venv\Scripts\python.exe -m uvicorn server.app.main:app --host 0.0.0.0 --port 8000"
