"""Alignment must map detected landmarks exactly onto the ArcFace template."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from common.face.align import ARCFACE_DST, CROP_SIZE, estimate_norm, norm_crop, umeyama


def _similarity(scale: float, angle_deg: float, tx: float, ty: float) -> np.ndarray:
    theta = math.radians(angle_deg)
    return np.array(
        [
            [scale * math.cos(theta), -scale * math.sin(theta), tx],
            [scale * math.sin(theta), scale * math.cos(theta), ty],
            [0.0, 0.0, 1.0],
        ]
    )


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
    return (matrix @ homogeneous.T).T[:, :2]


@pytest.mark.parametrize(
    ("scale", "angle", "tx", "ty"),
    [(2.5, 20.0, 100.0, 50.0), (0.8, -35.0, -10.0, 300.0), (1.0, 0.0, 0.0, 0.0)],
)
def test_estimate_norm_recovers_a_known_similarity(scale, angle, tx, ty):
    # Landmarks in a frame are the template moved by a known similarity transform.
    frame_to_template = _similarity(scale, angle, tx, ty)
    landmarks = _apply(np.linalg.inv(frame_to_template), ARCFACE_DST.astype(np.float64))

    matrix = estimate_norm(landmarks.astype(np.float32))

    assert matrix.shape == (2, 3) and matrix.dtype == np.float32
    mapped = _apply(np.vstack([matrix, [0, 0, 1]]), landmarks)
    assert np.allclose(mapped, ARCFACE_DST, atol=1e-3)
    assert np.allclose(matrix, frame_to_template[:2], atol=1e-3)


def test_umeyama_is_least_squares_for_noisy_points():
    rng = np.random.default_rng(0)
    truth = _similarity(1.7, 12.0, 40.0, -20.0)
    src = ARCFACE_DST.astype(np.float64) + rng.normal(0, 0.3, ARCFACE_DST.shape)
    dst = _apply(truth, ARCFACE_DST.astype(np.float64))
    estimated = umeyama(src, dst)
    # Linear part close to the truth; translation absorbs the noise (a couple of px);
    # the fit residual stays around the noise level scaled by 1.7.
    assert np.allclose(estimated[:2, :2], truth[:2, :2], atol=0.05)
    assert np.allclose(estimated[:2, 2], truth[:2, 2], atol=3.0)
    residual = np.linalg.norm(_apply(estimated, src) - dst, axis=1).mean()
    assert residual < 1.5


def test_norm_crop_moves_landmarks_onto_the_template():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame_to_template = _similarity(0.5, 10.0, -60.0, -40.0)
    landmarks = _apply(np.linalg.inv(frame_to_template), ARCFACE_DST.astype(np.float64))
    for x, y in landmarks:
        cv2.circle(frame, (round(x), round(y)), 4, (255, 255, 255), -1)

    crop = norm_crop(frame, landmarks.astype(np.float32))

    assert crop.shape == (CROP_SIZE, CROP_SIZE, 3)
    for x, y in ARCFACE_DST:
        patch = crop[int(y) - 2 : int(y) + 3, int(x) - 2 : int(x) + 3]
        assert patch.max() > 128, f"expected a bright blob at template point ({x:.0f}, {y:.0f})"


def test_estimate_norm_rejects_bad_input():
    with pytest.raises(ValueError, match="5x2"):
        estimate_norm(np.zeros((4, 2), np.float32))
    with pytest.raises(ValueError, match="multiple of 112"):
        estimate_norm(ARCFACE_DST, image_size=100)


def test_umeyama_handles_reflection_degeneracy_without_nan():
    # Mirrored points: the estimator must still return a proper rotation (det > 0).
    mirrored = ARCFACE_DST.astype(np.float64) * np.array([-1.0, 1.0])
    matrix = umeyama(mirrored, ARCFACE_DST.astype(np.float64))
    assert np.isfinite(matrix).all()
    assert np.linalg.det(matrix[:2, :2]) > 0
