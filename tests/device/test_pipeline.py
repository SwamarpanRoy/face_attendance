"""The worker thread turns frames into gate messages, outcomes and captures (headless Qt)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer

from common.face.engine import FrameResult
from common.face.matcher import MatchConfig, Recognizer, Template, TemplateIndex
from common.face.quality import gate
from common.face.types import Detection
from device.app.camera import ImageSource
from device.app.pipeline import PipelineWorker, StageStats, annotate

FIXTURE = Path("tests/fixtures/faces/portrait_2.jpg")


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeEngine:
    """Alternates: frame 1 fails the size gate, every other frame yields the same embedding."""

    def __init__(self, embedding: np.ndarray) -> None:
        self.embedding = embedding
        self.calls = 0

    def process(self, frame, *, embed=True):
        self.calls += 1
        face = Detection(
            bbox=np.array([10, 10, 150, 190], np.float32),
            score=0.9,
            kps=np.zeros((5, 2), np.float32),
        )
        if self.calls == 1:
            return FrameResult(
                detections=[face], gate=gate("too_small"), face=face, timings_ms={"detect": 5.0}
            )
        return FrameResult(
            detections=[face],
            gate=gate("ok"),
            face=face,
            aligned=np.zeros((112, 112, 3), np.uint8),
            blur=100.0,
            embedding=self.embedding,
            timings_ms={"detect": 5.0, "align": 1.0, "embed": 8.0, "total": 15.0},
        )


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def _run_until(worker: PipelineWorker, done, timeout_ms: int = 5000) -> None:
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if done() else None)
    poll.start(20)
    worker.start()
    loop.exec_()
    poll.stop()
    worker.stop()


def test_worker_emits_gate_messages_outcomes_and_stats(qapp):
    identity = _unit(1)
    engine = FakeEngine(identity)
    index = TemplateIndex([Template(usn="1BM22EC001", name="Aditi", embedding=identity)])
    worker = PipelineWorker(
        ImageSource(FIXTURE, fps=200),
        engine,
        Recognizer(index, MatchConfig(0.5)),
        stats_every_s=0.1,
    )

    gates, outcomes, stats, frames = [], [], [], []
    worker.gate_message.connect(lambda m, r: gates.append((m, r)))
    worker.outcome_ready.connect(outcomes.append)
    worker.stats_ready.connect(stats.append)
    worker.frame_ready.connect(lambda f, r: frames.append(r))

    _run_until(worker, lambda: any(o.kind == "already_marked" for o in outcomes) and stats)

    assert gates[0] == ("Move closer", "too_small")
    kinds = [o.kind for o in outcomes]
    assert kinds[:2] == ["pending", "matched"]
    assert outcomes[1].usn == "1BM22EC001" and outcomes[1].name == "Aditi"
    assert "already_marked" in kinds and "not_recognised" not in kinds
    assert stats and "detect" in stats[-1] and stats[-1]["detect"] == (5.0, 5.0)
    assert worker.frames_seen >= len(outcomes) + 1
    assert frames and all(isinstance(r, FrameResult) for r in frames)


def test_request_capture_emits_the_next_good_embedding_without_matching(qapp):
    identity = _unit(2)
    worker = PipelineWorker(
        ImageSource(FIXTURE, fps=200),
        FakeEngine(identity),
        Recognizer(TemplateIndex([]), MatchConfig(0.5)),
    )
    captured, outcomes = [], []
    worker.capture_ready.connect(captured.append)
    worker.outcome_ready.connect(outcomes.append)
    worker.request_capture()

    _run_until(worker, lambda: len(captured) >= 1 and len(outcomes) >= 1)

    assert np.array_equal(captured[0].embedding, identity)
    assert captured[0].aligned is not None and captured[0].aligned.shape == (112, 112, 3)
    assert captured[0].blur == 100.0
    # The captured frame did not count as a recognition attempt; later frames did.
    assert all(o.kind in ("pending", "not_recognised") for o in outcomes)


def test_swap_recognizer_takes_effect_between_frames(qapp):
    identity = _unit(3)
    worker = PipelineWorker(
        ImageSource(FIXTURE, fps=200),
        FakeEngine(identity),
        Recognizer(TemplateIndex([]), MatchConfig(0.5)),
    )
    outcomes = []
    worker.outcome_ready.connect(outcomes.append)
    worker.swap_recognizer(
        Recognizer(TemplateIndex([Template("1BM22EC002", "Arjun", identity)]), MatchConfig(0.5))
    )
    _run_until(worker, lambda: any(o.kind == "matched" for o in outcomes))
    assert any(o.kind == "matched" and o.usn == "1BM22EC002" for o in outcomes)


def test_camera_failure_is_reported_not_raised(qapp):
    worker = PipelineWorker(
        ImageSource(Path("does/not/exist.jpg")),
        FakeEngine(_unit(4)),
        Recognizer(TemplateIndex([]), MatchConfig(0.5)),
    )
    errors = []
    worker.failed.connect(errors.append)
    _run_until(worker, lambda: bool(errors), timeout_ms=3000)
    assert errors and "no readable images" in errors[0]


def test_stage_stats_percentiles():
    stats = StageStats(window=10)
    for value in range(1, 11):
        stats.add({"detect": float(value)})
    assert stats.percentile("detect", 0.5) == 5.0
    assert stats.percentile("detect", 0.95) == 10.0
    assert stats.percentile("missing", 0.5) is None
    assert stats.summary() == {"detect": (5.0, 10.0)}


def test_annotate_draws_without_modifying_the_frame():
    frame = np.zeros((200, 200, 3), np.uint8)
    face = Detection(
        bbox=np.array([20, 20, 120, 150], np.float32),
        score=0.9,
        kps=np.full((5, 2), 60, np.float32),
    )
    result = FrameResult(detections=[face], gate=gate("ok"), face=face)
    canvas = annotate(frame, result)
    assert canvas.max() > 0 and frame.max() == 0
