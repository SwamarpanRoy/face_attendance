"""Worker thread: camera -> FaceEngine -> Recognizer, reporting to the UI via Qt signals.

The UI thread never touches the camera or the models. Everything the window shows
arrives through signals, and the only thing the UI sends back is a request flag
(``request_capture``) read between frames, so no locks are needed around the models.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from PyQt5.QtCore import QThread, pyqtSignal

from common.face.engine import FaceEngine, FrameResult
from common.face.matcher import Outcome, Recognizer
from common.face.types import Array, FloatArray
from device.app.camera import CameraError, CameraSource
from device.app.enrol import Capture

log = logging.getLogger(__name__)


@dataclass
class StageStats:
    """Rolling latency stats per stage, in milliseconds."""

    window: int = 60
    samples: dict[str, deque[float]] = field(default_factory=dict)

    def add(self, timings: dict[str, float]) -> None:
        for stage, value in timings.items():
            self.samples.setdefault(stage, deque(maxlen=self.window)).append(value)

    def percentile(self, stage: str, q: float) -> float | None:
        values = sorted(self.samples.get(stage, ()))
        if not values:
            return None
        # Nearest-rank percentile: p50 of 1..10 is 5, p95 is 10.
        rank = max(1, math.ceil(q * len(values)))
        return values[rank - 1]

    def summary(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for stage in self.samples:
            p50 = self.percentile(stage, 0.50)
            p95 = self.percentile(stage, 0.95)
            if p50 is not None and p95 is not None:
                out[stage] = (p50, p95)
        return out


class PipelineWorker(QThread):
    """Runs the recognition loop until ``stop()`` is called."""

    frame_ready = pyqtSignal(object, object)  # frame (BGR ndarray), FrameResult
    gate_message = pyqtSignal(str, str)  # message, reason code
    outcome_ready = pyqtSignal(object)  # Outcome
    capture_ready = pyqtSignal(object)  # Capture: embedding, aligned crop (memory only), blur
    stats_ready = pyqtSignal(object)  # dict stage -> (p50, p95)
    failed = pyqtSignal(str)

    def __init__(
        self,
        camera: CameraSource,
        engine: FaceEngine,
        recognizer: Recognizer,
        *,
        stats_every_s: float = 2.0,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.camera = camera
        self.engine = engine
        self.recognizer = recognizer
        self.stats = StageStats()
        self._stop = threading.Event()
        self._capture_requested = threading.Event()
        self._suppress_outcomes = threading.Event()
        self._stats_every_s = stats_every_s
        self._pending_recognizer: Recognizer | None = None
        self.frames_seen = 0

    # -- UI thread API
    def request_capture(self) -> None:
        """Ask for the next good embedding to be emitted on ``capture_ready``."""
        self._capture_requested.set()

    def set_suppress_outcomes(self, suppress: bool) -> None:
        """While enrolling, frames are shown but not matched (no banners, no marks)."""
        if suppress:
            self._suppress_outcomes.set()
        else:
            self._suppress_outcomes.clear()

    def stop(self, timeout_ms: int = 3000) -> None:
        self._stop.set()
        if self.isRunning():
            self.wait(timeout_ms)

    def swap_recognizer(self, recognizer: Recognizer) -> None:
        """Replace the matcher (e.g. after new templates); applied between frames."""
        self._pending_recognizer = recognizer

    # -- worker thread
    def run(self) -> None:
        try:
            self.camera.start()
        except CameraError as exc:
            self.failed.emit(str(exc))
            return
        last_stats = time.monotonic()
        try:
            while not self._stop.is_set():
                if self._pending_recognizer is not None:
                    self.recognizer = self._pending_recognizer
                    self._pending_recognizer = None
                frame = self.camera.read()
                if frame is None:
                    time.sleep(0.02)
                    continue
                self.frames_seen += 1
                result = self.engine.process(frame)
                self.stats.add(result.timings_ms)
                self.frame_ready.emit(frame, result)
                self._handle(result)
                now = time.monotonic()
                if now - last_stats >= self._stats_every_s:
                    self.stats_ready.emit(self.stats.summary())
                    last_stats = now
        except Exception as exc:
            log.exception("pipeline crashed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.camera.stop()

    def _handle(self, result: FrameResult) -> None:
        if not result.ok or result.embedding is None:
            self.gate_message.emit(result.gate.message, result.gate.reason)
            return
        embedding: FloatArray = result.embedding
        if self._capture_requested.is_set():
            self._capture_requested.clear()
            aligned = result.aligned.copy() if result.aligned is not None else None
            self.capture_ready.emit(Capture(embedding, aligned, float(result.blur or 0.0)))
            return
        if self._suppress_outcomes.is_set():
            return
        outcome: Outcome = self.recognizer.observe(embedding)
        self.outcome_ready.emit(outcome)


def annotate(frame: Array, result: FrameResult) -> Array:
    """Draw boxes and landmarks on a copy of the frame (UI thread helper)."""
    import cv2

    canvas = frame.copy()
    for detection in result.detections:
        x1, y1, x2, y2 = detection.as_int_box()
        color = (0, 200, 0) if result.face is detection and result.gate.ok else (0, 165, 255)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        for x, y in detection.kps:
            cv2.circle(canvas, (int(x), int(y)), 2, (255, 255, 0), -1)
    return canvas
