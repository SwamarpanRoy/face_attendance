"""Keep the device and the server in step: prefetch, outbox upload, heartbeat.

The device prefetches everything for *all* of its assigned sections (templates,
catalog, rosters, faculty PIN hashes, calibration), so once it has been online after
the latest enrolments it can run any session with no network. Attendance flows the
other way through the outbox: sessions first, then events in batches, then session
ends, with exponential backoff while offline and idempotent UUIDs so a retry never
duplicates a record. Nothing here touches images.

``SyncWorker.tick`` holds all the scheduling decisions and takes the time as an
argument, so tests drive it deterministically without threads or sleeps.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from common.face.embedder import bytes_to_embedding
from common.schemas import (
    AttendanceBatchIn,
    AttendanceEventIn,
    CatalogOut,
    HeartbeatIn,
    SessionEndIn,
    SessionIn,
    TemplatesOut,
)
from device.app.client import DeviceRevokedError, ServerClient, ServerUnavailableError
from device.app.config import SyncSection
from device.app.store import DeviceStore

log = logging.getLogger(__name__)

SINCE_KEY = "templates_since"
LAST_SYNC_KEY = "last_sync_at"
CATALOG_SYNC_KEY = "catalog_synced_at"
SINCE_OVERLAP = timedelta(seconds=60)


class ModelMismatchError(RuntimeError):
    """Server templates were computed with a different model than this device runs."""


@dataclass(frozen=True)
class TemplateSyncResult:
    full: bool
    students: int
    templates: int
    deleted: int
    calibrated: bool


def _parse_since(value: str | None) -> datetime | None:
    """Last sync time minus an overlap, so a row committed just after our fetch is not missed.

    Merging is idempotent (per-student replace), so re-receiving a student is harmless.
    """
    return datetime.fromisoformat(value) - SINCE_OVERLAP if value else None


def apply_templates(
    store: DeviceStore, payload: TemplatesOut, model_version: str
) -> TemplateSyncResult:
    """Merge a /templates response into the store. Pure function of (store, payload)."""
    if payload.model_version != model_version:
        raise ModelMismatchError(
            f"server model {payload.model_version!r} != device model {model_version!r}; "
            "update device.toml [recognition].model_version and the models/ folder"
        )
    templates = 0
    if payload.full:
        rows = [
            (
                student.usn,
                student.name,
                student.section_id,
                template.idx,
                bytes_to_embedding(base64.b64decode(template.embedding_b64)),
                template.model_version,
            )
            for student in payload.students
            for template in student.templates
        ]
        templates = store.replace_all_templates(rows)
    else:
        if payload.deleted_usns:
            store.delete_student_templates(list(payload.deleted_usns))
        for student in payload.students:
            embeddings = [
                (t.idx, bytes_to_embedding(base64.b64decode(t.embedding_b64)), t.model_version)
                for t in student.templates
            ]
            templates += store.put_student_templates(
                student.usn, student.name, student.section_id, embeddings
            )

    if payload.calibrated and payload.threshold is not None and payload.margin is not None:
        store.set_calibration(payload.threshold, payload.margin, payload.model_version)
    else:
        store.clear_calibration()
    store.set_setting(SINCE_KEY, payload.generated_at.isoformat())
    store.set_setting(LAST_SYNC_KEY, datetime.now(UTC).isoformat())
    return TemplateSyncResult(
        full=payload.full,
        students=len(payload.students),
        templates=templates,
        deleted=len(payload.deleted_usns),
        calibrated=payload.calibrated,
    )


def apply_catalog(store: DeviceStore, payload: CatalogOut) -> None:
    store.put_catalog("courses", [c.model_dump() for c in payload.courses])
    store.put_catalog("sections", [s.model_dump() for s in payload.sections])
    store.put_catalog("periods", [p.model_dump() for p in payload.periods])
    store.put_catalog(
        "offerings", [{"id": i, **o.model_dump()} for i, o in enumerate(payload.offerings, start=1)]
    )
    store.replace_faculty_pins([(f.id, f.name, f.role, f.pin_hash) for f in payload.faculty])
    store.set_setting("consent_version", payload.consent_version)
    store.set_setting("consent_notice", payload.consent_notice)
    store.set_setting(CATALOG_SYNC_KEY, datetime.now(UTC).isoformat())


class Syncer:
    """One-shot pulls (catalog, rosters, templates) used by the worker and Settings > Sync now."""

    def __init__(self, store: DeviceStore, client: ServerClient, model_version: str) -> None:
        self.store = store
        self.client = client
        self.model_version = model_version

    def pull_templates(self, *, full: bool = False) -> TemplateSyncResult:
        since = None if full else _parse_since(self.store.get_setting(SINCE_KEY))
        payload = self.client.templates(since)
        result = apply_templates(self.store, payload, self.model_version)
        log.info(
            "templates synced: full=%s students=%d templates=%d deleted=%d calibrated=%s",
            result.full,
            result.students,
            result.templates,
            result.deleted,
            result.calibrated,
        )
        return result

    def pull_catalog(self) -> CatalogOut:
        payload = self.client.catalog()
        apply_catalog(self.store, payload)
        for section in payload.sections:
            try:
                roster = self.client.roster(section.id)
            except ServerUnavailableError as exc:
                log.warning("roster for section %s not refreshed: %s", section.id, exc)
                continue
            self.store.put_catalog(
                f"roster:{section.id}",
                [{"id": s.student_id, **s.model_dump()} for s in roster.students],
            )
        log.info(
            "catalog synced: %d courses, %d sections, %d periods, %d faculty",
            len(payload.courses),
            len(payload.sections),
            len(payload.periods),
            len(payload.faculty),
        )
        return payload

    def pull_all(self, *, full: bool = False) -> TemplateSyncResult:
        self.pull_catalog()
        return self.pull_templates(full=full)


# --------------------------------------------------------------------------- outbox
@dataclass(frozen=True)
class DrainResult:
    sessions: int = 0
    events: int = 0
    duplicates: int = 0
    rejected: int = 0
    ends: int = 0


def drain_outbox(store: DeviceStore, client: ServerClient, batch_size: int = 200) -> DrainResult:
    """Upload in dependency order: sessions, then events (batches), then session ends."""
    sessions = events = duplicates = rejected = ends = 0
    for row in store.unsynced_sessions():
        client.create_session(
            SessionIn(
                id=uuid.UUID(row["session_uuid"]),
                course_id=row["course_id"],
                section_id=row["section_id"],
                faculty_id=row["faculty_id"],
                period_id=row["period_id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                clock_synced=bool(row["clock_synced"]),
            )
        )
        store.mark_session_synced(row["session_uuid"])
        sessions += 1

    while True:
        pending = store.pending_events(batch_size)
        if not pending:
            break
        batch = AttendanceBatchIn(
            events=[
                AttendanceEventIn(
                    event_uuid=uuid.UUID(e["event_uuid"]),
                    session_id=uuid.UUID(e["session_uuid"]),
                    usn=e["usn"],
                    score=e["score"],
                    captured_at=datetime.fromisoformat(e["captured_at"]),
                    clock_synced=bool(e["clock_synced"]),
                )
                for e in pending
            ]
        )
        outcome = client.attendance_batch(batch)
        done = [str(u) for u in outcome.accepted] + [str(u) for u in outcome.duplicates]
        for bad_uuid, reason in outcome.rejected.items():
            log.warning("event %s rejected by server: %s", bad_uuid, reason)
            done.append(str(bad_uuid))  # never retry a permanently rejected event
        done_set = set(done)
        # Defensive: if the server omitted an id, do not loop forever on it.
        done.extend(e["event_uuid"] for e in pending if e["event_uuid"] not in done_set)
        store.mark_events_synced(done, datetime.now(UTC))
        events += len(outcome.accepted)
        duplicates += len(outcome.duplicates)
        rejected += len(outcome.rejected)

    for row in store.pending_session_ends():
        client.end_session(
            uuid.UUID(row["session_uuid"]),
            SessionEndIn(
                ended_at=datetime.fromisoformat(row["ended_at"]),
                clock_synced=bool(row["clock_synced"]),
            ),
        )
        store.mark_session_end_synced(row["session_uuid"])
        ends += 1
    return DrainResult(sessions, events, duplicates, rejected, ends)


# --------------------------------------------------------------------------- worker
@dataclass(frozen=True)
class SyncStatus:
    online: bool = False
    revoked: bool = False
    queue_len: int = 0
    last_sync_at: str | None = None
    last_heartbeat_at: datetime | None = None
    last_error: str | None = None
    retry_in_s: float = 0.0
    calibrated: bool = False


class SyncWorker(threading.Thread):
    """Background scheduler; ``tick`` is the whole policy and is unit-tested directly."""

    def __init__(
        self,
        store: DeviceStore,
        client: ServerClient,
        config: SyncSection,
        model_version: str,
        heartbeat_payload: Callable[[], HeartbeatIn],
        *,
        on_status: Callable[[SyncStatus], None] | None = None,
        on_templates: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="sync", daemon=True)
        self.store = store
        self.client = client
        self.config = config
        self.syncer = Syncer(store, client, model_version)
        self._heartbeat_payload = heartbeat_payload
        self._on_status = on_status
        self._on_templates = on_templates
        self._clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.status = SyncStatus(calibrated=store.calibration() is not None)
        self._next_heartbeat = 0.0
        self._next_prefetch = 0.0
        self._next_upload = 0.0
        self._retry_at = 0.0
        self._failures = 0

    # -- control
    def request_sync_now(self) -> None:
        self._next_prefetch = 0.0
        self._retry_at = 0.0
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick(self._clock())
            except Exception:
                log.exception("sync tick failed")
            self._wake.wait(1.0)
            self._wake.clear()

    # -- policy
    def tick(self, now: float) -> SyncStatus:
        if self.status.revoked or now < self._retry_at:
            return self._publish(retry_in_s=max(0.0, self._retry_at - now))
        try:
            if now >= self._next_heartbeat:
                self.client.heartbeat(self._heartbeat_payload())
                self._next_heartbeat = now + self.config.heartbeat_s
                self.status = replace(self.status, last_heartbeat_at=datetime.now(UTC))
            if now >= self._next_prefetch:
                self.syncer.pull_all()
                self._next_prefetch = now + self.config.prefetch_interval_s
                if self._on_templates:
                    self._on_templates()
            if now >= self._next_upload and self._has_uploads():
                drained = drain_outbox(self.store, self.client, self.config.batch_size)
                self._next_upload = now + self.config.interval_s
                if drained.events or drained.sessions or drained.ends:
                    log.info("outbox drained: %s", drained)
        except DeviceRevokedError:
            log.error("device revoked by the server: wiping cached templates, catalog and PINs")
            self.store.wipe_cache()
            return self._publish(online=False, revoked=True, last_error="Device revoked")
        except (ServerUnavailableError, ModelMismatchError, OSError) as exc:
            self._failures += 1
            delay = min(self.config.backoff_max_s, self.config.interval_s * (2**self._failures))
            self._retry_at = now + delay
            self._next_heartbeat = 0.0  # heartbeat first when we come back
            log.warning("server unavailable (%s); retrying in %.0f s", exc, delay)
            return self._publish(online=False, last_error=str(exc), retry_in_s=delay)
        self._failures = 0
        return self._publish(online=True, last_error=None, retry_in_s=0.0)

    def _has_uploads(self) -> bool:
        return bool(
            self.store.pending_count()
            or self.store.unsynced_sessions()
            or self.store.pending_session_ends()
        )

    def _publish(self, **changes: object) -> SyncStatus:
        self.status = replace(
            self.status,
            queue_len=self.store.pending_count(),
            last_sync_at=self.store.get_setting(LAST_SYNC_KEY),
            calibrated=self.store.calibration() is not None,
            **changes,  # type: ignore[arg-type]
        )
        if self._on_status:
            self._on_status(self.status)
        return self.status
