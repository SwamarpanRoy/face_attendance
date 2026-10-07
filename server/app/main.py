"""FastAPI application factory.

``create_app`` wires settings, database, hashing, sessions, templates and routers onto
one app instance. Nothing is module-level mutable state: tests call ``create_app``
with their own ``Settings`` and swap the DB dependency. ``app`` at the bottom exists
only so ``uvicorn server.app.main:app`` works.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from common.version import __version__
from server.app.admin.common import STATIC_DIR, Renderer
from server.app.admin.router import router as admin_router
from server.app.api.router import router as api_router
from server.app.auth import Hasher, LoginThrottle, SessionCodec
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory

log = logging.getLogger("server")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Safe to call several times (tests do)."""
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.using_dev_secret:
            log.warning("SECRET_KEY is the development default; set it in .env before any real use")
        for directory in (settings.crops_dir, settings.probes_dir, settings.calibration_dir):
            directory.mkdir(parents=True, exist_ok=True)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url, echo=settings.db_echo)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.hasher = Hasher(settings)
    app.state.session_codec = SessionCodec(settings)
    app.state.renderer = Renderer(settings, app.state.session_codec)
    app.state.login_throttle = LoginThrottle(
        settings.login_max_failures, settings.login_lockout_minutes * 60
    )

    @app.middleware("http")
    async def hardening(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Security headers on every response and a size cap on request bodies."""
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_request_bytes:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        if request.url.path.startswith("/admin"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(admin_router)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=302)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    @app.exception_handler(HTTPException)
    async def html_errors(request: Request, exc: HTTPException) -> Response:
        """Browsers hitting /admin get a page; API clients keep JSON."""
        wants_html = request.url.path.startswith("/admin") and "text/html" in request.headers.get(
            "accept", ""
        )
        if wants_html and exc.status_code in (403, 404) and not exc.headers:
            renderer: Renderer = app.state.renderer
            return renderer.render(
                request,
                "error.html",
                {"status_code": exc.status_code, "detail": exc.detail},
                status_code=exc.status_code,
            )
        return await http_exception_handler(request, exc)

    return app


app = create_app()
