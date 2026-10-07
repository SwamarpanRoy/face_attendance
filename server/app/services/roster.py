"""CSV roster import for a section.

Expected columns: ``usn,name`` with optional ``department`` and ``semester``
(defaults come from the section). Rows are upserted by USN so re-importing an
updated class list is safe. The result lists every rejected row with its reason so
the admin can fix the spreadsheet rather than guess.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.models import Section, Student
from server.app.services import audit


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    rejected: list[tuple[int, str, str]] = field(default_factory=list)  # (row no, usn, reason)

    @property
    def total(self) -> int:
        return self.created + self.updated + len(self.rejected)


def import_roster_csv(
    db: Session,
    section: Section,
    data: bytes,
    *,
    usn_pattern: str,
    actor_id: int | None,
) -> ImportResult:
    """Upsert students from CSV bytes into ``section``. Commits on success."""
    result = ImportResult()
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.rejected.append((0, "", "empty file"))
        return result
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "usn" not in headers or "name" not in headers:
        result.rejected.append((0, "", "header must include 'usn' and 'name'"))
        return result

    seen: set[str] = set()
    for row_no, row in enumerate(reader, start=2):
        usn = (row.get(headers["usn"]) or "").strip().upper()
        name = (row.get(headers["name"]) or "").strip()
        if not usn or not name:
            result.rejected.append((row_no, usn, "usn and name are required"))
            continue
        if not re.fullmatch(usn_pattern, usn):
            result.rejected.append((row_no, usn, "USN format looks wrong"))
            continue
        if usn in seen:
            result.rejected.append((row_no, usn, "duplicate USN in file"))
            continue
        seen.add(usn)
        department = (
            row.get(headers.get("department", ""), "") or ""
        ).strip() or section.department
        semester_raw = (row.get(headers.get("semester", ""), "") or "").strip()
        semester = int(semester_raw) if semester_raw.isdigit() else section.semester

        student = db.scalar(select(Student).where(Student.usn == usn))
        if student is None:
            db.add(
                Student(
                    usn=usn,
                    name=name[:128],
                    department=department[:64],
                    semester=semester,
                    section_id=section.id,
                )
            )
            result.created += 1
        else:
            student.name = name[:128]
            student.department = department[:64]
            student.semester = semester
            student.section_id = section.id
            student.active = True
            result.updated += 1

    audit.record(
        db,
        actor_id=actor_id,
        action="roster.import",
        entity="section",
        entity_id=section.id,
        after={
            "created": result.created,
            "updated": result.updated,
            "rejected": len(result.rejected),
        },
    )
    db.commit()
    return result
