"""Database-level guarantees: these must hold even if application code is bypassed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from server.app.models import (
    EMBEDDING_BYTES,
    AuditLog,
    Calibration,
    Consent,
    ConsentMethod,
    FaceTemplate,
    TemplateSource,
)
from server.app.services import audit


def test_face_template_without_consent_is_a_db_constraint_error(db, students):
    db.add(
        FaceTemplate(
            student_id=students[0].id,
            consent_id=None,  # type: ignore[arg-type]
            embedding=b"\0" * EMBEDDING_BYTES,
            model_version="test",
            source=TemplateSource.DEVICE,
        )
    )
    with pytest.raises(IntegrityError, match="consent_id"):
        db.flush()
    db.rollback()


def test_face_template_with_consent_is_accepted(db, students):
    consent = Consent(
        student_id=students[0].id,
        consent_at=datetime.now(UTC),
        consent_version="v1",
        method=ConsentMethod.PAPER,
    )
    db.add(consent)
    db.flush()
    db.add(
        FaceTemplate(
            student_id=students[0].id,
            consent_id=consent.id,
            embedding=b"\0" * EMBEDDING_BYTES,
            model_version="test",
            source=TemplateSource.IDCARD,
        )
    )
    db.flush()


def test_embedding_must_be_exactly_512_float32(db, students):
    consent = Consent(
        student_id=students[0].id,
        consent_at=datetime.now(UTC),
        consent_version="v1",
        method=ConsentMethod.PAPER,
    )
    db.add(consent)
    db.flush()
    db.add(
        FaceTemplate(
            student_id=students[0].id,
            consent_id=consent.id,
            embedding=b"\0" * 100,
            model_version="test",
            source=TemplateSource.IDCARD,
        )
    )
    with pytest.raises(IntegrityError, match="embedding_size"):
        db.flush()
    db.rollback()


def test_consent_timestamp_is_not_nullable(db, students):
    db.add(
        Consent(
            student_id=students[0].id,
            consent_at=None,
            consent_version="v1",
            method=ConsentMethod.PAPER,
        )
    )  # type: ignore[arg-type]
    with pytest.raises(IntegrityError, match="consent_at"):
        db.flush()
    db.rollback()


def test_attendance_edit_audit_requires_reason_in_service_and_db(db, admin):
    with pytest.raises(audit.ReasonRequiredError):
        audit.record(
            db,
            actor_id=admin.id,
            action="attendance.edit",
            entity="attendance",
            entity_id=1,
            reason="   ",
        )

    db.add(
        AuditLog(
            actor_id=admin.id,
            action="attendance.edit",
            entity="attendance",
            entity_id="1",
            reason=None,
        )
    )
    with pytest.raises(IntegrityError, match="reason_required_for_edits"):
        db.flush()
    db.rollback()

    # Non-edit actions do not need a reason.
    audit.record(db, actor_id=admin.id, action="auth.login", entity="faculty", entity_id=admin.id)
    db.flush()


def test_only_one_active_calibration_per_model(db):
    def row(model: str, active: bool) -> Calibration:
        return Calibration(
            model_version=model,
            threshold=0.4,
            margin=0.05,
            target_far=0.001,
            measured_far=0.0,
            measured_frr=0.1,
            n_genuine=10,
            n_impostor=90,
            active=active,
        )

    db.add_all([row("m1", True), row("m1", False), row("m2", True)])
    db.flush()
    db.add(row("m1", True))
    with pytest.raises(IntegrityError, match="uq_calibrations_active_model"):
        db.flush()
    db.rollback()
