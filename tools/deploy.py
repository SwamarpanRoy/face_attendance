"""Deploy the device app to the Raspberry Pi over SSH, from Windows, macOS or Linux.

    python tools/deploy.py --host attendance-pi          # push, pull, pip if needed, restart, tail
    python tools/deploy.py --host attendance-pi --rsync  # send uncommitted changes (needs rsync)
    python tools/deploy.py --host attendance-pi --logs     # just follow the device log
    python tools/deploy.py --host attendance-pi --restart-only
    python tools/deploy.py --host attendance-pi --dry-run  # print the commands, run nothing

Uses the OpenSSH client that ships with Windows 10/11, macOS and Linux and the alias in
``~/.ssh/config`` (see docs/wireless_dev.md). The Pi side keeps a hash of
``device/requirements.txt`` next to the venv so pip only runs when it changed. The app
is restarted by killing it; the autostart wrapper (``device/deploy/autostart/
run_device.sh``) starts it again within two seconds.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG_PATH = "~/.local/state/attendance/device.log"


@dataclass(frozen=True)
class Target:
    host: str
    remote_dir: str = "~/face-attendance"
    branch: str = "main"

    def ssh(self, remote_command: str) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", self.host, remote_command]


# ------------------------------------------------------------------ remote command builders
def pull_command(target: Target) -> str:
    return (
        f"cd {target.remote_dir} && git fetch --quiet origin "
        f"&& git checkout --quiet {target.branch} && git pull --ff-only"
    )


def pip_if_changed_command(target: Target) -> str:
    """Re-run pip only when device/requirements.txt changed since the last deploy."""
    return (
        f"cd {target.remote_dir} && "
        "NEW=$(sha256sum device/requirements.txt | cut -d' ' -f1); "
        "OLD=$(cat .venv/.requirements.sha256 2>/dev/null || true); "
        'if [ "$NEW" != "$OLD" ]; then '
        "echo 'requirements changed: running pip'; "
        ".venv/bin/pip install --quiet -r device/requirements.txt "
        "&& echo $NEW > .venv/.requirements.sha256; "
        "else echo 'requirements unchanged'; fi"
    )


def restart_command() -> str:
    # The run wrapper restarts the app after it exits; killing it is the restart.
    return (
        "pkill -f 'device.app.main' && echo 'app restarting' "
        "|| echo 'app was not running (autostart will start it)'"
    )


def tail_command(lines: int = 50, follow: bool = False) -> str:
    flag = "-F" if follow else ""
    return f"tail -n {lines} {flag} {LOG_PATH} 2>/dev/null || echo 'no log yet at {LOG_PATH}'"


def rsync_command(target: Target) -> list[str]:
    excludes = [
        ".git",
        ".venv",
        "models",
        "data",
        "backups",
        "calibration",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    ]
    cmd = ["rsync", "-az", "--delete"]
    for pattern in excludes:
        cmd += ["--exclude", pattern]
    cmd += [str(REPO) + "/", f"{target.host}:{target.remote_dir}/"]
    return cmd


# ------------------------------------------------------------------ local helpers
def run(cmd: list[str], *, dry_run: bool, check: bool = True) -> int:
    print("$ " + " ".join(cmd))
    if dry_run:
        return 0
    completed = subprocess.run(cmd, check=False)
    if check and completed.returncode != 0:
        raise SystemExit(f"command failed with exit code {completed.returncode}")
    return completed.returncode


def git_is_clean() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=REPO, check=False
    )
    return out.returncode == 0 and not out.stdout.strip()


def git_has_remote() -> bool:
    out = subprocess.run(["git", "remote"], capture_output=True, text=True, cwd=REPO, check=False)
    return bool(out.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", required=True, help="SSH alias of the Pi (from ~/.ssh/config)")
    parser.add_argument("--remote-dir", default="~/face-attendance")
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--rsync", action="store_true", help="rsync the working tree instead of git push/pull"
    )
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument(
        "--restart-only", action="store_true", help="only restart the app and tail the log"
    )
    parser.add_argument("--logs", action="store_true", help="only follow the device log")
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without running them"
    )
    args = parser.parse_args(argv)

    if shutil.which("ssh") is None:
        print(
            "ssh not found. On Windows enable the OpenSSH Client optional feature.", file=sys.stderr
        )
        return 2
    target = Target(args.host, args.remote_dir, args.branch)

    if args.logs:
        return run(target.ssh(tail_command(100, follow=True)), dry_run=args.dry_run, check=False)
    if args.restart_only:
        run(target.ssh(restart_command()), dry_run=args.dry_run, check=False)
        return run(target.ssh(tail_command()), dry_run=args.dry_run, check=False)

    if args.rsync:
        if shutil.which("rsync") is None:
            print(
                "rsync not found (Windows: run from WSL). Falling back to git push/pull.",
                file=sys.stderr,
            )
            args.rsync = False
        else:
            run(rsync_command(target), dry_run=args.dry_run)
    if not args.rsync:
        if not git_is_clean():
            print(
                "warning: uncommitted changes will NOT be deployed (commit them or use --rsync)",
                file=sys.stderr,
            )
        if not git_has_remote():
            print(
                "error: no git remote configured; add one (GIT_REMOTE) or use --rsync",
                file=sys.stderr,
            )
            return 2
        run(["git", "push"], dry_run=args.dry_run)
        run(target.ssh(pull_command(target)), dry_run=args.dry_run)

    run(target.ssh(pip_if_changed_command(target)), dry_run=args.dry_run)
    if not args.no_restart:
        run(target.ssh(restart_command()), dry_run=args.dry_run, check=False)
    return run(target.ssh(tail_command()), dry_run=args.dry_run, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
