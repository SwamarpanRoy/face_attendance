"""Run PostgreSQL 16 on a Windows laptop without admin rights or Docker.

Why: college laptops often cannot install services or Docker Desktop. The official
EDB "binaries only" zip needs no installer, so this script downloads it once into
``~/.attendance/pgsql``, initialises a cluster in ``~/.attendance/pgdata`` owned by the
``attendance`` role from ``.env``, and starts/stops it as a normal user process. Nothing
touches the registry or Windows services. On macOS/Linux use Docker or the system
package instead (see docs/setup_laptop.md); this script refuses to run there.

Usage (from the repo root, venv active)::

    python scripts/pg_portable.py up        # install + init + start + create database
    python scripts/pg_portable.py status
    python scripts/pg_portable.py stop
    python scripts/pg_portable.py start
    python scripts/pg_portable.py psql      # interactive psql as the attendance role

Credentials come from ``.env`` (``POSTGRES_USER``, ``POSTGRES_PASSWORD``,
``POSTGRES_DB``), so the result matches ``DATABASE_URL`` exactly.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home() / ".attendance"
PG_HOME = HOME / "pgsql"
PG_DATA = HOME / "pgdata"
PG_LOG = HOME / "postgres.log"
EDB_BASE = "https://get.enterprisedb.com/postgresql"
# Newest first; the first one that exists on EDB's server is used. Pin with --version.
CANDIDATE_VERSIONS = [f"16.{minor}" for minor in range(16, 3, -1)]
DEFAULT_PORT = 5432


def load_env(path: Path = REPO_ROOT / ".env") -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, # comments). Avoids importing the server package."""
    if not path.exists():
        sys.exit(f"{path} not found. Copy .env.example to .env and set POSTGRES_PASSWORD first.")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().split(" #")[0].strip()
    return values


def credentials(env: dict[str, str]) -> tuple[str, str, str]:
    user = env.get("POSTGRES_USER", "attendance")
    password = env.get("POSTGRES_PASSWORD", "")
    database = env.get("POSTGRES_DB", "attendance")
    if not password or password == "change-me":
        sys.exit("Set a real POSTGRES_PASSWORD in .env before running this script.")
    return user, password, database


def bin_path(name: str) -> Path:
    return PG_HOME / "bin" / f"{name}.exe"


def url_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "face-attendance"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status) == 200
    except urllib.error.HTTPError:
        return False


def resolve_version(explicit: str | None) -> str:
    if explicit:
        return explicit
    for version in CANDIDATE_VERSIONS:
        if url_exists(f"{EDB_BASE}/postgresql-{version}-1-windows-x64-binaries.zip"):
            return version
    sys.exit("Could not find a PostgreSQL 16 binaries zip on get.enterprisedb.com; pass --version.")


def install(version: str | None) -> None:
    """Download and unpack the EDB binaries zip once (~330 MB)."""
    if bin_path("pg_ctl").exists():
        print(f"PostgreSQL binaries already present in {PG_HOME}")
        return
    resolved = resolve_version(version)
    url = f"{EDB_BASE}/postgresql-{resolved}-1-windows-x64-binaries.zip"
    HOME.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    with tempfile.TemporaryDirectory(dir=HOME) as tmp:
        zip_path = Path(tmp) / "pg.zip"
        request = urllib.request.Request(url, headers={"User-Agent": "face-attendance"})
        with urllib.request.urlopen(request, timeout=60) as response, zip_path.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and done % (50 << 20) < (1 << 20):
                    print(f"  {done / 1e6:6.0f} / {total / 1e6:.0f} MB")
        print("extracting (binaries only: bin, lib, share)")
        with zipfile.ZipFile(zip_path) as archive:
            wanted = ("pgsql/bin/", "pgsql/lib/", "pgsql/share/")
            members = [m for m in archive.namelist() if m.startswith(wanted)]
            archive.extractall(HOME, members)
    if not bin_path("pg_ctl").exists():
        sys.exit(
            "extraction finished but pgsql/bin/pg_ctl.exe is missing; "
            "delete ~/.attendance/pgsql and retry"
        )
    print(f"installed PostgreSQL {resolved} binaries into {PG_HOME}")


def init(env: dict[str, str]) -> None:
    """Create the cluster with the .env role as its superuser (dev laptop, not production)."""
    if (PG_DATA / "PG_VERSION").exists():
        print(f"cluster already initialised in {PG_DATA}")
        return
    user, password, _ = credentials(env)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=HOME, encoding="utf-8") as pwfile:
        pwfile.write(password + "\n")
        pwfile_path = Path(pwfile.name)
    try:
        subprocess.run(
            [
                str(bin_path("initdb")),
                "-D",
                str(PG_DATA),
                "-U",
                user,
                "--auth=scram-sha-256",
                f"--pwfile={pwfile_path}",
                "-E",
                "UTF8",
                "--locale=C",
            ],
            check=True,
        )
    finally:
        pwfile_path.unlink(missing_ok=True)
    conf = PG_DATA / "postgresql.conf"
    with conf.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n# face-attendance dev defaults\n"
            "listen_addresses = 'localhost'\n"
            f"port = {DEFAULT_PORT}\n"
            "log_timezone = 'UTC'\ntimezone = 'UTC'\n"
        )
    print(f"initialised cluster in {PG_DATA}")


def pg_ctl(*args: str) -> int:
    return subprocess.run(
        [str(bin_path("pg_ctl")), "-D", str(PG_DATA), "-l", str(PG_LOG), *args]
    ).returncode


def is_running() -> bool:
    return pg_ctl("status") == 0


def start() -> None:
    if is_running():
        print("PostgreSQL is already running")
        return
    if pg_ctl("-w", "start") != 0:
        sys.exit(f"failed to start PostgreSQL; see {PG_LOG}")
    print(f"PostgreSQL started on 127.0.0.1:{DEFAULT_PORT} (log: {PG_LOG})")


def stop() -> None:
    if not is_running():
        print("PostgreSQL is not running")
        return
    pg_ctl("-m", "fast", "stop")


def psql(
    env: dict[str, str], *sql: str, database: str | None = None, interactive: bool = False
) -> int:
    user, password, default_db = credentials(env)
    cmd = [
        str(bin_path("psql")),
        "-h",
        "127.0.0.1",
        "-p",
        str(DEFAULT_PORT),
        "-U",
        user,
        "-d",
        database or default_db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    for statement in sql:
        cmd += ["-c", statement]
    return subprocess.run(cmd, env={**os.environ, "PGPASSWORD": password}, check=False).returncode


def create_database(env: dict[str, str]) -> None:
    _, _, database = credentials(env)
    exists = subprocess.run(
        [
            str(bin_path("psql")),
            "-h",
            "127.0.0.1",
            "-p",
            str(DEFAULT_PORT),
            "-U",
            credentials(env)[0],
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{database}'",
        ],
        env={**os.environ, "PGPASSWORD": credentials(env)[1]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if exists == "1":
        print(f"database {database} already exists")
        return
    if psql(env, f'CREATE DATABASE "{database}"', database="postgres") != 0:
        sys.exit("CREATE DATABASE failed")
    print(f"created database {database}")


def main(argv: list[str] | None = None) -> int:
    if platform.system() != "Windows":
        sys.exit(
            "This helper is for Windows laptops. "
            "On macOS/Linux use Docker or the system PostgreSQL 16."
        )
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=["up", "install", "init", "start", "stop", "status", "createdb", "psql"]
    )
    parser.add_argument(
        "--version", help="PostgreSQL version to download, e.g. 16.10 (default: newest available)"
    )
    args = parser.parse_args(argv)

    if args.command == "install":
        install(args.version)
        return 0
    env = load_env()
    if args.command == "up":
        install(args.version)
        init(env)
        start()
        create_database(env)
        print("ready: DATABASE_URL in .env should now connect")
    elif args.command == "init":
        init(env)
    elif args.command == "start":
        start()
    elif args.command == "stop":
        stop()
    elif args.command == "status":
        return pg_ctl("status")
    elif args.command == "createdb":
        create_database(env)
    elif args.command == "psql":
        return psql(env, interactive=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
