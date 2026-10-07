"""The Alembic migrations must produce exactly the schema the ORM describes."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from server.app.models import Base


def test_migrations_apply_to_a_fresh_database_and_match_models(migrated_engine):
    with migrated_engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        expected = set(Base.metadata.tables)
        assert expected <= tables, f"missing tables: {expected - tables}"

        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models and migrations disagree: {diff}"
