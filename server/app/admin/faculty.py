"""Faculty accounts (admin only): create, edit, set password, set device PIN."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from server.app.admin.common import Renderer, get_renderer
from server.app.admin.validators import clean_email, clean_password, clean_text
from server.app.auth import AdminUser, AppSettings, DbSession, PasswordHasherDep, pin_problem
from server.app.models import Faculty, Role
from server.app.services import audit
from server.app.services.pins import pin_in_use

router = APIRouter(prefix="/faculty")


def _faculty_or_404(db: DbSession, faculty_id: int) -> Faculty:
    member = db.get(Faculty, faculty_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Faculty member not found.")
    return member


@router.get("", response_class=HTMLResponse)
def list_faculty(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
) -> HTMLResponse:
    members = list(db.scalars(select(Faculty).order_by(Faculty.active.desc(), Faculty.name)))
    return renderer.render(request, "faculty/list.html", {"members": members}, user=user)


@router.get("/new", response_class=HTMLResponse)
def new_faculty_form(
    request: Request, renderer: Annotated[Renderer, Depends(get_renderer)], user: AdminUser
) -> HTMLResponse:
    return renderer.render(
        request,
        "faculty/form.html",
        {"member": None, "values": {"role": "faculty", "active": True}, "errors": {}},
        user=user,
    )


@router.post("", response_class=HTMLResponse)
def create_faculty(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    hasher: PasswordHasherDep,
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    errors: dict[str, str] = {}
    values: dict[str, object] = {
        "name": clean_text(name, field="name", errors=errors, max_len=128),
        "email": clean_email(email, errors=errors),
        "role": role if role in ("admin", "faculty") else "faculty",
        "active": True,
    }
    clean_password(password, field="password", errors=errors)
    if "email" not in errors and db.scalar(select(Faculty).where(Faculty.email == values["email"])):
        errors["email"] = "An account with this email already exists."
    if errors:
        return renderer.render(
            request,
            "faculty/form.html",
            {"member": None, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    member = Faculty(
        name=str(values["name"]),
        email=str(values["email"]),
        role=Role(str(values["role"])),
        password_hash=hasher.hash(password),
    )
    db.add(member)
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action="faculty.create",
        entity="faculty",
        entity_id=member.id,
        after={"email": member.email, "role": member.role.value},
    )
    db.commit()
    return renderer.redirect(
        f"/admin/faculty/{member.id}/edit", flash=f"Created account for {member.name}."
    )


@router.get("/{faculty_id}/edit", response_class=HTMLResponse)
def edit_faculty_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    faculty_id: int,
) -> HTMLResponse:
    member = _faculty_or_404(db, faculty_id)
    values = {
        "name": member.name,
        "email": member.email,
        "role": member.role.value,
        "active": member.active,
    }
    return renderer.render(
        request, "faculty/form.html", {"member": member, "values": values, "errors": {}}, user=user
    )


@router.post("/{faculty_id}", response_class=HTMLResponse)
def update_faculty(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    faculty_id: int,
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
    active: Annotated[str, Form()] = "",
) -> Response:
    member = _faculty_or_404(db, faculty_id)
    errors: dict[str, str] = {}
    values: dict[str, object] = {
        "name": clean_text(name, field="name", errors=errors, max_len=128),
        "email": clean_email(email, errors=errors),
        "role": role if role in ("admin", "faculty") else member.role.value,
        "active": active == "on",
    }
    if "email" not in errors:
        clash = db.scalar(select(Faculty).where(Faculty.email == values["email"]))
        if clash is not None and clash.id != member.id:
            errors["email"] = "An account with this email already exists."
    if member.id == user.id and (values["role"] != "admin" or not values["active"]):
        errors["role"] = "You cannot demote or deactivate your own account."
    if errors:
        return renderer.render(
            request,
            "faculty/form.html",
            {"member": member, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    before = {
        "name": member.name,
        "email": member.email,
        "role": member.role.value,
        "active": member.active,
    }
    member.name = str(values["name"])
    member.email = str(values["email"])
    member.role = Role(str(values["role"]))
    member.active = bool(values["active"])
    audit.record(
        db,
        actor_id=user.id,
        action="faculty.update",
        entity="faculty",
        entity_id=member.id,
        before=before,
        after={
            "name": member.name,
            "email": member.email,
            "role": member.role.value,
            "active": member.active,
        },
    )
    db.commit()
    return renderer.redirect(f"/admin/faculty/{member.id}/edit", flash="Account updated.")


@router.post("/{faculty_id}/password", response_class=HTMLResponse)
def set_password(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    hasher: PasswordHasherDep,
    faculty_id: int,
    password: Annotated[str, Form()],
) -> Response:
    member = _faculty_or_404(db, faculty_id)
    errors: dict[str, str] = {}
    clean_password(password, field="password", errors=errors)
    if errors:
        values = {
            "name": member.name,
            "email": member.email,
            "role": member.role.value,
            "active": member.active,
        }
        return renderer.render(
            request,
            "faculty/form.html",
            {"member": member, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    member.password_hash = hasher.hash(password)
    audit.record(
        db, actor_id=user.id, action="faculty.password_set", entity="faculty", entity_id=member.id
    )
    db.commit()
    return renderer.redirect(f"/admin/faculty/{member.id}/edit", flash="Password set.")


@router.post("/{faculty_id}/pin", response_class=HTMLResponse)
def set_pin(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    hasher: PasswordHasherDep,
    settings: AppSettings,
    faculty_id: int,
    pin: Annotated[str, Form()],
) -> Response:
    member = _faculty_or_404(db, faculty_id)
    errors: dict[str, str] = {}
    problem = pin_problem(pin, settings)
    if problem:
        errors["pin"] = problem
    elif pin_in_use(db, hasher, pin, exclude_id=member.id):
        errors["pin"] = "That PIN is already used by another account. Choose a different one."
    if errors:
        values = {
            "name": member.name,
            "email": member.email,
            "role": member.role.value,
            "active": member.active,
        }
        return renderer.render(
            request,
            "faculty/form.html",
            {"member": member, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    member.pin_hash = hasher.hash(pin)
    member.pin_updated_at = datetime.now(UTC)
    audit.record(
        db, actor_id=user.id, action="faculty.pin_set", entity="faculty", entity_id=member.id
    )
    db.commit()
    return renderer.redirect(
        f"/admin/faculty/{member.id}/edit",
        flash="Device PIN set. Devices pick it up on their next sync.",
    )
