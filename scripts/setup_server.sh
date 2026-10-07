#!/usr/bin/env bash
# One-time setup of the attendance server on Ubuntu 24.04 (run as a sudo-capable user).
#   sudo ./scripts/setup_server.sh            # system user, /opt/face-attendance, Postgres 16, venv,
#                                             # .env, migrate, models, seed, systemd units
set -euo pipefail
APP_DIR=/opt/face-attendance
APP_USER=attendance
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.11 python3.11-venv postgresql-16 libgl1 libglib2.0-0 rsync
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude .venv --exclude .git --exclude data --exclude backups "$SRC_DIR/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  PW=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  SK=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  sed -e "s/^SECRET_KEY=.*/SECRET_KEY=$SK/" -e "s/change-me/$PW/g" "$APP_DIR/.env.example" > "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<EOSQL
CREATE ROLE attendance LOGIN PASSWORD '$PW' CREATEDB;
CREATE DATABASE attendance OWNER attendance;
EOSQL
fi

sudo -u "$APP_USER" bash -c "cd $APP_DIR && [ -x .venv/bin/python ] || python3.11 -m venv .venv"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -e . -r server/requirements.txt -r server/requirements-headless.txt"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/python -m alembic -c server/alembic.ini upgrade head && .venv/bin/python tools/fetch_models.py --pack buffalo_sc && .venv/bin/python tools/seed_demo.py"

cp "$APP_DIR"/server/deploy/face-attendance.service "$APP_DIR"/server/deploy/face-attendance-backup.* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now face-attendance face-attendance-backup.timer
echo "server running on http://$(hostname -I | awk '{print $1}'):8000/admin ; logs: journalctl -u face-attendance -f"
