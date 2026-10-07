"""Calibration end to end: probes -> scores via the device matcher -> report -> active row."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from common.face.detector import DETECTOR_FILE
from common.face.embedder import EMBEDDER_FILE
from server.app.face import build_engine
from server.app.models import AuditLog, Calibration, ConsentMethod, TemplateSource
from server.app.services import enrolment
from tools import calibrate as calibrate_tool
from tools import capture_probes

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models"
FACES = REPO / "tests" / "fixtures" / "faces"

pytestmark = pytest.mark.skipif(
    not (MODELS / DETECTOR_FILE).exists() or not (MODELS / EMBEDDER_FILE).exists(),
    reason="ONNX models not downloaded (python tools/fetch_models.py)",
)


def _variants(image: np.ndarray) -> list[np.ndarray]:
    bright = cv2.convertScaleAbs(image, alpha=1.15, beta=10)
    return [
        cv2.flip(image, 1),
        cv2.resize(image, None, fx=0.9, fy=0.9),
        cv2.GaussianBlur(image, (3, 3), 0),
        bright,
    ]


@pytest.fixture
def two_enrolled(db, settings, students):
    """Student 1 = portrait_2, student 2 = portrait_1, each with one ID-card template."""
    engine = build_engine(settings)
    images = {
        students[0]: cv2.imread(str(FACES / "portrait_2.jpg")),
        students[1]: cv2.imread(str(FACES / "portrait_1.jpg")),
    }
    for student, image in images.items():
        consent = enrolment.record_consent(
            db,
            student,
            consent_at=datetime.now(UTC),
            consent_version="v1",
            method=ConsentMethod.PAPER,
            recorded_by=None,
        )
        result = engine.embed_single(image)
        assert result.aligned is not None, result.gate
        enrolment.store_templates(
            db,
            settings,
            engine.embedder,
            student=student,
            consent=consent,
            crops=[result.aligned],
            source=TemplateSource.IDCARD,
            actor_id=None,
        )
    return engine, images


def test_calibration_from_probes_writes_report_and_activates_row(
    db, settings, students, two_enrolled, client, device
):
    engine, images = two_enrolled
    for student, image in images.items():
        for idx, variant in enumerate(_variants(image)):
            result = engine.embed_single(variant)
            assert result.aligned is not None, (student.usn, idx, result.gate)
            capture_probes.save_probe(settings.probes_dir, student.usn, result.aligned, idx)

    op, out_dir, scores = calibrate_tool.run_calibration(
        db,
        settings,
        engine.embedder,
        target_far=0.001,
        margin_percentile=5.0,
    )
    # 8 genuine probes, each scored against the one other student -> 8 impostor trials.
    assert (op.n_genuine, op.n_impostor) == (8, 8)
    assert min(scores.genuine) > max(scores.impostor), "fixtures must be separable"
    assert max(scores.impostor) < op.threshold <= min(scores.genuine)
    assert op.far == 0.0 and op.frr == 0.0
    assert op.margin > 0.3
    assert op.far_upper_bound_95 == pytest.approx(3 / 8)
    assert any("Only 8 impostor trials" in w for w in op.warnings)

    for name in (
        "report.json",
        "report.md",
        "scores.csv",
        "histogram.png",
        "far_frr.png",
        "det.png",
    ):
        assert (out_dir / name).exists(), name
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["operating_point"]["threshold"] == pytest.approx(op.threshold)
    assert len(report["sweep"]) == 201
    assert "0 false accepts in 8 impostor trials" in (out_dir / "report.md").read_text(
        encoding="utf-8"
    )

    row = db.scalar(select(Calibration).where(Calibration.active.is_(True)))
    assert row is not None and row.threshold == pytest.approx(op.threshold)
    assert row.model_version == settings.model_version and row.n_impostor == 8
    assert db.scalar(select(AuditLog).where(AuditLog.action == "calibration.activate")) is not None

    # Devices see it on the next template sync.
    _, token = device
    feed = client.get("/api/v1/templates", headers={"Authorization": f"Bearer {token}"}).json()
    assert feed["calibrated"] is True
    assert feed["threshold"] == pytest.approx(op.threshold) and feed["margin"] == pytest.approx(
        op.margin
    )

    # A second run deactivates the first row; exactly one stays active per model.
    calibrate_tool.run_calibration(
        db, settings, engine.embedder, target_far=0.01, margin_percentile=10.0
    )
    active = list(db.scalars(select(Calibration).where(Calibration.active.is_(True))))
    assert len(active) == 1 and active[0].id != row.id
    assert active[0].target_far == pytest.approx(0.01)


def test_dry_run_reports_without_activating(db, settings, students, two_enrolled, tmp_path):
    engine, images = two_enrolled
    for student, image in images.items():
        result = engine.embed_single(cv2.flip(image, 1))
        capture_probes.save_probe(settings.probes_dir, student.usn, result.aligned, 0)
    op, out_dir, _ = calibrate_tool.run_calibration(
        db,
        settings,
        engine.embedder,
        target_far=0.001,
        margin_percentile=5.0,
        out_root=tmp_path / "cal",
        activate=False,
    )
    assert out_dir.parent == tmp_path / "cal" and (out_dir / "report.md").exists()
    assert db.scalar(select(Calibration)) is None
    assert op.n_genuine == 2


def test_calibration_refuses_without_probes_or_second_student(db, settings, students, two_enrolled):
    engine, _ = two_enrolled
    with pytest.raises(SystemExit, match="No probe crops"):
        calibrate_tool.run_calibration(
            db, settings, engine.embedder, target_far=0.001, margin_percentile=5.0
        )


def test_capture_probes_folder_import_groups_by_usn(settings, tmp_path):
    engine = build_engine(settings)
    raw = tmp_path / "raw"
    (raw / "1BM22EC001").mkdir(parents=True)
    (raw / "1BM22EC001" / "a.jpg").write_bytes((FACES / "portrait_2.jpg").read_bytes())
    (raw / "1BM22EC002_day2.jpg").write_bytes((FACES / "portrait_1.jpg").read_bytes())
    cv2.imwrite(str(raw / "1BM22EC003_blank.jpg"), np.zeros((200, 200, 3), np.uint8))
    found = capture_probes.crops_from_folder(engine, raw)
    assert set(found) == {"1BM22EC001", "1BM22EC002"}
    assert all(crop.shape == (112, 112, 3) for crops in found.values() for crop in crops)


def test_probe_upload_endpoint_requires_consent_and_stores_files(
    client, db, device, students, settings, two_enrolled
):
    engine, images = two_enrolled
    _, token = device
    headers = {"Authorization": f"Bearer {token}"}
    crop = engine.embed_single(images[students[0]]).aligned
    _, buffer = cv2.imencode(".png", crop)
    b64 = base64.b64encode(buffer.tobytes()).decode()

    no_consent = client.post(
        f"/api/v1/probes/{students[2].usn}", json={"crops_png_b64": [b64]}, headers=headers
    )
    assert no_consent.status_code == 409

    stored = client.post(
        f"/api/v1/probes/{students[0].usn}", json={"crops_png_b64": [b64, b64]}, headers=headers
    )
    assert stored.status_code == 200 and stored.json() == {"usn": students[0].usn, "stored": 2}
    assert len(list((settings.probes_dir / students[0].usn).glob("*.png"))) == 2

    bad = client.post(
        f"/api/v1/probes/{students[0].usn}", json={"crops_png_b64": ["zzz"]}, headers=headers
    )
    assert bad.status_code == 422
