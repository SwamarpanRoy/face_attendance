"""Device configuration: ``~/.config/attendance/device.toml`` parsed into pydantic models.

Every threshold, size and interval the device uses lives here, never in code. The
recognition thresholds in the file are *fallbacks*; a calibration delivered by the
server (stored in the device's SQLite ``settings`` table) always wins, and the UI says
"not calibrated" while the fallbacks are in use.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.face.detector import DetectorConfig
from common.face.embedder import EmbedderConfig
from common.face.engine import EngineConfig
from common.face.matcher import MatchConfig
from common.face.quality import QualityConfig

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "attendance" / "device.toml"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "attendance"
CONFIG_ENV = "ATTENDANCE_DEVICE_CONFIG"


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class DeviceSection(_Section):
    id: str = "pi-01"
    name: str = "Attendance device"


class ServerSection(_Section):
    url: str = "http://127.0.0.1:8000"
    token: str = ""
    timeout_s: float = Field(default=5.0, gt=0)

    @property
    def configured(self) -> bool:
        return bool(self.token) and "<SERVER_HOST>" not in self.url


class CameraSection(_Section):
    preview_size: tuple[int, int] = (640, 480)
    main_size: tuple[int, int] = (1280, 960)
    sim_webcam_index: int = 0


class RecognitionSection(_Section):
    model_version: str = "buffalo_s/w600k_mbf"
    detector_input: int = Field(default=320, ge=160, le=640)
    onnx_threads: int = Field(default=4, ge=1, le=8)
    fallback_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    fallback_margin: float = Field(default=0.05, ge=0.0)
    min_face_width_px: float = Field(default=90.0, gt=0)
    blur_min: float = Field(default=60.0, ge=0)
    max_yaw_deg: float = Field(default=25.0, gt=0, le=90)
    vote_window: int = Field(default=3, ge=1)
    vote_required: int = Field(default=2, ge=1)


class SyncSection(_Section):
    interval_s: float = Field(default=5.0, gt=0)
    batch_size: int = Field(default=200, ge=1, le=200)
    heartbeat_s: float = Field(default=30.0, gt=0)
    prefetch_interval_s: float = Field(default=600.0, gt=0)
    backoff_max_s: float = Field(default=300.0, gt=0)


class UiSection(_Section):
    sound: bool = False
    tts: bool = False


class LoggingSection(_Section):
    level: str = "INFO"


class PathsSection(_Section):
    """Where models and local state live. Relative paths resolve against the repo root."""

    models_dir: str = "models"
    state_dir: str = str(DEFAULT_STATE_DIR)


class DeviceConfig(_Section):
    device: DeviceSection = DeviceSection()
    server: ServerSection = ServerSection()
    camera: CameraSection = CameraSection()
    recognition: RecognitionSection = RecognitionSection()
    sync: SyncSection = SyncSection()
    ui: UiSection = UiSection()
    logging: LoggingSection = LoggingSection()
    paths: PathsSection = PathsSection()
    source_path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> DeviceConfig:
        """Read the TOML file; a missing file yields defaults (the simulator still runs)."""
        resolved = path or Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
        if not resolved.is_file():
            log.warning("device config %s not found; using defaults (no server)", resolved)
            return cls()
        with resolved.open("rb") as handle:
            data = tomllib.load(handle)
        return cls(**data, source_path=resolved)

    # -- derived objects for the shared pipeline code
    @property
    def models_dir(self) -> Path:
        override = os.environ.get("MODELS_DIR")
        return Path(override) if override else Path(self.paths.models_dir)

    @property
    def state_dir(self) -> Path:
        return Path(self.paths.state_dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.state_dir / "device.db"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "device.log"

    def engine_config(self) -> EngineConfig:
        rec = self.recognition
        return EngineConfig(
            detector=DetectorConfig(input_size=rec.detector_input, threads=rec.onnx_threads),
            quality=QualityConfig(
                min_face_width_px=rec.min_face_width_px,
                blur_min=rec.blur_min,
                max_yaw_deg=rec.max_yaw_deg,
            ),
            embedder=EmbedderConfig(threads=rec.onnx_threads),
        )

    def match_config(
        self, threshold: float | None = None, margin: float | None = None
    ) -> MatchConfig:
        """Matcher settings; pass calibrated values when the device has received them."""
        rec = self.recognition
        return MatchConfig(
            threshold=rec.fallback_threshold if threshold is None else threshold,
            margin=rec.fallback_margin if margin is None else margin,
            vote_window=rec.vote_window,
            vote_required=rec.vote_required,
        )
