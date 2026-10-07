"""Admin login, sessions, CSRF and role gates."""

from __future__ import annotations

from tests.server.conftest import csrf_from


def test_anonymous_browser_is_redirected_to_login(client):
    response = client.get("/admin")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_login_then_dashboard(client, admin, login):
    login(admin.email)
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Dashboard" in page.text
    assert admin.name in page.text


def test_wrong_password_is_rejected_with_one_generic_message(client, admin):
    page = client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"email": admin.email, "password": "nope", "csrf_token": csrf_from(page.text)},
    )
    assert response.status_code == 401
    assert "Email or password is incorrect" in response.text
    unknown = client.post(
        "/admin/login",
        data={
            "email": "nobody@test.local",
            "password": "nope",
            "csrf_token": csrf_from(response.text),
        },
    )
    assert unknown.status_code == 401
    assert "Email or password is incorrect" in unknown.text


def test_device_pin_never_works_as_dashboard_password(client, admin):
    page = client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"email": admin.email, "password": "135790", "csrf_token": csrf_from(page.text)},
    )
    assert response.status_code == 401


def test_login_without_csrf_token_is_rejected(client, admin):
    client.get("/admin/login")
    response = client.post("/admin/login", data={"email": admin.email, "password": "x"})
    assert response.status_code == 403


def test_post_without_csrf_token_is_rejected_when_logged_in(client, admin, login):
    login(admin.email)
    response = client.post(
        "/admin/students",
        data={"usn": "1BM22EC001", "name": "X", "department": "ECE", "semester": "7"},
    )
    assert response.status_code == 403


def test_csrf_token_accepted_as_header_for_htmx(client, admin, login):
    token = login(admin.email)
    response = client.post(
        "/admin/students",
        data={"usn": "1BM22EC001", "name": "Header Student", "department": "ECE", "semester": "7"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 303


def test_logout_clears_the_session(client, admin, login):
    token = login(admin.email)
    response = client.post("/admin/logout", data={"csrf_token": token})
    assert response.status_code == 303
    assert client.get("/admin").status_code == 303


def test_faculty_role_cannot_open_admin_only_pages(client, faculty, login):
    login(faculty.email)
    response = client.get("/admin/faculty", headers={"accept": "text/html"})
    assert response.status_code == 403
    assert "Not allowed" in response.text
    assert client.get("/admin/students").status_code == 200


def test_inactive_account_cannot_log_in(client, db, admin):
    admin.active = False
    db.commit()
    page = client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={
            "email": admin.email,
            "password": "correct horse battery staple",
            "csrf_token": csrf_from(page.text),
        },
    )
    assert response.status_code == 401


def test_user_can_set_own_pin_and_duplicates_are_refused(client, db, admin, faculty, login):
    token = login(admin.email)
    clash = client.post(
        "/admin/me/pin",
        data={
            "current_password": "correct horse battery staple",
            "pin": "246801",
            "confirm_pin": "246801",
            "csrf_token": token,
        },
    )
    assert clash.status_code == 400
    assert "already used" in clash.text
    ok = client.post(
        "/admin/me/pin",
        data={
            "current_password": "correct horse battery staple",
            "pin": "987654",
            "confirm_pin": "987654",
            "csrf_token": token,
        },
    )
    assert ok.status_code == 303
    db.refresh(admin)
    assert admin.pin_hash and admin.pin_updated_at is not None
