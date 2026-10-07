"""Enrolment flow and offline PIN checks without Qt or a network."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from argon2 import PasswordHasher

from common.schemas import EnrolmentCapturesOut, RosterOut, RosterStudentOut
from device.app.client import EnrolmentRejectedError, ServerUnavailableError
from device.app.enrol import Capture, EnrolmentFlow
from device.app.pins import PinLockedError, PinVerifier
from device.app.store import DeviceStore


def _capture(seed: int) -> Capture:
    rng = np.random.default_rng(seed)
    aligned = rng.integers(0, 256, size=(112, 112, 3), dtype=np.uint8)
    return Capture(embedding=np.ones(512, np.float32), aligned=aligned, blur=123.0 + seed)


class FakeClient:
    def __init__(self, *, offline: bool = False, reject: str | None = None) -> None:
        self.offline = offline
        self.reject = reject
        self.uploads = []
        self.templates_calls = 0

    def roster(self, section_id: int) -> RosterOut:
        if self.offline:
            raise ServerUnavailableError("offline")
        return RosterOut(
            section_id=section_id,
            students=[
                RosterStudentOut(
                    student_id=1,
                    usn="1BM22EC001",
                    name="Aditi",
                    has_consent=False,
                    template_count=0,
                ),
                RosterStudentOut(
                    student_id=2, usn="1BM22EC002", name="Arjun", has_consent=True, template_count=3
                ),
            ],
        )

    def upload_captures(self, usn, payload):
        if self.offline:
            raise ServerUnavailableError("offline")
        if self.reject:
            raise EnrolmentRejectedError(self.reject, 409)
        self.uploads.append((usn, payload))
        return EnrolmentCapturesOut(
            student_id=1,
            usn=usn,
            consent_id=5,
            templates_added=len(payload.crops_png_b64),
            total_templates=len(payload.crops_png_b64),
            model_version="m",
        )


class FakeSyncer:
    def __init__(self) -> None:
        self.pulls = 0

    def pull_templates(self, *, full: bool = False):
        self.pulls += 1


@pytest.fixture
def flow(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    store.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}])
    store.set_setting("consent_version", "2026-10-v1")
    store.set_setting("consent_notice", "the notice")
    client = FakeClient()
    syncer = FakeSyncer()
    yield EnrolmentFlow(client, store, syncer), client, syncer, store  # type: ignore[arg-type]
    store.close()


def test_roster_is_cached_for_offline_use(flow):
    enrol, client, _, _ = flow
    online = enrol.roster(1)
    assert [s.usn for s in online] == ["1BM22EC001", "1BM22EC002"]
    client.offline = True
    cached = enrol.roster(1)
    assert [s.usn for s in cached] == ["1BM22EC001", "1BM22EC002"]
    assert enrol.sections()[0]["name"] == "ECE-7A"
    assert enrol.consent_text() == ("2026-10-v1", "the notice")


def test_capture_requires_consent_unless_already_on_record(flow):
    enrol, _, _, _ = flow
    students = enrol.roster(1)
    enrol.start(students[0], faculty_id=7)
    with pytest.raises(PermissionError):
        enrol.add_capture(_capture(1))
    enrol.start(students[1], faculty_id=7)  # has_consent on the server already
    assert enrol.add_capture(_capture(1)) == 2


def test_full_flow_uploads_consent_and_crops_then_forgets_them(flow):
    enrol, client, syncer, _ = flow
    student = enrol.roster(1)[0]
    enrol.start(student, faculty_id=7)
    before = datetime.now(UTC)
    enrol.give_consent()
    assert enrol.add_capture(_capture(1)) == 2
    assert enrol.add_capture(_capture(2)) == 1
    assert not enrol.ready
    assert enrol.add_capture(_capture(3)) == 0 and enrol.ready

    result = enrol.upload()
    assert result.templates_added == 3
    usn, payload = client.uploads[0]
    assert usn == "1BM22EC001" and payload.faculty_id == 7
    assert payload.consent is not None and payload.consent.consent_at >= before
    assert payload.consent.consent_version == "2026-10-v1"
    assert len(payload.crops_png_b64) == 3 and payload.blur_scores == [124.0, 125.0, 126.0]
    assert payload.crops_png_b64[0].startswith("iVBORw0KGgo")  # PNG magic, base64
    assert enrol.state is None  # crops gone
    assert syncer.pulls == 1


def test_rejection_and_offline_still_drop_crops(flow):
    enrol, client, _, _ = flow
    student = enrol.roster(1)[0]
    for failure in ("reject", "offline"):
        client.reject = "No consent on record" if failure == "reject" else None
        client.offline = failure == "offline"
        enrol.start(student, faculty_id=7)
        enrol.give_consent()
        for seed in (1, 2, 3):
            enrol.add_capture(_capture(seed))
        with pytest.raises(
            EnrolmentRejectedError if failure == "reject" else ServerUnavailableError
        ):
            enrol.upload()
        assert enrol.state is None


def test_upload_needs_all_captures(flow):
    enrol, _, _, _ = flow
    enrol.start(enrol.roster(1)[1], faculty_id=7)
    enrol.add_capture(_capture(1))
    with pytest.raises(ValueError, match="need 3 captures"):
        enrol.build_payload()


def test_pin_verifier_matches_hashes_and_locks_out(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    hasher = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    store.replace_faculty_pins(
        [
            (7, "Prof", "faculty", hasher.hash("246801")),
            (1, "Admin", "admin", hasher.hash("135790")),
            (9, "NoPin", "faculty", None),
        ]
    )
    verifier = PinVerifier(store, max_failures=3, lockout_s=60)
    assert verifier.has_pins
    assert verifier.verify("246801", now=0.0).id == 7
    admin = verifier.verify("135790", now=0.0)
    assert admin is not None and admin.is_admin
    for _ in range(3):
        assert verifier.verify("000000", now=1.0) is None
    with pytest.raises(PinLockedError):
        verifier.verify("246801", now=2.0)
    assert verifier.verify("246801", now=62.0).name == "Prof"
    store.close()
