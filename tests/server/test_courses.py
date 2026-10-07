"""Courses, sections, offerings, roster CSV import and periods."""

from __future__ import annotations

from sqlalchemy import select

from server.app.models import Course, CourseSection, Period, Section, Student


def test_course_section_offering_flow(client, db, admin, faculty, login):
    token = login(admin.email)

    assert (
        client.post(
            "/admin/sections",
            data={"name": "ece-7b", "department": "ECE", "semester": "7", "csrf_token": token},
        ).status_code
        == 303
    )
    section = db.scalar(select(Section).where(Section.name == "ECE-7B"))
    assert section is not None

    assert (
        client.post(
            "/admin/courses",
            data={
                "code": "22ec72",
                "name": "Embedded Systems",
                "department": "ECE",
                "semester": "7",
                "csrf_token": token,
            },
        ).status_code
        == 303
    )
    course = db.scalar(select(Course).where(Course.code == "22EC72"))
    assert course is not None

    assert (
        client.post(
            f"/admin/courses/{course.id}/offerings",
            data={"section_id": section.id, "faculty_id": faculty.id, "csrf_token": token},
        ).status_code
        == 303
    )
    offering = db.scalar(select(CourseSection).where(CourseSection.course_id == course.id))
    assert offering is not None and offering.faculty_id == faculty.id

    detail = client.get(f"/admin/courses/{course.id}")
    assert "ECE-7B" in detail.text and faculty.name in detail.text

    assert (
        client.post(
            f"/admin/offerings/{offering.id}/delete", data={"csrf_token": token}
        ).status_code
        == 303
    )
    assert db.get(CourseSection, offering.id) is None


def test_csv_roster_import_upserts_and_reports_rejects(client, db, admin, login, section, students):
    token = login(admin.email)
    csv_bytes = (
        b"usn,name,semester\n"
        b"1BM22EC001,Renamed One,7\n"  # existing -> updated
        b"1BM22EC050,New Student,7\n"  # new -> created
        b"BAD,Broken Row,7\n"  # rejected
        b"1BM22EC050,Duplicate In File,7\n"  # rejected
    )
    response = client.post(
        f"/admin/sections/{section.id}/import",
        data={"csrf_token": token},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    assert "1 added, 1 updated, 2 rejected" in response.text
    assert "USN format looks wrong" in response.text
    assert "duplicate USN in file" in response.text

    renamed = db.scalar(select(Student).where(Student.usn == "1BM22EC001"))
    created = db.scalar(select(Student).where(Student.usn == "1BM22EC050"))
    assert renamed is not None and renamed.name == "Renamed One"
    assert created is not None and created.section_id == section.id


def test_csv_without_required_headers_is_rejected(client, admin, login, section):
    token = login(admin.email)
    response = client.post(
        f"/admin/sections/{section.id}/import",
        data={"csrf_token": token},
        files={"file": ("roster.csv", b"id,fullname\n1,x\n", "text/csv")},
    )
    assert response.status_code == 200
    assert "header must include" in response.text


def test_periods_create_validate_and_delete(client, db, admin, login):
    token = login(admin.email)
    bad = client.post(
        "/admin/periods",
        data={
            "ordinal": "1",
            "name": "P1",
            "start_time": "10:00",
            "end_time": "09:00",
            "csrf_token": token,
        },
    )
    assert bad.status_code == 400 and "End time must be after" in bad.text

    ok = client.post(
        "/admin/periods",
        data={
            "ordinal": "1",
            "name": "P1",
            "start_time": "09:00",
            "end_time": "10:00",
            "csrf_token": token,
        },
    )
    assert ok.status_code == 303
    period = db.scalar(select(Period).where(Period.ordinal == 1))
    assert period is not None

    dup = client.post(
        "/admin/periods",
        data={
            "ordinal": "1",
            "name": "Again",
            "start_time": "11:00",
            "end_time": "12:00",
            "csrf_token": token,
        },
    )
    assert dup.status_code == 400 and "already exists" in dup.text

    assert (
        client.post(f"/admin/periods/{period.id}/delete", data={"csrf_token": token}).status_code
        == 303
    )
    assert db.get(Period, period.id) is None
