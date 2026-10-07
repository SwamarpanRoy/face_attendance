"""Device config loading, the SQLite store and the image camera source."""

from __future__ import annotations

import numpy as np
import pytest

from common.face.matcher import Template
from device.app.camera import CameraError, ImageSource, OpenCVSource, make_source
from device.app.config import CONFIG_ENV, DeviceConfig
from device.app.store import DeviceStore

FIXTURE = "tests/fixtures/faces/portrait_2.jpg"


def test_config_defaults_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / "nope.toml"))
    config = DeviceConfig.load()
    assert config.source_path is None
    assert config.recognition.detector_input == 320
    assert config.server.configured is False
    assert config.match_config().threshold == pytest.approx(0.40)


def test_config_reads_toml_and_builds_pipeline_configs(tmp_path):
    path = tmp_path / "device.toml"
    path.write_text(
        '[device]\nid = "pi-07"\n'
        '[server]\nurl = "http://10.0.0.2:8000"\ntoken = "abc"\n'
        "[recognition]\ndetector_input = 480\nfallback_threshold = 0.55\nmin_face_width_px = 120\n"
        "[sync]\nprefetch_interval_s = 120\n"
        f'[paths]\nstate_dir = "{(tmp_path / "state").as_posix()}"\n',
        encoding="utf-8",
    )
    config = DeviceConfig.load(path)
    assert config.source_path == path
    assert config.device.id == "pi-07"
    assert config.server.configured is True
    engine = config.engine_config()
    assert engine.detector.input_size == 480
    assert engine.quality.min_face_width_px == 120
    assert config.match_config().threshold == pytest.approx(0.55)
    assert config.match_config(0.61, 0.07).threshold == pytest.approx(0.61)
    assert config.match_config(0.61, 0.07).margin == pytest.approx(0.07)
    assert config.sync.prefetch_interval_s == 120
    assert config.db_path == tmp_path / "state" / "device.db"
    assert config.log_path.name == "device.log"


def test_config_rejects_out_of_range_values(tmp_path):
    path = tmp_path / "device.toml"
    path.write_text("[recognition]\nfallback_threshold = 1.5\n", encoding="utf-8")
    with pytest.raises(ValueError):
        DeviceConfig.load(path)


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_store_templates_round_trip(tmp_path):
    store = DeviceStore(tmp_path / "device.db")
    assert store.load_templates() == []
    idx0 = store.add_template("1BM22EC001", "Aditi", _vec(1), "m1", section_id=5)
    idx1 = store.add_template("1BM22EC001", "Aditi", _vec(2), "m1", section_id=5)
    assert (idx0, idx1) == (0, 1)
    store.add_template("1BM22EC002", "Arjun", _vec(3), "m1", section_id=6)

    all_templates = store.load_templates()
    assert [t.usn for t in all_templates] == ["1BM22EC001", "1BM22EC001", "1BM22EC002"]
    assert np.allclose(all_templates[0].embedding, _vec(1))
    assert [t.usn for t in store.load_templates(section_id=6)] == ["1BM22EC002"]

    counts = store.counts()
    assert (counts.templates, counts.students, counts.pending_events) == (3, 2, 0)

    replaced = store.replace_section_templates(5, [("1BM22EC003", "Bhavana", 0, _vec(4), "m2")])
    assert replaced == 1
    assert [t.usn for t in store.load_templates(section_id=5)] == ["1BM22EC003"]
    assert store.delete_student_templates(["1BM22EC003", "nobody"]) == 1
    assert store.load_templates(section_id=5) == []
    store.close()


def test_store_settings_calibration_and_catalog(tmp_path):
    store = DeviceStore(tmp_path / "device.db")
    assert store.calibration() is None
    store.set_calibration(0.47, 0.06, "m1")
    assert store.calibration() == (pytest.approx(0.47), pytest.approx(0.06))
    assert store.get_setting("calibration_model_version") == "m1"
    assert store.get_setting("missing", "dflt") == "dflt"

    store.put_catalog("courses", [{"id": 2, "code": "B"}, {"id": 1, "code": "A"}])
    assert [c["code"] for c in store.get_catalog("courses")] == ["A", "B"]
    store.put_catalog("courses", [{"id": 9, "code": "Z"}])
    assert [c["id"] for c in store.get_catalog("courses")] == [9]
    store.close()


def test_store_uses_the_shared_embedding_layout(tmp_path):
    store = DeviceStore(tmp_path / "device.db")
    vec = _vec(7)
    store.add_template("1BM22EC009", "X", vec, "m1")
    template: Template = store.load_templates()[0]
    assert template.embedding.dtype == np.float32 and template.embedding.shape == (512,)
    assert np.array_equal(template.embedding, vec)
    store.close()


def test_image_source_loops_and_returns_copies():
    source = ImageSource(__import__("pathlib").Path(FIXTURE), fps=1000)
    source.start()
    first = source.read()
    second = source.read()
    assert first is not None and second is not None
    assert first.shape == second.shape and first is not second
    first[0, 0] = 0
    assert not np.array_equal(first, source.read())
    source.stop()


def test_image_source_missing_path_is_a_camera_error(tmp_path):
    with pytest.raises(CameraError):
        ImageSource(tmp_path / "missing.jpg").start()


def test_make_source_prefers_image_then_sim(tmp_path):
    assert isinstance(
        make_source(sim=True, webcam_index=0, size=(640, 480), image=tmp_path), ImageSource
    )
    assert isinstance(
        make_source(sim=True, webcam_index=1, size=(640, 480), image=None), OpenCVSource
    )
    pi = make_source(sim=False, webcam_index=0, size=(1280, 960), image=None)
    assert "picamera2" in pi.description
