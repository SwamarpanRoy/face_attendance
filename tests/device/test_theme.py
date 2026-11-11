"""Frosted glass over the live video: blurs only inside the panel, never crashes at edges."""

from __future__ import annotations

import numpy as np

from device.app.ui.theme import frost


def _noise(h: int = 200, w: int = 300) -> np.ndarray:
    return np.random.default_rng(0).integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_frost_blurs_inside_and_leaves_outside_untouched():
    image = _noise()
    original = image.copy()
    frost(image, 50, 40, 200, 80, radius=20)
    inside = image[60:100, 80:220].astype(float)
    assert inside.std() < original[60:100, 80:220].astype(float).std() / 3  # much smoother
    assert np.array_equal(image[:40], original[:40])  # above the panel
    assert np.array_equal(image[121:], original[121:])  # below it
    assert np.array_equal(image[40, 50], original[40, 50])  # rounded corner stays sharp


def test_frost_clips_to_the_image_and_ignores_tiny_panels():
    image = _noise()
    frost(image, -30, -30, 400, 100, radius=20)  # partly off-screen: no exception
    tiny = _noise()
    before = tiny.copy()
    frost(tiny, 10, 10, 4, 4, radius=2)
    assert np.array_equal(tiny, before)
