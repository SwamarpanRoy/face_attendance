"""Walks a whole session through the main window headlessly (offscreen Qt, fake engine).

PIN dialogs are modal, so the test patches the window's PIN prompt to return a faculty
member directly; everything else runs through the real controller, screens, session
manager, store and pipeline worker.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication

from common.face.engine import FrameResult
from common.face.matcher import MatchConfig, Recognizer, TemplateIndex
from common.face.quality import gate
from common.face.types import Detection
from device.app.camera import ImageSource
from device.app.config import DeviceConfig
from device.app.pins import FacultyRef, PinVerifier
from device.app.pipeline import PipelineWorker
from device.app.session import SessionManager
from device.app.store import DeviceStore
from device.app.ui.main_window import MainWindow

FIXTURE = Path("tests/fixtures/faces/portrait_2.jpg")
MODEL = "buffalo_s/w600k_mbf"


def _unit(seed: int) -> np.ndarray:
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


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(predicate, timeout_ms: int = 5000) -> None:
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(20)
    loop.exec_()
    poll.stop()


@pytest.fixture
def world(tmp_path, qapp):
    store = DeviceStore(tmp_path / "device.db")
    store.put_catalog("courses", [{"id": 1, "code": "22EC71", "name": "VLSI"}])
    store.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}])
    store.put_catalog(
        "periods",
        [{"id": 1, "ordinal": 1, "name": "P1", "start_time": "09:00", "end_time": "10:00"}],
    )
    store.put_catalog("offerings", [{"id": 1, "course_id": 1, "section_id": 1, "faculty_id": 7}])
    store.put_catalog(
        "roster:1", [{"id": i, "usn": f"1BM22EC00{i}", "name": f"S{i}"} for i in range(1, 4)]
    )
    identity = _unit(1)
    store.add_template("1BM22EC001", "Aditi", identity, MODEL, section_id=1)
    config = DeviceConfig()
    worker = PipelineWorker(
        ImageSource(FIXTURE, fps=120),
        FakeEngine(identity),
        Recognizer(TemplateIndex([]), MatchConfig(0.4)),
    )  # type: ignore[arg-type]
    window = MainWindow(
        config=config,
        store=store,
        worker=worker,
        sessions=SessionManager(store, config),
        client=None,
        verifier=PinVerifier(store),
        network_info=lambda: {"ssid": "test", "ip": "10.0.0.2"},
        clock_synced=lambda: True,
    )
    teacher = FacultyRef(7, "Prof", "faculty")
    window._ask_pin = lambda title, admin=False: teacher  # type: ignore[method-assign]
    window.show()
    window.start_workers()
    yield window, store, worker
    window.close()
    worker.stop()
    store.close()


def test_full_session_records_events_and_restores_cooldown_on_resume(world):
    window, store, _ = world
    assert window.stack.currentIndex() == window.IDLE
    assert "Not calibrated" in window.idle.warning.text()

    window.start_session_flow()
    assert window.stack.currentIndex() == window.START
    window.start._next()  # course -> section
    window.start._next()  # section -> period (templates exist, so no blocker)
    window.start._next()  # start
    assert window.stack.currentIndex() == window.LIVE and window.session is not None
    assert window.live.total == 3
    assert (
        "22EC71 VLSI" in window.live.header.text() and "NOT CALIBRATED" in window.live.header.text()
    )

    _wait(lambda: store.pending_count() >= 1)
    assert store.pending_count() == 1
    event = store.pending_events()[0]
    assert event["usn"] == "1BM22EC001" and event["session_uuid"] == window.session.uuid
    _wait(lambda: "Already marked" in window.live.banner.text(), timeout_ms=4000)
    assert store.pending_count() == 1  # cooldown: the same face is not recorded twice
    assert window.live.present == 1 and "Aditi" in window.live.recent.text()

    # Simulate a crash: a fresh window over the same store offers to resume, cooldown intact.
    session_uuid = window.session.uuid
    window.session = None
    window.show_idle()
    assert window.idle.resume_button.isVisible()
    window.resume_session()
    assert window.session is not None and window.session.uuid == session_uuid
    assert window.live.present == 1
    _wait(lambda: "Already marked" in window.live.banner.text(), timeout_ms=4000)
    assert store.pending_count() == 1

    window.end_session_flow()
    assert window.stack.currentIndex() == window.IDLE and window.session is None
    assert store.get_session(session_uuid)["ended_at"] is not None
    assert not window.idle.resume_button.isVisible()


def test_start_is_blocked_for_a_section_without_templates(world):
    window, store, _ = world
    store.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}, {"id": 2, "name": "ECE-7B"}])
    store.put_catalog("offerings", [{"id": 1, "course_id": 1, "section_id": 2, "faculty_id": 7}])
    window.start_session_flow()
    window.start._next()  # course
    window.start._next()  # section ECE-7B has no templates -> blocked
    assert window.stack.currentIndex() == window.START
    assert "not been synced" in window.start.hint.text()


def test_settings_shows_fallback_thresholds_and_test_mode_records_nothing(world):
    window, store, _ = world
    window.open_settings()
    assert window.stack.currentIndex() == window.SETTINGS
    assert (
        "fallback" in window.settings.info.text()
        and "NOT CALIBRATED" in window.settings.calibration_warning.text()
    )
    window.show_idle()
    window.begin_test_mode()
    _wait(
        lambda: (
            "Face matched" in window.live.banner.text()
            or "Already marked" in window.live.banner.text()
        )
    )
    assert store.pending_count() == 0
