"""Consent enforcement at the API, template storage, the template feed, bulk import, re-embed."""

from __future__ import annotations

import base64
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select

from common.face.detector import DETECTOR_FILE
from common.face.embedder import EMBEDDER_FILE, FaceEmbedder, bytes_to_embedding
from server.app.face import build_engine
from server.app.models import (
    AuditLog,
    Consent,
    ConsentMethod,
    FaceTemplate,
    TemplateSource,
    TemplateTombstone,
)
from server.app.services import enrolment
from tools import enroll_bulk, reembed

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models"
PORTRAIT = REPO / "tests" / "fixtures" / "faces" / "portrait_2.jpg"
OTHER = REPO / "tests" / "fixtures" / "faces" / "portrait_1.jpg"

pytestmark = pytest.mark.skipif(
    not (MODELS / DETECTOR_FILE).exists() or not (MODELS / EMBEDDER_FILE).exists(),
    reason="ONNX models not downloaded (python tools/fetch_models.py)",
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def aligned_crops(settings_module) -> list[np.ndarray]:
    engine = build_engine(settings_module)
    image = cv2.imread(str(PORTRAIT))
    result = engine.embed_single(image)
    assert result.aligned is not None
    flipped = engine.embed_single(cv2.flip(image, 1))
    assert flipped.aligned is not None
    return [result.aligned, flipped.aligned, result.aligned]


@pytest.fixture(scope="module")
def settings_module(pg_test_url, tmp_path_factory):
    from server.app.config import Settings

    base = tmp_path_factory.mktemp("enrol")
    return Settings(
        database_url=pg_test_url, secret_key="x", models_dir=MODELS, crops_dir=base / "crops"
    )


def _b64_png(crop: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".png", crop)
    assert ok
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _payload(faculty_id: int, crops: list[np.ndarray], *, consent: bool) -> dict:
    body: dict = {"faculty_id": faculty_id, "crops_png_b64": [_b64_png(c) for c in crops]}
    if consent:
        body["consent"] = {
            "consent_at": datetime.now(UTC).isoformat(),
            "consent_version": "2026-10-v1",
            "method": "device",
        }
    return body


def test_upload_without_consent_is_refused_and_creates_nothing(
    client, db, device, faculty, students, aligned_crops
):
    _, token = device
    usn = students[0].usn
    response = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json=_payload(faculty.id, aligned_crops, consent=False),
        headers=_auth(token),
    )
    assert response.status_code == 409
    assert "consent" in response.json()["detail"].lower()
    assert db.scalar(select(func.count()).select_from(FaceTemplate)) == 0
    assert db.scalar(select(func.count()).select_from(Consent)) == 0


def test_upload_with_consent_stores_consent_crops_and_templates(
    client, db, device, faculty, students, aligned_crops, settings
):
    _, token = device
    usn = students[0].usn
    response = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json=_payload(faculty.id, aligned_crops, consent=True),
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usn"] == usn and body["templates_added"] == 3 and body["total_templates"] == 3

    consent = db.scalar(select(Consent).where(Consent.student_id == students[0].id))
    assert consent is not None and consent.method is ConsentMethod.DEVICE
    assert consent.recorded_by == faculty.id
    rows = list(db.scalars(select(FaceTemplate).where(FaceTemplate.student_id == students[0].id)))
    assert len(rows) == 3 and all(
        r.consent_id == consent.id and r.source is TemplateSource.DEVICE for r in rows
    )
    for row in rows:
        assert row.crop_path and (settings.crops_dir / row.crop_path).exists()
        vector = bytes_to_embedding(row.embedding)
        assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-4)
    # Flipped and original crops of the same person agree.
    e0, e1 = (bytes_to_embedding(r.embedding) for r in rows[:2])
    assert float(e0 @ e1) > 0.6
    assert (
        db.scalar(select(AuditLog).where(AuditLog.action == "enrolment.templates_added"))
        is not None
    )

    # A second upload may rely on the recorded consent (no consent block).
    again = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json=_payload(faculty.id, aligned_crops[:1], consent=False),
        headers=_auth(token),
    )
    assert again.status_code == 200 and again.json()["total_templates"] == 4


def test_upload_rejects_bad_crops_and_other_sections(
    client, db, device, faculty, students, aligned_crops
):
    _, token = device
    usn = students[0].usn
    wrong_size = cv2.resize(aligned_crops[0], (64, 64))
    bad = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json=_payload(faculty.id, [wrong_size], consent=True),
        headers=_auth(token),
    )
    assert bad.status_code == 422

    garbage = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json={
            "faculty_id": faculty.id,
            "crops_png_b64": ["not base64!!"],
            "consent": _payload(faculty.id, [], consent=True)["consent"],
        },
        headers=_auth(token),
    )
    assert garbage.status_code == 422

    students[0].section_id = None
    db.commit()
    elsewhere = client.post(
        f"/api/v1/enrolment/{usn}/captures",
        json=_payload(faculty.id, aligned_crops, consent=True),
        headers=_auth(token),
    )
    assert elsewhere.status_code == 403
    assert client.get("/api/v1/templates", headers=_auth(token)).json()["students"] == []


def test_templates_feed_full_incremental_and_deletions(
    client, db, device, faculty, students, aligned_crops, settings
):
    _, token = device
    for student in students[:2]:
        assert (
            client.post(
                f"/api/v1/enrolment/{student.usn}/captures",
                json=_payload(faculty.id, aligned_crops[:2], consent=True),
                headers=_auth(token),
            ).status_code
            == 200
        )

    full = client.get("/api/v1/templates", headers=_auth(token)).json()
    assert full["full"] is True and full["calibrated"] is False and full["threshold"] is None
    assert [s["usn"] for s in full["students"]] == [students[0].usn, students[1].usn]
    assert all(len(s["templates"]) == 2 for s in full["students"])
    blob = base64.b64decode(full["students"][0]["templates"][0]["embedding_b64"])
    assert len(blob) == 2048

    since = full["generated_at"]
    nothing = client.get("/api/v1/templates", params={"since": since}, headers=_auth(token)).json()
    assert nothing["full"] is False and nothing["students"] == [] and nothing["deleted_usns"] == []

    # Withdraw consent for student 1: tombstone + deletion visible incrementally.
    removed = enrolment.withdraw_consent(
        db, settings, students[1], actor_id=None, reason="student request"
    )
    assert removed == 2
    assert (
        db.scalar(select(TemplateTombstone).where(TemplateTombstone.usn == students[1].usn))
        is not None
    )
    delta = client.get("/api/v1/templates", params={"since": since}, headers=_auth(token)).json()
    assert delta["deleted_usns"] == [students[1].usn]
    assert delta["students"] == []

    # New templates for student 2 after `since` show up with all of that student's templates.
    assert (
        client.post(
            f"/api/v1/enrolment/{students[2].usn}/captures",
            json=_payload(faculty.id, aligned_crops, consent=True),
            headers=_auth(token),
        ).status_code
        == 200
    )
    delta2 = client.get("/api/v1/templates", params={"since": since}, headers=_auth(token)).json()
    assert [s["usn"] for s in delta2["students"]] == [students[2].usn]
    assert len(delta2["students"][0]["templates"]) == 3


def test_bulk_import_refuses_missing_consent_and_reports(db, settings, students, tmp_path):
    engine = build_engine(settings)
    photos = tmp_path / "idcards"
    photos.mkdir()
    (photos / f"{students[0].usn}.jpg").write_bytes(PORTRAIT.read_bytes())  # consent present
    (photos / f"{students[1].usn}.jpg").write_bytes(PORTRAIT.read_bytes())  # no consent row
    (photos / "1BM22EC999.jpg").write_bytes(PORTRAIT.read_bytes())  # unknown student
    blank = np.zeros((300, 300, 3), np.uint8)
    cv2.imwrite(str(photos / f"{students[2].usn}.jpg"), blank)  # consent present, no face

    consents = tmp_path / "consents.csv"
    consents.write_text(
        "usn,consent_at,consent_version,method\n"
        f"{students[0].usn},2026-09-01T10:00:00+05:30,2026-10-v1,paper\n"
        f"{students[2].usn},2026-09-01T10:00:00+05:30,2026-10-v1,bulk_csv\n"
        "1BM22EC999,2026-09-01,2026-10-v1,paper\n",
        encoding="utf-8",
    )
    rows = enroll_bulk.run_bulk(db, settings, engine, photos, enroll_bulk.load_consents(consents))
    by_usn = {r.usn: r for r in rows}
    assert by_usn[students[0].usn].status == "accepted" and by_usn[students[0].usn].templates == 1
    assert (
        by_usn[students[1].usn].status == "rejected"
        and "no consent" in by_usn[students[1].usn].reason
    )
    assert (
        by_usn["1BM22EC999"].status == "rejected" and "unknown USN" in by_usn["1BM22EC999"].reason
    )
    assert (
        by_usn[students[2].usn].status == "rejected" and "no_face" in by_usn[students[2].usn].reason
    )

    template = db.scalar(select(FaceTemplate).where(FaceTemplate.student_id == students[0].id))
    assert template is not None and template.source is TemplateSource.IDCARD
    assert template.quality_score and template.quality_score > 0
    consent = db.scalar(select(Consent).where(Consent.student_id == students[0].id))
    assert consent is not None and consent.method is ConsentMethod.PAPER

    report = tmp_path / "report.csv"
    enroll_bulk.write_report(rows, report)
    with report.open(encoding="utf-8") as handle:
        parsed = list(csv.DictReader(handle))
    assert {r["status"] for r in parsed} == {"accepted", "rejected"}

    # Re-running with --replace keeps exactly one ID-card template per student.
    rows = enroll_bulk.run_bulk(
        db, settings, engine, photos, enroll_bulk.load_consents(consents), replace=True
    )
    count = db.scalar(
        select(func.count())
        .select_from(FaceTemplate)
        .where(FaceTemplate.student_id == students[0].id)
    )
    assert count == 1


def test_consent_csv_validation(tmp_path):
    bad = tmp_path / "c.csv"
    bad.write_text("usn,consent_at\n1BM22EC001,2026-01-01\n", encoding="utf-8")
    with pytest.raises(enroll_bulk.ConsentCsvError, match="needs columns"):
        enroll_bulk.load_consents(bad)
    bad.write_text(
        "usn,consent_at,consent_version,method\n1BM22EC001,2026-01-01,v1,device\n", encoding="utf-8"
    )
    with pytest.raises(enroll_bulk.ConsentCsvError, match="reserved"):
        enroll_bulk.load_consents(bad)
    bad.write_text(
        "usn,consent_at,consent_version,method\n1BM22EC001,yesterday,v1,paper\n", encoding="utf-8"
    )
    with pytest.raises(enroll_bulk.ConsentCsvError, match="ISO 8601"):
        enroll_bulk.load_consents(bad)


def test_reembed_regenerates_templates_from_crops(db, settings, students, aligned_crops):
    engine = build_engine(settings)
    consent = enrolment.record_consent(
        db,
        students[0],
        consent_at=datetime.now(UTC) - timedelta(days=1),
        consent_version="v1",
        method=ConsentMethod.PAPER,
        recorded_by=None,
    )
    enrolment.store_templates(
        db,
        settings,
        engine.embedder,
        student=students[0],
        consent=consent,
        crops=aligned_crops[:2],
        source=TemplateSource.IDCARD,
        actor_id=None,
        model_version="old-model",
    )
    stats = reembed.reembed(db, settings, FaceEmbedder(MODELS / EMBEDDER_FILE), "new-model")
    assert (stats.students, stats.created, stats.removed) == (1, 2, 2)
    versions = list(
        db.scalars(
            select(FaceTemplate.model_version).where(FaceTemplate.student_id == students[0].id)
        )
    )
    assert versions == ["new-model", "new-model"]
    assert stats.missing_crops == []
