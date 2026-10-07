"""Server-side face engine (shared, lazily created) for enrolment and re-embedding.

The server embeds crops uploaded by devices and detects faces in ID-card photos. One
engine per process is enough; it is created on first use so the admin UI starts even
when ``models/`` has not been downloaded yet, and requests that need it get a clear 503.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from common.face.detector import DetectorConfig
from common.face.embedder import EmbedderConfig
from common.face.engine import EngineConfig, FaceEngine, ModelsMissingError
from common.face.quality import QualityConfig
from server.app.config import Settings

_LOCK = threading.Lock()


def engine_config_for(settings: Settings) -> EngineConfig:
    """Enrolment photos are scans and stills: larger detector input, looser gates."""
    return EngineConfig(
        detector=DetectorConfig(input_size=settings.server_detector_input, threads=4),
        quality=QualityConfig(
            min_face_width_px=settings.enrol_min_face_px,
            blur_min=settings.enrol_min_blur,
            max_yaw_deg=settings.enrol_max_yaw_deg,
        ),
        embedder=EmbedderConfig(threads=4),
    )


def build_engine(settings: Settings) -> FaceEngine:
    return FaceEngine(settings.models_dir, engine_config_for(settings))


def get_face_engine(request: Request) -> FaceEngine:
    """FastAPI dependency: the process-wide engine, created on first use."""
    app = request.app
    engine: FaceEngine | None = getattr(app.state, "face_engine", None)
    if engine is not None:
        return engine
    with _LOCK:
        engine = getattr(app.state, "face_engine", None)
        if engine is None:
            try:
                engine = build_engine(app.state.settings)
            except ModelsMissingError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Face models are not installed on the server: {exc}",
                ) from exc
            app.state.face_engine = engine
    return engine


FaceEngineDep = Annotated[FaceEngine, Depends(get_face_engine)]
