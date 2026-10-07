"""Device enrolment upload and the template feed devices cache.

``POST /enrolment/{usn}/captures`` refuses to create templates without consent: either
the device sends the consent the student just gave, or an active consent must already
exist. ``GET /templates`` returns everything a device needs to match offline for all
of its assigned sections, incrementally when ``since`` is given.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from common.schemas import (
    EnrolmentCapturesIn,
    EnrolmentCapturesOut,
    StudentTemplatesOut,
    TemplateOut,
    TemplatesOut,
)
from server.app.auth import AppSettings, CurrentDevice, DbSession
from server.app.face import FaceEngineDep
from server.app.models import (
    Calibration,
    ConsentMethod,
    DeviceSection,
    FaceTemplate,
    Faculty,
    Student,
    TemplateSource,
    TemplateTombstone,
)
from server.app.services import enrolment

router = APIRouter()


def _assigned_section_ids(db: DbSession, device_id: int) -> list[int]:
    return list(
        db.scalars(select(DeviceSection.section_id).where(DeviceSection.device_id == device_id))
    )


@router.post("/enrolment/{usn}/captures", response_model=EnrolmentCapturesOut)
def upload_captures(
    usn: str,
    payload: EnrolmentCapturesIn,
    db: DbSession,
    device: CurrentDevice,
    settings: AppSettings,
    engine: FaceEngineDep,
) -> EnrolmentCapturesOut:
    student = db.scalar(select(Student).where(Student.usn == usn.strip().upper()))
    if student is None or not student.active:
        raise HTTPException(status_code=404, detail="Student not found.")
    if student.section_id not in _assigned_section_ids(db, device.id):
        raise HTTPException(
            status_code=403, detail="Student's section is not assigned to this device."
        )
    supervisor = db.get(Faculty, payload.faculty_id)
    if supervisor is None or not supervisor.active:
        raise HTTPException(status_code=400, detail="Unknown faculty_id.")

    try:
        crops = []
        for item in payload.crops_png_b64:
            try:
                raw = base64.b64decode(item, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise enrolment.BadCropError("crop is not valid base64") from exc
            crop = enrolment.decode_png(raw)
            enrolment.validate_crop(crop)
            crops.append(crop)

        if payload.consent is not None:
            consent = enrolment.record_consent(
                db,
                student,
                consent_at=payload.consent.consent_at,
                consent_version=payload.consent.consent_version,
                method=ConsentMethod(payload.consent.method),
                recorded_by=supervisor.id,
            )
        else:
            existing = enrolment.active_consent(db, student.id)
            if existing is None:
                raise enrolment.ConsentRequiredError(
                    "No consent on record for this student. Ask them to tap I consent first."
                )
            consent = existing

        stored = enrolment.store_templates(
            db,
            settings,
            engine.embedder,
            student=student,
            consent=consent,
            crops=crops,
            source=TemplateSource.DEVICE,
            quality_scores=list(payload.blur_scores) if payload.blur_scores else None,
            actor_id=supervisor.id,
            model_version=settings.model_version,
        )
    except enrolment.EnrolmentError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    return EnrolmentCapturesOut(
        student_id=student.id,
        usn=student.usn,
        consent_id=stored.consent.id,
        templates_added=len(stored.templates),
        total_templates=stored.total_templates,
        model_version=settings.model_version,
    )


@router.get("/templates", response_model=TemplatesOut)
def templates(
    db: DbSession,
    device: CurrentDevice,
    settings: AppSettings,
    since: Annotated[datetime | None, Query()] = None,
) -> TemplatesOut:
    section_ids = _assigned_section_ids(db, device.id)
    calibration = db.scalar(
        select(Calibration).where(
            Calibration.model_version == settings.model_version, Calibration.active.is_(True)
        )
    )

    students_stmt = (
        select(Student)
        .options(selectinload(Student.templates))
        .where(Student.section_id.in_(section_ids), Student.active.is_(True))
        .order_by(Student.usn)
    )
    if since is not None:
        changed = (
            select(FaceTemplate.student_id)
            .where(FaceTemplate.created_at > since)
            .distinct()
            .scalar_subquery()
        )
        students_stmt = students_stmt.where(Student.id.in_(changed))
    # populate_existing: refresh template collections even if this Session saw the student
    # earlier in the same transaction (long-lived sessions, tests).
    students = list(db.scalars(students_stmt.execution_options(populate_existing=True)))

    deleted: list[str] = []
    if since is not None:
        deleted = sorted(
            set(
                db.scalars(
                    select(TemplateTombstone.usn).where(
                        TemplateTombstone.deleted_at > since,
                        TemplateTombstone.section_id.in_(section_ids),
                    )
                )
            )
        )

    out_students = [
        StudentTemplatesOut(
            usn=s.usn,
            name=s.name,
            section_id=s.section_id,
            templates=[
                TemplateOut(
                    idx=i,
                    embedding_b64=base64.b64encode(t.embedding).decode("ascii"),
                    model_version=t.model_version,
                    created_at=t.created_at,
                )
                for i, t in enumerate(
                    sorted(
                        (t for t in s.templates if t.model_version == settings.model_version),
                        key=lambda t: t.id,
                    )
                )
            ],
        )
        for s in students
        if since is None or s.templates
    ]
    if since is None:
        out_students = [s for s in out_students if s.templates]

    return TemplatesOut(
        generated_at=datetime.now(UTC),
        model_version=settings.model_version,
        calibrated=calibration is not None,
        threshold=calibration.threshold if calibration else None,
        margin=calibration.margin if calibration else None,
        full=since is None,
        students=out_students,
        deleted_usns=deleted,
    )
