"""Seed demo data: admin + faculty accounts, 2 courses, 1 section, periods, students.

Idempotent: re-running updates the same rows by their natural keys (email, code, name,
USN) and never duplicates. Credentials come from ``.env`` (``SEED_ADMIN_PASSWORD``,
``SEED_FACULTY_PASSWORD``, ``SEED_PIN``); anything missing is generated and printed
once, because secrets must not live in the repo.

Usage::

    python tools/seed_demo.py                 # people, courses, section, students
    python tools/seed_demo.py --device pi-01  # also register a device and print its token
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import time
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.auth import Hasher, generate_device_token
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.models import (
    Course,
    CourseSection,
    Device,
    DeviceSection,
    Faculty,
    Period,
    Role,
    Section,
    Student,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SECTION = ("ECE-7A", "ECE", 7)
COURSES = [
    ("22EC71", "VLSI Design", "ECE", 7),
    ("22EC72", "Embedded Systems", "ECE", 7),
]
PERIODS = [
    (1, "Period 1", time(9, 0), time(10, 0)),
    (2, "Period 2", time(10, 0), time(11, 0)),
    (3, "Period 3", time(11, 15), time(12, 15)),
    (4, "Period 4", time(12, 15), time(13, 15)),
    (5, "Period 5", time(14, 0), time(15, 0)),
    (6, "Period 6", time(15, 0), time(16, 0)),
]
STUDENT_NAMES = [
    "Aditi Rao",
    "Arjun Nair",
    "Bhavana Shetty",
    "Chirag Hegde",
    "Deepika Kulkarni",
    "Farhan Khan",
    "Gowri Prasad",
    "Harsha Vardhan",
    "Ishita Jain",
    "Karthik Bhat",
    "Lakshmi Iyer",
    "Manoj Gowda",
    "Nandini Reddy",
    "Pranav Joshi",
    "Rahul Menon",
    "Sneha Patil",
    "Tanvi Desai",
    "Varun Krishnan",
]


def upsert_faculty(
    db: Session,
    hasher: Hasher,
    *,
    name: str,
    email: str,
    role: Role,
    password: str,
    pin: str | None,
) -> Faculty:
    member = db.scalar(select(Faculty).where(Faculty.email == email))
    if member is None:
        member = Faculty(name=name, email=email, role=role, password_hash=hasher.hash(password))
        db.add(member)
    else:
        member.name, member.role, member.active = name, role, True
        member.password_hash = hasher.hash(password)
    if pin:
        member.pin_hash = hasher.hash(pin)
    db.flush()
    return member


def seed(db: Session, hasher: Hasher, env: dict[str, str | None]) -> dict[str, str]:
    """Create or refresh the demo rows. Returns the credentials that were used."""
    creds = {
        "admin_email": env.get("SEED_ADMIN_EMAIL") or "admin@demo.local",
        "admin_password": env.get("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(12),
        "faculty_email": env.get("SEED_FACULTY_EMAIL") or "faculty@demo.local",
        "faculty_password": env.get("SEED_FACULTY_PASSWORD") or secrets.token_urlsafe(12),
        "admin_pin": env.get("SEED_ADMIN_PIN") or f"{secrets.randbelow(10**6):06d}",
        "faculty_pin": env.get("SEED_PIN") or f"{secrets.randbelow(10**6):06d}",
    }
    if creds["admin_pin"] == creds["faculty_pin"]:
        creds["admin_pin"] = f"{(int(creds['admin_pin']) + 1) % 10**6:06d}"

    admin = upsert_faculty(
        db,
        hasher,
        name="Demo Admin",
        email=creds["admin_email"],
        role=Role.ADMIN,
        password=creds["admin_password"],
        pin=creds["admin_pin"],
    )
    faculty = upsert_faculty(
        db,
        hasher,
        name="Prof. Demo Faculty",
        email=creds["faculty_email"],
        role=Role.FACULTY,
        password=creds["faculty_password"],
        pin=creds["faculty_pin"],
    )

    name, dept, sem = SECTION
    section = db.scalar(select(Section).where(Section.name == name))
    if section is None:
        section = Section(name=name, department=dept, semester=sem)
        db.add(section)
        db.flush()

    for code, cname, cdept, csem in COURSES:
        course = db.scalar(select(Course).where(Course.code == code))
        if course is None:
            course = Course(code=code, name=cname, department=cdept, semester=csem)
            db.add(course)
            db.flush()
        offering = db.scalar(
            select(CourseSection).where(
                CourseSection.course_id == course.id, CourseSection.section_id == section.id
            )
        )
        if offering is None:
            db.add(CourseSection(course_id=course.id, section_id=section.id, faculty_id=faculty.id))

    for ordinal, pname, start, end in PERIODS:
        if db.scalar(select(Period).where(Period.ordinal == ordinal)) is None:
            db.add(Period(ordinal=ordinal, name=pname, start_time=start, end_time=end))

    for index, sname in enumerate(STUDENT_NAMES, start=1):
        usn = f"1BM22EC{index:03d}"
        student = db.scalar(select(Student).where(Student.usn == usn))
        if student is None:
            db.add(
                Student(usn=usn, name=sname, department=dept, semester=sem, section_id=section.id)
            )
        else:
            student.section_id = section.id
            student.active = True
    db.commit()
    _ = admin
    return creds


def register_device(db: Session, name: str, section_name: str) -> str:
    """Create (or rotate the token of) a device assigned to the demo section."""
    token, token_hash = generate_device_token()
    device = db.scalar(select(Device).where(Device.name == name))
    if device is None:
        device = Device(name=name, token_hash=token_hash)
        db.add(device)
        db.flush()
    else:
        device.token_hash = token_hash
        device.active = True
        device.revoked_at = None
    section = db.scalar(select(Section).where(Section.name == section_name))
    if section is not None and db.get(DeviceSection, (device.id, section.id)) is None:
        db.add(DeviceSection(device_id=device.id, section_id=section.id))
    db.commit()
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--device", metavar="NAME", help="register a device with this name and print its token once"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    env = dotenv_values(REPO_ROOT / ".env")
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        creds = seed(db, Hasher(settings), env)
        token = register_device(db, args.device, SECTION[0]) if args.device else None

    print("Demo data ready.")
    print(
        f"  admin:   {creds['admin_email']}  "
        f"password: {creds['admin_password']}  PIN: {creds['admin_pin']}"
    )
    print(
        f"  faculty: {creds['faculty_email']}  "
        f"password: {creds['faculty_password']}  PIN: {creds['faculty_pin']}"
    )
    print(
        f"  section {SECTION[0]} with {len(STUDENT_NAMES)} students, "
        f"courses {', '.join(c[0] for c in COURSES)}"
    )
    if token:
        print(f"  device '{args.device}' token (shown once, put it in device.toml): {token}")
    print("Sign in at http://localhost:8000/admin", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
