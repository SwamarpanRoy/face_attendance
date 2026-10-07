"""Shared plumbing for the admin UI: template rendering, flash messages, pagination.

Templates are server-rendered Jinja2; HTMX swaps fragments. Every rendered page gets
the current user, the CSRF token and any pending flash message so individual routes
only pass their own data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette import status

from server.app.auth import SessionCodec
from server.app.config import Settings
from server.app.models import Faculty, Role

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
FLASH_COOKIE = "fa_flash"
FLASH_SALT = "flash"
PAGE_SIZE = 50


@dataclass(frozen=True)
class Page:
    """Offset pagination state for list views."""

    number: int
    size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.size))

    @property
    def offset(self) -> int:
        return (self.number - 1) * self.size

    @property
    def has_prev(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


def page_from_query(request: Request, total: int, size: int = PAGE_SIZE) -> Page:
    raw = request.query_params.get("page", "1")
    number = int(raw) if raw.isdigit() and int(raw) > 0 else 1
    return Page(
        number=min(number, max(1, math.ceil(total / size)) if total else 1), size=size, total=total
    )


def url_with(request: Request, **params: Any) -> str:
    """Current path with query params merged (used by pagination and filters)."""
    merged = dict(request.query_params)
    for key, value in params.items():
        if value is None or value == "":
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    query = urlencode(merged)
    return f"{request.url.path}?{query}" if query else request.url.path


class Renderer:
    """Renders templates with the standard context and handles flash cookies."""

    def __init__(self, settings: Settings, codec: SessionCodec) -> None:
        self.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        self.templates.env.globals.update(app_name=settings.app_name, url_with=url_with)
        # ``ids|zip(names)|list`` builds (value, label) pairs for the select macro.
        self.templates.env.filters["zip"] = zip
        self.templates.env.filters["local"] = self._local
        self.templates.env.filters["localdate"] = lambda v: self._local(v, "%d %b %Y")
        self.templates.env.filters["localtime"] = lambda v: self._local(v, "%H:%M")
        self.settings = settings
        self._codec = codec
        self._flash = URLSafeTimedSerializer(settings.secret_key, salt=FLASH_SALT)

    def _local(self, value: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
        if value is None:
            return "–"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(ZoneInfo(self.settings.timezone)).strftime(fmt)

    def render(
        self,
        request: Request,
        name: str,
        context: dict[str, Any] | None = None,
        *,
        user: Faculty | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        session = self._codec.read(request)
        flash = self._pop_flash(request)
        ctx: dict[str, Any] = {
            "user": user,
            "is_admin": user is not None and user.role is Role.ADMIN,
            "csrf_token": session.csrf if session else "",
            "flash": flash,
            "settings": self.settings,
            "is_htmx": request.headers.get("HX-Request") == "true",
        }
        ctx.update(context or {})
        response = self.templates.TemplateResponse(request, name, ctx, status_code=status_code)
        if flash is not None:
            response.delete_cookie(FLASH_COOKIE, path="/")
        return response

    def redirect(
        self, url: str, *, flash: str | None = None, kind: str = "success"
    ) -> RedirectResponse:
        """303 redirect after a successful POST, optionally carrying a one-shot message."""
        response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
        if flash:
            self.set_flash(response, flash, kind)
        return response

    def set_flash(self, response: Response, message: str, kind: str = "success") -> None:
        response.set_cookie(
            FLASH_COOKIE,
            self._flash.dumps({"m": message, "k": kind}),
            max_age=120,
            httponly=True,
            samesite="lax",
            secure=self.settings.session_cookie_secure,
            path="/",
        )

    def _pop_flash(self, request: Request) -> dict[str, str] | None:
        raw = request.cookies.get(FLASH_COOKIE)
        if not raw:
            return None
        try:
            data = self._flash.loads(raw, max_age=120)
        except (BadSignature, SignatureExpired):
            return None
        return {"message": str(data.get("m", "")), "kind": str(data.get("k", "info"))}


def get_renderer(request: Request) -> Renderer:
    renderer: Renderer = request.app.state.renderer
    return renderer
