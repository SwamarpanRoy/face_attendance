"""Device API: bearer tokens, heartbeat, scoped catalog, PIN check, roster."""

from __future__ import annotations

from datetime import UTC, datetime

from server.app.models import Course, CourseSection, Section


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _heartbeat_payload(**overrides):
    payload = {
        "device_id": "pi-test",
        "app_version": "0.1.0",
        "ip": "10.0.0.5",
        "ssid": "campus",
        "queue_len": 3,
        "cpu_temp_c": 51.2,
        "free_mem_mb": 812,
        "model_version": "buffalo_s/w600k_mbf",
        "clock_synced": False,
    }
    payload.update(overrides)
    return payload


def test_heartbeat_requires_a_valid_token(client, device):
    assert client.post("/api/v1/devices/heartbeat", json=_heartbeat_payload()).status_code == 401
    bad = client.post("/api/v1/devices/heartbeat", json=_heartbeat_payload(), headers=_auth("nope"))
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Unknown device token."


def test_heartbeat_updates_device_and_returns_assignments(client, db, device, section):
    row, token = device
    before = datetime.now(UTC)
    response = client.post(
        "/api/v1/devices/heartbeat", json=_heartbeat_payload(), headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device_name"] == "pi-test"
    assert body["assigned_section_ids"] == [section.id]
    db.refresh(row)
    assert row.queue_len == 3 and row.ssid == "campus" and row.clock_synced is False
    assert row.cpu_temp == 51.2 and row.free_mem_mb == 812
    assert row.last_seen_at is not None and row.last_seen_at >= before


def test_revoked_device_gets_a_distinct_401(client, db, device):
    row, token = device
    row.active = False
    db.commit()
    response = client.post(
        "/api/v1/devices/heartbeat", json=_heartbeat_payload(), headers=_auth(token)
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Device revoked."
    assert response.headers["x-device-status"] == "revoked"


def test_catalog_is_scoped_to_assigned_sections(
    client, db, device, section, course, offering, admin, faculty, make_faculty
):
    _, token = device
    other_section = Section(name="CSE-5A", department="CSE", semester=5)
    other_course = Course(code="22CS51", name="Databases", department="CSE", semester=5)
    other_faculty = make_faculty(email="other@test.local", pin="111222")
    db.add_all([other_section, other_course])
    db.flush()
    db.add(
        CourseSection(
            course_id=other_course.id, section_id=other_section.id, faculty_id=other_faculty.id
        )
    )
    db.commit()

    body = client.get("/api/v1/catalog", headers=_auth(token)).json()
    assert [s["id"] for s in body["sections"]] == [section.id]
    assert [c["code"] for c in body["courses"]] == ["22EC71"]
    assert body["offerings"] == [
        {"course_id": course.id, "section_id": section.id, "faculty_id": faculty.id}
    ]
    names = {f["id"]: f for f in body["faculty"]}
    assert faculty.id in names and admin.id in names and other_faculty.id not in names
    assert names[faculty.id]["pin_hash"].startswith("$argon2")
    assert names[faculty.id]["role"] == "faculty"


def test_pin_auth_identifies_the_faculty_member(client, device, faculty, admin):
    _, token = device
    ok = client.post("/api/v1/auth/pin", json={"pin": "246801"}, headers=_auth(token))
    assert ok.status_code == 200
    assert ok.json() == {"faculty_id": faculty.id, "name": faculty.name, "role": "faculty"}
    assert (
        client.post("/api/v1/auth/pin", json={"pin": "000000"}, headers=_auth(token)).status_code
        == 401
    )


def test_roster_only_for_assigned_sections(client, db, device, section, students):
    _, token = device
    other = Section(name="CSE-5A", department="CSE", semester=5)
    db.add(other)
    db.commit()
    assert (
        client.get(
            "/api/v1/roster", params={"section_id": other.id}, headers=_auth(token)
        ).status_code
        == 403
    )

    body = client.get(
        "/api/v1/roster", params={"section_id": section.id}, headers=_auth(token)
    ).json()
    assert body["section_id"] == section.id
    assert [s["usn"] for s in body["students"]] == [s.usn for s in students]
    assert all(s["has_consent"] is False and s["template_count"] == 0 for s in body["students"])


def test_openapi_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/devices/heartbeat" in schema["paths"]
