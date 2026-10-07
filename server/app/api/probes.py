"""Probe upload for calibration (used by ``tools/capture_probes.py --upload`` on a Pi).

Probes are held-out test crops. They are stored under ``PROBES_DIR/<USN>/`` only, never
turned into templates, and require the student's active consent like everything else
that touches their face.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

import cv2
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from common.schemas import ProbesIn, ProbesOut
from server.app.auth import AppSettings, CurrentDevice, DbSession
from server.app.models import DeviceSection, Student
from server.app.services import audit, enrolment

router = APIRouter()


@router.post("/probes/{usn}", response_model=ProbesOut)
def upload_probes(
    usn: str, payload: ProbesIn, db: DbSession, device: CurrentDevice, settings: AppSettings
) -> ProbesOut:
    student = db.scalar(select(Student).where(Student.usn == usn.strip().upper()))
    if student is None or not student.active:
        raise HTTPException(status_code=404, detail="Student not found.")
    assigned = set(
        db.scalars(select(DeviceSection.section_id).where(DeviceSection.device_id == device.id))
    )
    if student.section_id not in assigned:
        raise HTTPException(
            status_code=403, detail="Student's section is not assigned to this device."
        )
    if enrolment.active_consent(db, student.id) is None:
        raise HTTPException(
            status_code=409, detail="No active consent for this student; probes need consent too."
        )

    folder = settings.probes_dir / student.usn
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stored = 0
    for idx, item in enumerate(payload.crops_png_b64):
        try:
            crop = enrolment.decode_png(base64.b64decode(item, validate=True))
            enrolment.validate_crop(crop)
        except (binascii.Error, ValueError, enrolment.EnrolmentError) as exc:
            raise HTTPException(status_code=422, detail=f"crop {idx}: {exc}") from exc
        cv2.imwrite(str(folder / f"{stamp}_{idx}.png"), crop)
        stored += 1
    audit.record(
        db,
        actor_id=None,
        action="probes.added",
        entity="student",
        entity_id=student.id,
        after={"usn": student.usn, "count": stored, "device": device.name},
    )
    db.commit()
    return ProbesOut(usn=student.usn, stored=stored)
