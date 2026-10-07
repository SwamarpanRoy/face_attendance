"""Integration: the simulator's sync and enrolment code against the real server + Postgres.

The device-side ``ServerClient`` is given Starlette's TestClient (an httpx client that
talks to the app in-process), so this exercises the exact request/response path the Pi
will use: catalog with PIN hashes, enrolment upload with consent, incremental template
sync into the device SQLite cache, and recognition from that cache.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select

from common.face.detector import DETECTOR_FILE
from common.face.embedder import EMBEDDER_FILE
from common.face.matcher import MatchConfig, Recognizer, TemplateIndex
from device.app.client import EnrolmentRejectedError, ServerClient
from device.app.enrol import Capture, EnrolmentFlow
from device.app.pins import PinVerifier
from device.app.store import DeviceStore
from device.app.sync import Syncer
from server.app.face import build_engine
from server.app.models import Consent, FaceTemplate

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models"
PORTRAIT = REPO / "tests" / "fixtures" / "faces" / "portrait_2.jpg"

pytestmark = pytest.mark.skipif(
    not (MODELS / DETECTOR_FILE).exists() or not (MODELS / EMBEDDER_FILE).exists(),
    reason="ONNX models not downloaded (python tools/fetch_models.py)",
)


def test_device_syncs_enrols_and_recognises_through_the_real_api(
    client, db, device, faculty, admin, students, offering, settings, tmp_path
):
    _, token = device
    server = ServerClient("http://testserver", token, http=client)
    store = DeviceStore(tmp_path / "device.db")
    syncer = Syncer(store, server, settings.model_version)

    # 1. First sync: catalog (with argon2 PIN hashes) and an empty template cache.
    first = syncer.pull_all()
    assert first.full is True and first.templates == 0 and first.calibrated is False
    assert [s["name"] for s in store.get_catalog("sections")] == ["ECE-7A"]
    assert store.get_setting("consent_version") == settings.consent_version
    verifier = PinVerifier(store)
    assert verifier.verify("246801").id == faculty.id  # faculty PIN from the fixture
    assert verifier.verify("135790").is_admin  # admin PIN
    assert verifier.verify("999999") is None

    # 2. Enrol the first student from the device with real captures.
    engine = build_engine(settings)
    image = cv2.imread(str(PORTRAIT))
    flow = EnrolmentFlow(server, store, syncer)
    roster = flow.roster(students[0].section_id)
    assert [s.usn for s in roster] == [s.usn for s in students]
    target = next(s for s in roster if s.usn == students[0].usn)
    assert target.has_consent is False

    flow.start(target, faculty_id=faculty.id)
    with pytest.raises(PermissionError):
        flow.add_capture(Capture(np.zeros(512, np.float32), np.zeros((112, 112, 3), np.uint8), 1.0))
    flow.give_consent()
    for variant in (image, cv2.flip(image, 1), cv2.resize(image, None, fx=0.9, fy=0.9)):
        result = engine.embed_single(variant)
        assert result.embedding is not None and result.aligned is not None
        flow.add_capture(Capture(result.embedding, result.aligned, result.blur or 0.0))
    uploaded = flow.upload()
    assert uploaded.templates_added == 3 and uploaded.usn == students[0].usn
    assert flow.state is None

    # Server side: consent + templates + crops exist.
    assert (
        db.scalar(
            select(func.count()).select_from(Consent).where(Consent.student_id == students[0].id)
        )
        == 1
    )
    rows = list(db.scalars(select(FaceTemplate).where(FaceTemplate.student_id == students[0].id)))
    assert len(rows) == 3 and all((settings.crops_dir / r.crop_path).exists() for r in rows)

    # 3. The post-upload sync put the templates in the device cache; recognition works offline.
    cached = store.load_templates()
    assert len(cached) == 3 and {t.usn for t in cached} == {students[0].usn}
    recognizer = Recognizer(TemplateIndex(cached), MatchConfig(threshold=0.4, margin=0.05))
    probe = engine.embed_single(cv2.resize(image, None, fx=0.8, fy=0.8)).embedding
    assert probe is not None
    recognizer.observe(probe)
    outcome = recognizer.observe(probe)
    assert outcome.kind == "matched" and outcome.usn == students[0].usn

    # 4. A second device-side enrolment for a student without consent is refused by the server.
    other = next(s for s in roster if s.usn == students[1].usn)
    flow.start(other, faculty_id=faculty.id)
    other_with_flag = other.model_copy(update={"has_consent": True})  # device thinks it exists
    flow.start(other_with_flag, faculty_id=faculty.id)
    for _ in range(3):
        flow.add_capture(Capture(result.embedding, result.aligned, 50.0))  # type: ignore[arg-type]
    with pytest.raises(EnrolmentRejectedError) as excinfo:
        flow.upload()
    assert excinfo.value.status_code == 409
    assert flow.state is None
    store.close()
