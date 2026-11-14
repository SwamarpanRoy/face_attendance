"""Device controller: what the touch screen shows and what every tap means, with no visuals.

The QML screens (``device/app/ui/qml``) bind to this object's properties and call its
slots; they hold no logic. The controller owns the pipeline worker, the session manager,
the sync worker and the PIN verifier. Everything that crosses a thread (sync status,
template refresh, camera frames) arrives through Qt signals.

PIN entry and enrolment are screens, not blocking dialogs: an action that needs a PIN
asks for one and continues in a callback once a valid PIN is entered (cancelling returns
to where the user was).

Properties are grouped by screen, each group with one change signal, so QML re-reads a
group when anything in it changes. Texts the tests rely on ("Face matched – …",
"Already marked – …", "No faces enrolled…", "NOT CALIBRATED") are unchanged.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

import cv2
from PyQt5.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage

from common.face.engine import FrameResult
from common.face.matcher import MatchConfig, Outcome, Recognizer, TemplateIndex
from common.face.types import Array
from common.schemas import EnrolmentCapturesOut, RosterStudentOut
from common.version import __version__
from device.app.client import EnrolmentRejectedError, ServerClient, ServerUnavailableError
from device.app.config import DeviceConfig
from device.app.enrol import Capture, EnrolmentFlow
from device.app.pins import FacultyRef, PinLockedError, PinVerifier
from device.app.pipeline import PipelineWorker, annotate
from device.app.session import SessionInfo, SessionManager
from device.app.store import DeviceStore
from device.app.sync import Syncer, SyncStatus, SyncWorker
from device.app.ui.video import VideoSink

log = logging.getLogger(__name__)

EXIT_RESTART = 3  # the Pi run wrapper restarts the app on any exit; 3 says it was asked for
CAPTURE_GAP_MS = 700  # pause between enrolment captures so the student can move slightly
MAX_PIN_LENGTH = 12
SCREENS = ("idle", "start", "pin", "live", "settings", "enrol")
# While a result card is held, only an equally or more important one may replace it, so a
# green "Face matched" is not overwritten by the "Already marked" that the same face
# produces a moment later (the student would barely see green).
BANNER_PRIORITY = {"neutral": 0, "warn": 1, "error": 2, "ok": 3}


def _format_time(value: str | datetime | None) -> str:
    if value is None:
        return "never"
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return when.astimezone().strftime("%d %b %H:%M")


def _short_time(value: str | datetime | None, *, now: datetime | None = None) -> str:
    """For a small tile: the time if it was today, else the day ("09:41" / "11 Nov")."""
    if value is None:
        return "never"
    when = (datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone()
    today = (now or datetime.now()).astimezone().date()
    return when.strftime("%H:%M") if when.date() == today else when.strftime("%d %b")


class SyncBridge(QObject):
    """Marshals sync-thread callbacks onto the Qt thread."""

    status = pyqtSignal(object)
    templates = pyqtSignal()


class DeviceController(QObject):
    screenChanged = pyqtSignal()
    idleChanged = pyqtSignal()
    startChanged = pyqtSignal()
    pinChanged = pyqtSignal()
    liveChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    enrolChanged = pyqtSignal()
    quitRequested = pyqtSignal()

    def __init__(
        self,
        *,
        config: DeviceConfig,
        store: DeviceStore,
        worker: PipelineWorker,
        sessions: SessionManager,
        client: ServerClient | None,
        verifier: PinVerifier,
        network_info: Callable[[], dict[str, str | None]],
        clock_synced: Callable[[], bool],
        heartbeat_payload: Callable[[], Any] | None = None,
        on_matched_feedback: Callable[[Outcome], None] | None = None,
        on_restart: Callable[[], None] | None = None,
        show_capture_button: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.worker = worker
        self.sessions = sessions
        self.client = client
        self.verifier = verifier
        self._network_info = network_info
        self._clock_synced = clock_synced
        self._feedback = on_matched_feedback
        self._on_restart = on_restart
        self.session: SessionInfo | None = None
        self.test_mode = False
        self.sync_status: SyncStatus | None = None
        self.video = VideoSink(self)
        self._screen = "idle"

        # idle
        self._server = ("unset", "Server not set up")
        self._queue = 0
        self._last_sync = "never"
        self._network = {"ssid": None, "ip": None}
        self._idle_warning = ""
        self._message = ""
        self._resumable = False
        self._test_available = False

        # start session: three steps of (label, id) choices
        self._step = 0
        self._choices: list[list[tuple[str, Any]]] = [[], [], []]
        self._chosen = [0, 0, 0]
        self._start_hint = ""

        # pin
        self._pin_title = ""
        self._pin_message = ""
        self._pin_text = ""
        self._pin_admin = False
        self._pin_errors = 0
        self._pin_then: Callable[[FacultyRef], None] | None = None
        self._pin_back = "idle"

        # live
        self._live_title = ""
        self._live_subtitle = ""
        self._live_calibrated = True
        self._live_header = ""
        self.present = 0
        self.total = 0
        self.marked: deque[str] = deque(maxlen=5)
        self._end_label = "End session"
        self._capture_visible = show_capture_button
        self._stats = ""
        self._banner = ("", "neutral", "", "")  # text, tone, title, subtitle
        self._banner_fallback = self._banner
        self._banner_hold = QTimer(self)
        self._banner_hold.setSingleShot(True)
        self._banner_hold.timeout.connect(self._release_banner)

        # settings
        self._settings_rows: list[dict[str, str]] = []
        self._settings_warning = ""
        self._settings_feedback = ""

        # enrol
        self._flow: EnrolmentFlow | None = None
        self._enrol_teacher: FacultyRef | None = None
        self._enrol_page = "pick"
        self._enrol_sections: list[dict[str, Any]] = []
        self._enrol_section_index = 0
        self._enrol_roster: list[RosterStudentOut] = []
        self._enrol_digits = ""
        self._enrol_selected: RosterStudentOut | None = None
        self._enrol_consent = ("", "", "")  # title, notice, version
        self._enrol_capture = ("", "")  # title, status
        self._enrol_done = ("", "", False)  # title, text, retry
        self._enrolled_any = False
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self.worker.request_capture)

        worker.frame_ready.connect(self._on_frame)
        worker.gate_message.connect(self._on_gate)
        worker.outcome_ready.connect(self._on_outcome)
        worker.capture_ready.connect(self._on_capture)
        worker.stats_ready.connect(self._on_stats)
        worker.failed.connect(self._on_failed)

        self.bridge = SyncBridge()
        self.bridge.status.connect(self.on_sync_status)
        self.bridge.templates.connect(self.on_templates_refreshed)
        self.sync_worker: SyncWorker | None = None
        if client is not None and heartbeat_payload is not None:
            self.sync_worker = SyncWorker(
                store,
                client,
                config.sync,
                config.recognition.model_version,
                heartbeat_payload,
                on_status=self.bridge.status.emit,
                on_templates=self.bridge.templates.emit,
            )
        self._network_timer = QTimer(self)
        self._network_timer.timeout.connect(self.refresh_network)
        self._network_timer.start(10_000)
        self.refresh_network()
        self.worker.set_suppress_outcomes(True)
        self.show_idle()

    # ================================================================== QML properties
    screen = pyqtProperty(str, lambda self: self._screen, notify=screenChanged)
    deviceName = pyqtProperty(str, lambda self: self.config.device.name, constant=True)
    hasServer = pyqtProperty(bool, lambda self: self.client is not None, constant=True)
    videoSink = pyqtProperty(QObject, lambda self: self.video, constant=True)

    serverState = pyqtProperty(str, lambda self: self._server[0], notify=idleChanged)
    serverText = pyqtProperty(str, lambda self: self._server[1], notify=idleChanged)
    queueLength = pyqtProperty(int, lambda self: self._queue, notify=idleChanged)
    lastSync = pyqtProperty(str, lambda self: self._last_sync, notify=idleChanged)
    wifi = pyqtProperty(str, lambda self: self._network.get("ssid") or "–", notify=idleChanged)
    ip = pyqtProperty(str, lambda self: self._network.get("ip") or "–", notify=idleChanged)
    idleWarning = pyqtProperty(str, lambda self: self._idle_warning, notify=idleChanged)
    message = pyqtProperty(str, lambda self: self._message, notify=idleChanged)
    resumable = pyqtProperty(bool, lambda self: self._resumable, notify=idleChanged)
    testAvailable = pyqtProperty(bool, lambda self: self._test_available, notify=idleChanged)

    startStep = pyqtProperty(int, lambda self: self._step, notify=startChanged)
    startTitle = pyqtProperty(
        str,
        lambda self: ("Choose the course", "Choose the section", "Choose the period")[self._step],
        notify=startChanged,
    )
    startItems = pyqtProperty(
        "QVariantList",
        lambda self: [label for label, _ in self._choices[self._step]],
        notify=startChanged,
    )
    startIndex = pyqtProperty(int, lambda self: self._chosen[self._step], notify=startChanged)
    startHint = pyqtProperty(str, lambda self: self._start_hint, notify=startChanged)

    pinTitle = pyqtProperty(str, lambda self: self._pin_title, notify=pinChanged)
    pinMessage = pyqtProperty(str, lambda self: self._pin_message, notify=pinChanged)
    pinLength = pyqtProperty(int, lambda self: len(self._pin_text), notify=pinChanged)
    pinErrors = pyqtProperty(int, lambda self: self._pin_errors, notify=pinChanged)

    liveTitle = pyqtProperty(str, lambda self: self._live_title, notify=liveChanged)
    liveSubtitle = pyqtProperty(str, lambda self: self._live_subtitle, notify=liveChanged)
    liveHeader = pyqtProperty(str, lambda self: self._live_header, notify=liveChanged)
    liveCalibrated = pyqtProperty(bool, lambda self: self._live_calibrated, notify=liveChanged)
    presentCount = pyqtProperty(int, lambda self: self.present, notify=liveChanged)
    totalCount = pyqtProperty(int, lambda self: self.total, notify=liveChanged)
    recentText = pyqtProperty(str, lambda self: self.recent_text(), notify=liveChanged)
    endLabel = pyqtProperty(str, lambda self: self._end_label, notify=liveChanged)
    captureVisible = pyqtProperty(bool, lambda self: self._capture_visible, notify=liveChanged)
    stats = pyqtProperty(str, lambda self: self._stats, notify=liveChanged)
    bannerText = pyqtProperty(str, lambda self: self._banner[0], notify=liveChanged)
    bannerTone = pyqtProperty(str, lambda self: self._banner[1], notify=liveChanged)
    bannerTitle = pyqtProperty(str, lambda self: self._banner[2], notify=liveChanged)
    bannerSubtitle = pyqtProperty(str, lambda self: self._banner[3], notify=liveChanged)

    settingsRows = pyqtProperty(
        "QVariantList", lambda self: self._settings_rows, notify=settingsChanged
    )
    settingsWarning = pyqtProperty(str, lambda self: self._settings_warning, notify=settingsChanged)
    settingsFeedback = pyqtProperty(
        str, lambda self: self._settings_feedback, notify=settingsChanged
    )

    enrolPage = pyqtProperty(str, lambda self: self._enrol_page, notify=enrolChanged)
    enrolSections = pyqtProperty(
        "QVariantList",
        lambda self: [str(s["name"]) for s in self._enrol_sections],
        notify=enrolChanged,
    )
    enrolSectionIndex = pyqtProperty(
        int, lambda self: self._enrol_section_index, notify=enrolChanged
    )
    enrolDigits = pyqtProperty(str, lambda self: self._enrol_digits, notify=enrolChanged)
    enrolRoster = pyqtProperty(
        "QVariantList", lambda self: self.filtered_roster(), notify=enrolChanged
    )
    enrolConsentTitle = pyqtProperty(str, lambda self: self._enrol_consent[0], notify=enrolChanged)
    enrolNotice = pyqtProperty(str, lambda self: self._enrol_consent[1], notify=enrolChanged)
    enrolVersion = pyqtProperty(str, lambda self: self._enrol_consent[2], notify=enrolChanged)
    enrolCaptureTitle = pyqtProperty(str, lambda self: self._enrol_capture[0], notify=enrolChanged)
    enrolCaptureStatus = pyqtProperty(str, lambda self: self._enrol_capture[1], notify=enrolChanged)
    enrolCaptured = pyqtProperty(
        int,
        lambda self: len(self._flow.state.captures) if self._flow and self._flow.state else 0,
        notify=enrolChanged,
    )
    enrolRequired = pyqtProperty(
        int, lambda self: self._flow.required if self._flow else 3, notify=enrolChanged
    )
    enrolDoneTitle = pyqtProperty(str, lambda self: self._enrol_done[0], notify=enrolChanged)
    enrolDoneText = pyqtProperty(str, lambda self: self._enrol_done[1], notify=enrolChanged)
    enrolCanRetry = pyqtProperty(bool, lambda self: self._enrol_done[2], notify=enrolChanged)

    # ================================================================== lifecycle
    def start_workers(self) -> None:
        self.worker.start()
        if self.sync_worker is not None:
            self.sync_worker.start()

    def shutdown(self) -> None:
        """Stop background threads (window closed, Ctrl+C, restart)."""
        self._capture_timer.stop()
        if self.sync_worker is not None:
            self.sync_worker.stop()
        self.worker.stop()

    def _set_screen(self, screen: str) -> None:
        assert screen in SCREENS, screen
        if screen != self._screen:
            self._screen = screen
            self.screenChanged.emit()
        showing_video = screen in ("live", "enrol")
        # Only the live and enrol screens use the camera; elsewhere the pipeline sleeps.
        self.worker.set_paused(not showing_video)
        if not showing_video:
            self.video.clear()

    # ================================================================== idle
    @pyqtSlot()
    def show_idle(self) -> None:
        self.worker.set_suppress_outcomes(True)
        self.test_mode = False
        self._resumable = self.sessions.resume() is not None
        self._test_available = (
            not self.sessions.course_options() and self.store.counts().templates > 0
        )
        self._refresh_idle()
        self._set_screen("idle")

    def _refresh_idle(self) -> None:
        status = self.sync_status
        if status is None:
            self._server = ("unset", "Server not set up" if self.client is None else "Connecting")
        elif status.revoked:
            self._server = ("revoked", "Device revoked")
        elif status.online:
            self._server = ("online", "Online")
        else:
            retry = f" · retry {int(status.retry_in_s)} s" if status.retry_in_s else ""
            self._server = ("offline", f"Offline{retry}")
        self._queue = status.queue_len if status else self.store.pending_count()
        self._last_sync = _short_time(status.last_sync_at) if status else "never"
        warnings = []
        if self.store.calibration() is None:
            warnings.append("Not calibrated: using provisional thresholds.")
        if not self._clock_synced():
            warnings.append("Clock not synced (no NTP): record times will be flagged.")
        self._idle_warning = " ".join(warnings)
        self.idleChanged.emit()

    @pyqtSlot()
    def refresh_network(self) -> None:
        self._network = dict(self._network_info())
        self.idleChanged.emit()

    def on_sync_status(self, status: SyncStatus) -> None:
        self.sync_status = status
        self._refresh_idle()
        if self._screen == "settings":
            self._refresh_settings()

    def on_templates_refreshed(self) -> None:
        if self.session is not None:
            recognizer, _ = self.sessions.build_recognizer(self.session)
            self.worker.swap_recognizer(recognizer)
        elif self.test_mode:
            self.worker.swap_recognizer(self._all_templates_recognizer())
        if self._screen == "idle":
            self.show_idle()

    # ================================================================== PIN
    def _verify_online(self, pin: str) -> FacultyRef | None:
        if self.client is None:
            return None
        try:
            found = self.client.verify_pin(pin)
        except ServerUnavailableError:
            return None
        return FacultyRef(found.faculty_id, found.name, found.role) if found else None

    def ask_pin(
        self, title: str, then: Callable[[FacultyRef], None], *, admin: bool = False
    ) -> None:
        """Show the PIN screen; call ``then(faculty)`` once a valid PIN is entered."""
        self._pin_title = title
        self._pin_message = "Admin PIN required" if admin else "Enter your PIN"
        self._pin_text = ""
        self._pin_admin = admin
        self._pin_then = then
        if self._screen != "pin":
            self._pin_back = self._screen
        self.pinChanged.emit()
        self._set_screen("pin")

    @pyqtSlot(str)
    def pinKey(self, digit: str) -> None:
        if digit.isdigit() and len(self._pin_text) < MAX_PIN_LENGTH:
            self._pin_text += digit
            self.pinChanged.emit()

    @pyqtSlot()
    def pinBackspace(self) -> None:
        self._pin_text = self._pin_text[:-1]
        self.pinChanged.emit()

    def _pin_failed(self, message: str) -> None:
        self._pin_message = message
        self._pin_text = ""
        self._pin_errors += 1  # QML shakes the dots on every change
        self.pinChanged.emit()

    @pyqtSlot()
    def pinSubmit(self) -> None:
        pin = self._pin_text
        if not pin:
            return
        try:
            ref = self.verifier.verify(pin)
        except PinLockedError as exc:
            self._pin_failed(str(exc))
            return
        if ref is None and not self.verifier.has_pins:
            ref = self._verify_online(pin)  # before the first catalog sync
        if ref is None:
            self._pin_failed("PIN not recognised. Try again.")
            return
        if self._pin_admin and not ref.is_admin:
            self._pin_failed("Admin PIN required for this screen.")
            return
        then, self._pin_then = self._pin_then, None
        self._pin_text = ""
        self.pinChanged.emit()
        if then is not None:
            then(ref)

    @pyqtSlot()
    def pinCancel(self) -> None:
        self._pin_then = None
        self._pin_text = ""
        self.pinChanged.emit()
        if self._pin_back == "idle":
            self.show_idle()
        else:
            self._set_screen(self._pin_back)

    # ================================================================== start session
    @pyqtSlot()
    def startSession(self) -> None:
        self._choices[0] = [
            (f"{c['code']}  ·  {c['name']}", int(c["id"])) for c in self.sessions.course_options()
        ]
        self._choices[2] = [("No specific period", None)] + [
            (f"{p['name']}  ·  {p['start_time']}–{p['end_time']}", int(p["id"]))
            for p in self.sessions.period_options()
        ]
        self._choices[1] = []
        self._chosen = [0, 0, 0]
        self._step = 0
        self._start_hint = (
            ""
            if self._choices[0]
            else "No courses synced yet. Connect to WiFi and use Settings > Sync now."
        )
        self.startChanged.emit()
        self._set_screen("start")

    def _chosen_id(self, step: int) -> Any:
        options = self._choices[step]
        index = self._chosen[step]
        return options[index][1] if 0 <= index < len(options) else None

    @pyqtSlot(int)
    def startSelect(self, index: int) -> None:
        if 0 <= index < len(self._choices[self._step]):
            self._chosen[self._step] = index
            self._start_hint = ""
            self.startChanged.emit()

    @pyqtSlot()
    def startNext(self) -> None:
        if self._step == 0:
            course_id = self._chosen_id(0)
            if course_id is None:
                return
            self._choices[1] = [
                (str(s["name"]), int(s["id"])) for s in self.sessions.section_options(course_id)
            ]
            self._chosen[1] = 0
            self._step = 1
            self._start_hint = ""
        elif self._step == 1:
            section_id = self._chosen_id(1)
            if section_id is None:
                return
            blocker = self.sessions.blocker(int(section_id))
            if blocker:
                self._start_hint = blocker
                self.startChanged.emit()
                return
            self._chosen[2] = 0
            self._step = 2
            self._start_hint = ""
        else:
            course_id, section_id, period_id = (self._chosen_id(i) for i in range(3))
            self.begin_session(int(course_id), int(section_id), period_id)
            return
        self.startChanged.emit()

    @pyqtSlot()
    def startBack(self) -> None:
        if self._step == 0:
            self.show_idle()
            return
        self._step -= 1
        self._start_hint = ""
        self.startChanged.emit()

    def begin_session(self, course_id: int, section_id: int, period_id: object) -> None:
        self.ask_pin(
            "Teacher PIN",
            lambda teacher: self._begin_session_as(teacher, course_id, section_id, period_id),
        )

    def _begin_session_as(
        self, teacher: FacultyRef, course_id: int, section_id: int, period_id: object
    ) -> None:
        if self.client is not None and (self.sync_status is None or self.sync_status.online):
            self._quick_refresh()
        blocker = self.sessions.blocker(section_id)
        if blocker:
            self._start_hint = blocker
            self.startChanged.emit()
            self._set_screen("start")
            return
        faculty_id = self.sessions.faculty_for_offering(course_id, section_id) or teacher.id
        if not teacher.is_admin and faculty_id != teacher.id:
            faculty_id = teacher.id  # whoever is physically running the class is recorded
        period = int(period_id) if isinstance(period_id, int) else None
        info = self.sessions.start(
            course_id=course_id, section_id=section_id, period_id=period, faculty_id=faculty_id
        )
        self._enter_live(info)
        if self.sync_worker is not None:
            self.sync_worker.request_sync_now()

    @pyqtSlot()
    def resumeSession(self) -> None:
        info = self.sessions.resume()
        if info is None:
            self.show_idle()
            return
        self.ask_pin("Teacher PIN", lambda _teacher: self._enter_live(info))

    def _quick_refresh(self) -> None:
        """Best-effort template refresh right before a session (bounded by the client timeout)."""
        if self.client is None:
            return
        done = threading.Event()

        def pull() -> None:
            try:
                Syncer(
                    self.store, self.client, self.config.recognition.model_version
                ).pull_templates()  # type: ignore[arg-type]
            except Exception as exc:
                log.info("pre-session refresh skipped: %s", exc)
            finally:
                done.set()

        threading.Thread(target=pull, daemon=True).start()
        done.wait(self.config.server.timeout_s + 1)

    # ================================================================== live
    def _begin_live(
        self,
        *,
        title: str,
        subtitle: str,
        calibrated: bool,
        total: int,
        present: int,
        recent: list[str],
        end_label: str,
    ) -> None:
        self._live_title = title
        self._live_subtitle = subtitle
        self._live_calibrated = calibrated
        self._live_header = " · ".join(p for p in (title, subtitle) if p) + (
            "" if calibrated else " · NOT CALIBRATED"
        )
        self.total = total
        self.present = present
        self.marked = deque(recent, maxlen=5)
        self._end_label = end_label
        self.video.set_message("Starting camera…")
        self._banner_hold.stop()
        self.show_banner("Stand in front of the camera", "neutral")
        self.liveChanged.emit()
        self._set_screen("live")

    def _enter_live(self, info: SessionInfo) -> None:
        self.session = info
        recognizer, calibrated = self.sessions.build_recognizer(info)
        self.worker.swap_recognizer(recognizer)
        self._begin_live(
            title=info.course_label,
            subtitle=f"{info.section_label} · {info.period_label}",
            calibrated=calibrated,
            total=self.sessions.roster_size(info.section_id),
            present=self.sessions.present_count(info),
            recent=[f"{e['usn']}" for e in self.sessions.recent(info)],
            end_label="End session",
        )
        self.worker.set_suppress_outcomes(False)

    def recent_text(self) -> str:
        return "Recent: " + " · ".join(self.marked) if self.marked else ""

    def show_banner(
        self, text: str, tone: str = "neutral", hold_ms: int = 0, *, title: str = "", sub: str = ""
    ) -> None:
        """Result card text; a held message (match) is not overwritten by gate hints."""
        banner = (text, tone, title or text, sub)
        held = self._banner_hold.isActive()
        if held and (
            hold_ms == 0 or BANNER_PRIORITY.get(tone, 0) < BANNER_PRIORITY.get(self._banner[1], 0)
        ):
            self._banner_fallback = banner  # show after the held message
            return
        self._banner = banner
        if hold_ms:
            self._banner_hold.start(hold_ms)
        self.liveChanged.emit()

    def _release_banner(self) -> None:
        self._banner = self._banner_fallback
        self.liveChanged.emit()

    def on_matched(self, outcome: Outcome) -> None:
        if self.session is not None and outcome.usn:
            self.sessions.record_match(self.session, outcome.usn, outcome.score)
            if self.sync_worker is not None:
                self.sync_worker.request_sync_now()
        if self._feedback:
            self._feedback(outcome)

    @pyqtSlot()
    def endSession(self) -> None:
        if self.session is None:
            self.show_idle()
            return
        self.ask_pin("Teacher PIN", lambda _teacher: self._end_session())

    def _end_session(self) -> None:
        if self.session is not None:
            self.sessions.end(self.session)
            self.session = None
        if self.sync_worker is not None:
            self.sync_worker.request_sync_now()
        self.show_idle()

    @pyqtSlot()
    def captureTest(self) -> None:
        self.show_banner("Look at the camera…", "neutral")
        self.worker.request_capture()

    # ---------------------------------------------------------------- worker signals
    def _wants_frames(self) -> bool:
        return self._screen == "live" or (self._screen == "enrol" and self._enrol_page == "capture")

    def _on_frame(self, frame: Array, result: FrameResult) -> None:
        if not self._wants_frames():
            return
        canvas = annotate(frame, result)
        width = self.config.camera.preview_size[0]
        if canvas.shape[1] > width:  # downsize once here; QML only scales the last bit
            height = round(canvas.shape[0] * width / canvas.shape[1])
            canvas = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        self.video.set_image(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())

    def _on_gate(self, message: str, reason: str) -> None:
        if self._screen == "live":
            self.show_banner(message, "neutral")
        elif self._screen == "enrol" and self._enrol_page == "capture" and message:
            self._enrol_capture = (message, self._enrol_capture[1])
            self.enrolChanged.emit()

    def _on_outcome(self, outcome: Outcome) -> None:
        if self._screen != "live":
            return
        if outcome.kind == "matched" and outcome.usn:
            self.present += 1
            self.marked.appendleft(f"{outcome.name} ({outcome.usn})")
            self.show_banner(
                f"Face matched – {outcome.name} ({outcome.usn})",
                "ok",
                hold_ms=2500,
                title=str(outcome.name),
                sub=f"{outcome.usn}  ·  Marked present {datetime.now():%H:%M}",
            )
            log.info("matched %s score=%.3f", outcome.usn, outcome.score or 0.0)
            self.on_matched(outcome)
        elif outcome.kind == "already_marked":
            self.show_banner(
                f"Already marked – {outcome.name}",
                "warn",
                hold_ms=1500,
                title=str(outcome.name),
                sub="Already marked present",
            )
        elif outcome.kind == "not_recognised":
            self.show_banner("Not recognised – try again", "error", hold_ms=1500)
        else:
            self.show_banner("Hold on…", "neutral")

    def _on_capture(self, capture: Capture) -> None:
        if self._screen == "enrol":
            self._enrol_capture_ready(capture)
        elif self._screen == "live":
            self.show_banner("Test template captured", "ok", hold_ms=1500)

    def _on_stats(self, summary: dict[str, tuple[float, float]]) -> None:
        self._stats = "  ".join(
            f"{stage} p50 {p50:.0f}/p95 {p95:.0f} ms" for stage, (p50, p95) in summary.items()
        )
        if self._screen == "live":
            self.liveChanged.emit()

    def _on_failed(self, message: str) -> None:
        self.video.set_message(f"Camera / pipeline error:\n{message}")
        self.show_banner("Device error – see log", "error")

    # ================================================================== test modes
    def _all_templates_recognizer(self) -> Recognizer:
        calibration = self.store.calibration()
        match_config: MatchConfig = (
            self.config.match_config(*calibration) if calibration else self.config.match_config()
        )
        return Recognizer(TemplateIndex(self.store.load_templates()), match_config)

    @pyqtSlot()
    def testRecognition(self) -> None:
        """Recognise against every cached template without recording anything."""
        self.session = None
        self.test_mode = True
        self.worker.swap_recognizer(self._all_templates_recognizer())
        self._begin_live(
            title="Test recognition",
            subtitle="Nothing is recorded",
            calibrated=self.store.calibration() is not None,
            total=0,
            present=0,
            recent=[],
            end_label="Back",
        )
        self.worker.set_suppress_outcomes(False)

    @pyqtSlot()
    def testCamera(self) -> None:
        self.session = None
        self.test_mode = False
        self._begin_live(
            title="Camera test",
            subtitle="Check focus and framing",
            calibrated=True,
            total=0,
            present=0,
            recent=[],
            end_label="Back",
        )
        self.worker.set_suppress_outcomes(True)

    # ================================================================== settings
    @pyqtSlot()
    def openSettings(self) -> None:
        self.ask_pin("Admin PIN", lambda _admin: self._show_settings(), admin=True)

    def _show_settings(self) -> None:
        self._settings_feedback = ""
        self._refresh_settings()
        self._set_screen("settings")

    def _refresh_settings(self) -> None:
        calibration = self.store.calibration()
        counts = self.store.counts()
        rows = {
            "Device": f"{self.config.device.name} ({self.config.device.id})",
            "App version": __version__,
            "Server": self.config.server.url if self.client else "not configured",
            "Model": self.config.recognition.model_version,
            "Threshold": f"{calibration[0]:.3f}"
            if calibration
            else f"{self.config.recognition.fallback_threshold:.2f} (fallback)",
            "Margin": f"{calibration[1]:.3f}"
            if calibration
            else f"{self.config.recognition.fallback_margin:.2f} (fallback)",
            "Liveness": self.worker.engine.liveness.name,
            "Templates": f"{counts.templates} for {counts.students} students",
            "Queued": str(counts.pending_events),
            "Last sync": (self.sync_status.last_sync_at if self.sync_status else None) or "never",
            "Network": " / ".join(str(v) for v in self._network_info().values() if v) or "–",
            "Clock synced": "yes" if self._clock_synced() else "NO",
            "Config": str(self.config.source_path or "defaults"),
            "Database": str(self.store.path),
        }
        self._settings_rows = [{"key": k, "value": v} for k, v in rows.items()]
        self._settings_warning = (
            ""
            if calibration
            else "NOT CALIBRATED: run tools/calibrate.py on the server, then Sync now."
        )
        self.settingsChanged.emit()

    @pyqtSlot()
    def syncNow(self) -> None:
        if self.sync_worker is None:
            self._settings_feedback = "No server configured."
        else:
            self.sync_worker.request_sync_now()
            self._settings_feedback = "Sync requested…"
            QTimer.singleShot(3000, self._refresh_settings)
        self.settingsChanged.emit()

    @pyqtSlot()
    def restartApp(self) -> None:
        log.info("restart requested from Settings")
        if self._on_restart:
            self._on_restart()
        self.quitRequested.emit()

    # ================================================================== enrolment
    @pyqtSlot()
    def openEnrol(self) -> None:
        if self.client is None:
            self._message = "Enrolment needs the server. Configure device.toml first."
            self.idleChanged.emit()
            return
        self.ask_pin("Teacher PIN", self._start_enrol)

    def _start_enrol(self, teacher: FacultyRef) -> None:
        assert self.client is not None
        flow = EnrolmentFlow(
            self.client,
            self.store,
            Syncer(self.store, self.client, self.config.recognition.model_version),
        )
        self.start_enrol(flow, teacher)

    def start_enrol(self, flow: EnrolmentFlow, teacher: FacultyRef) -> None:
        """Open the enrolment screens with a given flow (tests pass a flow on fakes)."""
        self._flow = flow
        self._enrol_teacher = teacher
        self._enrol_selected = None
        self._enrolled_any = False
        self._enrol_digits = ""
        self._enrol_page = "pick"
        self.worker.set_suppress_outcomes(True)
        self._enrol_sections = list(flow.sections())
        self._enrol_section_index = 0
        self._load_roster()
        self._set_screen("enrol")

    def _load_roster(self) -> None:
        self._enrol_roster = []
        if self._flow is not None and self._enrol_sections:
            section = self._enrol_sections[self._enrol_section_index]
            self._enrol_roster = self._flow.roster(int(section["id"]))
        self.enrolChanged.emit()

    def filtered_roster(self) -> list[dict[str, Any]]:
        return [
            {"usn": s.usn, "name": s.name, "enrolled": s.template_count}
            for s in self._enrol_roster
            if not self._enrol_digits or s.usn.endswith(self._enrol_digits)
        ]

    @pyqtSlot(int)
    def enrolSelectSection(self, index: int) -> None:
        if 0 <= index < len(self._enrol_sections):
            self._enrol_section_index = index
            self._load_roster()

    @pyqtSlot(str)
    def enrolKey(self, digit: str) -> None:
        if digit.isdigit() and len(self._enrol_digits) < 3:
            self._enrol_digits += digit
            self.enrolChanged.emit()

    @pyqtSlot()
    def enrolBackspace(self) -> None:
        self._enrol_digits = self._enrol_digits[:-1]
        self.enrolChanged.emit()

    @pyqtSlot(str)
    def enrolChoose(self, usn: str) -> None:
        student = next((s for s in self._enrol_roster if s.usn == usn), None)
        if student is None or self._flow is None or self._enrol_teacher is None:
            return
        self._enrol_selected = student
        self._flow.start(student, self._enrol_teacher.id)
        version, notice = self._flow.consent_text()
        self._enrol_consent = (f"{student.name} ({student.usn})", notice, version)
        self._enrol_page = "consent"
        self.enrolChanged.emit()

    @pyqtSlot()
    def enrolConsent(self) -> None:
        if self._flow is None:
            return
        self._flow.give_consent()
        self._enrol_page = "capture"
        self._update_capture_status("Look at the camera")
        self.video.set_message("Starting camera…")
        self.worker.request_capture()

    def _update_capture_status(self, title: str) -> None:
        if self._flow is None or self._flow.state is None:
            return
        have = len(self._flow.state.captures)
        status = f"Capture {min(have + 1, self._flow.required)} of {self._flow.required}"
        self._enrol_capture = (title, status)
        self.enrolChanged.emit()

    def _enrol_capture_ready(self, capture: Capture) -> None:
        if self._enrol_page != "capture" or self._flow is None or self._flow.state is None:
            return
        remaining = self._flow.add_capture(capture)
        self._update_capture_status("Captured")
        if remaining > 0:
            self._capture_timer.start(CAPTURE_GAP_MS)
            return
        self._enrol_upload()

    def _enrol_upload(self) -> None:
        assert self._flow is not None
        self._enrol_capture = (self._enrol_capture[0], "Uploading…")
        self.enrolChanged.emit()
        try:
            result: EnrolmentCapturesOut = self._flow.upload()
        except EnrolmentRejectedError as exc:
            self._enrol_finish("Enrolment refused", exc.detail, retry=True)
            return
        except ServerUnavailableError as exc:
            self._enrol_finish(
                "Server unreachable", f"{exc}. Connect to WiFi and try again.", retry=True
            )
            return
        except Exception as exc:
            log.exception("enrolment upload failed")
            self._enrol_finish("Enrolment failed", f"{type(exc).__name__}: {exc}", retry=True)
            return
        self._enrolled_any = True
        self._enrol_finish(
            "Enrolled",
            f"{result.usn}: {result.templates_added} templates added "
            f"({result.total_templates} total). The device can recognise them now.",
            retry=False,
        )
        self.on_templates_refreshed()

    def _enrol_finish(self, title: str, text: str, *, retry: bool) -> None:
        self._capture_timer.stop()
        self._enrol_done = (title, text, retry)
        self._enrol_page = "done"
        self.enrolChanged.emit()

    @pyqtSlot()
    def enrolRetry(self) -> None:
        if self._flow is None or self._enrol_selected is None or self._enrol_teacher is None:
            self._enrol_page = "pick"
        else:
            self._flow.start(self._enrol_selected, self._enrol_teacher.id)
            self._enrol_page = "consent"
        self.enrolChanged.emit()

    @pyqtSlot()
    def enrolBack(self) -> None:
        """Decline on the consent page goes back to the list; elsewhere closes enrolment."""
        if self._enrol_page == "consent" and self._flow is not None:
            self._flow.discard()
            self._enrol_page = "pick"
            self.enrolChanged.emit()
            return
        self.enrolClose()

    @pyqtSlot()
    def enrolClose(self) -> None:
        self._capture_timer.stop()
        if self._flow is not None:
            self._flow.discard()  # drops any captured crops from memory
        self._flow = None
        self._enrol_page = "pick"
        self.enrolChanged.emit()
        self.show_idle()

    # ================================================================== tests / simulator
    def store_test_template(
        self, embedding: Array, usn: str = "TEST001", name: str = "Test User"
    ) -> None:
        self.store.add_template(usn, name, embedding, self.config.recognition.model_version, None)
        self.on_templates_refreshed()
