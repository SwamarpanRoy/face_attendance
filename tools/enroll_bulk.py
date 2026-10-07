"""Bulk enrolment from existing ID-card photos, gated by a consent CSV.

    python tools/enroll_bulk.py --photos idcards/ --consents consents.csv --report report.csv

``idcards/`` holds ``<USN>.jpg`` files. ``consents.csv`` has the columns
``usn,consent_at,consent_version,method`` (method: paper or bulk_csv). Any USN without a
consent row is refused before its photo is even opened. Each accepted photo is
detected, aligned, quality-checked, saved as a 112x112 crop under ``CROPS_DIR/<USN>/``
and stored as a template with ``source=idcard``. The report CSV lists every file with
accepted/rejected and the reason, so the office can fix the spreadsheet or re-scan.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.face.engine import FaceEngine
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.face import build_engine
from server.app.models import ConsentMethod, Student, TemplateSource
from server.app.services import audit, enrolment

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class ConsentRow:
    usn: str
    consent_at: datetime
    consent_version: str
    method: ConsentMethod


@dataclass
class ReportRow:
    file: str
    usn: str
    status: str  # accepted | rejected
    reason: str
    blur: float | None = None
    templates: int = 0


class ConsentCsvError(ValueError):
    pass


def parse_consent_time(value: str) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConsentCsvError(
            f"bad consent_at {value!r}: use ISO 8601 like 2026-10-01T10:30+05:30"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def load_consents(path: Path) -> dict[str, ConsentRow]:
    """Parse the CSV; raises on a malformed row because silent skips would hide refusals."""
    rows: dict[str, ConsentRow] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"usn", "consent_at", "consent_version", "method"}
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        if not required <= headers:
            raise ConsentCsvError(
                f"consents.csv needs columns {sorted(required)}, got {sorted(headers)}"
            )
        for number, raw in enumerate(reader, start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            usn = row["usn"].upper()
            if not usn:
                continue
            method_name = row["method"].lower() or "bulk_csv"
            try:
                method = ConsentMethod(method_name)
            except ValueError as exc:
                raise ConsentCsvError(f"row {number}: unknown method {method_name!r}") from exc
            if method is ConsentMethod.DEVICE:
                raise ConsentCsvError(
                    f"row {number}: method 'device' is reserved for on-device consent"
                )
            if not row["consent_version"]:
                raise ConsentCsvError(f"row {number}: consent_version is required")
            rows[usn] = ConsentRow(
                usn, parse_consent_time(row["consent_at"]), row["consent_version"], method
            )
    return rows


def run_bulk(
    db: Session,
    settings: Settings,
    engine: FaceEngine,
    photos_dir: Path,
    consents: dict[str, ConsentRow],
    *,
    replace: bool = False,
    min_blur: float | None = None,
    actor_id: int | None = None,
) -> list[ReportRow]:
    """Process every image in ``photos_dir``; returns one report row per file."""
    blur_floor = settings.enrol_min_blur if min_blur is None else min_blur
    report: list[ReportRow] = []
    for path in sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        usn = path.stem.strip().upper()
        row = ReportRow(file=path.name, usn=usn, status="rejected", reason="")
        report.append(row)

        consent_row = consents.get(usn)
        if consent_row is None:
            row.reason = "no consent in CSV"
            continue
        student = db.scalar(select(Student).where(Student.usn == usn))
        if student is None:
            row.reason = "unknown USN (not in students table)"
            continue
        if not student.active:
            row.reason = "student inactive"
            continue

        image = cv2.imread(str(path))
        if image is None:
            row.reason = "unreadable image"
            continue
        result = engine.embed_single(image)
        row.blur = result.blur
        if not result.gate.ok or result.aligned is None:
            row.reason = f"{result.gate.reason}: {result.gate.message}"
            continue
        if result.blur is not None and result.blur < blur_floor:
            row.reason = f"blurry ({result.blur:.0f} < {blur_floor:.0f})"
            continue

        consent = enrolment.record_consent(
            db,
            student,
            consent_at=consent_row.consent_at,
            consent_version=consent_row.consent_version,
            method=consent_row.method,
            recorded_by=actor_id,
        )
        if replace:
            enrolment.delete_templates(
                db,
                settings,
                student,
                actor_id=actor_id,
                reason="bulk re-import",
                remove_crops=False,
                source=TemplateSource.IDCARD,
            )
        stored = enrolment.store_templates(
            db,
            settings,
            engine.embedder,
            student=student,
            consent=consent,
            crops=[result.aligned],
            source=TemplateSource.IDCARD,
            quality_scores=[result.blur],
            actor_id=actor_id,
        )
        row.status = "accepted"
        row.reason = ""
        row.templates = stored.total_templates
    return report


def write_report(rows: list[ReportRow], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "usn", "status", "reason", "blur", "templates"])
        for row in rows:
            writer.writerow(
                [
                    row.file,
                    row.usn,
                    row.status,
                    row.reason,
                    f"{row.blur:.1f}" if row.blur is not None else "",
                    row.templates,
                ]
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--photos", type=Path, required=True, help="folder of <USN>.jpg files")
    parser.add_argument("--consents", type=Path, required=True, help="consents.csv")
    parser.add_argument("--report", type=Path, default=Path("enrol_report.csv"))
    parser.add_argument("--replace", action="store_true", help="replace existing ID-card templates")
    parser.add_argument("--min-blur", type=float, default=None, help="override ENROL_MIN_BLUR")
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        consents = load_consents(args.consents)
    except ConsentCsvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    engine = build_engine(settings)
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        rows = run_bulk(
            db,
            settings,
            engine,
            args.photos,
            consents,
            replace=args.replace,
            min_blur=args.min_blur,
        )
    write_report(rows, args.report)
    accepted = sum(1 for r in rows if r.status == "accepted")
    with factory() as db:
        audit.record(
            db,
            actor_id=None,
            action="enrolment.bulk_import",
            entity="import",
            entity_id=args.report.name,
            after={
                "accepted": accepted,
                "rejected": len(rows) - accepted,
                "report": str(args.report),
                "photos": str(args.photos),
            },
        )
        db.commit()
    print(f"{accepted} accepted, {len(rows) - accepted} rejected; report: {args.report}")
    for row in rows:
        if row.status != "accepted":
            print(f"  rejected {row.file}: {row.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
