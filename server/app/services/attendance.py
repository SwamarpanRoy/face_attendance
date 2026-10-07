"""Sessions and attendance ingestion from devices.

Devices mint session and event UUIDs offline, so everything here is idempotent: posting
the same session or the same event twice changes nothing. When a session ends the
roster students without a record get an ``absent`` row, so reports never have holes;
a face event that arrives late (synced after the end) upgrades such a row to present.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.schemas import AttendanceEventIn, SessionIn
from server.app.models import (
    Attendance,
    AttendanceMethod,
    AttendanceSession,
    AttendanceStatus,
    Course,
    CourseSection,
    Device,
    DeviceSection,
    Faculty,
    Section,
    SessionStatus,
    Student,
)
from server.app.services import audit

log = logging.getLogger(__name__)


class SessionError(Exception):
    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAssignedError(SessionError):
    status = 403


class NotFoundError(SessionError):
    status = 404


@dataclass
class BatchResult:
    accepted: list[uuid.UUID] = field(default_factory=list)
    duplicates: list[uuid.UUID] = field(default_factory=list)
    rejected: dict[uuid.UUID, str] = field(default_factory=dict)


def assigned_section_ids(db: Session, device_id: int) -> set[int]:
    return set(
        db.scalars(select(DeviceSection.section_id).where(DeviceSection.device_id == device_id))
    )


def upsert_session(db: Session, device: Device, payload: SessionIn) -> AttendanceSession:
    """Create the device's session, or return it unchanged if it already exists. Commits."""
    existing = db.get(AttendanceSession, payload.id)
    if existing is not None:
        if existing.device_id not in (None, device.id):
            raise NotAssignedError("Session belongs to another device.")
        return existing
    if payload.section_id not in assigned_section_ids(db, device.id):
        raise NotAssignedError("Section is not assigned to this device.")
    if db.get(Course, payload.course_id) is None or db.get(Section, payload.section_id) is None:
        raise NotFoundError("Unknown course or section.")
    faculty = db.get(Faculty, payload.faculty_id)
    if faculty is None or not faculty.active:
        raise SessionError("Unknown or inactive faculty.")
    offering = db.scalar(
        select(CourseSection).where(
            CourseSection.course_id == payload.course_id,
            CourseSection.section_id == payload.section_id,
        )
    )
    if offering is None:
        raise SessionError("This course is not taught to that section.")
    session = AttendanceSession(
        id=payload.id,
        course_id=payload.course_id,
        section_id=payload.section_id,
        faculty_id=payload.faculty_id,
        device_id=device.id,
        period_id=payload.period_id,
        started_at=payload.started_at,
        status=SessionStatus.LIVE,
        clock_synced=payload.clock_synced,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        # Two uploads raced; the first one won and that is fine.
        db.rollback()
        found = db.get(AttendanceSession, payload.id)
        if found is None:
            raise
        return found
    log.info("session %s created by %s", session.id, device.name)
    return session


def materialise_absent(db: Session, session: AttendanceSession) -> int:
    """Insert absent rows for roster students with no record in this session. Flushes."""
    roster = db.scalars(
        select(Student.id).where(Student.section_id == session.section_id, Student.active.is_(True))
    ).all()
    present = set(
        db.scalars(select(Attendance.student_id).where(Attendance.session_id == session.id))
    )
    created = 0
    for student_id in roster:
        if student_id in present:
            continue
        db.add(
            Attendance(
                session_id=session.id,
                student_id=student_id,
                status=AttendanceStatus.ABSENT,
                method=AttendanceMethod.MANUAL,
                score=None,
                captured_at=None,
                clock_synced=True,
            )
        )
        created += 1
    db.flush()
    return created


def end_session(
    db: Session,
    device: Device | None,
    session: AttendanceSession,
    ended_at: datetime,
    *,
    actor_id: int | None = None,
) -> tuple[int, int]:
    """Mark the session ended and fill in absents. Idempotent. Commits.

    Returns ``(present_count, absent_marked)``.
    """
    if device is not None and session.device_id not in (None, device.id):
        raise NotAssignedError("Session belongs to another device.")
    if session.status is SessionStatus.LIVE:
        session.status = SessionStatus.ENDED
        session.ended_at = ended_at
    absent_marked = materialise_absent(db, session)
    present = sum(
        1
        for s in db.scalars(select(Attendance.status).where(Attendance.session_id == session.id))
        if s is AttendanceStatus.PRESENT
    )
    audit.record(
        db,
        actor_id=actor_id,
        action="session.end",
        entity="session",
        entity_id=str(session.id),
        after={
            "present": present,
            "absent_marked": absent_marked,
            "ended_at": ended_at.isoformat(),
        },
    )
    db.commit()
    return present, absent_marked


def ingest_batch(db: Session, device: Device, events: list[AttendanceEventIn]) -> BatchResult:
    """Record face events idempotently. Each event is committed in its own savepoint."""
    result = BatchResult()
    received_at = datetime.now(UTC)
    for event in events:
        if db.scalar(select(Attendance.id).where(Attendance.event_uuid == event.event_uuid)):
            result.duplicates.append(event.event_uuid)
            continue
        session = db.get(AttendanceSession, event.session_id)
        if session is None:
            result.rejected[event.event_uuid] = "unknown session"
            continue
        if session.device_id not in (None, device.id):
            result.rejected[event.event_uuid] = "session belongs to another device"
            continue
        student = db.scalar(select(Student).where(Student.usn == event.usn.upper()))
        if student is None:
            result.rejected[event.event_uuid] = "unknown student"
            continue
        if student.section_id != session.section_id:
            result.rejected[event.event_uuid] = "student not in the session's section"
            continue

        existing = db.scalar(
            select(Attendance).where(
                Attendance.session_id == session.id, Attendance.student_id == student.id
            )
        )
        if existing is not None:
            if (
                existing.status is AttendanceStatus.ABSENT
                and existing.method is AttendanceMethod.MANUAL
            ):
                # Materialised absent, now contradicted by a late face event.
                existing.status = AttendanceStatus.PRESENT
                existing.method = AttendanceMethod.FACE
                existing.score = event.score
                existing.captured_at = event.captured_at
                existing.received_at = received_at
                existing.clock_synced = event.clock_synced
                existing.event_uuid = event.event_uuid
                db.commit()
                result.accepted.append(event.event_uuid)
            else:
                result.duplicates.append(event.event_uuid)
            continue

        nested = db.begin_nested()
        try:
            db.add(
                Attendance(
                    session_id=session.id,
                    student_id=student.id,
                    status=AttendanceStatus.PRESENT,
                    method=AttendanceMethod.FACE,
                    score=event.score,
                    captured_at=event.captured_at,
                    received_at=received_at,
                    clock_synced=event.clock_synced,
                    event_uuid=event.event_uuid,
                )
            )
            nested.commit()
            result.accepted.append(event.event_uuid)
        except IntegrityError:
            nested.rollback()
            result.duplicates.append(event.event_uuid)
    db.commit()
    return result


# --------------------------------------------------------------------------- manual edits
def _snapshot(record: Attendance | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "status": record.status.value,
        "method": record.method.value,
        "score": record.score,
        "captured_at": record.captured_at.isoformat() if record.captured_at else None,
    }


def set_status(
    db: Session,
    *,
    session: AttendanceSession,
    student: Student,
    status: AttendanceStatus,
    reason: str,
    actor_id: int,
    bulk: bool = False,
) -> Attendance:
    """Create or change one record by hand. Always audited with the reason. Commits."""
    if student.section_id != session.section_id:
        raise SessionError("Student is not in this session's section.")
    record = db.scalar(
        select(Attendance).where(
            Attendance.session_id == session.id, Attendance.student_id == student.id
        )
    )
    before = _snapshot(record)
    if record is None:
        record = Attendance(
            session_id=session.id,
            student_id=student.id,
            status=status,
            method=AttendanceMethod.MANUAL,
            clock_synced=True,
        )
        db.add(record)
    else:
        record.status = status
        record.method = AttendanceMethod.MANUAL
    db.flush()
    audit.record(
        db,
        actor_id=actor_id,
        action="attendance.bulk_edit" if bulk else "attendance.edit",
        entity="attendance",
        entity_id=record.id,
        before=before,
        after={**(_snapshot(record) or {}), "session_id": str(session.id), "usn": student.usn},
        reason=reason,
    )
    db.commit()
    return record


def bulk_set_status(
    db: Session,
    *,
    session: AttendanceSession,
    students: list[Student],
    status: AttendanceStatus,
    reason: str,
    actor_id: int,
) -> int:
    """Apply one status to many students, one audited row per record. Commits."""
    changed = 0
    for student in students:
        set_status(
            db,
            session=session,
            student=student,
            status=status,
            reason=reason,
            actor_id=actor_id,
            bulk=True,
        )
        changed += 1
    return changed
