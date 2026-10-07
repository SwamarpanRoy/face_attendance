"""Server settings, loaded from the environment and the repo-root ``.env``.

Everything tunable lives here rather than in code so the office computer, the laptop
and the test suite differ only by configuration. ``Settings`` is immutable after
construction and is attached to the FastAPI app (``app.state.settings``) by the app
factory; nothing reads it through a module-level global.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "change-me"


class Settings(BaseSettings):
    """All server configuration. Field names map to upper-case ``.env`` keys."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    app_name: str = "Face Attendance"

    # --- database
    database_url: str = "postgresql+psycopg://attendance:change-me@localhost:5432/attendance"
    test_database_url: str | None = None
    db_echo: bool = False

    # --- admin sessions and CSRF
    secret_key: str = DEV_SECRET
    session_cookie_name: str = "fa_session"
    session_cookie_secure: bool = False
    session_max_age_hours: int = Field(default=12, ge=1, le=24 * 30)

    # --- login throttling: after N failures for an email or an IP, wait this long.
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Largest request body accepted (device uploads are ~150 KB; 5 MB leaves headroom).
    max_request_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024)

    # --- password / PIN hashing (argon2id). Lowered in tests for speed.
    argon2_time_cost: int = Field(default=2, ge=1)
    argon2_memory_kib: int = Field(default=64 * 1024, ge=8 * 1024)
    argon2_parallelism: int = Field(default=2, ge=1)
    pin_min_length: int = Field(default=4, ge=4)
    pin_max_length: int = Field(default=8, le=12)

    # --- files (server only; never on the device)
    crops_dir: Path = Path("data/crops")
    probes_dir: Path = Path("data/probes")
    calibration_dir: Path = Path("calibration")
    models_dir: Path = Path("models")
    model_version: str = "buffalo_s/w600k_mbf"

    # --- policy
    # BMSCE USNs look like 1BM22EC001: campus digit, college code, year, branch, roll.
    usn_pattern: str = r"^[0-9][A-Z]{2}[0-9]{2}[A-Z]{2,3}[0-9]{3}$"
    attendance_shortage_threshold_pct: float = Field(default=75.0, ge=0, le=100)
    faculty_edit_window_days: int = Field(default=7, ge=0)
    consent_version: str = "2026-10-v1"
    consent_notice: str = (
        "By tapping I consent you agree that BMS College of Engineering stores a "
        "mathematical template of your face (not photographs) and a few aligned test "
        "images on the college attendance server, and uses them only to mark your "
        "attendance in class. You can withdraw consent at any time through the "
        "department office; your attendance history is kept, your face data is deleted."
    )

    # --- server-side recognition (enrolment photos, probes, re-embedding)
    server_detector_input: int = Field(default=640, ge=160, le=640)
    enrol_min_face_px: float = Field(default=60.0, gt=0)
    enrol_min_blur: float = Field(default=30.0, ge=0)
    enrol_max_yaw_deg: float = Field(default=30.0, gt=0, le=90)

    # Display timezone for the admin UI and reports (data is stored in UTC).
    timezone: str = "Asia/Kolkata"

    log_level: str = "INFO"

    @property
    def using_dev_secret(self) -> bool:
        """True when ``SECRET_KEY`` was never set; the app warns loudly in that case."""
        return self.secret_key == DEV_SECRET

    @property
    def session_max_age_seconds(self) -> int:
        return self.session_max_age_hours * 3600
