"""Capture held-out test images (probes) per enrolled student for calibration.

    python tools/capture_probes.py --usn 1BM22EC001 --count 4          # webcam, OpenCV window
    python tools/capture_probes.py --from-folder probes_raw/            # <USN>/*.jpg or <USN>_x.jpg
    python tools/capture_probes.py --usn 1BM22EC001 --upload http://server:8000 --token ...

Probes are aligned 112x112 crops saved under ``PROBES_DIR/<USN>/`` on the server and
**never** used as templates. Capture them on a different day or under different light
than enrolment where possible. They are covered by the student's existing consent (the
notice says test images are taken); the server refuses probes for students without one.
On a Pi, pass ``--upload`` so the crops go straight to the server instead of local disk.
"""

from __future__ import annotations

import argparse
import base64
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import httpx
from sqlalchemy import select

from common.face.engine import FaceEngine
from common.face.types import Array
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.face import build_engine
from server.app.models import Student
from server.app.services import enrolment

log = logging.getLogger("capture_probes")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def save_probe(probes_dir: Path, usn: str, crop: Array, idx: int) -> Path:
    folder = probes_dir / usn
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}_{idx}.png"
    if not cv2.imwrite(str(path), crop):
        raise RuntimeError(f"could not write {path}")
    return path


def upload_probes(base_url: str, token: str, usn: str, crops: list[Array]) -> int:
    payload = []
    for crop in crops:
        ok, buffer = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError("PNG encode failed")
        payload.append(base64.b64encode(buffer.tobytes()).decode("ascii"))
    with httpx.Client(
        base_url=base_url.rstrip("/"), timeout=30, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = client.post(f"/api/v1/probes/{usn}", json={"crops_png_b64": payload})
    if response.status_code >= 400:
        raise RuntimeError(f"server refused probes: {response.status_code} {response.text}")
    return int(response.json()["stored"])


def require_consent_locally(usn: str) -> Student:
    settings = Settings()
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        student = db.scalar(select(Student).where(Student.usn == usn))
        if student is None:
            raise SystemExit(f"{usn}: unknown student")
        if enrolment.active_consent(db, student.id) is None:
            raise SystemExit(f"{usn}: no active consent; probes need the same consent as enrolment")
        return student


def crops_from_folder(engine: FaceEngine, folder: Path) -> dict[str, list[Array]]:
    """``<USN>/*.jpg`` or ``<USN>_anything.jpg`` -> aligned crops per USN (gates applied)."""
    found: dict[str, list[Array]] = {}
    files: list[tuple[str, Path]] = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        usn = (path.parent.name if path.parent != folder else path.stem.split("_")[0]).upper()
        files.append((usn, path))
    for usn, path in files:
        image = cv2.imread(str(path))
        if image is None:
            log.warning("unreadable %s", path)
            continue
        result = engine.embed_single(image)
        if not result.gate.ok or result.aligned is None:
            log.warning("skipped %s: %s", path.name, result.gate.message or result.gate.reason)
            continue
        found.setdefault(usn, []).append(result.aligned)
    return found


def capture_from_camera(
    engine: FaceEngine, count: int, webcam_index: int, gap_s: float
) -> list[Array]:
    """Show the webcam, keep every gated frame at least ``gap_s`` apart, until ``count``."""
    capture = cv2.VideoCapture(
        webcam_index, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    )
    if not capture.isOpened():
        raise SystemExit(f"could not open webcam {webcam_index}")
    crops: list[Array] = []
    last = 0.0
    try:
        while len(crops) < count:
            ok, frame = capture.read()
            if not ok:
                continue
            result = engine.process(frame, embed=False)
            text = result.gate.message or "Hold still…"
            if result.gate.ok and result.aligned is not None and time.monotonic() - last >= gap_s:
                crops.append(result.aligned.copy())
                last = time.monotonic()
                text = f"Captured {len(crops)}/{count}"
            for det in result.detections:
                x1, y1, x2, y2 = det.as_int_box()
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
            cv2.imshow("capture_probes (q to abort)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()
    return crops


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--usn", help="student to capture for (camera mode)")
    parser.add_argument("--count", type=int, default=4, help="probes to capture per student (3-5)")
    parser.add_argument("--gap", type=float, default=1.0, help="seconds between captures")
    parser.add_argument("--webcam", type=int, default=0)
    parser.add_argument(
        "--from-folder", type=Path, help="import existing photos instead of the camera"
    )
    parser.add_argument(
        "--upload", metavar="URL", help="send probes to this server instead of local disk"
    )
    parser.add_argument("--token", help="device token for --upload")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not 1 <= args.count <= 10:
        parser.error("--count must be between 1 and 10")
    if bool(args.upload) != bool(args.token):
        parser.error("--upload and --token go together")
    if not args.from_folder and not args.usn:
        parser.error("give --usn (camera) or --from-folder")

    settings = Settings()
    engine = build_engine(settings)
    per_usn: dict[str, list[Array]]
    if args.from_folder:
        per_usn = crops_from_folder(engine, args.from_folder)
    else:
        usn = args.usn.strip().upper()
        if not args.upload:
            require_consent_locally(usn)
        per_usn = {usn: capture_from_camera(engine, args.count, args.webcam, args.gap)}

    total = 0
    for usn, crops in per_usn.items():
        if not crops:
            continue
        if args.upload:
            stored = upload_probes(args.upload, args.token, usn, crops)
        else:
            if args.from_folder:
                require_consent_locally(usn)
            for idx, crop in enumerate(crops):
                save_probe(settings.probes_dir, usn, crop, idx)
            stored = len(crops)
        total += stored
        print(f"{usn}: {stored} probes stored")
    print(f"{total} probes total. Next: python tools/calibrate.py")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
