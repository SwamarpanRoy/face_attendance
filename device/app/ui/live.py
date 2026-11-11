"""Live recognition screen, camera-first: full-bleed preview with floating glass overlays.

The video fills the screen edge to edge; the course line and present counter float on a
glass bar at the top and the result card ("Face matched – Name (USN)") floats over the
bottom of the video, so the camera stays the biggest thing on a 480x800 panel. Overlays
share the video's grid cell (later widgets paint on top), which needs no manual geometry.

All heavy work happens in :class:`PipelineWorker`; this widget only paints frames and
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
    QFrame,
    QGridLayout,
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
from device.app.ui.theme import font, set_props

log = logging.getLogger(__name__)

PORTRAIT = (720, 1280)
BANNER_TONES = ("neutral", "ok", "warn", "error")


class Banner(QLabel):
    """Result card that can hold a message for a while, then fall back."""

    def __init__(self) -> None:
        super().__init__("")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setFont(font(20, QFont.Bold))
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
        self.setVisible(bool(text))  # no empty glass card over the video
        set_props(self, role="banner", tone=kind if kind in BANNER_TONES else "neutral")
        if hold_ms:
            self._hold.start(hold_ms)

    def _release(self) -> None:
        text, kind = self._fallback
        self.show_message(text, kind)


def _overlay(widget: QWidget, *, top: bool) -> QWidget:
    """Transparent holder that insets an overlay from the video's edges."""
    holder = QWidget()
    layout = QVBoxLayout()
    layout.setContentsMargins(12, 12 if top else 0, 12, 0 if top else 12)
    layout.addWidget(widget)
    holder.setLayout(layout)
    return holder


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

        # ---- top glass bar: what is running + how many are in
        self.header = QLabel("")
        self.header.setFont(font(13, QFont.Medium))
        # Long one-line labels set the minimum width of the whole window (the stacked
        # pages share one size), so they must wrap: on a 480 px panel the header
        # "22EC72 Embedded Systems · ECE-7A · Period 6 15:00–16:00 · …" alone needed 660 px.
        self.header.setWordWrap(True)
        self.counter = QLabel("0 present")
        self.counter.setFont(font(14, QFont.Bold))
        self.counter.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        set_props(self.counter, role="badge", tone="ok")
        bar = QFrame()
        set_props(bar, role="glass")
        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(14, 10, 10, 10)
        bar_layout.setSpacing(10)
        bar_layout.addWidget(self.header, stretch=1)
        bar_layout.addWidget(self.counter, alignment=Qt.AlignVCenter)
        bar.setLayout(bar_layout)

        # ---- the camera, full bleed
        self.video = QLabel("Starting camera…")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setFont(font(15))
        # A QLabel holding a pixmap insists on at least the pixmap's size, and the pixmap
        # is scaled to the label's current size: with the default policy the preview
        # pins the whole window at its current size (or larger), which pushed the UI off
        # a 480x800 panel. Ignored = take whatever space the layout gives.
        self.video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.video.setMinimumSize(160, 120)
        set_props(self.video, role="video")
        self.banner = Banner()

        stage = QWidget()
        stage_layout = QGridLayout()
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(self.video, 0, 0)
        stage_layout.addWidget(_overlay(bar, top=True), 0, 0, Qt.AlignTop)
        stage_layout.addWidget(_overlay(self.banner, top=False), 0, 0, Qt.AlignBottom)
        stage.setLayout(stage_layout)

        # ---- below the video
        self.recent = QLabel("")
        self.recent.setFont(font(13))
        self.recent.setWordWrap(True)
        set_props(self.recent, role="muted")
        self.stats = QLabel("")
        set_props(self.stats, role="caption")
        self.stats.setWordWrap(True)  # updated from startup, even while hidden

        self.capture_button = QPushButton("Capture test template")
        self.capture_button.setMinimumHeight(64)
        self.capture_button.setFont(font(15, QFont.Medium))
        self.capture_button.clicked.connect(self._capture_clicked)
        self.capture_button.setVisible(show_capture_button)
        self.end_button = QPushButton("End session")
        self.end_button.setMinimumHeight(64)
        self.end_button.setFont(font(17, QFont.DemiBold))
        set_props(self.end_button, variant="destructive")
        self.end_button.clicked.connect(self.end_requested)
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addWidget(self.capture_button)
        buttons.addWidget(self.end_button)

        footer = QVBoxLayout()
        footer.setContentsMargins(16, 12, 16, 16)
        footer.setSpacing(8)
        footer.addWidget(self.recent)
        footer.addWidget(self.stats)
        footer.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(stage, stretch=1)
        layout.addLayout(footer)
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
        self.counter.setText(f"{self.present}{total} present")
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
        # Fill the stage (crop the overflow) rather than letterbox: full-bleed camera.
        pixmap = QPixmap.fromImage(image).scaled(
            self.video.width(),
            self.video.height(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
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
                f"✓  Face matched – {outcome.name} ({outcome.usn})", "ok", hold_ms=2500
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
