"""students.updated_at: re-send changed students to devices

Revision ID: c4e8a1f2b7d3
Revises: a5fedb2ee63d
Create Date: 2026-11-11 19:00:00

Incremental template sync used to send only students with new templates, so a renamed
student (or a changed USN or section) kept the old details on every device. Existing rows
start at the migration time, so devices re-fetch their students once after upgrading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1f2b7d3"
down_revision: str | None = "a5fedb2ee63d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_students_updated_at"), "students", ["updated_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_students_updated_at"), table_name="students")
    op.drop_column("students", "updated_at")
