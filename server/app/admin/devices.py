"""Devices: register (token shown once), assign sections, rotate token, revoke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer
from server.app.admin.validators import clean_text
from server.app.auth import AdminUser, DbSession
from server.app.models import Device, Section
from server.app.services import devices as device_service

router = APIRouter(prefix="/devices")
ONLINE_WINDOW = timedelta(minutes=2)


def _device_or_404(db: DbSession, device_id: int) -> Device:
    device = db.get(Device, device_id, options=[selectinload(Device.sections)])
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    return device


@router.get("", response_class=HTMLResponse)
def list_devices(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
) -> HTMLResponse:
    rows = list(
        db.scalars(
            select(Device)
            .options(selectinload(Device.sections))
            .order_by(Device.active.desc(), Device.name)
        )
    )
    cutoff = datetime.now(UTC) - ONLINE_WINDOW
    sections = list(db.scalars(select(Section).order_by(Section.name)))
    return renderer.render(
        request,
        "devices/list.html",
        {"devices": rows, "cutoff": cutoff, "sections": sections, "errors": {}, "values": {}},
        user=user,
    )


@router.post("", response_class=HTMLResponse)
def register_device(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    name: Annotated[str, Form()],
) -> HTMLResponse:
    errors: dict[str, str] = {}
    cleaned = clean_text(name, field="name", errors=errors, max_len=64)
    if not errors and db.scalar(select(Device).where(Device.name == cleaned)) is not None:
        errors["name"] = "A device with this name already exists."
    if errors:
        rows = list(
            db.scalars(select(Device).options(selectinload(Device.sections)).order_by(Device.name))
        )
        sections = list(db.scalars(select(Section).order_by(Section.name)))
        return renderer.render(
            request,
            "devices/list.html",
            {
                "devices": rows,
                "cutoff": datetime.now(UTC) - ONLINE_WINDOW,
                "sections": sections,
                "errors": errors,
                "values": {"name": name},
            },
            user=user,
            status_code=400,
        )
    device, token = device_service.register(db, cleaned, actor_id=user.id)
    return renderer.render(
        request, "devices/token.html", {"device": device, "token": token}, user=user
    )


@router.post("/{device_id}/sections")
async def assign_sections(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    device_id: int,
) -> RedirectResponse:
    device = _device_or_404(db, device_id)
    form = await request.form()
    ids = [int(v) for v in form.getlist("section_id") if isinstance(v, str) and v.isdigit()]
    assigned = device_service.assign_sections(db, device, ids, actor_id=user.id)
    return renderer.redirect(
        "/admin/devices", flash=f"{device.name}: {len(assigned)} sections assigned."
    )


@router.post("/{device_id}/rotate")
def rotate(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    device_id: int,
) -> HTMLResponse:
    device = _device_or_404(db, device_id)
    token = device_service.rotate_token(db, device, actor_id=user.id)
    return renderer.render(
        request, "devices/token.html", {"device": device, "token": token}, user=user
    )


@router.post("/{device_id}/revoke")
def revoke(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    device_id: int,
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    device = _device_or_404(db, device_id)
    device_service.revoke(db, device, actor_id=user.id, reason=reason)
    return renderer.redirect(
        "/admin/devices",
        flash=f"{device.name} revoked. It wipes its cache on its next contact.",
        kind="info",
    )
