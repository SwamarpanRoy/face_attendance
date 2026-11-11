"""Idle, Start-session and Settings screens (portrait 480x800, touch-first, no typing).

Layout only: colours and shapes come from :mod:`device.app.ui.theme`. Every label that
shows run-time text wraps, because the stacked pages share one width and a single long
line would push the window past a 480 px panel.
"""

from __future__ import annotations

import html
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from device.app.sync import SyncStatus
from device.app.ui.pinpad import BUTTON_HEIGHT
from device.app.ui.theme import COLORS, font, set_props

MARGIN = 20


def big_button(text: str, *, primary: bool = False, variant: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(BUTTON_HEIGHT + 8 if primary else BUTTON_HEIGHT)
    button.setFont(font(18 if primary else 16, QFont.DemiBold if primary else QFont.Medium))
    chosen = variant or ("primary" if primary else None)
    if chosen:
        set_props(button, variant=chosen)
    return button


def _label(text: str = "", *, px: int = 15, weight: int = QFont.Normal, role: str = "") -> QLabel:
    label = QLabel(text)
    label.setFont(font(px, weight))
    label.setWordWrap(True)
    if role:
        set_props(label, role=role)
    return label


def _format_time(value: str | datetime | None) -> str:
    if value is None:
        return "never"
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return when.astimezone().strftime("%d %b %H:%M")


def _stat_tile(caption: str, value: QLabel) -> QFrame:
    """Small card: big value over a muted caption."""
    tile = QFrame()
    set_props(tile, role="card")
    layout = QVBoxLayout()
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(2)
    layout.addWidget(value)
    layout.addWidget(_label(caption, px=12, role="subtle"))
    tile.setLayout(layout)
    return tile


class IdleScreen(QWidget):
    start_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    test_requested = pyqtSignal()
    enrol_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, device_name: str, network_info: Callable[[], dict[str, str | None]]) -> None:
        super().__init__()
        self._network_info = network_info

        name = _label(device_name, px=14, weight=QFont.DemiBold, role="muted")
        self.server_chip = _label("● Connecting…", px=13)
        self.server_chip.setWordWrap(False)
        self.server_chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        set_props(self.server_chip, role="badge")
        header = QHBoxLayout()
        header.addWidget(name, stretch=1)
        header.addWidget(self.server_chip, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.clock = QLabel("")
        self.clock.setFont(font(96, QFont.Light))
        self.clock.setAlignment(Qt.AlignCenter)
        self.date = _label("", px=16, role="muted")
        self.date.setAlignment(Qt.AlignCenter)

        self.queue_label = _label("0", px=22, weight=QFont.DemiBold)
        self.sync_label = _label("never", px=22, weight=QFont.DemiBold)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        tiles.addWidget(_stat_tile("Waiting to upload", self.queue_label), stretch=1)
        tiles.addWidget(_stat_tile("Last sync", self.sync_label), stretch=1)
        self.network_label = _label("", px=13, role="subtle")
        self.network_label.setAlignment(Qt.AlignCenter)

        self.warning = _label("", px=14)
        set_props(self.warning, role="alert", tone="warn")
        self.warning.setVisible(False)  # an empty alert would still draw its box
        self.message = _label("", px=14, role="muted")
        self.message.setAlignment(Qt.AlignCenter)

        self.start_button = big_button("Start session", primary=True)
        self.start_button.setMinimumHeight(76)
        self.start_button.clicked.connect(self.start_requested)
        self.resume_button = big_button("Resume session", primary=True, variant="accent")
        self.resume_button.setMinimumHeight(76)
        self.resume_button.clicked.connect(self.resume_requested)
        self.resume_button.setVisible(False)
        self.test_button = big_button("Test recognition (no session)")
        self.test_button.clicked.connect(self.test_requested)
        self.test_button.setVisible(False)
        enrol = big_button("+  Enrol")
        enrol.clicked.connect(self.enrol_requested)
        settings = big_button("⚙  Settings")
        settings.clicked.connect(self.settings_requested)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(enrol)
        row.addWidget(settings)

        layout = QVBoxLayout()
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addStretch(2)
        layout.addWidget(self.clock)
        layout.addWidget(self.date)
        layout.addStretch(2)
        layout.addLayout(tiles)
        layout.addWidget(self.network_label)
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
        self.clock.setText(now.strftime("%H:%M"))
        self.date.setText(now.strftime("%A, %d %B"))

    def refresh_network(self) -> None:
        info = self._network_info()
        self.network_label.setText(f"WiFi {info.get('ssid') or '–'}  ·  IP {info.get('ip') or '–'}")

    def show_status(
        self, status: SyncStatus | None, *, calibrated: bool, clock_synced: bool
    ) -> None:
        if status is None:
            self.server_chip.setText("● Server not set up")
            set_props(self.server_chip, tone="warn")
        elif status.revoked:
            self.server_chip.setText("● Device revoked")
            set_props(self.server_chip, tone="error")
        elif status.online:
            self.server_chip.setText("● Online")
            set_props(self.server_chip, tone="ok")
        else:
            retry = f" · retry {int(status.retry_in_s)} s" if status.retry_in_s else ""
            self.server_chip.setText(f"● Offline{retry}")
            set_props(self.server_chip, tone="error")
        queue = status.queue_len if status else 0
        self.queue_label.setText(str(queue))
        self.sync_label.setText(_format_time(status.last_sync_at) if status else "never")
        warnings = []
        if not calibrated:
            warnings.append("Not calibrated: using provisional thresholds.")
        if not clock_synced:
            warnings.append("Clock not synced (no NTP): record times will be flagged.")
        self.warning.setText(" ".join(warnings))
        self.warning.setVisible(bool(warnings))

    def set_resumable(self, resumable: bool) -> None:
        self.resume_button.setVisible(resumable)

    def set_test_mode_available(self, available: bool) -> None:
        self.test_button.setVisible(available)


class StartSessionScreen(QWidget):
    """Course -> section -> period, each a list of big cards; emits the ids when confirmed."""

    chosen = pyqtSignal(int, int, object)  # course_id, section_id, period_id | None
    cancelled = pyqtSignal()

    STEPS = ("Choose the course", "Choose the section", "Choose the period")

    def __init__(self) -> None:
        super().__init__()
        self.step_label = _label("", px=12, weight=QFont.DemiBold, role="eyebrow")
        self.step_bars: list[QFrame] = []
        bars = QHBoxLayout()
        bars.setSpacing(6)
        for _ in self.STEPS:
            bar = QFrame()
            bar.setFixedHeight(4)
            set_props(bar, role="step")
            self.step_bars.append(bar)
            bars.addWidget(bar)
        self.title = _label(self.STEPS[0], px=28, weight=QFont.Bold)
        self.hint = _label("", px=14)
        set_props(self.hint, role="danger")
        self.pages = QStackedWidget()
        self.course_list = self._list()
        self.section_list = self._list()
        self.period_list = self._list()
        for widget in (self.course_list, self.section_list, self.period_list):
            self.pages.addWidget(widget)
        back = big_button("Back", variant="ghost")
        back.clicked.connect(self._back)
        self.next_button = big_button("Next", primary=True)
        self.next_button.clicked.connect(self._next)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(back, stretch=1)
        row.addWidget(self.next_button, stretch=2)
        layout = QVBoxLayout()
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(12)
        layout.addLayout(bars)
        layout.addWidget(self.step_label)
        layout.addWidget(self.title)
        layout.addSpacing(4)
        layout.addWidget(self.pages, stretch=1)
        layout.addWidget(self.hint)
        layout.addLayout(row)
        self.setLayout(layout)
        self._sections_for: Callable[[int], list[dict[str, Any]]] = lambda _c: []
        self._blocker_for: Callable[[int], str | None] = lambda _s: None
        self._show_step(0)

    @staticmethod
    def _list() -> QListWidget:
        widget = QListWidget()
        widget.setFont(font(17, QFont.Medium))
        widget.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        return widget

    def _show_step(self, step: int) -> None:
        self.pages.setCurrentIndex(step)
        self.title.setText(self.STEPS[step])
        self.step_label.setText(f"STEP {step + 1} OF {len(self.STEPS)}")
        for index, bar in enumerate(self.step_bars):
            set_props(bar, tone="active" if index <= step else "")
        self.next_button.setText("Start" if step == len(self.STEPS) - 1 else "Next")

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
            item = QListWidgetItem(f"{course['code']}  ·  {course['name']}")
            item.setData(Qt.UserRole, int(course["id"]))
            self.course_list.addItem(item)
        self.period_list.clear()
        none_item = QListWidgetItem("No specific period")
        none_item.setData(Qt.UserRole, None)
        self.period_list.addItem(none_item)
        for period in periods:
            item = QListWidgetItem(
                f"{period['name']}  ·  {period['start_time']}–{period['end_time']}"
            )
            item.setData(Qt.UserRole, int(period["id"]))
            self.period_list.addItem(item)
        self._show_step(0)
        self.hint.setText(
            "" if courses else "No courses synced yet. Connect to WiFi and use Settings > Sync now."
        )
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
            self.hint.setText("")
            self._show_step(1)
        elif page == 1:
            section_id = self._selected(self.section_list)
            if section_id is None:
                return
            blocker = self._blocker_for(int(section_id))
            if blocker:
                self.hint.setText(blocker)
                return
            self.hint.setText("")
            self.period_list.setCurrentRow(0)
            self._show_step(2)
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
        self.hint.setText("")
        self._show_step(page - 1)


class SettingsScreen(QWidget):
    sync_requested = pyqtSignal()
    test_camera_requested = pyqtSignal()
    restart_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        eyebrow = _label("DEVICE", px=12, weight=QFont.DemiBold, role="eyebrow")
        title = _label("Settings", px=28, weight=QFont.Bold)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setFont(font(13))
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Scrolls instead of growing: with long paths on the Pi the table alone could
        # push the page past 800 px.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.info)
        set_props(scroll, role="plain")
        scroll.viewport().setAutoFillBackground(False)
        card = QFrame()
        set_props(card, role="card")
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(16, 10, 10, 10)
        card_layout.addWidget(scroll)
        card.setLayout(card_layout)
        self.calibration_warning = _label("", px=13)
        set_props(self.calibration_warning, role="alert", tone="warn")
        self.calibration_warning.setVisible(False)
        self.feedback = _label("", px=13, role="muted")

        sync = big_button("Sync now")
        sync.clicked.connect(self.sync_requested)
        camera = big_button("Test camera")
        camera.clicked.connect(self.test_camera_requested)
        restart = big_button("Restart app")
        restart.clicked.connect(self.restart_requested)
        back = big_button("Done", primary=True)
        back.clicked.connect(self.back_requested)
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.addWidget(sync, 0, 0)
        grid.addWidget(camera, 0, 1)
        grid.addWidget(restart, 1, 0)
        grid.addWidget(back, 1, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(12)
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(card, stretch=1)
        layout.addWidget(self.calibration_warning)
        layout.addWidget(self.feedback)
        layout.addLayout(grid)
        self.setLayout(layout)

    def show_info(self, rows: dict[str, str], *, calibrated: bool) -> None:
        # A two-column table that wraps: keys muted on the left, values on the right.
        cells = "".join(
            f"<tr><td style='color:{COLORS['subtle']};padding:5px 12px 5px 0'>"
            f"{html.escape(key)}</td>"
            f"<td style='padding:5px 0'>{html.escape(value)}</td></tr>"
            for key, value in rows.items()
        )
        self.info.setText(f"<table width='100%' cellspacing='0'>{cells}</table>")
        self.calibration_warning.setText(
            ""
            if calibrated
            else "NOT CALIBRATED: run tools/calibrate.py on the server, then Sync now."
        )
        self.calibration_warning.setVisible(not calibrated)

    def show_feedback(self, text: str) -> None:
        self.feedback.setText(text)
