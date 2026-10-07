"""fetch_models must only ever leave verified files in models/.

The tests build a tiny fake release zip, so no network is used, and inject the hashes
through PackSpec exactly like the real script does with its pinned constants.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from tools import fetch_models as fm


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RecordingDownloader:
    """Stands in for the network: writes a fixed blob and remembers each call."""

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.calls: list[str] = []

    def __call__(self, url: str, dest: Path) -> None:
        self.calls.append(url)
        dest.write_bytes(self.blob)


@pytest.fixture
def fake_pack():
    files = {
        "det_500m.onnx": b"detector-bytes" * 100,
        "w600k_mbf.onnx": b"recogniser-bytes" * 100,
        "genderage.onnx": b"unused-model" * 10,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            # Nested layout on purpose; the official zips are flat and must work too.
            archive.writestr(f"buffalo_test/{name}", data)
    blob = buf.getvalue()
    spec = fm.PackSpec(
        name="buffalo_test",
        url="https://example.invalid/buffalo_test.zip",
        zip_sha256=_sha(blob),
        size_mb=0,
        file_sha256={name: _sha(files[name]) for name in fm.REQUIRED_MODELS},
    )
    return spec, blob, files


def test_extracts_only_required_models_and_writes_manifest(tmp_path, fake_pack):
    spec, blob, files = fake_pack
    downloader = RecordingDownloader(blob)
    dest = tmp_path / "models"

    paths = fm.fetch_models(dest, spec, downloader=downloader)

    assert downloader.calls == [spec.url]
    assert [p.name for p in paths] == list(fm.REQUIRED_MODELS)
    for path in paths:
        assert path.read_bytes() == files[path.name]
    assert not (dest / "genderage.onnx").exists()
    manifest = json.loads((dest / fm.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["pack"] == "buffalo_test"
    assert manifest["files"] == dict(spec.file_sha256)
    # temp dir cleaned up, nothing else left behind
    assert sorted(p.name for p in dest.iterdir()) == sorted([*fm.REQUIRED_MODELS, fm.MANIFEST_NAME])


def test_second_run_is_a_verified_no_op_unless_forced(tmp_path, fake_pack):
    spec, blob, _ = fake_pack
    downloader = RecordingDownloader(blob)
    dest = tmp_path / "models"

    fm.fetch_models(dest, spec, downloader=downloader)
    fm.fetch_models(dest, spec, downloader=downloader)
    assert len(downloader.calls) == 1

    fm.fetch_models(dest, spec, downloader=downloader, force=True)
    assert len(downloader.calls) == 2


def test_corrupted_installed_model_is_refetched(tmp_path, fake_pack):
    spec, blob, files = fake_pack
    downloader = RecordingDownloader(blob)
    dest = tmp_path / "models"
    fm.fetch_models(dest, spec, downloader=downloader)

    (dest / "det_500m.onnx").write_bytes(b"bit rot")
    fm.fetch_models(dest, spec, downloader=downloader)

    assert len(downloader.calls) == 2
    assert (dest / "det_500m.onnx").read_bytes() == files["det_500m.onnx"]


def test_tampered_zip_is_rejected_and_nothing_is_written(tmp_path, fake_pack):
    spec, blob, _ = fake_pack
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    dest = tmp_path / "models"

    with pytest.raises(fm.FetchError, match="SHA-256 mismatch"):
        fm.fetch_models(dest, spec, downloader=RecordingDownloader(bytes(tampered)))

    assert not list(dest.glob("*.onnx"))
    assert not (dest / fm.MANIFEST_NAME).exists()
    assert not list(dest.glob(".fetch-*")), "temporary directory must be cleaned up"


def test_member_hash_mismatch_is_rejected(tmp_path, fake_pack):
    spec, blob, _ = fake_pack
    wrong = fm.PackSpec(
        name=spec.name,
        url=spec.url,
        zip_sha256=spec.zip_sha256,
        size_mb=0,
        file_sha256={**spec.file_sha256, "w600k_mbf.onnx": "0" * 64},
    )
    dest = tmp_path / "models"

    with pytest.raises(fm.FetchError, match=re.escape("w600k_mbf.onnx")):
        fm.fetch_models(dest, wrong, downloader=RecordingDownloader(blob))

    assert not list(dest.glob("*.onnx"))


def test_zip_missing_a_model_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("det_500m.onnx", b"only-detector")
    blob = buf.getvalue()
    spec = fm.PackSpec(
        name="partial",
        url="https://example.invalid/partial.zip",
        zip_sha256=_sha(blob),
        size_mb=0,
        file_sha256={"det_500m.onnx": _sha(b"only-detector"), "w600k_mbf.onnx": "0" * 64},
    )

    with pytest.raises(fm.FetchError, match=re.escape("does not contain w600k_mbf.onnx")):
        fm.fetch_models(tmp_path / "models", spec, downloader=RecordingDownloader(blob))


def test_local_zip_skips_download_and_is_left_alone(tmp_path, fake_pack):
    spec, blob, files = fake_pack
    local = tmp_path / "buffalo_test.zip"
    local.write_bytes(blob)
    downloader = RecordingDownloader(b"")

    paths = fm.fetch_models(tmp_path / "models", spec, downloader=downloader, zip_path=local)

    assert downloader.calls == []
    assert local.exists()
    assert paths[0].read_bytes() == files["det_500m.onnx"]


def test_pinned_constants_are_well_formed():
    for spec in fm.PACKS.values():
        assert spec.url.startswith(fm.RELEASE_BASE_URL)
        assert len(spec.zip_sha256) == 64 and int(spec.zip_sha256, 16)
        assert set(spec.file_sha256) >= set(fm.REQUIRED_MODELS)
    for digest in fm.MODEL_SHA256.values():
        assert len(digest) == 64 and int(digest, 16)


def test_cli_success_prints_paths(tmp_path, fake_pack, monkeypatch, capsys):
    spec, blob, _ = fake_pack
    monkeypatch.setitem(fm.PACKS, "buffalo_test", spec)
    monkeypatch.setattr(fm, "download", RecordingDownloader(blob))

    rc = fm.main(["--pack", "buffalo_test", "--dest", str(tmp_path / "models")])

    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert [Path(line).name for line in out] == list(fm.REQUIRED_MODELS)


def test_cli_failure_is_a_message_not_a_traceback(tmp_path, fake_pack, monkeypatch, capsys):
    spec, blob, _ = fake_pack
    monkeypatch.setitem(fm.PACKS, "buffalo_test", spec)
    monkeypatch.setattr(fm, "download", RecordingDownloader(blob[:-1]))

    rc = fm.main(["--pack", "buffalo_test", "--dest", str(tmp_path / "models")])

    assert rc == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err
