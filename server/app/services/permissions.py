"""Who may see and change what.

Admins see everything. Faculty see the courses they teach (``course_sections`` rows
with their id) plus sessions they ran, and may edit attendance only for those sessions
and only within ``FACULTY_EDIT_WINDOW_DAYS`` of the session start. All checks live here
so routers and templates agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.orm import Session

from server.app.config import Settings
from server.app.models import AttendanceSession, CourseSection, Faculty, Role


@dataclass(frozen=True)
class EditDecision:
    allowed: bool
    reason: str = ""


def is_admin(user: Faculty) -> bool:
    return user.role is Role.ADMIN


def own_offerings(db: Session, user: Faculty) -> list[tuple[int, int]]:
    """(course_id, section_id) pairs this faculty member teaches."""
    rows = db.execute(
        select(CourseSection.course_id, CourseSection.section_id).where(
            CourseSection.faculty_id == user.id
        )
    ).all()
    return [(int(c), int(s)) for c, s in rows]


def own_course_ids(db: Session, user: Faculty) -> set[int]:
    return {course_id for course_id, _ in own_offerings(db, user)}


def session_visibility_clause(db: Session, user: Faculty) -> ColumnElement[bool]:
    """SQL filter for sessions this user may see."""
    if is_admin(user):
        return true()
    clauses = [AttendanceSession.faculty_id == user.id]
    for course_id, section_id in own_offerings(db, user):
        clauses.append(
            (AttendanceSession.course_id == course_id)
            & (AttendanceSession.section_id == section_id)
        )
    return or_(*clauses)


def can_view_session(db: Session, user: Faculty, session: AttendanceSession) -> bool:
    if is_admin(user) or session.faculty_id == user.id:
        return True
    return (session.course_id, session.section_id) in set(own_offerings(db, user))


def can_edit_session(
    db: Session,
    user: Faculty,
    session: AttendanceSession,
    settings: Settings,
    now: datetime | None = None,
) -> EditDecision:
    if is_admin(user):
        return EditDecision(True)
    if not can_view_session(db, user, session):
        return EditDecision(False, "You can only edit attendance for your own courses.")
    current = now or datetime.now(UTC)
    deadline = session.started_at + timedelta(days=settings.faculty_edit_window_days)
    if current > deadline:
        return EditDecision(
            False,
            f"The edit window of {settings.faculty_edit_window_days} days has passed; "
            "ask an admin.",
        )
    return EditDecision(True)
