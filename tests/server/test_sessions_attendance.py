"""Device sessions, idempotent attendance ingest, absent materialisation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from server.app.models import (
    Attendance,
    AttendanceMethod,
    AttendanceSession,
    AttendanceStatus,
    SessionStatus,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _session_payload(course, section, faculty, **overrides):
    payload = {
        "id": str(uuid.uuid4()),
        "course_id": course.id,
        "section_id": section.id,
        "faculty_id": faculty.id,
        "period_id": None,
        "started_at": datetime.now(UTC).isoformat(),
        "clock_synced": True,
    }
    payload.update(overrides)
    return payload


def _event(session_id: str, usn: str, score: float = 0.71, **overrides):
    event = {
        "event_uuid": str(uuid.uuid4()),
        "session_id": session_id,
        "usn": usn,
        "score": score,
        "captured_at": datetime.now(UTC).isoformat(),
        "clock_synced": True,
    }
    event.update(overrides)
    return event


def test_session_create_is_validated_and_idempotent(
    client, db, device, course, section, faculty, offering
):
    _, token = device
    payload = _session_payload(course, section, faculty)
    first = client.post("/api/v1/sessions", json=payload, headers=_auth(token))
    assert first.status_code == 200, first.text
    assert first.json() == {"id": payload["id"], "status": "live"}
    again = client.post("/api/v1/sessions", json=payload, headers=_auth(token))
    assert again.status_code == 200
    assert db.scalar(select(func.count()).select_from(AttendanceSession)) == 1

    unknown_course = client.post(
        "/api/v1/sessions",
        json=_session_payload(course, section, faculty, course_id=999),
        headers=_auth(token),
    )
    assert unknown_course.status_code == 404
    not_offered = _session_payload(course, section, faculty)
    db.delete(offering)
    db.commit()
    assert (
        client.post("/api/v1/sessions", json=not_offered, headers=_auth(token)).status_code == 400
    )


def test_session_for_unassigned_section_is_forbidden(client, db, device, course, faculty):
    from server.app.models import CourseSection, Section

    _, token = device
    other = Section(name="CSE-5A", department="CSE", semester=5)
    db.add(other)
    db.flush()
    db.add(CourseSection(course_id=course.id, section_id=other.id, faculty_id=faculty.id))
    db.commit()
    response = client.post(
        "/api/v1/sessions", json=_session_payload(course, other, faculty), headers=_auth(token)
    )
    assert response.status_code == 403


def test_batch_ingest_is_idempotent_and_reports_each_event(
    client, db, device, course, section, faculty, offering, students
):
    _, token = device
    payload = _session_payload(course, section, faculty)
    assert client.post("/api/v1/sessions", json=payload, headers=_auth(token)).status_code == 200
    sid = payload["id"]

    events = [_event(sid, students[0].usn), _event(sid, students[1].usn)]
    stranger = _event(sid, "1BM22EC999")
    other_session = _event(str(uuid.uuid4()), students[2].usn)
    same_student_again = _event(sid, students[0].usn, score=0.66)
    body = {"events": [*events, stranger, other_session, same_student_again]}

    first = client.post("/api/v1/attendance/batch", json=body, headers=_auth(token)).json()
    assert sorted(first["accepted"]) == sorted(e["event_uuid"] for e in events)
    assert first["duplicates"] == [same_student_again["event_uuid"]]
    assert first["rejected"] == {
        stranger["event_uuid"]: "unknown student",
        other_session["event_uuid"]: "unknown session",
    }

    # Exactly the same batch again (the device never heard the first answer): no new rows.
    second = client.post("/api/v1/attendance/batch", json=body, headers=_auth(token)).json()
    assert second["accepted"] == []
    assert sorted(second["duplicates"]) == sorted(
        e["event_uuid"] for e in [*events, same_student_again]
    )
    assert db.scalar(select(func.count()).select_from(Attendance)) == 2

    row = db.scalar(select(Attendance).where(Attendance.student_id == students[0].id))
    assert (
        row is not None
        and row.status is AttendanceStatus.PRESENT
        and row.method is AttendanceMethod.FACE
    )
    assert row.score == 0.71 and row.event_uuid == uuid.UUID(events[0]["event_uuid"])
    assert row.received_at is not None


def test_end_session_materialises_absent_rows_once(
    client, db, device, course, section, faculty, offering, students
):
    _, token = device
    payload = _session_payload(course, section, faculty)
    client.post("/api/v1/sessions", json=payload, headers=_auth(token))
    sid = payload["id"]
    client.post(
        "/api/v1/attendance/batch",
        json={"events": [_event(sid, students[0].usn)]},
        headers=_auth(token),
    )

    ended_at = (datetime.now(UTC) + timedelta(minutes=50)).isoformat()
    end = client.post(
        f"/api/v1/sessions/{sid}/end", json={"ended_at": ended_at}, headers=_auth(token)
    )
    assert end.status_code == 200, end.text
    assert end.json() == {"id": sid, "status": "ended", "present": 1, "absent_marked": 2}

    rows = {
        r.student_id: r
        for r in db.scalars(select(Attendance).where(Attendance.session_id == uuid.UUID(sid)))
    }
    assert len(rows) == 3
    assert (
        rows[students[1].id].status is AttendanceStatus.ABSENT
        and rows[students[1].id].method is AttendanceMethod.MANUAL
    )
    session = db.get(AttendanceSession, uuid.UUID(sid))
    assert (
        session is not None
        and session.status is SessionStatus.ENDED
        and session.ended_at is not None
    )

    again = client.post(
        f"/api/v1/sessions/{sid}/end", json={"ended_at": ended_at}, headers=_auth(token)
    ).json()
    assert again["absent_marked"] == 0 and again["present"] == 1
    assert db.scalar(select(func.count()).select_from(Attendance)) == 3


def test_late_face_event_upgrades_a_materialised_absent(
    client, db, device, course, section, faculty, offering, students
):
    _, token = device
    payload = _session_payload(course, section, faculty)
    client.post("/api/v1/sessions", json=payload, headers=_auth(token))
    sid = payload["id"]
    client.post(
        f"/api/v1/sessions/{sid}/end",
        json={"ended_at": datetime.now(UTC).isoformat()},
        headers=_auth(token),
    )
    assert db.scalar(select(func.count()).select_from(Attendance)) == 3

    late = _event(sid, students[2].usn, score=0.8)
    result = client.post(
        "/api/v1/attendance/batch", json={"events": [late]}, headers=_auth(token)
    ).json()
    assert result["accepted"] == [late["event_uuid"]]
    row = db.scalar(select(Attendance).where(Attendance.student_id == students[2].id))
    db.refresh(row)
    assert (
        row.status is AttendanceStatus.PRESENT
        and row.method is AttendanceMethod.FACE
        and row.score == 0.8
    )
    assert db.scalar(select(func.count()).select_from(Attendance)) == 3


def test_unknown_session_end_is_404(client, device):
    _, token = device
    response = client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/end",
        json={"ended_at": datetime.now(UTC).isoformat()},
        headers=_auth(token),
    )
    assert response.status_code == 404
