"""Live recognition screen: camera preview, result banner, counters, big buttons.

Portrait 720x1280 on the Pi's touch display; the simulator shows it at half size. All
heavy work happens in :class:`PipelineWorker`; this widget only paints frames and
reacts to signals. Recording a match is the controller's job (``matched`` signal), so
this screen is the same whether a real session is running or the device is in
"test recognition" mode.
"""

from __future__ import annotations

import logging
from collections import deque

import cv2
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from common.face.engine import FrameResult
from common.face.matcher import Outcome
from common.face.types import Array
from device.app.enrol import Capture
from device.app.pipeline import PipelineWorker, annotate

log = logging.getLogger(__name__)

PORTRAIT = (720, 1280)
KIND_STYLES = {
    "neutral": "background:#263238;color:#eceff1;",
    "ok": "background:#1b5e20;color:#ffffff;",
    "warn": "background:#e65100;color:#ffffff;",
    "error": "background:#b71c1c;color:#ffffff;",
}


class Banner(QLabel):
    """Large status line that can hold a message for a while, then fall back."""

    def __init__(self) -> None:
        super().__init__("")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(110)
        self.setFont(QFont("Sans", 20, QFont.Bold))
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._release)
        self._fallback = ("", "neutral")
        self.show_message("", "neutral")

    def show_message(self, text: str, kind: str = "neutral", hold_ms: int = 0) -> None:
        if self._hold.isActive() and hold_ms == 0:
            self._fallback = (text, kind)  # remember, show after the held message
            return
        self.setText(text)
        self.setStyleSheet(
            KIND_STYLES.get(kind, KIND_STYLES["neutral"]) + "padding:12px;border-radius:12px;"
        )
        if hold_ms:
            self._hold.start(hold_ms)

    def _release(self) -> None:
        text, kind = self._fallback
        self.show_message(text, kind)


class LiveScreen(QWidget):
    matched = pyqtSignal(object)  # Outcome with kind == "matched"
    end_requested = pyqtSignal()
    capture_test_requested = pyqtSignal()

    def __init__(self, worker: PipelineWorker, *, show_capture_button: bool = False) -> None:
        super().__init__()
        self.worker = worker
        self.marked: deque[str] = deque(maxlen=5)
        self.present = 0
        self.total = 0

        self.header = QLabel("")
        self.header.setFont(QFont("Sans", 13))
        self.header.setAlignment(Qt.AlignCenter)
        self.video = QLabel("Starting camera…")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video.setStyleSheet("background:#000;color:#9e9e9e;")
        self.banner = Banner()
        self.counter = QLabel("Present: 0")
        self.counter.setFont(QFont("Sans", 15, QFont.Bold))
        self.recent = QLabel("")
        self.recent.setFont(QFont("Sans", 11))
        self.recent.setWordWrap(True)
        self.recent.setStyleSheet("color:#546e7a;")
        self.stats = QLabel("")
        self.stats.setStyleSheet("color:#90a4ae;font-size:10px;")

        self.capture_button = QPushButton("Capture test template")
        self.capture_button.setMinimumHeight(56)
        self.capture_button.clicked.connect(self._capture_clicked)
        self.capture_button.setVisible(show_capture_button)
        self.end_button = QPushButton("End session")
        self.end_button.setMinimumHeight(64)
        self.end_button.setFont(QFont("Sans", 14, QFont.Bold))
        self.end_button.setStyleSheet("background:#b71c1c;color:#fff;border-radius:12px;")
        self.end_button.clicked.connect(self.end_requested)

        buttons = QHBoxLayout()
        buttons.addWidget(self.capture_button)
        buttons.addWidget(self.end_button)
        layout = QVBoxLayout()
        layout.addWidget(self.header)
        layout.addWidget(self.video, stretch=6)
        layout.addWidget(self.banner)
        layout.addWidget(self.counter)
        layout.addWidget(self.recent)
        layout.addWidget(self.stats)
        layout.addLayout(buttons)
        self.setLayout(layout)

        worker.frame_ready.connect(self.on_frame)
        worker.gate_message.connect(self.on_gate)
        worker.outcome_ready.connect(self.on_outcome)
        worker.stats_ready.connect(self.on_stats)
        worker.failed.connect(self.on_failed)

    # ------------------------------------------------------------------ controller API
    def begin(
        self, header: str, *, total: int, present: int, recent: list[str], end_label: str
    ) -> None:
        self.header.setText(header)
        self.total = total
        self.present = present
        self.marked = deque(recent, maxlen=5)
        self.end_button.setText(end_label)
        self._refresh_counters()
        self.banner.show_message("Stand in front of the camera", "neutral")

    def _refresh_counters(self) -> None:
        total = f" / {self.total}" if self.total else ""
        self.counter.setText(f"Present: {self.present}{total}")
        self.recent.setText("Recent: " + " · ".join(self.marked) if self.marked else "")

    # ------------------------------------------------------------------ slots
    @pyqtSlot(object, object)
    def on_frame(self, frame: Array, result: FrameResult) -> None:
        if not self.isVisible():
            return
        canvas = annotate(frame, result)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.video.width(), self.video.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.video.setPixmap(pixmap)

    @pyqtSlot(str, str)
    def on_gate(self, message: str, reason: str) -> None:
        self.banner.show_message(message, "neutral")

    @pyqtSlot(object)
    def on_outcome(self, outcome: Outcome) -> None:
        if outcome.kind == "matched" and outcome.usn:
            self.present += 1
            self.marked.appendleft(f"{outcome.name} ({outcome.usn})")
            self._refresh_counters()
            self.banner.show_message(
                f"Face matched – {outcome.name} ({outcome.usn})", "ok", hold_ms=2500
            )
            log.info("matched %s score=%.3f", outcome.usn, outcome.score or 0.0)
            self.matched.emit(outcome)
        elif outcome.kind == "already_marked":
            self.banner.show_message(f"Already marked – {outcome.name}", "warn", hold_ms=1500)
        elif outcome.kind == "not_recognised":
            self.banner.show_message("Not recognised – try again", "error", hold_ms=1500)
        else:
            self.banner.show_message("Hold on…", "neutral")

    @pyqtSlot(object)
    def on_capture_ready(self, capture: Capture) -> None:
        self.banner.show_message("Test template captured", "ok", hold_ms=1500)

    @pyqtSlot(object)
    def on_stats(self, summary: dict[str, tuple[float, float]]) -> None:
        parts = [f"{stage} p50 {p50:.0f}/p95 {p95:.0f} ms" for stage, (p50, p95) in summary.items()]
        self.stats.setText("  ".join(parts))

    @pyqtSlot(str)
    def on_failed(self, message: str) -> None:
        self.video.setText(f"Camera / pipeline error:\n{message}")
        self.banner.show_message("Device error – see log", "error")

    def _capture_clicked(self) -> None:
        self.banner.show_message("Look at the camera…", "neutral")
        self.capture_test_requested.emit()
