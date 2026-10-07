"""Enrolment wizard: pick student -> consent -> three captures -> upload.

All decisions live in :class:`device.app.enrol.EnrolmentFlow`; this dialog only shows
pages and forwards taps. It listens to the pipeline worker for frames and captures
while it is open and asks the worker for one more capture after each accepted one.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

import cv2
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from common.face.engine import FrameResult
from common.face.types import Array
from common.schemas import EnrolmentCapturesOut, RosterStudentOut
from device.app.client import EnrolmentRejectedError, ServerUnavailableError
from device.app.enrol import Capture, EnrolmentFlow
from device.app.pins import FacultyRef
from device.app.pipeline import PipelineWorker, annotate
from device.app.ui.pinpad import BUTTON_HEIGHT, NumberPad

log = logging.getLogger(__name__)
CAPTURE_GAP_MS = 700


def _big(button: QPushButton) -> QPushButton:
    button.setMinimumHeight(BUTTON_HEIGHT)
    button.setFont(QFont("Sans", 14))
    return button


class EnrolDialog(QDialog):
    PAGE_PICK, PAGE_CONSENT, PAGE_CAPTURE, PAGE_DONE = range(4)

    def __init__(
        self,
        flow: EnrolmentFlow,
        worker: PipelineWorker,
        faculty: FacultyRef,
        *,
        on_enrolled: Callable[[EnrolmentCapturesOut], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enrol a student")
        self.setModal(True)
        self.flow = flow
        self.worker = worker
        self.faculty = faculty
        self._on_enrolled = on_enrolled
        self._roster: list[RosterStudentOut] = []
        self._selected: RosterStudentOut | None = None
        self.result_out: EnrolmentCapturesOut | None = None

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_pick_page())
        self.pages.addWidget(self._build_consent_page())
        self.pages.addWidget(self._build_capture_page())
        self.pages.addWidget(self._build_done_page())
        layout = QVBoxLayout()
        layout.addWidget(self.pages)
        self.setLayout(layout)
        self.resize(360, 640)

        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self.worker.request_capture)
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.capture_ready.connect(self.on_capture)
        self.worker.gate_message.connect(self.on_gate)
        self._load_sections()

    # ------------------------------------------------------------------ page builders
    def _build_pick_page(self) -> QWidget:
        page = QWidget()
        title = QLabel("Pick the student")
        title.setFont(QFont("Sans", 16, QFont.Bold))
        self.section_box = QComboBox()
        self.section_box.setMinimumHeight(48)
        self.section_box.currentIndexChanged.connect(lambda _i: self._load_roster())
        self.search_pad = NumberPad(max_length=3)
        self.search_pad.changed.connect(self._filter_roster)
        self.search_pad.submitted.connect(lambda _t: self._choose_highlighted())
        self.roster_list = QListWidget()
        self.roster_list.setFont(QFont("Sans", 13))
        self.roster_list.itemClicked.connect(lambda item: self._choose(item))
        hint = QLabel("Type the last digits of the USN, or tap a name.")
        hint.setWordWrap(True)
        cancel = _big(QPushButton("Cancel"))
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(self.section_box)
        layout.addWidget(hint)
        layout.addWidget(self.roster_list, stretch=2)
        layout.addWidget(self.search_pad, stretch=1)
        layout.addWidget(cancel)
        page.setLayout(layout)
        return page

    def _build_consent_page(self) -> QWidget:
        page = QWidget()
        self.consent_title = QLabel("Consent")
        self.consent_title.setFont(QFont("Sans", 16, QFont.Bold))
        self.consent_text = QTextEdit()
        self.consent_text.setReadOnly(True)
        self.consent_text.setFont(QFont("Sans", 12))
        self.consent_version_label = QLabel("")
        agree = _big(QPushButton("I consent"))
        agree.setStyleSheet("background:#1b5e20;color:#fff;")
        agree.clicked.connect(self._consent_given)
        decline = _big(QPushButton("Decline"))
        decline.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(decline)
        buttons.addWidget(agree)
        layout = QVBoxLayout()
        layout.addWidget(self.consent_title)
        layout.addWidget(self.consent_text, stretch=1)
        layout.addWidget(self.consent_version_label)
        layout.addLayout(buttons)
        page.setLayout(layout)
        return page

    def _build_capture_page(self) -> QWidget:
        page = QWidget()
        self.capture_title = QLabel("Look at the camera")
        self.capture_title.setFont(QFont("Sans", 16, QFont.Bold))
        self.capture_title.setAlignment(Qt.AlignCenter)
        self.video = QLabel("")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumHeight(300)
        self.video.setStyleSheet("background:#000;")
        self.capture_status = QLabel("")
        self.capture_status.setAlignment(Qt.AlignCenter)
        self.capture_status.setFont(QFont("Sans", 14))
        cancel = _big(QPushButton("Cancel"))
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self.capture_title)
        layout.addWidget(self.video, stretch=1)
        layout.addWidget(self.capture_status)
        layout.addWidget(cancel)
        page.setLayout(layout)
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        self.done_title = QLabel("")
        self.done_title.setFont(QFont("Sans", 16, QFont.Bold))
        self.done_title.setAlignment(Qt.AlignCenter)
        self.done_text = QLabel("")
        self.done_text.setWordWrap(True)
        self.done_text.setAlignment(Qt.AlignCenter)
        self.retry_button = _big(QPushButton("Try again"))
        self.retry_button.clicked.connect(self._retry)
        close = _big(QPushButton("Close"))
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(close)
        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(self.done_title)
        layout.addWidget(self.done_text)
        layout.addStretch(1)
        layout.addLayout(buttons)
        page.setLayout(layout)
        return page

    # ------------------------------------------------------------------ pick page
    def _load_sections(self) -> None:
        self.section_box.blockSignals(True)
        self.section_box.clear()
        for section in self.flow.sections():
            self.section_box.addItem(str(section["name"]), int(section["id"]))
        self.section_box.blockSignals(False)
        if self.section_box.count() == 0:
            self.roster_list.addItem("No sections synced yet. Connect to WiFi and use Sync now.")
        else:
            self._load_roster()

    def _load_roster(self) -> None:
        section_id = self.section_box.currentData()
        if section_id is None:
            return
        self._roster = self.flow.roster(int(section_id))
        self._filter_roster(self.search_pad.text)

    def _filter_roster(self, digits: str) -> None:
        self.roster_list.clear()
        for student in self._roster:
            if digits and not student.usn.endswith(digits):
                continue
            label = f"{student.usn}  {student.name}"
            if student.template_count:
                label += f"  (enrolled x{student.template_count})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, student.usn)
            self.roster_list.addItem(item)
        if self.roster_list.count() == 1:
            self.roster_list.setCurrentRow(0)

    def _choose_highlighted(self) -> None:
        item = self.roster_list.currentItem()
        if item is not None:
            self._choose(item)

    def _choose(self, item: QListWidgetItem) -> None:
        usn = item.data(Qt.UserRole)
        student = next((s for s in self._roster if s.usn == usn), None)
        if student is None:
            return
        self._selected = student
        self.flow.start(student, self.faculty.id)
        version, notice = self.flow.consent_text()
        self.consent_title.setText(f"{student.name} ({student.usn})")
        self.consent_text.setPlainText(notice)
        self.consent_version_label.setText(f"Consent version {version}")
        self.pages.setCurrentIndex(self.PAGE_CONSENT)

    # ------------------------------------------------------------------ consent + capture
    def _consent_given(self) -> None:
        self.flow.give_consent()
        self.pages.setCurrentIndex(self.PAGE_CAPTURE)
        self._update_capture_status()
        self.worker.request_capture()

    def _update_capture_status(self) -> None:
        if self.flow.state is None:
            return
        have = len(self.flow.state.captures)
        self.capture_status.setText(
            f"Capture {min(have + 1, self.flow.required)} of {self.flow.required}"
        )

    @pyqtSlot(object, object)
    def on_frame(self, frame: Array, result: FrameResult) -> None:
        if self.pages.currentIndex() != self.PAGE_CAPTURE:
            return
        rgb = cv2.cvtColor(annotate(frame, result), cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()
        self.video.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.video.width(), self.video.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    @pyqtSlot(str, str)
    def on_gate(self, message: str, reason: str) -> None:
        if self.pages.currentIndex() == self.PAGE_CAPTURE and message:
            self.capture_title.setText(message)

    @pyqtSlot(object)
    def on_capture(self, capture: Capture) -> None:
        if self.pages.currentIndex() != self.PAGE_CAPTURE or self.flow.state is None:
            return
        remaining = self.flow.add_capture(capture)
        self.capture_title.setText("Captured")
        self._update_capture_status()
        if remaining > 0:
            self._capture_timer.start(CAPTURE_GAP_MS)
            return
        self._upload()

    def _upload(self) -> None:
        self.capture_status.setText("Uploading…")
        try:
            result = self.flow.upload()
        except EnrolmentRejectedError as exc:
            self._show_done("Enrolment refused", exc.detail, retry=True)
            return
        except ServerUnavailableError as exc:
            self._show_done(
                "Server unreachable", f"{exc}. Connect to WiFi and try again.", retry=True
            )
            return
        except Exception as exc:
            log.exception("enrolment upload failed")
            self._show_done("Enrolment failed", f"{type(exc).__name__}: {exc}", retry=True)
            return
        self.result_out = result
        self._show_done(
            "Enrolled",
            f"{result.usn}: {result.templates_added} templates added "
            f"({result.total_templates} total). The device can recognise them now.",
            retry=False,
        )
        if self._on_enrolled:
            self._on_enrolled(result)

    def _show_done(self, title: str, text: str, *, retry: bool) -> None:
        self.done_title.setText(title)
        self.done_text.setText(text)
        self.retry_button.setVisible(retry)
        self.pages.setCurrentIndex(self.PAGE_DONE)

    def _retry(self) -> None:
        if self._selected is None:
            self.pages.setCurrentIndex(self.PAGE_PICK)
            return
        self.flow.start(self._selected, self.faculty.id)
        self.pages.setCurrentIndex(self.PAGE_CONSENT)

    def done(self, result: int) -> None:
        self._capture_timer.stop()
        self.flow.discard()
        for signal, slot in (
            (self.worker.frame_ready, self.on_frame),
            (self.worker.capture_ready, self.on_capture),
            (self.worker.gate_message, self.on_gate),
        ):
            with contextlib.suppress(TypeError):
                signal.disconnect(slot)
        super().done(result)
