"""deploy.py command building, bench percentiles, backup pruning (no network, no Pi)."""

from __future__ import annotations

from scripts import backup
from tools import bench_pi, deploy


def test_deploy_commands_are_built_for_the_right_host_and_dir():
    target = deploy.Target("attendance-pi", "~/face-attendance", "main")
    assert target.ssh("echo hi") == ["ssh", "-o", "BatchMode=yes", "attendance-pi", "echo hi"]
    assert deploy.pull_command(target) == (
        "cd ~/face-attendance && git fetch --quiet origin "
        "&& git checkout --quiet main && git pull --ff-only"
    )
    pip = deploy.pip_if_changed_command(target)
    assert "sha256sum device/requirements.txt" in pip and ".venv/bin/pip install" in pip
    assert ".venv/.requirements.sha256" in pip
    assert "pkill -f 'device.app.main'" in deploy.restart_command()
    assert deploy.tail_command(50) == (
        "tail -n 50  ~/.local/state/attendance/device.log 2>/dev/null "
        "|| echo 'no log yet at ~/.local/state/attendance/device.log'"
    )
    assert "-F" in deploy.tail_command(100, follow=True)
    rsync = deploy.rsync_command(target)
    assert rsync[:3] == ["rsync", "-az", "--delete"]
    assert rsync[-1] == "attendance-pi:~/face-attendance/"
    for excluded in (".venv", "models", "data", ".git"):
        assert excluded in rsync


def test_deploy_dry_run_prints_without_running(capsys):
    code = deploy.main(["--host", "pi", "--restart-only", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "$ ssh -o BatchMode=yes pi pkill" in out and "tail -n 50" in out


def test_bench_percentiles_and_synthetic_index():
    assert bench_pi.percentile([3.0, 1.0, 2.0], 0.5) == 2.0
    assert bench_pi.percentile([3.0, 1.0, 2.0], 0.95) == 3.0
    assert bench_pi.percentile([], 0.5) != bench_pi.percentile([], 0.5)  # nan
    index = bench_pi.synthetic_index(students=5, per_student=2)
    assert index.n_students == 5 and index.n_templates == 10


def test_backup_prune_keeps_the_newest_runs(tmp_path):
    for stamp in ("20260101-020000", "20260102-020000", "20260103-020000"):
        folder = tmp_path / stamp
        folder.mkdir()
        (folder / "attendance.dump").write_bytes(b"x")
    (tmp_path / "unrelated").mkdir()
    removed = backup.prune(tmp_path, keep=2)
    assert [p.name for p in removed] == ["20260101-020000"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "20260102-020000",
        "20260103-020000",
        "unrelated",
    ]
    assert backup.prune(tmp_path, keep=0) == []
