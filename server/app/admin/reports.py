"""Attendance reports: per-course percentages, shortage list, CSV/XLSX export."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from server.app.admin.common import Renderer, get_renderer
from server.app.auth import AppSettings, CurrentUser, DbSession
from server.app.models import Course, Section
from server.app.services import permissions, reports

router = APIRouter(prefix="/reports")


def _scope(db: DbSession, user: CurrentUser, course: int | None) -> set[int] | None:
    """Course ids the report may cover: None means all (admin without a filter)."""
    if permissions.is_admin(user):
        return {course} if course else None
    own = permissions.own_course_ids(db, user)
    return {course} & own if course else own


def _range(start: date | None, end: date | None) -> tuple[date, date]:
    today = datetime.now(UTC).astimezone().date()
    return (start or today - timedelta(days=90), end or today)


@router.get("", response_class=HTMLResponse)
def index(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    course: int | None = None,
    section: int | None = None,
    start: date | None = None,
    end: date | None = None,
    threshold: float | None = None,
    view: str = "matrix",
) -> HTMLResponse:
    start_d, end_d = _range(start, end)
    lower, upper = reports.date_bounds(start_d, end_d, settings.timezone)
    rows = reports.course_matrix(
        db, course_ids=_scope(db, user, course), section_id=section, lower=lower, upper=upper
    )
    limit = threshold if threshold is not None else settings.attendance_shortage_threshold_pct
    short = reports.shortage(rows, limit)
    courses = list(db.scalars(select(Course).order_by(Course.code)))
    if not permissions.is_admin(user):
        own = permissions.own_course_ids(db, user)
        courses = [c for c in courses if c.id in own]
    sections = list(db.scalars(select(Section).order_by(Section.name)))
    return renderer.render(
        request,
        "reports/index.html",
        {
            "rows": short if view == "shortage" else rows,
            "shortage_count": len(short),
            "view": view,
            "courses": courses,
            "sections": sections,
            "course_id": course,
            "section_id": section,
            "start": start_d,
            "end": end_d,
            "threshold": limit,
        },
        user=user,
    )


@router.get("/export")
def export(
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    fmt: str = "csv",
    course: int | None = None,
    section: int | None = None,
    start: date | None = None,
    end: date | None = None,
    threshold: float | None = None,
    view: str = "matrix",
) -> Response:
    start_d, end_d = _range(start, end)
    lower, upper = reports.date_bounds(start_d, end_d, settings.timezone)
    rows = reports.course_matrix(
        db, course_ids=_scope(db, user, course), section_id=section, lower=lower, upper=upper
    )
    if view == "shortage":
        rows = reports.shortage(
            rows, threshold if threshold is not None else settings.attendance_shortage_threshold_pct
        )
    stem = f"attendance_{view}_{start_d.isoformat()}_{end_d.isoformat()}"
    if fmt == "xlsx":
        return Response(
            reports.export_xlsx(rows, title=view),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
        )
    return Response(
        reports.export_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
    )
