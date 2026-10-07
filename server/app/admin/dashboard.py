"""Dashboard and the signed-in user's own account page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer
from server.app.admin.validators import clean_password
from server.app.auth import AppSettings, CurrentUser, DbSession, PasswordHasherDep, pin_problem
from server.app.models import (
    Attendance,
    AttendanceSession,
    Course,
    Device,
    FaceTemplate,
    Faculty,
    Section,
    Student,
)
from server.app.services import audit, permissions, reports
from server.app.services.pins import pin_in_use

router = APIRouter()


def dashboard(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> HTMLResponse:
    now = datetime.now(UTC)
    today = now.astimezone().date()
    lower, upper = reports.date_bounds(today, today, settings.timezone)
    online_cutoff = now - timedelta(minutes=2)
    stale_cutoff = now - timedelta(minutes=10)
    counts = {
        "students": db.scalar(select(func.count()).select_from(Student).where(Student.active)),
        "sections": db.scalar(select(func.count()).select_from(Section)),
        "courses": db.scalar(select(func.count()).select_from(Course)),
        "faculty": db.scalar(select(func.count()).select_from(Faculty).where(Faculty.active)),
        "templates": db.scalar(select(func.count()).select_from(FaceTemplate)),
        "devices_online": db.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.active, Device.last_seen_at >= online_cutoff)
        ),
        "devices_total": db.scalar(select(func.count()).select_from(Device).where(Device.active)),
    }
    sessions_today = list(
        db.scalars(
            select(AttendanceSession)
            .options(
                selectinload(AttendanceSession.course),
                selectinload(AttendanceSession.section),
                selectinload(AttendanceSession.faculty),
            )
            .where(permissions.session_visibility_clause(db, user))
            .where(AttendanceSession.started_at >= lower, AttendanceSession.started_at < upper)
            .order_by(AttendanceSession.started_at.desc())
            .limit(20)
        )
    )
    present_counts = {
        s.id: sum(
            1
            for st in db.scalars(select(Attendance.status).where(Attendance.session_id == s.id))
            if st in reports.ATTENDED
        )
        for s in sessions_today
    }
    devices = list(db.scalars(select(Device).where(Device.active).order_by(Device.name)))
    warnings = []
    for device in devices:
        if device.last_seen_at is None:
            warnings.append((device, "never contacted the server"))
        elif device.last_seen_at < stale_cutoff:
            warnings.append((device, "offline for more than 10 minutes"))
        if device.queue_len:
            warnings.append((device, f"{device.queue_len} records waiting to sync"))
        if device.clock_synced is False:
            warnings.append((device, "clock not NTP-synced"))
    scope = None if permissions.is_admin(user) else permissions.own_course_ids(db, user)
    since, _ = reports.date_bounds(today - timedelta(days=90), today, settings.timezone)
    short = reports.shortage(
        reports.course_matrix(db, course_ids=scope, section_id=None, lower=since, upper=upper),
        settings.attendance_shortage_threshold_pct,
    )
    return renderer.render(
        request,
        "dashboard.html",
        {
            "counts": counts,
            "sessions_today": sessions_today,
            "present_counts": present_counts,
            "now": now,
            "devices": devices,
            "online_cutoff": online_cutoff,
            "warnings": warnings,
            "shortage": short[:10],
            "shortage_total": len(short),
            "threshold": settings.attendance_shortage_threshold_pct,
        },
        user=user,
    )


@router.get("/me", response_class=HTMLResponse)
def my_account(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    user: CurrentUser,
    settings: AppSettings,
) -> HTMLResponse:
    return renderer.render(request, "me.html", {"errors": {}, "settings": settings}, user=user)


@router.post("/me/password")
def change_password(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    hasher: PasswordHasherDep,
    settings: AppSettings,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
) -> Response:
    errors: dict[str, str] = {}
    if not hasher.verify(user.password_hash, current_password):
        errors["current_password"] = "Current password is incorrect."
    clean_password(new_password, field="new_password", errors=errors)
    if new_password != confirm_password:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return renderer.render(
            request, "me.html", {"errors": errors, "settings": settings}, user=user, status_code=400
        )
    user.password_hash = hasher.hash(new_password)
    audit.record(
        db, actor_id=user.id, action="faculty.password_set", entity="faculty", entity_id=user.id
    )
    db.commit()
    return renderer.redirect("/admin/me", flash="Password changed.")


@router.post("/me/pin")
def set_my_pin(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    hasher: PasswordHasherDep,
    settings: AppSettings,
    current_password: Annotated[str, Form()],
    pin: Annotated[str, Form()],
    confirm_pin: Annotated[str, Form()],
) -> Response:
    errors: dict[str, str] = {}
    if not hasher.verify(user.password_hash, current_password):
        errors["pin_current_password"] = "Current password is incorrect."
    problem = pin_problem(pin, settings)
    if problem:
        errors["pin"] = problem
    elif pin != confirm_pin:
        errors["confirm_pin"] = "PINs do not match."
    elif pin_in_use(db, hasher, pin, exclude_id=user.id):
        errors["pin"] = "That PIN is already used by another account. Choose a different one."
    if errors:
        return renderer.render(
            request, "me.html", {"errors": errors, "settings": settings}, user=user, status_code=400
        )
    user.pin_hash = hasher.hash(pin)
    user.pin_updated_at = datetime.now(UTC)
    audit.record(
        db, actor_id=user.id, action="faculty.pin_set", entity="faculty", entity_id=user.id
    )
    db.commit()
    return renderer.redirect(
        "/admin/me", flash="Device PIN updated. Devices pick it up on their next sync."
    )
