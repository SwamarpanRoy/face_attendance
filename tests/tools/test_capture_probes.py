"""capture_probes on the Pi: no server imports, device camera, device.toml url/token."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from common.face.detector import DETECTOR_FILE
from common.face.embedder import EMBEDDER_FILE
from tools import capture_probes

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models"
FACE = REPO / "tests" / "fixtures" / "faces" / "portrait_2.jpg"
models_missing = pytest.mark.skipif(
    not (MODELS / DETECTOR_FILE).exists() or not (MODELS / EMBEDDER_FILE).exists(),
    reason="ONNX models not downloaded (python tools/fetch_models.py)",
)


def test_importing_the_tool_does_not_need_the_server_stack():
    # The Pi's venv has no sqlalchemy/fastapi/server code: the import itself must not
    # pull them in, or `--device` fails before it starts.
    code = (
        "import sys, tools.capture_probes; "
        "print(sorted({m.split('.')[0] for m in sys.modules} "
        "& {'server', 'sqlalchemy', 'fastapi'}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


class FakeSource:
    description = "fake"

    def __init__(self, frames: int) -> None:
        self.left = frames
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def read(self):
        self.left -= 1
        return np.zeros((8, 8, 3), np.uint8)

    def stop(self) -> None:
        self.stopped = True


class FakeEngine:
    """First frame fails a gate, then every frame passes."""

    def __init__(self) -> None:
        self.calls = 0

    def process(self, frame, *, embed=True):
        self.calls += 1
        ok = self.calls > 1
        gate = SimpleNamespace(ok=ok, message="" if ok else "Move closer")
        aligned = np.full((112, 112, 3), self.calls, np.uint8) if ok else None
        return SimpleNamespace(gate=gate, aligned=aligned, detections=[])


def test_capture_from_camera_collects_gated_crops_and_prints_hints(capsys):
    source = FakeSource(frames=10)
    crops = capture_probes.capture_from_camera(FakeEngine(), 3, source, gap_s=0.0, preview=False)
    assert len(crops) == 3 and all(c.shape == (112, 112, 3) for c in crops)
    assert source.started and source.stopped
    out = capsys.readouterr().out
    assert "Move closer" in out and "Captured 3/3" in out


@models_missing
def test_device_mode_uses_device_toml_and_uploads(tmp_path, monkeypatch):
    config = tmp_path / "device.toml"
    config.write_text(
        '[server]\nurl = "http://10.0.0.5:8000"\ntoken = "tok-123"\n'
        f'[paths]\nmodels_dir = "{MODELS}"\nstate_dir = "{tmp_path}"\n'
    )
    sent: list[tuple[str, str, str, int]] = []

    def fake_upload(base_url, token, usn, crops):
        sent.append((base_url, token, usn, len(crops)))
        return len(crops)

    monkeypatch.setattr(capture_probes, "upload_probes", fake_upload)
    code = capture_probes.main(
        [
            *("--device", "--config", str(config), "--usn", "1bm22ec001", "--image", str(FACE)),
            *("--count", "2", "--gap", "0", "--no-preview"),
        ]
    )
    assert code == 0
    assert sent == [("http://10.0.0.5:8000", "tok-123", "1BM22EC001", 2)]


def test_device_mode_refuses_without_server_settings(tmp_path):
    config = tmp_path / "device.toml"
    config.write_text(f'[paths]\nmodels_dir = "{MODELS}"\nstate_dir = "{tmp_path}"\n')
    with pytest.raises(SystemExit, match=r"device\.toml"):
        capture_probes.main(["--device", "--config", str(config), "--usn", "X", "--no-preview"])
