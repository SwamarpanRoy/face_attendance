"""Export attendance reports from the command line (same numbers as the admin UI).

python tools/export_report.py --out report.xlsx                       # all courses, last 90 days
python tools/export_report.py --course 22EC71 --start 2026-08-01 --end 2026-11-30 --out vlsi.csv
python tools/export_report.py --shortage --threshold 75 --out short.csv
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.models import Course, Section
from server.app.services import reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True, help=".csv or .xlsx")
    parser.add_argument("--course", help="course code")
    parser.add_argument("--section", help="section name")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--shortage", action="store_true", help="only students below the threshold")
    parser.add_argument("--threshold", type=float, default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    today = datetime.now(UTC).astimezone().date()
    start = args.start or today - timedelta(days=90)
    end = args.end or today
    lower, upper = reports.date_bounds(start, end, settings.timezone)
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        course_ids = None
        if args.course:
            course = db.scalar(select(Course).where(Course.code == args.course.upper()))
            if course is None:
                raise SystemExit(f"unknown course {args.course}")
            course_ids = {course.id}
        section_id = None
        if args.section:
            section = db.scalar(select(Section).where(Section.name == args.section.upper()))
            if section is None:
                raise SystemExit(f"unknown section {args.section}")
            section_id = section.id
        rows = reports.course_matrix(
            db, course_ids=course_ids, section_id=section_id, lower=lower, upper=upper
        )
    if args.shortage:
        rows = reports.shortage(rows, args.threshold or settings.attendance_shortage_threshold_pct)
    data = (
        reports.export_xlsx(rows)
        if args.out.suffix.lower() == ".xlsx"
        else reports.export_csv(rows)
    )
    args.out.write_bytes(data)
    print(f"{len(rows)} rows ({start} to {end}) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
