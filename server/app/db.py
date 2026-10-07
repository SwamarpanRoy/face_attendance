"""SQLAlchemy engine and session plumbing.

Synchronous SQLAlchemy 2.x with psycopg 3 is used on purpose: the load is a handful
of devices and a few browsers, and sync code keeps the services and tests simple.
Sessions are created per request by the ``get_db`` dependency; the app factory puts
the session factory on ``app.state`` so tests can swap in a transaction-bound session.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Engine with ``pool_pre_ping`` so a restarted Postgres does not poison the pool."""
    return create_engine(url, echo=echo, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """``expire_on_commit=False`` lets templates read ORM objects after the commit."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db(request: Request) -> Iterator[Session]:
    """Per-request session. Services commit explicitly; anything left is rolled back."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
