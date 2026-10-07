"""Aggregates the admin UI routers under ``/admin`` with CSRF checking on every POST."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from server.app.admin import (
    audit,
    auth,
    courses,
    dashboard,
    devices,
    enrolment,
    faculty,
    reports,
    sessions,
    students,
)
from server.app.auth import verify_csrf

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_csrf)], include_in_schema=False)
# The dashboard lives at exactly /admin (no trailing slash), so it is registered here
# where the prefix makes an empty path legal.
router.add_api_route("", dashboard.dashboard, methods=["GET"], response_class=HTMLResponse)
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(students.router)
router.include_router(courses.router)
router.include_router(faculty.router)
router.include_router(sessions.router)
router.include_router(reports.router)
router.include_router(devices.router)
router.include_router(audit.router)
router.include_router(enrolment.router)
