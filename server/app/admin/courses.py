"""Courses, sections, course offerings (course x section x faculty) and timetable periods."""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from server.app.admin.common import Renderer, get_renderer
from server.app.admin.validators import clean_int, clean_text
from server.app.auth import AdminUser, AppSettings, CurrentUser, DbSession
from server.app.models import Course, CourseSection, Faculty, Period, Section, Student
from server.app.services import audit
from server.app.services.roster import ImportResult, import_roster_csv

router = APIRouter()

MAX_CSV_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------- courses
def _course_or_404(db: DbSession, course_id: int) -> Course:
    course = db.get(
        Course,
        course_id,
        options=[
            selectinload(Course.offerings).selectinload(CourseSection.section),
            selectinload(Course.offerings).selectinload(CourseSection.faculty),
        ],
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found.")
    return course


@router.get("/courses", response_class=HTMLResponse)
def list_courses(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    courses = list(
        db.scalars(
            select(Course)
            .options(
                selectinload(Course.offerings).selectinload(CourseSection.section),
                selectinload(Course.offerings).selectinload(CourseSection.faculty),
            )
            .order_by(Course.semester, Course.code)
        )
    )
    return renderer.render(request, "courses/list.html", {"courses": courses}, user=user)


def _validate_course(
    db: DbSession, *, code: str, name: str, department: str, semester: str, current: Course | None
) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    values: dict[str, Any] = {
        "code": clean_text(code, field="code", errors=errors, max_len=16).upper(),
        "name": clean_text(name, field="name", errors=errors, max_len=128),
        "department": clean_text(department, field="department", errors=errors, max_len=64),
        "semester": clean_int(semester, field="semester", errors=errors, lo=1, hi=8),
    }
    if "code" not in errors:
        clash = db.scalar(select(Course).where(Course.code == values["code"]))
        if clash is not None and (current is None or clash.id != current.id):
            errors["code"] = "A course with this code already exists."
    return values, errors


@router.get("/courses/new", response_class=HTMLResponse)
def new_course_form(
    request: Request, renderer: Annotated[Renderer, Depends(get_renderer)], user: AdminUser
) -> HTMLResponse:
    return renderer.render(
        request, "courses/form.html", {"course": None, "values": {}, "errors": {}}, user=user
    )


@router.post("/courses", response_class=HTMLResponse)
def create_course(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    code: Annotated[str, Form()],
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
) -> Response:
    values, errors = _validate_course(
        db, code=code, name=name, department=department, semester=semester, current=None
    )
    if errors:
        return renderer.render(
            request,
            "courses/form.html",
            {"course": None, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    course = Course(
        code=str(values["code"]),
        name=str(values["name"]),
        department=str(values["department"]),
        semester=int(values["semester"]),
    )
    db.add(course)
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action="course.create",
        entity="course",
        entity_id=course.id,
        after={"code": course.code, "name": course.name},
    )
    db.commit()
    return renderer.redirect(f"/admin/courses/{course.id}", flash=f"Added {course.code}.")


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_detail(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    course_id: int,
) -> HTMLResponse:
    course = _course_or_404(db, course_id)
    sections = list(db.scalars(select(Section).order_by(Section.name)))
    faculty = list(db.scalars(select(Faculty).where(Faculty.active).order_by(Faculty.name)))
    return renderer.render(
        request,
        "courses/detail.html",
        {"course": course, "sections": sections, "faculty": faculty, "errors": {}},
        user=user,
    )


@router.get("/courses/{course_id}/edit", response_class=HTMLResponse)
def edit_course_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    course_id: int,
) -> HTMLResponse:
    course = _course_or_404(db, course_id)
    values = {
        "code": course.code,
        "name": course.name,
        "department": course.department,
        "semester": course.semester,
    }
    return renderer.render(
        request, "courses/form.html", {"course": course, "values": values, "errors": {}}, user=user
    )


@router.post("/courses/{course_id}", response_class=HTMLResponse)
def update_course(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    course_id: int,
    code: Annotated[str, Form()],
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
) -> Response:
    course = _course_or_404(db, course_id)
    values, errors = _validate_course(
        db, code=code, name=name, department=department, semester=semester, current=course
    )
    if errors:
        return renderer.render(
            request,
            "courses/form.html",
            {"course": course, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    before = {"code": course.code, "name": course.name}
    course.code = str(values["code"])
    course.name = str(values["name"])
    course.department = str(values["department"])
    course.semester = int(values["semester"])
    audit.record(
        db,
        actor_id=user.id,
        action="course.update",
        entity="course",
        entity_id=course.id,
        before=before,
        after={"code": course.code, "name": course.name},
    )
    db.commit()
    return renderer.redirect(f"/admin/courses/{course.id}", flash="Course updated.")


@router.post("/courses/{course_id}/offerings")
def add_offering(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    course_id: int,
    section_id: Annotated[int, Form()],
    faculty_id: Annotated[int, Form()],
) -> Response:
    course = _course_or_404(db, course_id)
    section = db.get(Section, section_id)
    faculty = db.get(Faculty, faculty_id)
    if section is None or faculty is None or not faculty.active:
        raise HTTPException(status_code=400, detail="Unknown section or faculty.")
    existing = db.scalar(
        select(CourseSection).where(
            CourseSection.course_id == course.id, CourseSection.section_id == section.id
        )
    )
    if existing is not None:
        existing.faculty_id = faculty.id
        action = "offering.update"
        offering = existing
    else:
        offering = CourseSection(course_id=course.id, section_id=section.id, faculty_id=faculty.id)
        db.add(offering)
        action = "offering.create"
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action=action,
        entity="course_section",
        entity_id=offering.id,
        after={"course": course.code, "section": section.name, "faculty_id": faculty.id},
    )
    db.commit()
    return renderer.redirect(
        f"/admin/courses/{course.id}",
        flash=f"{course.code} is now taught to {section.name} by {faculty.name}.",
    )


@router.post("/offerings/{offering_id}/delete")
def delete_offering(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    offering_id: int,
) -> RedirectResponse:
    offering = db.get(CourseSection, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Offering not found.")
    course_id = offering.course_id
    audit.record(
        db,
        actor_id=user.id,
        action="offering.delete",
        entity="course_section",
        entity_id=offering.id,
        before={
            "course_id": offering.course_id,
            "section_id": offering.section_id,
            "faculty_id": offering.faculty_id,
        },
    )
    db.delete(offering)
    db.commit()
    return renderer.redirect(f"/admin/courses/{course_id}", flash="Offering removed.")


# --------------------------------------------------------------------------- sections
def _section_or_404(db: DbSession, section_id: int) -> Section:
    section = db.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    return section


@router.get("/sections", response_class=HTMLResponse)
def list_sections(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    student_count = (
        select(func.count(Student.id))
        .where(Student.section_id == Section.id, Student.active)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Section, student_count.label("students")).order_by(Section.semester, Section.name)
    ).all()
    return renderer.render(request, "sections/list.html", {"rows": rows}, user=user)


def _validate_section(
    db: DbSession, *, name: str, department: str, semester: str, current: Section | None
) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    values: dict[str, Any] = {
        "name": clean_text(name, field="name", errors=errors, max_len=32).upper(),
        "department": clean_text(department, field="department", errors=errors, max_len=64),
        "semester": clean_int(semester, field="semester", errors=errors, lo=1, hi=8),
    }
    if "name" not in errors:
        clash = db.scalar(select(Section).where(Section.name == values["name"]))
        if clash is not None and (current is None or clash.id != current.id):
            errors["name"] = "A section with this name already exists."
    return values, errors


@router.get("/sections/new", response_class=HTMLResponse)
def new_section_form(
    request: Request, renderer: Annotated[Renderer, Depends(get_renderer)], user: AdminUser
) -> HTMLResponse:
    return renderer.render(
        request, "sections/form.html", {"section": None, "values": {}, "errors": {}}, user=user
    )


@router.post("/sections", response_class=HTMLResponse)
def create_section(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
) -> Response:
    values, errors = _validate_section(
        db, name=name, department=department, semester=semester, current=None
    )
    if errors:
        return renderer.render(
            request,
            "sections/form.html",
            {"section": None, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    section = Section(
        name=str(values["name"]),
        department=str(values["department"]),
        semester=int(values["semester"]),
    )
    db.add(section)
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action="section.create",
        entity="section",
        entity_id=section.id,
        after={"name": section.name},
    )
    db.commit()
    return renderer.redirect(
        f"/admin/sections/{section.id}", flash=f"Added section {section.name}."
    )


@router.get("/sections/{section_id}", response_class=HTMLResponse)
def section_detail(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
    section_id: int,
) -> HTMLResponse:
    section = _section_or_404(db, section_id)
    students = list(
        db.scalars(select(Student).where(Student.section_id == section.id).order_by(Student.usn))
    )
    offerings = list(
        db.scalars(
            select(CourseSection)
            .options(selectinload(CourseSection.course), selectinload(CourseSection.faculty))
            .where(CourseSection.section_id == section.id)
        )
    )
    return renderer.render(
        request,
        "sections/detail.html",
        {"section": section, "students": students, "offerings": offerings, "import_result": None},
        user=user,
    )


@router.get("/sections/{section_id}/edit", response_class=HTMLResponse)
def edit_section_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    section_id: int,
) -> HTMLResponse:
    section = _section_or_404(db, section_id)
    values = {"name": section.name, "department": section.department, "semester": section.semester}
    return renderer.render(
        request,
        "sections/form.html",
        {"section": section, "values": values, "errors": {}},
        user=user,
    )


@router.post("/sections/{section_id}", response_class=HTMLResponse)
def update_section(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    section_id: int,
    name: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
) -> Response:
    section = _section_or_404(db, section_id)
    values, errors = _validate_section(
        db, name=name, department=department, semester=semester, current=section
    )
    if errors:
        return renderer.render(
            request,
            "sections/form.html",
            {"section": section, "values": values, "errors": errors},
            user=user,
            status_code=400,
        )
    before = {"name": section.name, "department": section.department, "semester": section.semester}
    section.name = str(values["name"])
    section.department = str(values["department"])
    section.semester = int(values["semester"])
    audit.record(
        db,
        actor_id=user.id,
        action="section.update",
        entity="section",
        entity_id=section.id,
        before=before,
        after={
            "name": section.name,
            "department": section.department,
            "semester": section.semester,
        },
    )
    db.commit()
    return renderer.redirect(f"/admin/sections/{section.id}", flash="Section updated.")


@router.post("/sections/{section_id}/import", response_class=HTMLResponse)
async def import_section_roster(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    section_id: int,
    file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    section = _section_or_404(db, section_id)
    data = await file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        result = ImportResult(rejected=[(0, "", "file larger than 2 MB")])
    else:
        result = import_roster_csv(
            db, section, data, usn_pattern=settings.usn_pattern, actor_id=user.id
        )
    students = list(
        db.scalars(select(Student).where(Student.section_id == section.id).order_by(Student.usn))
    )
    offerings = list(
        db.scalars(
            select(CourseSection)
            .options(selectinload(CourseSection.course), selectinload(CourseSection.faculty))
            .where(CourseSection.section_id == section.id)
        )
    )
    return renderer.render(
        request,
        "sections/detail.html",
        {"section": section, "students": students, "offerings": offerings, "import_result": result},
        user=user,
    )


# --------------------------------------------------------------------------- periods
@router.get("/periods", response_class=HTMLResponse)
def list_periods(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    periods = list(db.scalars(select(Period).order_by(Period.ordinal)))
    return renderer.render(request, "periods.html", {"periods": periods, "errors": {}}, user=user)


def _parse_time(value: str) -> time | None:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        return None


@router.post("/periods", response_class=HTMLResponse)
def create_period(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    ordinal: Annotated[str, Form()],
    name: Annotated[str, Form()],
    start_time: Annotated[str, Form()],
    end_time: Annotated[str, Form()],
) -> Response:
    errors: dict[str, str] = {}
    number = clean_int(ordinal, field="ordinal", errors=errors, lo=1, hi=20)
    label = clean_text(name, field="name", errors=errors, max_len=32)
    start = _parse_time(start_time)
    end = _parse_time(end_time)
    if start is None or end is None:
        errors["time"] = "Enter times as HH:MM."
    elif end <= start:
        errors["time"] = "End time must be after start time."
    if number is not None and db.scalar(select(Period).where(Period.ordinal == number)) is not None:
        errors["ordinal"] = "That period number already exists."
    if errors:
        periods = list(db.scalars(select(Period).order_by(Period.ordinal)))
        return renderer.render(
            request,
            "periods.html",
            {"periods": periods, "errors": errors},
            user=user,
            status_code=400,
        )
    assert number is not None and start is not None and end is not None
    period = Period(ordinal=number, name=label, start_time=start, end_time=end)
    db.add(period)
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action="period.create",
        entity="period",
        entity_id=period.id,
        after={"ordinal": number, "name": label},
    )
    db.commit()
    return renderer.redirect("/admin/periods", flash=f"Added {label}.")


@router.post("/periods/{period_id}/delete")
def delete_period(
    renderer: Annotated[Renderer, Depends(get_renderer)],
    db: DbSession,
    user: AdminUser,
    period_id: int,
) -> RedirectResponse:
    period = db.get(Period, period_id)
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found.")
    audit.record(
        db,
        actor_id=user.id,
        action="period.delete",
        entity="period",
        entity_id=period.id,
        before={"ordinal": period.ordinal, "name": period.name},
    )
    db.delete(period)
    db.commit()
    return renderer.redirect("/admin/periods", flash="Period removed.")
