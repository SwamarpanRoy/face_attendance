"""Quality gates: only well-posed, sharp, single faces reach the embedder.

Every rejection maps to a short instruction for the person in front of the camera
("Move closer"), which is what the device shows. Thresholds come from config, not
code. The yaw estimate is a landmark heuristic, good enough to reject strong profile
views; it is not a pose model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from common.face.types import Array, Detection


@dataclass(frozen=True)
class QualityConfig:
    min_face_width_px: float = 90.0
    blur_min: float = 60.0  # variance of the Laplacian on the aligned 112x112 crop
    max_yaw_deg: float = 25.0


@dataclass(frozen=True)
class GateResult:
    """``ok`` plus a stable ``reason`` code and the on-screen ``message``."""

    ok: bool
    reason: str
    message: str


MESSAGES: dict[str, str] = {
    "ok": "",
    "no_face": "Stand in front of the camera",
    "multiple_faces": "One person at a time",
    "too_small": "Move closer",
    "blurry": "Hold still",
    "yaw": "Look at the camera",
    "liveness": "Try again",
}


def gate(reason: str) -> GateResult:
    return GateResult(ok=reason == "ok", reason=reason, message=MESSAGES[reason])


def blur_score(image: Array) -> float:
    """Variance of the Laplacian; low values mean motion blur or defocus."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def yaw_estimate_deg(kps: Array) -> float:
    """Signed yaw estimate from how far the nose sits from the eye and mouth midlines.

    For a frontal face the nose projects onto the middle of the eye line (t = 0.5). As
    the head turns, the nose moves towards one eye; ``asin`` of the normalised offset
    is a reasonable angle proxy. Positive means the nose moved towards the right eye.
    """
    left_eye, right_eye, nose, mouth_left, mouth_right = (
        np.asarray(p, dtype=np.float64) for p in kps
    )

    def offset(left: Array, right: Array) -> float:
        axis = right - left
        length_sq = float(axis @ axis)
        if length_sq <= 1e-6:
            return 0.0
        t = float((nose - left) @ axis) / length_sq
        return (t - 0.5) * 2.0

    ratio = (offset(left_eye, right_eye) + offset(mouth_left, mouth_right)) / 2.0
    ratio = max(-1.0, min(1.0, ratio))
    return math.degrees(math.asin(ratio))


def roll_estimate_deg(kps: Array) -> float:
    """Head tilt from the eye line (positive = clockwise in image coordinates)."""
    left_eye, right_eye = kps[0], kps[1]
    return math.degrees(
        math.atan2(float(right_eye[1] - left_eye[1]), float(right_eye[0] - left_eye[0]))
    )


def select_face(
    detections: Sequence[Detection], config: QualityConfig
) -> tuple[GateResult, Detection | None]:
    """Gates that need only the detections: exactly one face, large enough, roughly frontal."""
    if not detections:
        return gate("no_face"), None
    if len(detections) > 1:
        return gate("multiple_faces"), None
    face = detections[0]
    if face.width < config.min_face_width_px:
        return gate("too_small"), face
    if abs(yaw_estimate_deg(face.kps)) > config.max_yaw_deg:
        return gate("yaw"), face
    return gate("ok"), face


def check_sharpness(aligned_bgr: Array, config: QualityConfig) -> tuple[GateResult, float]:
    """Blur gate on the aligned crop, so the score does not depend on distance."""
    score = blur_score(aligned_bgr)
    if score < config.blur_min:
        return gate("blurry"), score
    return gate("ok"), score
