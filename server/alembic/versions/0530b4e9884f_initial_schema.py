"""initial schema

Revision ID: 0530b4e9884f
Revises:
Create Date: 2026-10-06 15:28:49.444025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0530b4e9884f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("margin", sa.Float(), nullable=False),
        sa.Column("target_far", sa.Float(), nullable=False),
        sa.Column("measured_far", sa.Float(), nullable=False),
        sa.Column("measured_frr", sa.Float(), nullable=False),
        sa.Column("eer", sa.Float(), nullable=True),
        sa.Column("n_genuine", sa.Integer(), nullable=False),
        sa.Column("n_impostor", sa.Integer(), nullable=False),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calibrations")),
    )
    op.create_index(
        "uq_calibrations_active_model",
        "calibrations",
        ["model_version"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courses")),
        sa.UniqueConstraint("code", name=op.f("uq_courses_code")),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("ssid", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("queue_len", sa.Integer(), nullable=True),
        sa.Column("cpu_temp", sa.Float(), nullable=True),
        sa.Column("free_mem_mb", sa.Integer(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("clock_synced", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("name", name=op.f("uq_devices_name")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_devices_token_hash")),
    )
    op.create_table(
        "faculty",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.Enum("admin", "faculty", name="role"), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("pin_hash", sa.Text(), nullable=True),
        sa.Column("pin_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_faculty")),
        sa.UniqueConstraint("email", name=op.f("uq_faculty_email")),
    )
    op.create_table(
        "periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_periods")),
        sa.UniqueConstraint("ordinal", name=op.f("uq_periods_ordinal")),
    )
    op.create_table(
        "sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sections")),
        sa.UniqueConstraint("name", name=op.f("uq_sections_name")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action NOT IN ('attendance.edit', 'attendance.bulk_edit') "
            "OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name=op.f("ck_audit_log_reason_required_for_edits"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["faculty.id"], name=op.f("fk_audit_log_actor_id_faculty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(op.f("ix_audit_log_actor_id"), "audit_log", ["actor_id"], unique=False)
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"], unique=False)
    op.create_index("ix_audit_log_entity", "audit_log", ["entity", "entity_id"], unique=False)
    op.create_table(
        "course_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("faculty_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], name=op.f("fk_course_sections_course_id_courses")
        ),
        sa.ForeignKeyConstraint(
            ["faculty_id"], ["faculty.id"], name=op.f("fk_course_sections_faculty_id_faculty")
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"], name=op.f("fk_course_sections_section_id_sections")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_sections")),
        sa.UniqueConstraint("course_id", "section_id", name=op.f("uq_course_sections_course_id")),
    )
    op.create_index(
        op.f("ix_course_sections_faculty_id"), "course_sections", ["faculty_id"], unique=False
    )
    op.create_index(
        op.f("ix_course_sections_section_id"), "course_sections", ["section_id"], unique=False
    )
    op.create_table(
        "device_sections",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_device_sections_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["sections.id"],
            name=op.f("fk_device_sections_section_id_sections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("device_id", "section_id", name=op.f("pk_device_sections")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("faculty_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("period_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Enum("live", "ended", name="session_status"), nullable=False),
        sa.Column("clock_synced", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], name=op.f("fk_sessions_course_id_courses")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], name=op.f("fk_sessions_device_id_devices")
        ),
        sa.ForeignKeyConstraint(
            ["faculty_id"], ["faculty.id"], name=op.f("fk_sessions_faculty_id_faculty")
        ),
        sa.ForeignKeyConstraint(
            ["period_id"], ["periods.id"], name=op.f("fk_sessions_period_id_periods")
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"], name=op.f("fk_sessions_section_id_sections")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_course_id"), "sessions", ["course_id"], unique=False)
    op.create_index(op.f("ix_sessions_faculty_id"), "sessions", ["faculty_id"], unique=False)
    op.create_index(op.f("ix_sessions_section_id"), "sessions", ["section_id"], unique=False)
    op.create_index(op.f("ix_sessions_started_at"), "sessions", ["started_at"], unique=False)
    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usn", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"], name=op.f("fk_students_section_id_sections")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_students")),
        sa.UniqueConstraint("usn", name=op.f("uq_students_usn")),
    )
    op.create_index(op.f("ix_students_section_id"), "students", ["section_id"], unique=False)
    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("present", "absent", "late", "excused", name="attendance_status"),
            nullable=False,
        ),
        sa.Column("method", sa.Enum("face", "manual", name="attendance_method"), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("clock_synced", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("event_uuid", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_attendance_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_attendance_student_id_students")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance")),
        sa.UniqueConstraint("event_uuid", name=op.f("uq_attendance_event_uuid")),
        sa.UniqueConstraint("session_id", "student_id", name=op.f("uq_attendance_session_id")),
    )
    op.create_index(op.f("ix_attendance_session_id"), "attendance", ["session_id"], unique=False)
    op.create_index(op.f("ix_attendance_student_id"), "attendance", ["student_id"], unique=False)
    op.create_table(
        "consents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(length=32), nullable=False),
        sa.Column(
            "method", sa.Enum("device", "paper", "bulk_csv", name="consent_method"), nullable=False
        ),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"], ["faculty.id"], name=op.f("fk_consents_recorded_by_faculty")
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_consents_student_id_students")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(op.f("ix_consents_student_id"), "consents", ["student_id"], unique=False)
    op.create_table(
        "face_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("consent_id", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.Enum("idcard", "device", name="template_source"), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("crop_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(embedding) = 2048", name=op.f("ck_face_templates_embedding_size")
        ),
        sa.ForeignKeyConstraint(
            ["consent_id"], ["consents.id"], name=op.f("fk_face_templates_consent_id_consents")
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_face_templates_student_id_students")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_face_templates")),
    )
    op.create_index(
        op.f("ix_face_templates_student_id"), "face_templates", ["student_id"], unique=False
    )
    op.create_index(
        "ix_face_templates_student_model",
        "face_templates",
        ["student_id", "model_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_face_templates_student_model", table_name="face_templates")
    op.drop_index(op.f("ix_face_templates_student_id"), table_name="face_templates")
    op.drop_table("face_templates")
    op.drop_index(op.f("ix_consents_student_id"), table_name="consents")
    op.drop_table("consents")
    op.drop_index(op.f("ix_attendance_student_id"), table_name="attendance")
    op.drop_index(op.f("ix_attendance_session_id"), table_name="attendance")
    op.drop_table("attendance")
    op.drop_index(op.f("ix_students_section_id"), table_name="students")
    op.drop_table("students")
    op.drop_index(op.f("ix_sessions_started_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_section_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_faculty_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_course_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("device_sections")
    op.drop_index(op.f("ix_course_sections_section_id"), table_name="course_sections")
    op.drop_index(op.f("ix_course_sections_faculty_id"), table_name="course_sections")
    op.drop_table("course_sections")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_created_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("sections")
    op.drop_table("periods")
    op.drop_table("faculty")
    op.drop_table("devices")
    op.drop_table("courses")
    op.drop_index(
        "uq_calibrations_active_model",
        table_name="calibrations",
        postgresql_where=sa.text("active"),
    )
    op.drop_table("calibrations")
    # Native enum types are created implicitly by create_table but never dropped by
    # drop_table, so a downgrade must remove them explicitly.
    for enum_name in (
        "template_source",
        "consent_method",
        "attendance_method",
        "attendance_status",
        "session_status",
        "role",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
