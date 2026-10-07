"""Server test fixtures: a real PostgreSQL, migrated by Alembic, one transaction per test.

Why a real database: the things under test (consent NOT NULL, the audit CHECK, the
unique active calibration, native enums) are database constraints and SQLite cannot
express them. When no PostgreSQL is reachable the whole directory skips with a clear
message; ``--run-integration`` turns that skip into a failure for CI.

The test database is created from scratch, migrated with ``alembic upgrade head`` (so
every run also proves the migrations apply to a fresh DB) and dropped at the end. Each
test runs inside an outer transaction with the app committing to savepoints, so tests
never see each other's rows.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from server.app.auth import Hasher, generate_device_token
from server.app.config import Settings
from server.app.db import get_db
from server.app.main import create_app
from server.app.models import (
    Course,
    CourseSection,
    Device,
    DeviceSection,
    Faculty,
    Role,
    Section,
    Student,
)

REPO = Path(__file__).resolve().parents[2]
TEST_DB_NAME = "attendance_test"
DEFAULT_PASSWORD = "correct horse battery staple"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _test_url() -> str:
    settings = Settings(_env_file=str(REPO / ".env"))  # type: ignore[call-arg]
    if settings.test_database_url:
        return settings.test_database_url
    return (
        make_url(settings.database_url)
        .set(database=TEST_DB_NAME)
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def pg_test_url(request: pytest.FixtureRequest) -> Iterator[str]:
    test_url = _test_url()
    maintenance = make_url(test_url).set(database="postgres")
    db_name = make_url(test_url).database
    admin_engine = create_engine(maintenance, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        conn = admin_engine.connect()
    except OperationalError as exc:
        if request.config.getoption("--run-integration"):
            pytest.fail(f"PostgreSQL not reachable at {maintenance.host}:{maintenance.port}: {exc}")
        pytest.skip(
            f"PostgreSQL not reachable at {maintenance.host}:{maintenance.port}; "
            "server tests skipped (see docs/setup_laptop.md step 4)"
        )
    with conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    yield test_url
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def alembic_config(pg_test_url: str) -> Config:
    cfg = Config(str(REPO / "server" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", pg_test_url)
    return cfg


@pytest.fixture(scope="session")
def migrated_engine(pg_test_url: str, alembic_config: Config) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(pg_test_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Session]:
    connection = migrated_engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def settings(pg_test_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database_url=pg_test_url,
        secret_key="test-secret-key-not-for-production",
        argon2_time_cost=1,
        argon2_memory_kib=8 * 1024,
        argon2_parallelism=1,
        crops_dir=tmp_path / "crops",
        probes_dir=tmp_path / "probes",
        calibration_dir=tmp_path / "calibration",
        # Real models when downloaded (enrolment tests need them), else an empty dir.
        models_dir=REPO / "models"
        if (REPO / "models" / "det_500m.onnx").exists()
        else tmp_path / "models",
    )


@pytest.fixture
def app(settings: Settings, db: Session) -> FastAPI:
    application = create_app(settings)
    application.dependency_overrides[get_db] = lambda: db
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def hasher(settings: Settings) -> Hasher:
    return Hasher(settings)


@pytest.fixture
def make_faculty(db: Session, hasher: Hasher) -> Callable[..., Faculty]:
    def _make(
        *,
        email: str,
        role: Role = Role.FACULTY,
        password: str = DEFAULT_PASSWORD,
        pin: str | None = None,
        name: str | None = None,
    ) -> Faculty:
        member = Faculty(
            name=name or email.split("@")[0].replace(".", " ").title(),
            email=email,
            role=role,
            password_hash=hasher.hash(password),
            pin_hash=hasher.hash(pin) if pin else None,
        )
        db.add(member)
        db.commit()
        return member

    return _make


@pytest.fixture
def admin(make_faculty: Callable[..., Faculty]) -> Faculty:
    return make_faculty(email="admin@test.local", role=Role.ADMIN, pin="135790")


@pytest.fixture
def faculty(make_faculty: Callable[..., Faculty]) -> Faculty:
    return make_faculty(email="prof@test.local", role=Role.FACULTY, pin="246801")


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "no csrf_token field in page"
    return match.group(1)


@pytest.fixture
def login(client: TestClient) -> Callable[..., str]:
    """Log in through the real form and return the session CSRF token for later POSTs."""

    def _login(email: str, password: str = DEFAULT_PASSWORD) -> str:
        page = client.get("/admin/login")
        assert page.status_code == 200
        response = client.post(
            "/admin/login",
            data={"email": email, "password": password, "csrf_token": csrf_from(page.text)},
        )
        assert response.status_code == 303, response.text
        dashboard = client.get("/admin")
        assert dashboard.status_code == 200
        return csrf_from(dashboard.text)

    return _login


@pytest.fixture
def section(db: Session) -> Section:
    row = Section(name="ECE-7A", department="ECE", semester=7)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def course(db: Session) -> Course:
    row = Course(code="22EC71", name="VLSI Design", department="ECE", semester=7)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def offering(db: Session, course: Course, section: Section, faculty: Faculty) -> CourseSection:
    row = CourseSection(course_id=course.id, section_id=section.id, faculty_id=faculty.id)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def students(db: Session, section: Section) -> list[Student]:
    rows = [
        Student(
            usn=f"1BM22EC{i:03d}",
            name=f"Student {i}",
            department="ECE",
            semester=7,
            section_id=section.id,
        )
        for i in range(1, 4)
    ]
    db.add_all(rows)
    db.commit()
    return rows


@pytest.fixture
def device(db: Session, section: Section) -> tuple[Device, str]:
    token, token_hash = generate_device_token()
    row = Device(name="pi-test", token_hash=token_hash)
    db.add(row)
    db.flush()
    db.add(DeviceSection(device_id=row.id, section_id=section.id))
    db.commit()
    return row, token
