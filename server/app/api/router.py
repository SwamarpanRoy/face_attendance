"""Device-facing JSON API, versioned under ``/api/v1``. Every route needs a device token."""

from __future__ import annotations

from fastapi import APIRouter

from server.app.api import devices, enrolment, probes, sessions

router = APIRouter(prefix="/api/v1", tags=["device"])
router.include_router(devices.router)
router.include_router(enrolment.router)
router.include_router(probes.router)
router.include_router(sessions.router)
