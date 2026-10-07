"""Enrolment overview: who is missing consent or templates, and recent bulk imports."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import exists, func, select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer
from server.app.auth import AdminUser, DbSession
from server.app.models import AuditLog, Consent, FaceTemplate, Student

router = APIRouter(prefix="/enrolment")


@router.get("", response_class=HTMLResponse)
def overview(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
) -> HTMLResponse:
    has_consent = exists().where(Consent.student_id == Student.id, Consent.withdrawn_at.is_(None))
    has_templates = exists().where(FaceTemplate.student_id == Student.id)
    active = select(Student).options(selectinload(Student.section)).where(Student.active.is_(True))
    missing_consent = list(db.scalars(active.where(~has_consent).order_by(Student.usn)))
    missing_templates = list(
        db.scalars(active.where(has_consent, ~has_templates).order_by(Student.usn))
    )
    counts = {
        "students": db.scalar(select(func.count()).select_from(Student).where(Student.active)) or 0,
        "consented": db.scalar(
            select(func.count()).select_from(Student).where(Student.active, has_consent)
        )
        or 0,
        "enrolled": db.scalar(
            select(func.count()).select_from(Student).where(Student.active, has_templates)
        )
        or 0,
        "templates": db.scalar(select(func.count()).select_from(FaceTemplate)) or 0,
    }
    imports = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "enrolment.bulk_import")
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
    )
    return renderer.render(
        request,
        "enrolment.html",
        {
            "counts": counts,
            "missing_consent": missing_consent,
            "missing_templates": missing_templates,
            "imports": imports,
        },
        user=user,
    )
