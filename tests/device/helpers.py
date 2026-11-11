"""Fakes and helpers shared by the device controller and QML tests."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QEventLoop, QTimer

from common.face.engine import FrameResult
from common.face.quality import gate
from common.face.types import Detection
from device.app.controller import DeviceController
from device.app.pins import FacultyRef

FIXTURE = Path("tests/fixtures/faces/portrait_2.jpg")
MODEL = "buffalo_s/w600k_mbf"
TEACHER = FacultyRef(7, "Prof", "faculty")
ADMIN = FacultyRef(1, "Admin", "admin")


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeEngine:
    """Always sees one good face whose embedding is ``identity``."""

    class _Liveness:
        name = "fake"

    liveness = _Liveness()

    def __init__(self, identity: np.ndarray) -> None:
        self.identity = identity

    def process(self, frame, *, embed=True):
        face = Detection(
            bbox=np.array([10, 10, 150, 190], np.float32),
            score=0.9,
            kps=np.zeros((5, 2), np.float32),
        )
        return FrameResult(
            detections=[face],
            gate=gate("ok"),
            face=face,
            aligned=np.zeros((112, 112, 3), np.uint8),
            blur=99.0,
            embedding=self.identity,
            timings_ms={"detect": 1.0, "total": 2.0},
        )


class FakeVerifier:
    """PIN 1234 is a teacher, 9999 the admin; anything else is wrong."""

    has_pins = True

    def verify(self, pin: str, *, now: float | None = None) -> FacultyRef | None:
        return {"1234": TEACHER, "9999": ADMIN}.get(pin)


def wait(predicate, timeout_ms: int = 5000) -> None:
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(20)
    loop.exec_()
    poll.stop()


def enter_pin(device: DeviceController, pin: str) -> None:
    assert device.screen == "pin"
    for digit in pin:
        device.pinKey(digit)
    device.pinSubmit()
