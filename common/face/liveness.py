"""Liveness / anti-spoofing seam.

Out of scope for this build, but the pipeline calls it on every gated frame so a real
check (IR, challenge-response, texture model) can be dropped in later without touching
the pipeline. ``NoOpLiveness`` always passes and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from common.face.types import Array, Detection


@dataclass(frozen=True)
class LivenessResult:
    passed: bool
    reason: str


class LivenessCheck(Protocol):
    """Decide whether the face in ``frame_bgr``/``aligned_bgr`` belongs to a live person."""

    name: str

    def check(
        self, frame_bgr: Array, detection: Detection, aligned_bgr: Array
    ) -> LivenessResult: ...


class NoOpLiveness:
    """Placeholder that accepts everything. The device shows its name in Settings."""

    name = "none (anti-spoofing not enabled)"

    def check(self, frame_bgr: Array, detection: Detection, aligned_bgr: Array) -> LivenessResult:
        return LivenessResult(passed=True, reason="liveness check disabled")
