"""Pydantic models shared by the device API client and the server.

Both sides import this module, so wire changes happen in exactly one place and the
device's sync code is type-checked against what the server actually returns. Keep it
free of server-only or Qt-only imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for every wire model: tolerant of unknown fields so old devices keep working."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)


# --------------------------------------------------------------------------- heartbeat
class HeartbeatIn(ApiModel):
    device_id: str = Field(max_length=64)
    app_version: str = Field(max_length=32)
    ip: str | None = Field(default=None, max_length=64)
    ssid: str | None = Field(default=None, max_length=64)
    queue_len: int = Field(ge=0)
    cpu_temp_c: float | None = None
    free_mem_mb: int | None = Field(default=None, ge=0)
    model_version: str | None = Field(default=None, max_length=64)
    clock_synced: bool = True


class HeartbeatOut(ApiModel):
    device_name: str
    server_time: datetime
    server_version: str
    model_version: str
    assigned_section_ids: list[int]
    # A revoked device never gets this far: it receives 401 with detail "Device revoked."
    # and must wipe its local cache.


# --------------------------------------------------------------------------- catalog
class CourseOut(ApiModel):
    id: int
    code: str
    name: str


class SectionOut(ApiModel):
    id: int
    name: str


class PeriodOut(ApiModel):
    id: int
    ordinal: int
    name: str
    start_time: str
    end_time: str


class OfferingOut(ApiModel):
    """Course taught to a section by a faculty member; drives the device picker."""

    course_id: int
    section_id: int
    faculty_id: int


class FacultyPinOut(ApiModel):
    """Enough to verify a PIN offline. The hash is argon2id and unlocks the device only."""

    id: int
    name: str
    role: Literal["admin", "faculty"]
    pin_hash: str | None


class CatalogOut(ApiModel):
    generated_at: datetime
    courses: list[CourseOut]
    sections: list[SectionOut]
    periods: list[PeriodOut]
    offerings: list[OfferingOut]
    faculty: list[FacultyPinOut]
    # Shown on the device's consent screen; the version is stored with each consent.
    consent_version: str = ""
    consent_notice: str = ""


# --------------------------------------------------------------------------- pin auth
class PinAuthIn(ApiModel):
    pin: str = Field(min_length=4, max_length=12)


class PinAuthOut(ApiModel):
    faculty_id: int
    name: str
    role: Literal["admin", "faculty"]


# --------------------------------------------------------------------------- roster
class RosterStudentOut(ApiModel):
    student_id: int
    usn: str
    name: str
    has_consent: bool
    template_count: int


class RosterOut(ApiModel):
    section_id: int
    students: list[RosterStudentOut]


# --------------------------------------------------------------------------- enrolment
ConsentMethodName = Literal["device", "paper", "bulk_csv"]


class ConsentIn(ApiModel):
    """Consent given on the device: the student tapped "I consent" at ``consent_at``."""

    consent_at: datetime
    consent_version: str = Field(max_length=32)
    method: ConsentMethodName = "device"


class EnrolmentCapturesIn(ApiModel):
    """Three aligned 112x112 crops (PNG, base64) plus who supervised and the consent."""

    faculty_id: int
    crops_png_b64: list[str] = Field(min_length=1, max_length=5)
    consent: ConsentIn | None = None
    blur_scores: list[float] | None = None
    model_version: str | None = Field(default=None, max_length=64)


class EnrolmentCapturesOut(ApiModel):
    student_id: int
    usn: str
    consent_id: int
    templates_added: int
    total_templates: int
    model_version: str


class ProbesIn(ApiModel):
    """Held-out calibration crops (aligned 112x112 PNGs, base64); never templates."""

    crops_png_b64: list[str] = Field(min_length=1, max_length=10)


class ProbesOut(ApiModel):
    usn: str
    stored: int


class TemplateOut(ApiModel):
    idx: int
    embedding_b64: str  # 512 float32 little-endian (2048 bytes), base64
    model_version: str
    created_at: datetime


class StudentTemplatesOut(ApiModel):
    usn: str
    name: str
    section_id: int | None
    templates: list[TemplateOut]


class TemplatesOut(ApiModel):
    """Templates for every section assigned to the device.

    ``full`` is True when no ``since`` was given: the device replaces its whole cache.
    Otherwise ``students`` holds every student whose templates changed after ``since``
    (with *all* of their current templates) and ``deleted_usns`` lists students whose
    templates were removed, e.g. after a consent withdrawal.
    """

    generated_at: datetime
    model_version: str
    calibrated: bool
    threshold: float | None
    margin: float | None
    full: bool
    students: list[StudentTemplatesOut]
    deleted_usns: list[str]


# --------------------------------------------------------------------------- sessions (M6)
class SessionIn(ApiModel):
    id: UUID
    course_id: int
    section_id: int
    faculty_id: int
    period_id: int | None = None
    started_at: datetime
    clock_synced: bool = True


class SessionOut(ApiModel):
    id: UUID
    status: Literal["live", "ended"]


class SessionEndIn(ApiModel):
    ended_at: datetime
    clock_synced: bool = True


class SessionEndOut(ApiModel):
    id: UUID
    status: Literal["live", "ended"]
    present: int
    absent_marked: int


class AttendanceEventIn(ApiModel):
    event_uuid: UUID
    session_id: UUID
    usn: str
    score: float | None = None
    captured_at: datetime
    clock_synced: bool = True


class AttendanceBatchIn(ApiModel):
    events: list[AttendanceEventIn] = Field(max_length=200)


class AttendanceBatchOut(ApiModel):
    accepted: list[UUID]
    duplicates: list[UUID]
    rejected: dict[UUID, str]
