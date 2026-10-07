"""Login throttling, security headers and the request body cap."""

from __future__ import annotations

from server.app.auth import LoginThrottle
from tests.server.conftest import csrf_from


def test_login_throttle_counts_per_key_and_expires():
    throttle = LoginThrottle(max_failures=3, lockout_seconds=60)
    for t in (0.0, 1.0):
        throttle.record_failure("email:a", "ip:1", now=t)
    assert throttle.seconds_blocked("email:a", now=2.0) == 0
    throttle.record_failure("email:a", "ip:1", now=2.0)
    assert throttle.seconds_blocked("email:a", now=3.0) == 57.0
    assert throttle.seconds_blocked("email:b", "ip:1", now=3.0) == 57.0  # same IP is blocked too
    assert throttle.seconds_blocked("email:a", now=61.0) == 0.0  # oldest failure expired
    throttle.record_failure("email:c", now=100.0)
    throttle.reset("email:c")
    assert throttle.seconds_blocked("email:c", now=100.0) == 0.0


def test_repeated_wrong_passwords_lock_the_account(client, admin, app):
    app.state.login_throttle = LoginThrottle(max_failures=3, lockout_seconds=600)
    for _ in range(3):
        page = client.get("/admin/login")
        response = client.post(
            "/admin/login",
            data={"email": admin.email, "password": "wrong", "csrf_token": csrf_from(page.text)},
        )
        assert response.status_code == 401
    page = client.get("/admin/login")
    locked = client.post(
        "/admin/login",
        data={
            "email": admin.email,
            "password": "correct horse battery staple",
            "csrf_token": csrf_from(page.text),
        },
    )
    assert locked.status_code == 429 and "Too many failed attempts" in locked.text
    assert client.get("/admin").status_code == 303  # still not logged in


def test_security_headers_and_body_cap(client):
    response = client.get("/admin/login")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    too_big = client.post(
        "/api/v1/devices/heartbeat",
        content=b"x",
        headers={"Content-Length": str(50 * 1024 * 1024), "Content-Type": "application/json"},
    )
    assert too_big.status_code == 413
