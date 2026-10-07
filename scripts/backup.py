"""Nightly backup: pg_dump of the database plus a zip of the enrolment crops and probes.

    python scripts/backup.py                      # -> backups/<stamp>/{attendance.dump, data.zip}
    python scripts/backup.py --keep 14            # prune backups older than the newest 14
    python scripts/backup.py --restore backups/<stamp>   # restore both (asks first)

``pg_dump``/``pg_restore`` are found on PATH or in the portable install
(``~/.attendance/pgsql/bin``). The dump uses the custom format so a restore can be
selective. Schedule it with Task Scheduler (Windows) or the systemd timer in
``server/deploy/`` (Linux); see docs/operations.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from server.app.config import Settings

REPO = Path(__file__).resolve().parents[1]
PORTABLE_BIN = Path.home() / ".attendance" / "pgsql" / "bin"


def find_tool(name: str) -> str:
    exe = name + (".exe" if sys.platform == "win32" else "")
    found = shutil.which(name) or (
        str(PORTABLE_BIN / exe) if (PORTABLE_BIN / exe).exists() else None
    )
    if not found:
        raise SystemExit(f"{name} not found on PATH or in {PORTABLE_BIN}")
    return found


def pg_env(settings: Settings) -> tuple[dict[str, str], list[str]]:
    url = make_url(settings.database_url)
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    args = [
        "-h",
        url.host or "127.0.0.1",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "attendance",
    ]
    return env, args


def backup(settings: Settings, out_root: Path, keep: int) -> Path:
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    target = out_root / stamp
    target.mkdir(parents=True, exist_ok=True)
    env, conn = pg_env(settings)
    database = make_url(settings.database_url).database or "attendance"
    dump = target / "attendance.dump"
    subprocess.run(
        [find_tool("pg_dump"), *conn, "-Fc", "-f", str(dump), database], env=env, check=True
    )

    archive = target / "data.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in (settings.crops_dir, settings.probes_dir):
            if folder.exists():
                for path in folder.rglob("*"):
                    if path.is_file():
                        zf.write(path, Path(folder.name) / path.relative_to(folder))
    (target / "MANIFEST.txt").write_text(
        f"created {stamp}\ndatabase {database}\ndump {dump.name} ({dump.stat().st_size} bytes)\n"
        f"data {archive.name} ({archive.stat().st_size} bytes)\n"
        f"crops_dir {settings.crops_dir}\nprobes_dir {settings.probes_dir}\n",
        encoding="utf-8",
    )
    prune(out_root, keep)
    return target


def prune(out_root: Path, keep: int) -> list[Path]:
    runs = sorted(p for p in out_root.iterdir() if p.is_dir() and (p / "attendance.dump").exists())
    removed = []
    for old in runs[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old)
    return removed


def restore(settings: Settings, folder: Path, *, yes: bool) -> None:
    dump = folder / "attendance.dump"
    archive = folder / "data.zip"
    if not dump.exists():
        raise SystemExit(f"{dump} not found")
    database = make_url(settings.database_url).database or "attendance"
    if not yes:
        answer = input(
            f"This REPLACES database {database!r} and {settings.crops_dir} with {folder}. "
            "Type RESTORE to continue: "
        )
        if answer.strip() != "RESTORE":
            raise SystemExit("aborted")
    env, conn = pg_env(settings)
    subprocess.run(
        [
            find_tool("pg_restore"),
            *conn,
            "--clean",
            "--if-exists",
            "--no-owner",
            "-d",
            database,
            str(dump),
        ],
        env=env,
        check=True,
    )
    if archive.exists():
        base = settings.crops_dir.parent
        base.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(base)
    print(f"restored {folder}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=REPO / "backups")
    parser.add_argument("--keep", type=int, default=30, help="how many backups to keep (0 = all)")
    parser.add_argument("--restore", type=Path, default=None, metavar="FOLDER")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation on restore")
    args = parser.parse_args(argv)
    settings = Settings()
    if args.restore:
        restore(settings, args.restore, yes=args.yes)
        return 0
    target = backup(settings, args.out, args.keep)
    print(f"backup written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
