"""Quality gates produce the right instruction for each failure."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from common.face.align import ARCFACE_DST
from common.face.quality import (
    QualityConfig,
    blur_score,
    check_sharpness,
    roll_estimate_deg,
    select_face,
    yaw_estimate_deg,
)
from common.face.types import Detection


def _face(width: float = 120.0, kps: np.ndarray | None = None, x: float = 100.0) -> Detection:
    bbox = np.array([x, 50.0, x + width, 50.0 + width * 1.3], dtype=np.float32)
    return Detection(
        bbox=bbox,
        score=0.9,
        kps=(ARCFACE_DST + np.array([x, 50.0], np.float32)) if kps is None else kps,
    )


def test_blur_score_drops_when_image_is_blurred():
    rng = np.random.default_rng(0)
    sharp = rng.integers(0, 256, size=(112, 112, 3), dtype=np.uint8)
    blurred = cv2.GaussianBlur(sharp, (9, 9), 0)
    assert blur_score(sharp) > 10 * blur_score(blurred)
    assert blur_score(cv2.cvtColor(sharp, cv2.COLOR_BGR2GRAY)) == pytest.approx(blur_score(sharp))


def test_yaw_estimate_is_zero_for_a_frontal_face_and_signed_otherwise():
    frontal = ARCFACE_DST.copy()
    assert abs(yaw_estimate_deg(frontal)) < 3.0

    turned = frontal.copy()
    turned[2, 0] += 10.0  # nose towards the right eye
    assert yaw_estimate_deg(turned) > 15.0

    turned[2, 0] -= 20.0  # nose towards the left eye
    assert yaw_estimate_deg(turned) < -15.0

    extreme = frontal.copy()
    extreme[2, 0] = 1000.0
    assert yaw_estimate_deg(extreme) == pytest.approx(90.0)


def test_roll_estimate_from_eye_line():
    level = ARCFACE_DST.copy()
    level[1, 1] = level[0, 1]
    assert roll_estimate_deg(level) == pytest.approx(0.0)
    tilted = level.copy()
    tilted[1, 1] += tilted[1, 0] - tilted[0, 0]  # 45 degrees
    assert roll_estimate_deg(tilted) == pytest.approx(45.0)


def test_select_face_messages():
    config = QualityConfig(min_face_width_px=90, max_yaw_deg=25)

    verdict, face = select_face([], config)
    assert (verdict.ok, verdict.reason, verdict.message) == (
        False,
        "no_face",
        "Stand in front of the camera",
    )

    verdict, _ = select_face([_face(), _face(x=400)], config)
    assert verdict.reason == "multiple_faces" and verdict.message == "One person at a time"

    verdict, face = select_face([_face(width=60)], config)
    assert verdict.reason == "too_small" and verdict.message == "Move closer" and face is not None

    turned = ARCFACE_DST + np.array([100.0, 50.0], np.float32)
    turned[2, 0] += 12.0
    verdict, _ = select_face([_face(kps=turned)], config)
    assert verdict.reason == "yaw" and verdict.message == "Look at the camera"

    verdict, face = select_face([_face()], config)
    assert verdict.ok and verdict.message == "" and face is not None


def test_check_sharpness_gate():
    config = QualityConfig(blur_min=60.0)
    flat = np.full((112, 112, 3), 128, np.uint8)
    verdict, score = check_sharpness(flat, config)
    assert not verdict.ok and verdict.message == "Hold still" and score == 0.0
    rng = np.random.default_rng(1)
    noisy = rng.integers(0, 256, size=(112, 112, 3), dtype=np.uint8)
    verdict, score = check_sharpness(noisy, config)
    assert verdict.ok and score > 60.0
