"""On-device enrolment logic, independent of Qt so it can be unit-tested.

Flow: pick a student from the roster -> student taps "I consent" -> three frames that
pass the quality gates are captured -> the aligned 112x112 crops are uploaded -> the
crops are dropped from memory -> templates are re-synced so the device can recognise
the student straight away. Crops are never written to disk on the device.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import cv2

from common.face.types import Array, FloatArray
from common.schemas import ConsentIn, EnrolmentCapturesIn, EnrolmentCapturesOut, RosterStudentOut
from device.app.client import ServerClient, ServerUnavailableError
from device.app.store import DeviceStore
from device.app.sync import Syncer

log = logging.getLogger(__name__)

DEFAULT_NOTICE = (
    "By tapping I consent you agree that the college stores a mathematical template of "
    "your face (not photographs) and a few aligned test images on the attendance server, "
    "used only to mark your attendance. You can withdraw at any time via the department."
)


@dataclass
class Capture:
    """One accepted frame: embedding, aligned crop (memory only) and its blur score."""

    embedding: FloatArray
    aligned: Array
    blur: float


@dataclass
class EnrolmentState:
    student: RosterStudentOut
    faculty_id: int
    consent_at: datetime | None
    consent_version: str
    captures: list[Capture] = field(default_factory=list)


class EnrolmentFlow:
    def __init__(
        self,
        client: ServerClient,
        store: DeviceStore,
        syncer: Syncer,
        *,
        required_captures: int = 3,
    ) -> None:
        self.client = client
        self.store = store
        self.syncer = syncer
        self.required = required_captures
        self.state: EnrolmentState | None = None

    # ------------------------------------------------------------------ data for the screens
    def sections(self) -> list[dict[str, object]]:
        return self.store.get_catalog("sections")

    def roster(self, section_id: int) -> list[RosterStudentOut]:
        """Live roster when online; the last cached copy otherwise."""
        key = f"roster:{section_id}"
        try:
            payload = self.client.roster(section_id)
        except ServerUnavailableError as exc:
            log.warning("roster fetch failed (%s); using cached roster", exc)
            return [RosterStudentOut.model_validate(row) for row in self.store.get_catalog(key)]
        rows = [{"id": s.student_id, **s.model_dump()} for s in payload.students]
        self.store.put_catalog(key, rows)
        return payload.students

    def consent_text(self) -> tuple[str, str]:
        version = self.store.get_setting("consent_version") or "unversioned"
        notice = self.store.get_setting("consent_notice") or DEFAULT_NOTICE
        return version, notice

    # ------------------------------------------------------------------ the flow itself
    def start(self, student: RosterStudentOut, faculty_id: int) -> EnrolmentState:
        version, _ = self.consent_text()
        self.state = EnrolmentState(
            student=student, faculty_id=faculty_id, consent_at=None, consent_version=version
        )
        return self.state

    def give_consent(self, when: datetime | None = None) -> None:
        state = self._require_state()
        state.consent_at = when or datetime.now(UTC)

    def add_capture(self, capture: Capture) -> int:
        """Store one capture; returns how many are still needed."""
        state = self._require_state()
        if state.consent_at is None and not state.student.has_consent:
            raise PermissionError("consent must be given before capturing")
        if len(state.captures) < self.required:
            state.captures.append(capture)
        return self.remaining

    @property
    def remaining(self) -> int:
        state = self._require_state()
        return max(0, self.required - len(state.captures))

    @property
    def ready(self) -> bool:
        return self.state is not None and len(self.state.captures) >= self.required

    def build_payload(self) -> EnrolmentCapturesIn:
        state = self._require_state()
        if len(state.captures) < self.required:
            raise ValueError(f"need {self.required} captures, have {len(state.captures)}")
        crops = []
        for capture in state.captures:
            ok, buffer = cv2.imencode(".png", capture.aligned)
            if not ok:
                raise ValueError("could not encode crop as PNG")
            crops.append(base64.b64encode(buffer.tobytes()).decode("ascii"))
        consent = (
            ConsentIn(consent_at=state.consent_at, consent_version=state.consent_version)
            if state.consent_at is not None
            else None
        )
        return EnrolmentCapturesIn(
            faculty_id=state.faculty_id,
            crops_png_b64=crops,
            consent=consent,
            blur_scores=[c.blur for c in state.captures],
        )

    def upload(self) -> EnrolmentCapturesOut:
        """Send the crops, forget them, refresh templates. Raises on rejection/network errors."""
        state = self._require_state()
        payload = self.build_payload()
        try:
            result = self.client.upload_captures(state.student.usn, payload)
        finally:
            # Whatever happened, the images do not stay on the device.
            self.discard()
        log.info("enrolled %s: %d templates added", result.usn, result.templates_added)
        try:
            self.syncer.pull_templates()
        except ServerUnavailableError as exc:
            log.warning("template refresh after enrolment failed: %s", exc)
        return result

    def discard(self) -> None:
        if self.state is not None:
            self.state.captures.clear()
        self.state = None

    def _require_state(self) -> EnrolmentState:
        if self.state is None:
            raise RuntimeError("enrolment not started")
        return self.state
