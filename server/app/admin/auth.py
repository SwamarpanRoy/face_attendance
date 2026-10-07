"""Login and logout for the admin UI."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from server.app.admin.common import Renderer, get_renderer
from server.app.auth import (
    LOGIN_CSRF_COOKIE,
    DbSession,
    LoginThrottle,
    PasswordHasherDep,
    SessionCodec,
    current_user_optional,
    get_login_throttle,
    get_session_codec,
)
from server.app.models import Faculty
from server.app.services import audit

router = APIRouter()


def _safe_next(value: str | None) -> str:
    """Only allow same-site relative redirects after login."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/admin"


def _login_page(
    request: Request,
    renderer: Renderer,
    codec: SessionCodec,
    *,
    next_url: str,
    error: str | None,
    email: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Render the login form with a fresh pre-login CSRF token (cookie + hidden field)."""
    token = secrets.token_urlsafe(32)
    response = renderer.render(
        request,
        "login.html",
        {"next": next_url, "error": error, "email": email, "login_csrf": token},
        status_code=status_code,
    )
    codec.issue_login_csrf(response, token)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    user: Annotated[Faculty | None, Depends(current_user_optional)],
    next: str | None = None,
) -> Response:
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _login_page(request, renderer, codec, next_url=next or "", error=None)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    renderer: Annotated[Renderer, Depends(get_renderer)],
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    hasher: PasswordHasherDep,
    db: DbSession,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    normalised = email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    blocked = throttle.seconds_blocked(f"email:{normalised}", f"ip:{client_ip}")
    if blocked > 0:
        return _login_page(
            request,
            renderer,
            codec,
            next_url=next,
            error=f"Too many failed attempts. Try again in {int(blocked // 60) + 1} minutes.",
            email=email,
            status_code=429,
        )
    user = db.scalar(select(Faculty).where(Faculty.email == normalised))
    hashed = user.password_hash if user is not None and user.active else None
    if user is None or not hasher.verify(hashed, password):
        throttle.record_failure(f"email:{normalised}", f"ip:{client_ip}")
        # One message for unknown email, wrong password and disabled account.
        return _login_page(
            request,
            renderer,
            codec,
            next_url=next,
            error="Email or password is incorrect.",
            email=email,
            status_code=401,
        )
    throttle.reset(f"email:{normalised}")
    if hasher.needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(password)
    audit.record(db, actor_id=user.id, action="auth.login", entity="faculty", entity_id=user.id)
    db.commit()
    response = RedirectResponse(_safe_next(next), status_code=303)
    codec.write(response, codec.new_session(user.id))
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/admin/login")
    return response


@router.post("/logout")
def logout(
    db: DbSession,
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    user: Annotated[Faculty | None, Depends(current_user_optional)],
) -> RedirectResponse:
    if user is not None:
        audit.record(
            db, actor_id=user.id, action="auth.logout", entity="faculty", entity_id=user.id
        )
        db.commit()
    response = RedirectResponse("/admin/login", status_code=303)
    codec.clear(response)
    return response
