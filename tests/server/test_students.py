"""Student CRUD through the admin UI, including validation and audit rows."""

from __future__ import annotations

from sqlalchemy import select

from server.app.models import AuditLog, Student


def _form(**overrides):
    data = {"usn": "1bm22ec001", "name": "Aditi Rao", "department": "ECE", "semester": "7"}
    data.update(overrides)
    return data


def test_admin_creates_student_normalises_usn_and_audits(client, db, admin, login):
    token = login(admin.email)
    response = client.post("/admin/students", data=_form(csrf_token=token))
    assert response.status_code == 303, response.text
    student = db.scalar(select(Student).where(Student.usn == "1BM22EC001"))
    assert student is not None and student.name == "Aditi Rao"
    assert response.headers["location"] == f"/admin/students/{student.id}"

    listing = client.get("/admin/students", params={"q": "aditi"})
    assert "1BM22EC001" in listing.text

    log = db.scalar(select(AuditLog).where(AuditLog.action == "student.create"))
    assert log is not None and log.actor_id == admin.id and log.entity_id == str(student.id)


def test_invalid_usn_is_rejected_with_a_readable_message(client, admin, login):
    token = login(admin.email)
    response = client.post("/admin/students", data=_form(usn="abc", csrf_token=token))
    assert response.status_code == 400
    assert "USN format looks wrong" in response.text


def test_duplicate_usn_is_rejected(client, admin, login):
    token = login(admin.email)
    assert client.post("/admin/students", data=_form(csrf_token=token)).status_code == 303
    response = client.post("/admin/students", data=_form(name="Someone Else", csrf_token=token))
    assert response.status_code == 400
    assert "already exists" in response.text


def test_faculty_can_view_but_not_create_students(client, faculty, login, students):
    token = login(faculty.email)
    assert client.get("/admin/students").status_code == 200
    response = client.post("/admin/students", data=_form(csrf_token=token))
    assert response.status_code == 403


def test_edit_moves_student_between_sections_and_can_deactivate(
    client, db, admin, login, students, section
):
    token = login(admin.email)
    target = students[0]
    response = client.post(
        f"/admin/students/{target.id}",
        data={
            "usn": target.usn,
            "name": "Renamed Student",
            "department": "ECE",
            "semester": "7",
            "section_id": "",
            "csrf_token": token,
        },
    )
    assert response.status_code == 303
    db.refresh(target)
    assert target.name == "Renamed Student"
    assert target.section_id is None
    assert target.active is False  # checkbox not sent => inactive

    detail = client.get(f"/admin/students/{target.id}")
    assert "inactive" in detail.text
    assert "No consent recorded" in detail.text
