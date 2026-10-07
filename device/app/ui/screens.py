"""Idle, Start-session and Settings screens (portrait, touch-first, no typing)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from device.app.sync import SyncStatus
from device.app.ui.pinpad import BUTTON_HEIGHT

CHIP_ON = "background:#1b5e20;color:#fff;padding:4px 10px;border-radius:10px;"
CHIP_OFF = "background:#b71c1c;color:#fff;padding:4px 10px;border-radius:10px;"
CHIP_WARN = "background:#e65100;color:#fff;padding:4px 10px;border-radius:10px;"


def big_button(text: str, *, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(BUTTON_HEIGHT + 8 if primary else BUTTON_HEIGHT)
    button.setFont(QFont("Sans", 16 if primary else 14, QFont.Bold if primary else QFont.Normal))
    if primary:
        button.setStyleSheet("background:#1d4ed8;color:#fff;border-radius:12px;")
    return button


def _format_time(value: str | datetime | None) -> str:
    if value is None:
        return "never"
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return when.astimezone().strftime("%d %b %H:%M")


class IdleScreen(QWidget):
    start_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    test_requested = pyqtSignal()
    enrol_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, device_name: str, network_info: Callable[[], dict[str, str | None]]) -> None:
        super().__init__()
        self._network_info = network_info
        title = QLabel(device_name)
        title.setFont(QFont("Sans", 22, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        self.clock = QLabel("")
        self.clock.setFont(QFont("Sans", 40, QFont.Bold))
        self.clock.setAlignment(Qt.AlignCenter)
        self.date = QLabel("")
        self.date.setAlignment(Qt.AlignCenter)

        self.server_chip = QLabel("server: unknown")
        self.server_chip.setStyleSheet(CHIP_OFF)
        self.queue_label = QLabel("Queued records: 0")
        self.sync_label = QLabel("Last sync: never")
        self.network_label = QLabel("")
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color:#e65100;font-weight:bold;")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)

        info = QGridLayout()
        info.addWidget(self.server_chip, 0, 0)
        info.addWidget(self.queue_label, 0, 1)
        info.addWidget(self.sync_label, 1, 0, 1, 2)
        info.addWidget(self.network_label, 2, 0, 1, 2)
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        box.setLayout(info)

        self.start_button = big_button("Start session", primary=True)
        self.start_button.clicked.connect(self.start_requested)
        self.resume_button = big_button("Resume session", primary=True)
        self.resume_button.clicked.connect(self.resume_requested)
        self.resume_button.setVisible(False)
        self.test_button = big_button("Test recognition (no session)")
        self.test_button.clicked.connect(self.test_requested)
        self.test_button.setVisible(False)
        enrol = big_button("Enrol")
        enrol.clicked.connect(self.enrol_requested)
        settings = big_button("Settings")
        settings.clicked.connect(self.settings_requested)
        row = QHBoxLayout()
        row.addWidget(enrol)
        row.addWidget(settings)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(self.clock)
        layout.addWidget(self.date)
        layout.addWidget(box)
        layout.addWidget(self.warning)
        layout.addWidget(self.message)
        layout.addStretch(1)
        layout.addWidget(self.resume_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.test_button)
        layout.addLayout(row)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()
        self._network_timer = QTimer(self)
        self._network_timer.timeout.connect(self.refresh_network)
        self._network_timer.start(10_000)
        self.refresh_network()

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.date.setText(now.strftime("%A, %d %B %Y"))

    def refresh_network(self) -> None:
        info = self._network_info()
        self.network_label.setText(f"WiFi: {info.get('ssid') or '–'}   IP: {info.get('ip') or '–'}")

    def show_status(
        self, status: SyncStatus | None, *, calibrated: bool, clock_synced: bool
    ) -> None:
        if status is None:
            self.server_chip.setText("server: not configured")
            self.server_chip.setStyleSheet(CHIP_WARN)
        elif status.revoked:
            self.server_chip.setText("server: DEVICE REVOKED")
            self.server_chip.setStyleSheet(CHIP_OFF)
        elif status.online:
            self.server_chip.setText("server: online")
            self.server_chip.setStyleSheet(CHIP_ON)
        else:
            retry = f", retry in {int(status.retry_in_s)} s" if status.retry_in_s else ""
            self.server_chip.setText(f"server: offline{retry}")
            self.server_chip.setStyleSheet(CHIP_OFF)
        queue = status.queue_len if status else 0
        self.queue_label.setText(f"Queued records: {queue}")
        self.sync_label.setText(
            f"Last sync: {_format_time(status.last_sync_at) if status else 'never'}"
        )
        warnings = []
        if not calibrated:
            warnings.append("Not calibrated: using provisional thresholds.")
        if not clock_synced:
            warnings.append("Clock not synced (no NTP): record times will be flagged.")
        self.warning.setText(" ".join(warnings))

    def set_resumable(self, resumable: bool) -> None:
        self.resume_button.setVisible(resumable)

    def set_test_mode_available(self, available: bool) -> None:
        self.test_button.setVisible(available)


class StartSessionScreen(QWidget):
    """Course -> section -> period, each a big list; emits the ids when confirmed."""

    chosen = pyqtSignal(int, int, object)  # course_id, section_id, period_id | None
    cancelled = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.title = QLabel("Choose the course")
        self.title.setFont(QFont("Sans", 18, QFont.Bold))
        self.title.setAlignment(Qt.AlignCenter)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#b71c1c;")
        self.pages = QStackedWidget()
        self.course_list = self._list()
        self.section_list = self._list()
        self.period_list = self._list()
        for widget in (self.course_list, self.section_list, self.period_list):
            self.pages.addWidget(widget)
        back = big_button("Back")
        back.clicked.connect(self._back)
        self.next_button = big_button("Next", primary=True)
        self.next_button.clicked.connect(self._next)
        row = QHBoxLayout()
        row.addWidget(back)
        row.addWidget(self.next_button)
        layout = QVBoxLayout()
        layout.addWidget(self.title)
        layout.addWidget(self.pages, stretch=1)
        layout.addWidget(self.hint)
        layout.addLayout(row)
        self.setLayout(layout)
        self._sections_for: Callable[[int], list[dict[str, Any]]] = lambda _c: []
        self._blocker_for: Callable[[int], str | None] = lambda _s: None

    @staticmethod
    def _list() -> QListWidget:
        widget = QListWidget()
        widget.setFont(QFont("Sans", 15))
        widget.setStyleSheet("QListWidget::item { padding: 14px; }")
        return widget

    def load(
        self,
        courses: list[dict[str, Any]],
        sections_for: Callable[[int], list[dict[str, Any]]],
        periods: list[dict[str, Any]],
        blocker_for: Callable[[int], str | None],
    ) -> None:
        self._sections_for = sections_for
        self._blocker_for = blocker_for
        self.course_list.clear()
        for course in courses:
            item = QListWidgetItem(f"{course['code']}  {course['name']}")
            item.setData(Qt.UserRole, int(course["id"]))
            self.course_list.addItem(item)
        self.period_list.clear()
        none_item = QListWidgetItem("No specific period")
        none_item.setData(Qt.UserRole, None)
        self.period_list.addItem(none_item)
        for period in periods:
            item = QListWidgetItem(f"{period['name']}  {period['start_time']}–{period['end_time']}")
            item.setData(Qt.UserRole, int(period["id"]))
            self.period_list.addItem(item)
        self.pages.setCurrentIndex(0)
        self.title.setText("Choose the course")
        self.hint.setText(
            "" if courses else "No courses synced yet. Connect to WiFi and use Settings > Sync now."
        )
        self.next_button.setText("Next")
        if self.course_list.count():
            self.course_list.setCurrentRow(0)

    def _selected(self, widget: QListWidget) -> Any:
        item = widget.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _next(self) -> None:
        page = self.pages.currentIndex()
        if page == 0:
            course_id = self._selected(self.course_list)
            if course_id is None:
                return
            self.section_list.clear()
            for section in self._sections_for(int(course_id)):
                item = QListWidgetItem(str(section["name"]))
                item.setData(Qt.UserRole, int(section["id"]))
                self.section_list.addItem(item)
            if self.section_list.count():
                self.section_list.setCurrentRow(0)
            self.title.setText("Choose the section")
            self.hint.setText("")
            self.pages.setCurrentIndex(1)
        elif page == 1:
            section_id = self._selected(self.section_list)
            if section_id is None:
                return
            blocker = self._blocker_for(int(section_id))
            if blocker:
                self.hint.setText(blocker)
                return
            self.hint.setText("")
            self.title.setText("Choose the period")
            self.period_list.setCurrentRow(0)
            self.next_button.setText("Start")
            self.pages.setCurrentIndex(2)
        else:
            course_id = self._selected(self.course_list)
            section_id = self._selected(self.section_list)
            period_id = self._selected(self.period_list)
            self.chosen.emit(int(course_id), int(section_id), period_id)

    def _back(self) -> None:
        page = self.pages.currentIndex()
        if page == 0:
            self.cancelled.emit()
            return
        self.pages.setCurrentIndex(page - 1)
        self.title.setText("Choose the course" if page == 1 else "Choose the section")
        self.next_button.setText("Next")
        self.hint.setText("")


class SettingsScreen(QWidget):
    sync_requested = pyqtSignal()
    test_camera_requested = pyqtSignal()
    restart_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        title = QLabel("Settings")
        title.setFont(QFont("Sans", 18, QFont.Bold))
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setFont(QFont("Monospace", 10))
        self.calibration_warning = QLabel("")
        self.calibration_warning.setWordWrap(True)
        self.calibration_warning.setStyleSheet("color:#e65100;font-weight:bold;")
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)

        sync = big_button("Sync now")
        sync.clicked.connect(self.sync_requested)
        camera = big_button("Test camera")
        camera.clicked.connect(self.test_camera_requested)
        restart = big_button("Restart app")
        restart.clicked.connect(self.restart_requested)
        back = big_button("Back", primary=True)
        back.clicked.connect(self.back_requested)
        grid = QGridLayout()
        grid.addWidget(sync, 0, 0)
        grid.addWidget(camera, 0, 1)
        grid.addWidget(restart, 1, 0)
        grid.addWidget(back, 1, 1)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(self.info)
        layout.addWidget(self.calibration_warning)
        layout.addWidget(self.feedback)
        layout.addStretch(1)
        layout.addLayout(grid)
        self.setLayout(layout)

    def show_info(self, rows: dict[str, str], *, calibrated: bool) -> None:
        width = max(len(k) for k in rows) if rows else 0
        self.info.setText("\n".join(f"{k.ljust(width)}  {v}" for k, v in rows.items()))
        self.calibration_warning.setText(
            ""
            if calibrated
            else "NOT CALIBRATED: run tools/calibrate.py on the server, then Sync now."
        )

    def show_feedback(self, text: str) -> None:
        self.feedback.setText(text)
