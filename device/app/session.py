"""Session lifecycle on the device: start, record matches, end, resume. No Qt here.

Sessions and events are written to the local store first and uploaded by the sync
worker, so a session can run with the WiFi off from boot as long as the device synced
once after the latest enrolments.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.face.matcher import Recognizer, TemplateIndex
from device.app.config import DeviceConfig
from device.app.store import DeviceStore

log = logging.getLogger(__name__)

# Sections in the picker come from the synced catalog, so "no templates" means nobody in
# the section has an enrolled face yet (a session would only mark everyone absent).
NO_TEMPLATES_MESSAGE = "No faces enrolled for this section yet. Use Enrol first, then try again."


@dataclass(frozen=True)
class SessionInfo:
    uuid: str
    course_id: int
    section_id: int
    period_id: int | None
    faculty_id: int
    started_at: datetime
    course_label: str
    section_label: str
    period_label: str


class SessionManager:
    def __init__(
        self,
        store: DeviceStore,
        config: DeviceConfig,
        *,
        clock: Callable[[], datetime] | None = None,
        clock_synced: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._clock_synced = clock_synced or (lambda: True)

    # ------------------------------------------------------------------ catalog helpers
    def _label(self, kind: str, item_id: int | None, fmt: Callable[[dict[str, Any]], str]) -> str:
        if item_id is None:
            return "–"
        for item in self.store.get_catalog(kind):
            if int(item["id"]) == item_id:
                return fmt(item)
        return f"{kind} {item_id}"

    def course_options(self) -> list[dict[str, Any]]:
        """Courses that are taught to at least one cached section."""
        offered = {int(o["course_id"]) for o in self.store.get_catalog("offerings")}
        return [c for c in self.store.get_catalog("courses") if int(c["id"]) in offered]

    def section_options(self, course_id: int) -> list[dict[str, Any]]:
        wanted = {
            int(o["section_id"])
            for o in self.store.get_catalog("offerings")
            if int(o["course_id"]) == course_id
        }
        return [s for s in self.store.get_catalog("sections") if int(s["id"]) in wanted]

    def period_options(self) -> list[dict[str, Any]]:
        return self.store.get_catalog("periods")

    def faculty_for_offering(self, course_id: int, section_id: int) -> int | None:
        for offering in self.store.get_catalog("offerings"):
            if (
                int(offering["course_id"]) == course_id
                and int(offering["section_id"]) == section_id
            ):
                return int(offering["faculty_id"])
        return None

    def roster_size(self, section_id: int) -> int:
        roster = self.store.get_catalog(f"roster:{section_id}")
        if roster:
            return len(roster)
        return self.store.template_counts_by_section().get(section_id, 0)

    # ------------------------------------------------------------------ lifecycle
    def blocker(self, section_id: int) -> str | None:
        """Why a session cannot start for this section, or None when it can."""
        if self.store.template_counts_by_section().get(section_id, 0) == 0:
            return NO_TEMPLATES_MESSAGE
        return None

    def start(
        self, *, course_id: int, section_id: int, period_id: int | None, faculty_id: int
    ) -> SessionInfo:
        blocker = self.blocker(section_id)
        if blocker:
            raise RuntimeError(blocker)
        now = self._clock()
        session_uuid = str(uuid.uuid4())
        self.store.create_session(
            session_uuid,
            course_id=course_id,
            section_id=section_id,
            period_id=period_id,
            faculty_id=faculty_id,
            started_at=now,
            clock_synced=self._clock_synced(),
        )
        log.info(
            "session %s started: course=%s section=%s period=%s",
            session_uuid,
            course_id,
            section_id,
            period_id,
        )
        return self._info(self.store.get_session(session_uuid) or {})

    def resume(self) -> SessionInfo | None:
        row = self.store.open_session()
        return self._info(row) if row else None

    def _info(self, row: dict[str, Any]) -> SessionInfo:
        return SessionInfo(
            uuid=str(row["session_uuid"]),
            course_id=int(row["course_id"]),
            section_id=int(row["section_id"]),
            period_id=int(row["period_id"]) if row.get("period_id") is not None else None,
            faculty_id=int(row["faculty_id"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            course_label=self._label(
                "courses", int(row["course_id"]), lambda c: f"{c['code']} {c['name']}"
            ),
            section_label=self._label("sections", int(row["section_id"]), lambda s: str(s["name"])),
            period_label=self._label(
                "periods",
                int(row["period_id"]) if row.get("period_id") is not None else None,
                lambda p: f"{p['name']} {p['start_time']}–{p['end_time']}",
            ),
        )

    def build_recognizer(self, session: SessionInfo) -> tuple[Recognizer, bool]:
        """Matcher over the section's templates with the cooldown restored from the outbox."""
        calibration = self.store.calibration()
        match_config = (
            self.config.match_config(*calibration) if calibration else self.config.match_config()
        )
        templates = self.store.load_templates(session.section_id)
        recognizer = Recognizer(TemplateIndex(templates), match_config)
        recognizer.mark_known(self.store.session_marked_usns(session.uuid))
        return recognizer, calibration is not None

    def record_match(self, session: SessionInfo, usn: str, score: float | None) -> str:
        event_uuid = str(uuid.uuid4())
        self.store.add_event(
            event_uuid,
            session_uuid=session.uuid,
            usn=usn,
            score=score,
            captured_at=self._clock(),
            clock_synced=self._clock_synced(),
        )
        return event_uuid

    def present_count(self, session: SessionInfo) -> int:
        return len(self.store.session_marked_usns(session.uuid))

    def recent(self, session: SessionInfo, limit: int = 5) -> list[dict[str, Any]]:
        return self.store.session_events(session.uuid, limit)

    def end(self, session: SessionInfo) -> None:
        self.store.end_session(session.uuid, self._clock())
        log.info("session %s ended with %d present", session.uuid, self.present_count(session))
