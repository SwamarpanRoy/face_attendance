"""Plain data types shared across the face pipeline (no Qt, no server imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float32]
#: Any numpy image/array input (dtype not constrained).
Array = npt.NDArray[Any]

#: Landmark order produced by det_500m and expected by the ArcFace alignment template.
LANDMARK_NAMES = ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right")


@dataclass(frozen=True)
class Detection:
    """One detected face in *original frame* pixel coordinates.

    ``bbox`` is ``[x1, y1, x2, y2]``; ``kps`` is ``(5, 2)`` in :data:`LANDMARK_NAMES` order.
    """

    bbox: FloatArray
    score: float
    kps: FloatArray

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return float(self.bbox[3] - self.bbox[1])

    @property
    def center(self) -> tuple[float, float]:
        return (float(self.bbox[0] + self.bbox[2]) / 2, float(self.bbox[1] + self.bbox[3]) / 2)

    def as_int_box(self) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = (round(float(v)) for v in self.bbox)
        return x1, y1, x2, y2
