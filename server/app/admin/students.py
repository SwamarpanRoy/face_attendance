"""Student list, detail and CRUD. Enrolment, consent and history views arrive in M4/M7."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer, page_from_query
from server.app.admin.validators import clean_int, clean_text, clean_usn
from server.app.auth import AdminUser, AppSettings, CurrentUser, DbSession
from server.app.models import Consent, FaceTemplate, Section, Student
from server.app.services import audit, enrolment, reports

router = APIRouter(prefix="/students")


def _student_or_404(db: DbSession, student_id: int) -> Student:
    student = db.get(Student, student_id, options=[selectinload(Student.section)])
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    return student


def _sections(db: DbSession) -> list[Section]:
    return list(db.scalars(select(Section).order_by(Section.name)))


@router.get("", response_class=HTMLResponse)
def list_students(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    q: str = "",
    section: int | None = None,
    show_inactive: bool = False,
) -> HTMLResponse:
    template_count = (
        select(func.count(FaceTemplate.id))
        .where(FaceTemplate.student_id == Student.id)
        .scalar_subquery()
    )
    has_consent = exists().where(Consent.student_id == Student.id, Consent.withdrawn_at.is_(None))
    stmt = select(Student, template_count.label("templates"), has_consent.label("consented"))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Student.usn.ilike(like), Student.name.ilike(like)))
    if section:
        stmt = stmt.where(Student.section_id == section)
    if not show_inactive:
        stmt = stmt.where(Student.active.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = page_from_query(request, total)
    rows = db.execute(
        stmt.options(selectinload(Student.section))
        .order_by(Student.usn)
        .offset(page.offset)
        .limit(page.size)
    ).all()
    return renderer.render(
        request,
        "students/list.html",
        {
            "rows": rows,
            "page": page,
            "q": q,
            "section_id": section,
            "sections": _sections(db),
            "show_inactive": show_inactive,
        },
        user=user,
    )


@router.get("/new", response_class=HTMLResponse)
def new_student_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
) -> HTMLResponse:
    return renderer.render(
        request,
        "students/form.html",
        {"student": None, "values": {"active": True}, "errors": {}, "sections": _sections(db)},
        user=user,
    )


def _validate_student_form(
    db: DbSession,
    settings: AppSettings,
    *,
    usn: str,
    name: str,
    department: str,
    semester: str,
    section_id: str,
    current: Student | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    values: dict[str, Any] = {
        "usn": clean_usn(usn, pattern=settings.usn_pattern, errors=errors),
        "name": clean_text(name, field="name", errors=errors, max_len=128),
        "department": clean_text(department, field="department", errors=errors, max_len=64),
        "semester": clean_int(semester, field="semester", errors=errors, lo=1, hi=8),
        "section_id": int(section_id) if section_id.isdigit() else None,
    }
    if values["section_id"] is not None and db.get(Section, values["section_id"]) is None:
        errors["section_id"] = "Unknown section."
    if "usn" not in errors:
        clash = db.scalar(select(Student).where(Student.usn == values["usn"]))
        if clash is not None and (current is None or clash.id != current.id):
            errors["usn"] = "A student with this USN already exists."
    return values, errors


@router.post("", response_class=HTMLResponse)
def create_student(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    usn: Annotated[str, Form()],
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
    section_id: Annotated[str, Form()] = "",
) -> Response:
    values, errors = _validate_student_form(
        db,
        settings,
        usn=usn,
        name=name,
        department=department,
        semester=semester,
        section_id=section_id,
        current=None,
    )
    if errors:
        return renderer.render(
            request,
            "students/form.html",
            {"student": None, "values": values, "errors": errors, "sections": _sections(db)},
            user=user,
            status_code=400,
        )
    student = Student(
        usn=str(values["usn"]),
        name=str(values["name"]),
        department=str(values["department"]),
        semester=int(values["semester"]),
        section_id=values["section_id"],
    )
    db.add(student)
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action="student.create",
        entity="student",
        entity_id=student.id,
        after={"usn": student.usn, "name": student.name},
    )
    db.commit()
    return renderer.redirect(f"/admin/students/{student.id}", flash=f"Added {student.usn}.")


@router.get("/{student_id}", response_class=HTMLResponse)
def student_detail(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    student_id: int,
) -> HTMLResponse:
    student = _student_or_404(db, student_id)
    consents = list(
        db.scalars(
            select(Consent)
            .where(Consent.student_id == student.id)
            .order_by(Consent.consent_at.desc())
        )
    )
    templates = list(
        db.scalars(
            select(FaceTemplate)
            .where(FaceTemplate.student_id == student.id)
            .order_by(FaceTemplate.created_at.desc())
        )
    )
    history = reports.student_history(db, student, tz=settings.timezone)
    return renderer.render(
        request,
        "students/detail.html",
        {
            "student": student,
            "consents": consents,
            "templates": templates,
            "history": history,
            "has_active_consent": any(c.withdrawn_at is None for c in consents),
        },
        user=user,
    )


@router.get("/{student_id}/edit", response_class=HTMLResponse)
def edit_student_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    student_id: int,
) -> HTMLResponse:
    student = _student_or_404(db, student_id)
    values = {
        "usn": student.usn,
        "name": student.name,
        "department": student.department,
        "semester": student.semester,
        "section_id": student.section_id,
        "active": student.active,
    }
    return renderer.render(
        request,
        "students/form.html",
        {"student": student, "values": values, "errors": {}, "sections": _sections(db)},
        user=user,
    )


@router.post("/{student_id}", response_class=HTMLResponse)
def update_student(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    student_id: int,
    usn: Annotated[str, Form()],
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
    section_id: Annotated[str, Form()] = "",
    active: Annotated[str, Form()] = "",
) -> Response:
    student = _student_or_404(db, student_id)
    values, errors = _validate_student_form(
        db,
        settings,
        usn=usn,
        name=name,
        department=department,
        semester=semester,
        section_id=section_id,
        current=student,
    )
    values["active"] = active == "on"
    if errors:
        return renderer.render(
            request,
            "students/form.html",
            {"student": student, "values": values, "errors": errors, "sections": _sections(db)},
            user=user,
            status_code=400,
        )
    before = {
        "usn": student.usn,
        "name": student.name,
        "section_id": student.section_id,
        "active": student.active,
    }
    student.usn = str(values["usn"])
    student.name = str(values["name"])
    student.department = str(values["department"])
    student.semester = int(values["semester"])
    student.section_id = values["section_id"]
    student.active = bool(values["active"])
    after = {
        "usn": student.usn,
        "name": student.name,
        "section_id": student.section_id,
        "active": student.active,
    }
    audit.record(
        db,
        actor_id=user.id,
        action="student.update",
        entity="student",
        entity_id=student.id,
        before=before,
        after=after,
    )
    db.commit()
    return renderer.redirect(f"/admin/students/{student.id}", flash="Student updated.")


@router.post("/{student_id}/reenrol")
def reenrol(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    student_id: int,
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Drop the current templates so the next device enrolment starts clean. Consent stays."""
    student = _student_or_404(db, student_id)
    if not reason.strip():
        return renderer.redirect(
            f"/admin/students/{student.id}", flash="A reason is required.", kind="error"
        )
    removed = enrolment.delete_templates(
        db, settings, student, actor_id=user.id, reason=reason, remove_crops=False
    )
    return renderer.redirect(
        f"/admin/students/{student.id}",
        flash=f"{removed} templates removed; enrol {student.usn} again on a device.",
    )


@router.post("/{student_id}/withdraw-consent")
def withdraw_consent(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    student_id: int,
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """DPDP withdrawal: templates and crops deleted, attendance kept, fully audited."""
    student = _student_or_404(db, student_id)
    if not reason.strip():
        return renderer.redirect(
            f"/admin/students/{student.id}", flash="A reason is required.", kind="error"
        )
    removed = enrolment.withdraw_consent(db, settings, student, actor_id=user.id, reason=reason)
    return renderer.redirect(
        f"/admin/students/{student.id}",
        flash=(
            f"Consent withdrawn; {removed} templates and all crops deleted. "
            "Attendance history kept."
        ),
    )
