"""Consent and face-template storage: the only path that creates biometric data.

Rules enforced here (and again by the database):

* a template is always created under an active consent row (``consent_id NOT NULL``);
* aligned 112x112 crops are written only under ``CROPS_DIR/<USN>/`` on the server, so
  templates can be regenerated when the model changes;
* every change is audited; withdrawals leave a tombstone so devices drop the student.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.face.embedder import FaceEmbedder, embedding_to_bytes
from common.face.types import Array
from server.app.config import Settings
from server.app.models import (
    Consent,
    ConsentMethod,
    FaceTemplate,
    Student,
    TemplateSource,
    TemplateTombstone,
)
from server.app.services import audit

log = logging.getLogger(__name__)

CROP_SIZE = 112


class EnrolmentError(Exception):
    """A rule was violated; ``status`` is the HTTP code the API should answer with."""

    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConsentRequiredError(EnrolmentError):
    status = 409


class StudentNotFoundError(EnrolmentError):
    status = 404


class BadCropError(EnrolmentError):
    status = 422


@dataclass(frozen=True)
class StoredTemplates:
    consent: Consent
    templates: list[FaceTemplate]
    total_templates: int


def active_consent(db: Session, student_id: int) -> Consent | None:
    """Most recent consent that has not been withdrawn."""
    return db.scalar(
        select(Consent)
        .where(Consent.student_id == student_id, Consent.withdrawn_at.is_(None))
        .order_by(Consent.consent_at.desc())
        .limit(1)
    )


def record_consent(
    db: Session,
    student: Student,
    *,
    consent_at: datetime,
    consent_version: str,
    method: ConsentMethod,
    recorded_by: int | None,
) -> Consent:
    """Add a consent row (idempotent for the same student/version/timestamp)."""
    existing = db.scalar(
        select(Consent).where(
            Consent.student_id == student.id,
            Consent.consent_at == consent_at,
            Consent.consent_version == consent_version,
        )
    )
    if existing is not None:
        return existing
    consent = Consent(
        student_id=student.id,
        consent_at=consent_at,
        consent_version=consent_version,
        method=method,
        recorded_by=recorded_by,
    )
    db.add(consent)
    db.flush()
    audit.record(
        db,
        actor_id=recorded_by,
        action="consent.record",
        entity="student",
        entity_id=student.id,
        after={"consent_id": consent.id, "version": consent_version, "method": method.value},
    )
    return consent


def validate_crop(crop: Array) -> None:
    if crop.ndim != 3 or crop.shape[:2] != (CROP_SIZE, CROP_SIZE) or crop.shape[2] != 3:
        raise BadCropError(f"crop must be {CROP_SIZE}x{CROP_SIZE} BGR, got {crop.shape}")


def _crop_dir(settings: Settings, usn: str) -> Path:
    return settings.crops_dir / usn


def save_crop(
    settings: Settings, usn: str, crop: Array, *, source: TemplateSource, idx: int
) -> str:
    """Write the aligned crop as PNG; returns the path relative to ``crops_dir``."""
    folder = _crop_dir(settings, usn)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    name = f"{stamp}_{source.value}_{idx}.png"
    if not cv2.imwrite(str(folder / name), crop):
        raise EnrolmentError(f"could not write crop to {folder / name}")
    return f"{usn}/{name}"


def store_templates(
    db: Session,
    settings: Settings,
    embedder: FaceEmbedder,
    *,
    student: Student,
    consent: Consent,
    crops: list[Array],
    source: TemplateSource,
    quality_scores: list[float | None] | None = None,
    actor_id: int | None,
    model_version: str | None = None,
) -> StoredTemplates:
    """Embed aligned crops, persist crops + templates, audit. Commits."""
    if not crops:
        raise BadCropError("no crops supplied")
    if consent.withdrawn_at is not None or consent.student_id != student.id:
        raise ConsentRequiredError("consent is withdrawn or belongs to another student")
    for crop in crops:
        validate_crop(crop)
    version = model_version or settings.model_version
    embeddings = embedder.embed_batch(crops)
    scores = quality_scores or [None] * len(crops)
    rows: list[FaceTemplate] = []
    for idx, (crop, embedding) in enumerate(zip(crops, embeddings, strict=True)):
        crop_path = save_crop(settings, student.usn, crop, source=source, idx=idx)
        row = FaceTemplate(
            student_id=student.id,
            consent_id=consent.id,
            embedding=embedding_to_bytes(embedding),
            model_version=version,
            source=source,
            quality_score=scores[idx] if idx < len(scores) else None,
            crop_path=crop_path,
            # Explicit wall-clock stamp: Postgres now() is the transaction start, and the
            # device syncs by created_at > since.
            created_at=datetime.now(UTC),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    total = db.scalar(
        select(func.count()).select_from(FaceTemplate).where(FaceTemplate.student_id == student.id)
    )
    audit.record(
        db,
        actor_id=actor_id,
        action="enrolment.templates_added",
        entity="student",
        entity_id=student.id,
        after={"usn": student.usn, "added": len(rows), "source": source.value, "model": version},
    )
    db.commit()
    return StoredTemplates(consent=consent, templates=rows, total_templates=int(total or 0))


def delete_templates(
    db: Session,
    settings: Settings,
    student: Student,
    *,
    actor_id: int | None,
    reason: str,
    remove_crops: bool,
    source: TemplateSource | None = None,
) -> int:
    """Remove templates (optionally only one source) and leave a tombstone. Commits."""
    stmt = select(FaceTemplate).where(FaceTemplate.student_id == student.id)
    if source is not None:
        stmt = stmt.where(FaceTemplate.source == source)
    rows = list(db.scalars(stmt))
    for row in rows:
        db.delete(row)
    if rows:
        db.add(
            TemplateTombstone(
                usn=student.usn, section_id=student.section_id, deleted_at=datetime.now(UTC)
            )
        )
        audit.record(
            db,
            actor_id=actor_id,
            action="enrolment.templates_deleted",
            entity="student",
            entity_id=student.id,
            before={"count": len(rows), "source": source.value if source else "all"},
            reason=reason,
        )
    if remove_crops:
        folder = _crop_dir(settings, student.usn)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    db.commit()
    return len(rows)


def withdraw_consent(
    db: Session, settings: Settings, student: Student, *, actor_id: int | None, reason: str
) -> int:
    """DPDP withdrawal: delete templates and crops, keep attendance, mark consents withdrawn."""
    now = datetime.now(UTC)
    for consent in db.scalars(
        select(Consent).where(Consent.student_id == student.id, Consent.withdrawn_at.is_(None))
    ):
        consent.withdrawn_at = now
    audit.record(
        db,
        actor_id=actor_id,
        action="consent.withdraw",
        entity="student",
        entity_id=student.id,
        reason=reason,
    )
    return delete_templates(
        db, settings, student, actor_id=actor_id, reason=reason, remove_crops=True
    )


def decode_png(data: bytes) -> Array:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise BadCropError("crop is not a decodable PNG/JPEG image")
    return np.asarray(image)
