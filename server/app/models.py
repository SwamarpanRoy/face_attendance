"""SQLAlchemy ORM for the attendance server (PostgreSQL 16).

Design notes, because they are constraints rather than conveniences:

* ``face_templates.consent_id`` is ``NOT NULL``. That is the database-level half of
  "no enrolment without consent": a template cannot exist without pointing at the
  consent it was captured under. The API and UI layers check the same thing earlier
  so users get a readable error instead of an ``IntegrityError``.
* ``audit_log.reason`` is nullable in general (logins, device registration) but a
  ``CHECK`` constraint makes it mandatory for attendance edits.
* ``attendance`` has ``UNIQUE(session_id, student_id)`` and a unique client-generated
  ``event_uuid`` so device uploads are idempotent.
* ``sessions.id`` is a UUID generated on the device so sessions work offline.
* Embeddings are stored as ``BYTEA`` (512 float32, little-endian, 2048 bytes), the same
  layout the device keeps in SQLite, so no conversion happens on sync.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, time
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 512
EMBEDDING_BYTES = EMBEDDING_DIM * 4

# Deterministic constraint names keep Alembic diffs readable and make
# ``IntegrityError`` messages greppable in tests.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with the project naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[type, object]] = {
        dict: JSON().with_variant(JSONB(), "postgresql")
    }


def _enum(py_enum: type[enum.Enum], name: str) -> Enum:
    """Native Postgres enum storing the *values* (lower-case), not the member names."""
    return Enum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


def utcnow_default() -> datetime:
    return datetime.now(tz=None)  # pragma: no cover - server_default is used instead


# --------------------------------------------------------------------------- enums
class Role(enum.Enum):
    ADMIN = "admin"
    FACULTY = "faculty"


class ConsentMethod(enum.Enum):
    DEVICE = "device"
    PAPER = "paper"
    BULK_CSV = "bulk_csv"


class TemplateSource(enum.Enum):
    IDCARD = "idcard"
    DEVICE = "device"


class SessionStatus(enum.Enum):
    LIVE = "live"
    ENDED = "ended"


class AttendanceStatus(enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"


class AttendanceMethod(enum.Enum):
    FACE = "face"
    MANUAL = "manual"


# --------------------------------------------------------------------------- people
class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    department: Mapped[str] = mapped_column(String(64))
    semester: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    students: Mapped[list[Student]] = relationship(back_populates="section")


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    usn: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(64))
    semester: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    section: Mapped[Section | None] = relationship(back_populates="students")
    consents: Mapped[list[Consent]] = relationship(back_populates="student")
    templates: Mapped[list[FaceTemplate]] = relationship(back_populates="student")


class Faculty(Base):
    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    password_hash: Mapped[str] = mapped_column(Text)
    # Device PIN (argon2). Unlocks device actions only; never a dashboard credential.
    pin_hash: Mapped[str | None] = mapped_column(Text)
    pin_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(32))
    method: Mapped[ConsentMethod] = mapped_column(_enum(ConsentMethod, "consent_method"))
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("faculty.id"))
    # Set when biometric data is withdrawn; attendance history is kept.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    student: Mapped[Student] = relationship(back_populates="consents")


class FaceTemplate(Base):
    __tablename__ = "face_templates"
    __table_args__ = (
        CheckConstraint(f"octet_length(embedding) = {EMBEDDING_BYTES}", name="embedding_size"),
        Index("ix_face_templates_student_model", "student_id", "model_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64))
    source: Mapped[TemplateSource] = mapped_column(_enum(TemplateSource, "template_source"))
    quality_score: Mapped[float | None] = mapped_column(Float)
    crop_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    student: Mapped[Student] = relationship(back_populates="templates")
    consent: Mapped[Consent] = relationship()


class TemplateTombstone(Base):
    """Record of a student's templates being removed, so devices can drop them on sync."""

    __tablename__ = "template_tombstones"

    id: Mapped[int] = mapped_column(primary_key=True)
    usn: Mapped[str] = mapped_column(String(16), index=True)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), index=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


# --------------------------------------------------------------------------- courses
class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(64))
    semester: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    offerings: Mapped[list[CourseSection]] = relationship(back_populates="course")


class CourseSection(Base):
    """A course taught to a section by one faculty member. Drives faculty permissions."""

    __tablename__ = "course_sections"
    __table_args__ = (UniqueConstraint("course_id", "section_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), index=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculty.id"), index=True)

    course: Mapped[Course] = relationship(back_populates="offerings")
    section: Mapped[Section] = relationship()
    faculty: Mapped[Faculty] = relationship()


class Period(Base):
    """Timetable slot a session is started for (shown on the device's picker)."""

    __tablename__ = "periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(32))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


# --------------------------------------------------------------------------- devices
class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # SHA-256 hex of the 32-byte random token. The token itself is shown once.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    ssid: Mapped[str | None] = mapped_column(String(64))
    app_version: Mapped[str | None] = mapped_column(String(32))
    queue_len: Mapped[int | None] = mapped_column(Integer)
    cpu_temp: Mapped[float | None] = mapped_column(Float)
    free_mem_mb: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String(64))
    clock_synced: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sections: Mapped[list[Section]] = relationship(secondary="device_sections")


class DeviceSection(Base):
    """Which sections a device caches templates for (prefetched on every sync)."""

    __tablename__ = "device_sections"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), primary_key=True
    )


class Calibration(Base):
    """Operating point measured by tools/calibrate.py. Devices receive the active row."""

    __tablename__ = "calibrations"
    __table_args__ = (
        Index(
            "uq_calibrations_active_model",
            "model_version",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    target_far: Mapped[float] = mapped_column(Float)
    measured_far: Mapped[float] = mapped_column(Float)
    measured_frr: Mapped[float] = mapped_column(Float)
    eer: Mapped[float | None] = mapped_column(Float)
    n_genuine: Mapped[int] = mapped_column(Integer)
    n_impostor: Mapped[int] = mapped_column(Integer)
    report_path: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- attendance
class AttendanceSession(Base):
    """A class session. The UUID is minted on the device so it works offline."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), index=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculty.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    period_id: Mapped[int | None] = mapped_column(ForeignKey("periods.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "session_status"), default=SessionStatus.LIVE
    )
    clock_synced: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    course: Mapped[Course] = relationship()
    section: Mapped[Section] = relationship()
    faculty: Mapped[Faculty] = relationship()
    device: Mapped[Device | None] = relationship()
    period: Mapped[Period | None] = relationship()
    records: Mapped[list[Attendance]] = relationship(back_populates="session")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    status: Mapped[AttendanceStatus] = mapped_column(_enum(AttendanceStatus, "attendance_status"))
    method: Mapped[AttendanceMethod] = mapped_column(_enum(AttendanceMethod, "attendance_method"))
    score: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    clock_synced: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    event_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[AttendanceSession] = relationship(back_populates="records")
    student: Mapped[Student] = relationship()


# --------------------------------------------------------------------------- audit
EDIT_ACTIONS = ("attendance.edit", "attendance.bulk_edit")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "action NOT IN ('attendance.edit', 'attendance.bulk_edit') "
            "OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="reason_required_for_edits",
        ),
        Index("ix_audit_log_entity", "entity", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("faculty.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    actor: Mapped[Faculty | None] = relationship()
