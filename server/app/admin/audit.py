"""Audit log browser (admin only): filter by user, entity, action and date."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer, page_from_query
from server.app.auth import AdminUser, AppSettings, DbSession
from server.app.models import AuditLog, Faculty
from server.app.services import reports

router = APIRouter(prefix="/audit")


@router.get("", response_class=HTMLResponse)
def audit_log(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    actor: int | None = None,
    entity: str = "",
    action: str = "",
    start: date | None = None,
    end: date | None = None,
) -> HTMLResponse:
    stmt = select(AuditLog).options(selectinload(AuditLog.actor))
    if actor:
        stmt = stmt.where(AuditLog.actor_id == actor)
    if entity.strip():
        stmt = stmt.where(AuditLog.entity == entity.strip())
    if action.strip():
        stmt = stmt.where(AuditLog.action.like(f"{action.strip()}%"))
    lower, upper = reports.date_bounds(start, end, settings.timezone)
    if lower is not None:
        stmt = stmt.where(AuditLog.created_at >= lower)
    if upper is not None:
        stmt = stmt.where(AuditLog.created_at < upper)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = page_from_query(request, total)
    rows = list(
        db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset(page.offset).limit(page.size))
    )
    actors = list(db.scalars(select(Faculty).order_by(Faculty.name)))
    entities = sorted(set(db.scalars(select(AuditLog.entity).distinct())))
    return renderer.render(
        request,
        "audit.html",
        {
            "rows": rows,
            "page": page,
            "actors": actors,
            "entities": entities,
            "actor_id": actor,
            "entity": entity,
            "action": action,
            "start": start,
            "end": end,
        },
        user=user,
    )
