"""Sessions list, live session view (HTMX polling), attendance edits with a reason."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer
from server.app.auth import AppSettings, CurrentUser, DbSession
from server.app.models import (
    Attendance,
    AttendanceSession,
    AttendanceStatus,
    Course,
    SessionStatus,
    Student,
)
from server.app.services import attendance, permissions, reports

router = APIRouter(prefix="/sessions")
STATUSES = [s.value for s in AttendanceStatus]


def _session_or_404(db: DbSession, user: CurrentUser, session_id: uuid.UUID) -> AttendanceSession:
    session = db.get(
        AttendanceSession,
        session_id,
        options=[
            selectinload(AttendanceSession.course),
            selectinload(AttendanceSession.section),
            selectinload(AttendanceSession.faculty),
            selectinload(AttendanceSession.device),
            selectinload(AttendanceSession.period),
        ],
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not permissions.can_view_session(db, user, session):
        raise HTTPException(status_code=403, detail="This session belongs to another course.")
    return session


def _grid_rows(db: DbSession, session: AttendanceSession) -> list[dict[str, object]]:
    students = list(
        db.scalars(
            select(Student)
            .where(Student.section_id == session.section_id, Student.active.is_(True))
            .order_by(Student.usn)
        )
    )
    records = {
        r.student_id: r
        for r in db.scalars(select(Attendance).where(Attendance.session_id == session.id))
    }
    return [{"student": s, "record": records.get(s.id)} for s in students]


@router.get("", response_class=HTMLResponse)
def list_sessions(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    day: date | None = None,
    course: int | None = None,
    status: str | None = None,
) -> HTMLResponse:
    chosen_day = day or datetime.now(UTC).astimezone().date()
    lower, upper = reports.date_bounds(chosen_day, chosen_day, settings.timezone)
    stmt = (
        select(AttendanceSession)
        .options(
            selectinload(AttendanceSession.course),
            selectinload(AttendanceSession.section),
            selectinload(AttendanceSession.faculty),
        )
        .where(permissions.session_visibility_clause(db, user))
        .where(AttendanceSession.started_at >= lower, AttendanceSession.started_at < upper)
        .order_by(AttendanceSession.started_at.desc())
    )
    if course:
        stmt = stmt.where(AttendanceSession.course_id == course)
    if status in ("live", "ended"):
        stmt = stmt.where(AttendanceSession.status == SessionStatus(status))
    sessions = list(db.scalars(stmt))
    counts = {}
    for s in sessions:
        rows = db.scalars(select(Attendance.status).where(Attendance.session_id == s.id)).all()
        counts[s.id] = sum(1 for r in rows if r in reports.ATTENDED)
    courses = list(db.scalars(select(Course).order_by(Course.code)))
    if not permissions.is_admin(user):
        own = permissions.own_course_ids(db, user)
        courses = [c for c in courses if c.id in own]
    return renderer.render(
        request,
        "sessions/list.html",
        {
            "sessions": sessions,
            "counts": counts,
            "day": chosen_day,
            "course_id": course,
            "status": status,
            "courses": courses,
        },
        user=user,
    )


@router.get("/{session_id}", response_class=HTMLResponse)
def session_detail(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
) -> HTMLResponse:
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    return renderer.render(
        request,
        "sessions/detail.html",
        {
            "session": session,
            "rows": _grid_rows(db, session),
            "can_edit": decision.allowed,
            "edit_reason": decision.reason,
            "statuses": STATUSES,
        },
        user=user,
    )


@router.get("/{session_id}/grid", response_class=HTMLResponse)
def session_grid(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
) -> HTMLResponse:
    """HTMX fragment polled every 3 s by the live view."""
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    db.refresh(session)
    return renderer.render(
        request,
        "sessions/_grid.html",
        {
            "session": session,
            "rows": _grid_rows(db, session),
            "can_edit": decision.allowed,
            "statuses": STATUSES,
        },
        user=user,
    )


@router.get("/{session_id}/students/{student_id}/edit", response_class=HTMLResponse)
def edit_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
    student_id: int,
) -> HTMLResponse:
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    student = db.get(Student, student_id)
    if student is None or student.section_id != session.section_id:
        raise HTTPException(status_code=404, detail="Student not in this session.")
    record = db.scalar(
        select(Attendance).where(
            Attendance.session_id == session.id, Attendance.student_id == student.id
        )
    )
    return renderer.render(
        request,
        "sessions/_edit_form.html",
        {
            "session": session,
            "student": student,
            "record": record,
            "statuses": STATUSES,
            "error": None,
        },
        user=user,
    )


@router.post("/{session_id}/students/{student_id}", response_class=HTMLResponse)
def edit_record(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
    student_id: int,
    status: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
) -> Response:
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    student = db.get(Student, student_id)
    if student is None or student.section_id != session.section_id:
        raise HTTPException(status_code=404, detail="Student not in this session.")
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status.")
    if not reason.strip():
        record = db.scalar(
            select(Attendance).where(
                Attendance.session_id == session.id, Attendance.student_id == student.id
            )
        )
        return renderer.render(
            request,
            "sessions/_edit_form.html",
            {
                "session": session,
                "student": student,
                "record": record,
                "statuses": STATUSES,
                "error": "A reason is required for every change.",
            },
            user=user,
            status_code=400,
        )
    attendance.set_status(
        db,
        session=session,
        student=student,
        status=AttendanceStatus(status),
        reason=reason,
        actor_id=user.id,
    )
    if request.headers.get("HX-Request") == "true":
        return renderer.render(
            request,
            "sessions/_grid.html",
            {
                "session": session,
                "rows": _grid_rows(db, session),
                "can_edit": True,
                "statuses": STATUSES,
            },
            user=user,
        )
    return renderer.redirect(
        f"/admin/sessions/{session.id}", flash=f"{student.usn} set to {status}."
    )


@router.post("/{session_id}/bulk")
async def bulk_edit(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
) -> RedirectResponse:
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    form = await request.form()
    status = str(form.get("status", ""))
    reason = str(form.get("reason", "")).strip()
    ids = [int(v) for v in form.getlist("student_id") if isinstance(v, str) and v.isdigit()]
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status.")
    if not reason:
        return renderer.redirect(
            f"/admin/sessions/{session.id}", flash="A reason is required.", kind="error"
        )
    if not ids:
        return renderer.redirect(
            f"/admin/sessions/{session.id}", flash="Select at least one student.", kind="error"
        )
    students = list(
        db.scalars(
            select(Student).where(Student.id.in_(ids), Student.section_id == session.section_id)
        )
    )
    changed = attendance.bulk_set_status(
        db,
        session=session,
        students=students,
        status=AttendanceStatus(status),
        reason=reason,
        actor_id=user.id,
    )
    return renderer.redirect(
        f"/admin/sessions/{session.id}", flash=f"{changed} records set to {status}."
    )


@router.post("/{session_id}/end")
def end_from_dashboard(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    session_id: uuid.UUID,
) -> RedirectResponse:
    """End a session from the browser when the device cannot (materialises absents)."""
    session = _session_or_404(db, user, session_id)
    decision = permissions.can_edit_session(db, user, session, settings)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    present, absent = attendance.end_session(db, None, session, datetime.now(UTC), actor_id=user.id)
    return renderer.redirect(
        f"/admin/sessions/{session.id}",
        flash=f"Session ended: {present} present, {absent} marked absent.",
    )
