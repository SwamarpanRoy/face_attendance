"""Regenerate every template from its stored crop with a new recogniser model.

    python tools/reembed.py --new-model-version buffalo_l/w600k_r50 --models-dir models_new

Crops are kept on the server for exactly this purpose. Each existing template with a
crop on disk gets a sibling row under the new ``model_version`` (same consent, source,
quality and crop); the old rows are removed unless ``--keep-old``. Devices notice the
new ``created_at`` on their next sync and refresh. Remember to set ``MODEL_VERSION`` in
``.env`` (and on the devices) to the new name afterwards.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from common.face.embedder import EMBEDDER_FILE, FaceEmbedder, embedding_to_bytes
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.models import FaceTemplate, Student
from server.app.services import audit

log = logging.getLogger("reembed")


@dataclass
class ReembedStats:
    students: int = 0
    created: int = 0
    removed: int = 0
    missing_crops: list[str] = field(default_factory=list)


def reembed(
    db: Session,
    settings: Settings,
    embedder: FaceEmbedder,
    new_version: str,
    *,
    keep_old: bool = False,
    actor_id: int | None = None,
) -> ReembedStats:
    stats = ReembedStats()
    students = list(
        db.scalars(select(Student).options(selectinload(Student.templates)).order_by(Student.usn))
    )
    for student in students:
        sources = [t for t in student.templates if t.model_version != new_version and t.crop_path]
        if not sources:
            continue
        crops = []
        kept: list[FaceTemplate] = []
        for template in sources:
            path = settings.crops_dir / str(template.crop_path)
            image = cv2.imread(str(path))
            if image is None:
                stats.missing_crops.append(str(path))
                continue
            crops.append(image)
            kept.append(template)
        if not crops:
            continue
        embeddings = embedder.embed_batch(crops)
        for template, embedding in zip(kept, embeddings, strict=True):
            db.add(
                FaceTemplate(
                    student_id=student.id,
                    consent_id=template.consent_id,
                    embedding=embedding_to_bytes(embedding),
                    model_version=new_version,
                    source=template.source,
                    quality_score=template.quality_score,
                    crop_path=template.crop_path,
                )
            )
            stats.created += 1
            if not keep_old:
                db.delete(template)
                stats.removed += 1
        stats.students += 1
        audit.record(
            db,
            actor_id=actor_id,
            action="enrolment.reembedded",
            entity="student",
            entity_id=student.id,
            after={"model_version": new_version, "templates": len(kept), "kept_old": keep_old},
        )
    db.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--new-model-version", required=True)
    parser.add_argument(
        "--models-dir", type=Path, default=None, help="folder holding the new w600k_mbf.onnx"
    )
    parser.add_argument("--keep-old", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = Settings()
    models_dir = args.models_dir or settings.models_dir
    embedder = FaceEmbedder(models_dir / EMBEDDER_FILE)
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        stats = reembed(db, settings, embedder, args.new_model_version, keep_old=args.keep_old)
    print(f"{stats.students} students, {stats.created} templates created, {stats.removed} removed")
    for path in stats.missing_crops:
        print(f"  missing crop: {path}")
    print(
        f"Now set MODEL_VERSION={args.new_model_version} in .env and on the devices, "
        "then restart the server."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
