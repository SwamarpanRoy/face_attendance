"""Main window: a stack of screens plus the controller that ties the device together.

The controller owns the pipeline worker, the session manager, the sync worker and the
PIN verifier, and decides what each tap means. Screens stay dumb. Everything that
crosses a thread (sync status, template refresh) arrives through Qt signals.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QStackedWidget

from common.face.matcher import MatchConfig, Outcome, Recognizer, TemplateIndex
from common.face.types import Array
from common.version import __version__
from device.app.client import ServerClient, ServerUnavailableError
from device.app.config import DeviceConfig
from device.app.enrol import EnrolmentFlow
from device.app.pins import FacultyRef, PinVerifier
from device.app.pipeline import PipelineWorker
from device.app.session import SessionInfo, SessionManager
from device.app.store import DeviceStore
from device.app.sync import Syncer, SyncStatus, SyncWorker
from device.app.ui.enrol import EnrolDialog
from device.app.ui.live import LiveScreen
from device.app.ui.pinpad import PinDialog
from device.app.ui.screens import IdleScreen, SettingsScreen, StartSessionScreen

log = logging.getLogger(__name__)

EXIT_RESTART = 3  # the Pi run wrapper restarts the app on any exit; 3 says it was asked for


class SyncBridge(QObject):
    """Marshals sync-thread callbacks onto the Qt thread."""

    status = pyqtSignal(object)
    templates = pyqtSignal()


class MainWindow(QMainWindow):
    IDLE, START, LIVE, SETTINGS = range(4)

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
        fullscreen: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.worker = worker
        self.sessions = sessions
        self.client = client
        self.verifier = verifier
        self._clock_synced = clock_synced
        self._feedback = on_matched_feedback
        self._on_restart = on_restart
        self.session: SessionInfo | None = None
        self.test_mode = False
        self.sync_status: SyncStatus | None = None

        self.setWindowTitle(config.device.name + ("" if client else " · no server"))
        self.idle = IdleScreen(config.device.name, network_info)
        self.start = StartSessionScreen()
        self.live = LiveScreen(worker, show_capture_button=show_capture_button)
        self.settings = SettingsScreen()
        self.stack = QStackedWidget()
        for screen in (self.idle, self.start, self.live, self.settings):
            self.stack.addWidget(screen)
        self.setCentralWidget(self.stack)
        self.resize(360, 640)
        if fullscreen:
            self.setWindowState(Qt.WindowFullScreen)

        self.idle.start_requested.connect(self.start_session_flow)
        self.idle.resume_requested.connect(self.resume_session)
        self.idle.test_requested.connect(self.begin_test_mode)
        self.idle.enrol_requested.connect(self.open_enrol)
        self.idle.settings_requested.connect(self.open_settings)
        self.start.chosen.connect(self.begin_session)
        self.start.cancelled.connect(self.show_idle)
        self.live.matched.connect(self.on_matched)
        self.live.end_requested.connect(self.end_session_flow)
        self.live.capture_test_requested.connect(self.worker.request_capture)
        self.worker.capture_ready.connect(self.live.on_capture_ready)
        self.settings.sync_requested.connect(self.sync_now)
        self.settings.test_camera_requested.connect(self.begin_camera_test)
        self.settings.restart_requested.connect(self.restart)
        self.settings.back_requested.connect(self.show_idle)

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
        self.worker.set_suppress_outcomes(True)
        self.show_idle()

    # ------------------------------------------------------------------ lifecycle
    def start_workers(self) -> None:
        self.worker.start()
        if self.sync_worker is not None:
            self.sync_worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt override
        if self.sync_worker is not None:
            self.sync_worker.stop()
        self.worker.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------ idle
    def show_idle(self) -> None:
        self.worker.set_suppress_outcomes(True)
        self.test_mode = False
        self.idle.set_resumable(self.sessions.resume() is not None)
        self.idle.set_test_mode_available(
            not self.sessions.course_options() and self.store.counts().templates > 0
        )
        self._refresh_idle()
        self.stack.setCurrentIndex(self.IDLE)

    def _refresh_idle(self) -> None:
        self.idle.show_status(
            self.sync_status,
            calibrated=self.store.calibration() is not None,
            clock_synced=self._clock_synced(),
        )

    def on_sync_status(self, status: SyncStatus) -> None:
        self.sync_status = status
        if self.stack.currentIndex() == self.IDLE:
            self._refresh_idle()
        if self.stack.currentIndex() == self.SETTINGS:
            self._refresh_settings()

    def on_templates_refreshed(self) -> None:
        if self.session is not None:
            recognizer, _ = self.sessions.build_recognizer(self.session)
            self.worker.swap_recognizer(recognizer)
        elif self.test_mode:
            self.worker.swap_recognizer(self._all_templates_recognizer())
        if self.stack.currentIndex() == self.IDLE:
            self.show_idle()

    # ------------------------------------------------------------------ sessions
    def _ask_pin(self, title: str, *, admin: bool = False) -> FacultyRef | None:
        fallback = None
        if self.client is not None:

            def fallback(pin: str) -> FacultyRef | None:
                try:
                    found = self.client.verify_pin(pin) if self.client else None
                except ServerUnavailableError:
                    return None
                return FacultyRef(found.faculty_id, found.name, found.role) if found else None

        return PinDialog.ask(
            self.verifier,
            title=title,
            require_admin=admin,
            parent=self,
            on_online_fallback=fallback,
        )

    def start_session_flow(self) -> None:
        self.start.load(
            self.sessions.course_options(),
            self.sessions.section_options,
            self.sessions.period_options(),
            self.sessions.blocker,
        )
        self.stack.setCurrentIndex(self.START)

    def begin_session(self, course_id: int, section_id: int, period_id: object) -> None:
        teacher = self._ask_pin("Teacher PIN to start the session")
        if teacher is None:
            return
        if self.client is not None and (self.sync_status is None or self.sync_status.online):
            self._quick_refresh()
        blocker = self.sessions.blocker(section_id)
        if blocker:
            self.start.hint.setText(blocker)
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

    def resume_session(self) -> None:
        info = self.sessions.resume()
        if info is None:
            self.show_idle()
            return
        if self._ask_pin("Teacher PIN to resume the session") is None:
            return
        self._enter_live(info)

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

    def _enter_live(self, info: SessionInfo) -> None:
        self.session = info
        recognizer, calibrated = self.sessions.build_recognizer(info)
        self.worker.swap_recognizer(recognizer)
        header = f"{info.course_label} · {info.section_label} · {info.period_label}"
        if not calibrated:
            header += " · NOT CALIBRATED"
        recent = [f"{e['usn']}" for e in self.sessions.recent(info)]
        self.live.begin(
            header,
            total=self.sessions.roster_size(info.section_id),
            present=self.sessions.present_count(info),
            recent=recent,
            end_label="End session",
        )
        self.stack.setCurrentIndex(self.LIVE)
        self.worker.set_suppress_outcomes(False)

    def on_matched(self, outcome: Outcome) -> None:
        if self.session is not None and outcome.usn:
            self.sessions.record_match(self.session, outcome.usn, outcome.score)
            if self.sync_worker is not None:
                self.sync_worker.request_sync_now()
        if self._feedback:
            self._feedback(outcome)

    def end_session_flow(self) -> None:
        if self.session is None:
            self.show_idle()
            return
        if self._ask_pin("Teacher PIN to end the session") is None:
            return
        self.sessions.end(self.session)
        self.session = None
        if self.sync_worker is not None:
            self.sync_worker.request_sync_now()
        self.show_idle()

    # ------------------------------------------------------------------ test modes
    def _all_templates_recognizer(self) -> Recognizer:
        calibration = self.store.calibration()
        match_config: MatchConfig = (
            self.config.match_config(*calibration) if calibration else self.config.match_config()
        )
        return Recognizer(TemplateIndex(self.store.load_templates()), match_config)

    def begin_test_mode(self) -> None:
        """Recognise against every cached template without recording anything."""
        self.session = None
        self.test_mode = True
        self.worker.swap_recognizer(self._all_templates_recognizer())
        self.live.begin(
            "Test recognition (nothing is recorded)",
            total=0,
            present=0,
            recent=[],
            end_label="Back",
        )
        self.stack.setCurrentIndex(self.LIVE)
        self.worker.set_suppress_outcomes(False)

    def begin_camera_test(self) -> None:
        self.session = None
        self.test_mode = False
        self.live.begin("Camera test", total=0, present=0, recent=[], end_label="Back")
        self.stack.setCurrentIndex(self.LIVE)
        self.worker.set_suppress_outcomes(True)

    # ------------------------------------------------------------------ enrol / settings
    def open_enrol(self) -> None:
        if self.client is None:
            self.idle.message.setText("Enrolment needs the server. Configure device.toml first.")
            return
        teacher = self._ask_pin("Teacher PIN")
        if teacher is None:
            return
        flow = EnrolmentFlow(
            self.client,
            self.store,
            Syncer(self.store, self.client, self.config.recognition.model_version),
        )
        self.worker.set_suppress_outcomes(True)
        dialog = EnrolDialog(
            flow,
            self.worker,
            teacher,
            on_enrolled=lambda _r: self.on_templates_refreshed(),
            parent=self,
        )
        dialog.exec_()
        self.show_idle()

    def open_settings(self) -> None:
        if self._ask_pin("Admin PIN", admin=True) is None:
            return
        self._refresh_settings()
        self.stack.setCurrentIndex(self.SETTINGS)

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
            "Network": " / ".join(str(v) for v in self.idle._network_info().values() if v) or "–",
            "Clock synced": "yes" if self._clock_synced() else "NO",
            "Config": str(self.config.source_path or "defaults"),
            "Database": str(self.store.path),
        }
        self.settings.show_info(rows, calibrated=calibration is not None)

    def sync_now(self) -> None:
        if self.sync_worker is None:
            self.settings.show_feedback("No server configured.")
            return
        self.sync_worker.request_sync_now()
        self.settings.show_feedback("Sync requested…")
        QTimer.singleShot(3000, self._refresh_settings)

    def restart(self) -> None:
        log.info("restart requested from Settings")
        if self._on_restart:
            self._on_restart()
        self.close()

    # ------------------------------------------------------------------ helpers for tests / sim
    def store_test_template(
        self, embedding: Array, usn: str = "TEST001", name: str = "Test User"
    ) -> None:
        self.store.add_template(usn, name, embedding, self.config.recognition.model_version, None)
        self.on_templates_refreshed()
