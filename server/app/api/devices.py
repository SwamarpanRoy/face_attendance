"""Heartbeat, catalog, PIN check and roster endpoints used by the device app.

Everything a device receives is scoped to the sections assigned to it in
``device_sections``, so a device in one classroom never holds another department's
roster or templates.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from common.schemas import (
    CatalogOut,
    CourseOut,
    FacultyPinOut,
    HeartbeatIn,
    HeartbeatOut,
    OfferingOut,
    PeriodOut,
    PinAuthIn,
    PinAuthOut,
    RosterOut,
    RosterStudentOut,
    SectionOut,
)
from common.version import __version__
from server.app.auth import AppSettings, CurrentDevice, DbSession, PasswordHasherDep
from server.app.models import (
    Consent,
    Course,
    CourseSection,
    DeviceSection,
    FaceTemplate,
    Faculty,
    Period,
    Role,
    Section,
    Student,
)
from server.app.services.pins import faculty_for_pin

router = APIRouter()


def _assigned_section_ids(db: DbSession, device_id: int) -> list[int]:
    return list(
        db.scalars(
            select(DeviceSection.section_id)
            .where(DeviceSection.device_id == device_id)
            .order_by(DeviceSection.section_id)
        )
    )


@router.post("/devices/heartbeat", response_model=HeartbeatOut)
def heartbeat(
    payload: HeartbeatIn,
    request: Request,
    db: DbSession,
    device: CurrentDevice,
    settings: AppSettings,
) -> HeartbeatOut:
    device.last_seen_at = datetime.now(UTC)
    device.last_ip = payload.ip or (request.client.host if request.client else None)
    device.ssid = payload.ssid
    device.app_version = payload.app_version
    device.queue_len = payload.queue_len
    device.cpu_temp = payload.cpu_temp_c
    device.free_mem_mb = payload.free_mem_mb
    device.model_version = payload.model_version
    device.clock_synced = payload.clock_synced
    db.commit()
    return HeartbeatOut(
        device_name=device.name,
        server_time=datetime.now(UTC),
        server_version=__version__,
        model_version=settings.model_version,
        assigned_section_ids=_assigned_section_ids(db, device.id),
    )


@router.get("/catalog", response_model=CatalogOut)
def catalog(db: DbSession, device: CurrentDevice, settings: AppSettings) -> CatalogOut:
    """Courses, sections, periods, offerings and faculty PIN hashes for this device.

    Faculty included: everyone teaching one of the device's sections plus all active
    admins (so an admin can always open Settings on any device).
    """
    section_ids = _assigned_section_ids(db, device.id)
    sections = list(
        db.scalars(select(Section).where(Section.id.in_(section_ids)).order_by(Section.name))
    )
    offerings = list(
        db.scalars(select(CourseSection).where(CourseSection.section_id.in_(section_ids)))
    )
    course_ids = {o.course_id for o in offerings}
    courses = list(
        db.scalars(select(Course).where(Course.id.in_(course_ids)).order_by(Course.code))
    )
    teaching_ids = {o.faculty_id for o in offerings}
    faculty = list(
        db.scalars(
            select(Faculty)
            .where(Faculty.active, (Faculty.id.in_(teaching_ids)) | (Faculty.role == Role.ADMIN))
            .order_by(Faculty.name)
        )
    )
    periods = list(db.scalars(select(Period).order_by(Period.ordinal)))
    return CatalogOut(
        generated_at=datetime.now(UTC),
        consent_version=settings.consent_version,
        consent_notice=settings.consent_notice,
        courses=[CourseOut(id=c.id, code=c.code, name=c.name) for c in courses],
        sections=[SectionOut(id=s.id, name=s.name) for s in sections],
        periods=[
            PeriodOut(
                id=p.id,
                ordinal=p.ordinal,
                name=p.name,
                start_time=p.start_time.strftime("%H:%M"),
                end_time=p.end_time.strftime("%H:%M"),
            )
            for p in periods
        ],
        offerings=[
            OfferingOut(course_id=o.course_id, section_id=o.section_id, faculty_id=o.faculty_id)
            for o in offerings
        ],
        faculty=[
            FacultyPinOut(id=f.id, name=f.name, role=f.role.value, pin_hash=f.pin_hash)
            for f in faculty
        ],
    )


@router.post("/auth/pin", response_model=PinAuthOut)
def verify_pin(
    payload: PinAuthIn, db: DbSession, device: CurrentDevice, hasher: PasswordHasherDep
) -> PinAuthOut:
    """Online PIN check. Devices normally verify offline against the synced hashes."""
    member = faculty_for_pin(db, hasher, payload.pin)
    if member is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN not recognised.")
    return PinAuthOut(faculty_id=member.id, name=member.name, role=member.role.value)


@router.get("/roster", response_model=RosterOut)
def roster(section_id: int, db: DbSession, device: CurrentDevice) -> RosterOut:
    """Students of one assigned section, for picking who to enrol on the device."""
    if section_id not in _assigned_section_ids(db, device.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Section is not assigned to this device."
        )
    template_count = (
        select(func.count(FaceTemplate.id))
        .where(FaceTemplate.student_id == Student.id)
        .scalar_subquery()
    )
    has_consent = (
        select(func.count(Consent.id))
        .where(Consent.student_id == Student.id, Consent.withdrawn_at.is_(None))
        .scalar_subquery()
    )
    rows = db.execute(
        select(Student, template_count, has_consent)
        .where(Student.section_id == section_id, Student.active)
        .order_by(Student.usn)
    ).all()
    return RosterOut(
        section_id=section_id,
        students=[
            RosterStudentOut(
                student_id=s.id,
                usn=s.usn,
                name=s.name,
                has_consent=consents > 0,
                template_count=templates,
            )
            for s, templates, consents in rows
        ],
    )
