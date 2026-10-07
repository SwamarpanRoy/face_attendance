"""template tombstones

Revision ID: a5fedb2ee63d
Revises: 0530b4e9884f
Create Date: 2026-10-06 16:09:19.690855
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5fedb2ee63d"
down_revision: str | None = "0530b4e9884f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_tombstones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usn", sa.String(length=16), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"], name=op.f("fk_template_tombstones_section_id_sections")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_tombstones")),
    )
    op.create_index(
        op.f("ix_template_tombstones_deleted_at"),
        "template_tombstones",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_template_tombstones_section_id"),
        "template_tombstones",
        ["section_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_template_tombstones_usn"), "template_tombstones", ["usn"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_template_tombstones_usn"), table_name="template_tombstones")
    op.drop_index(op.f("ix_template_tombstones_section_id"), table_name="template_tombstones")
    op.drop_index(op.f("ix_template_tombstones_deleted_at"), table_name="template_tombstones")
    op.drop_table("template_tombstones")
