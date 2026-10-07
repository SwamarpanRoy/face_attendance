"""Outbox persistence, drain order, backoff and revocation, driven without threads."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest

from common.schemas import (
    AttendanceBatchOut,
    CatalogOut,
    HeartbeatIn,
    HeartbeatOut,
    SessionEndOut,
    SessionOut,
    TemplatesOut,
)
from device.app.client import DeviceRevokedError, ServerUnavailableError
from device.app.config import DeviceConfig, SyncSection
from device.app.session import NO_TEMPLATES_MESSAGE, SessionManager
from device.app.store import DeviceStore
from device.app.sync import SyncWorker, drain_outbox

MODEL = "buffalo_s/w600k_mbf"


def _vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeServer:
    """Remembers what it received so tests can assert no duplicates ever get through."""

    def __init__(self) -> None:
        self.offline = False
        self.revoked = False
        self.sessions: dict[str, dict] = {}
        self.events: dict[str, dict] = {}
        self.ends: list[str] = []
        self.heartbeats = 0
        self.catalog_calls = 0
        self.template_calls = 0
        self.batches: list[int] = []

    def _gate(self) -> None:
        if self.revoked:
            raise DeviceRevokedError("revoked")
        if self.offline:
            raise ServerUnavailableError("offline")

    def heartbeat(self, payload: HeartbeatIn) -> HeartbeatOut:
        self._gate()
        self.heartbeats += 1
        return HeartbeatOut(
            device_name="pi",
            server_time=datetime.now(UTC),
            server_version="x",
            model_version=MODEL,
            assigned_section_ids=[1],
        )

    def catalog(self) -> CatalogOut:
        self._gate()
        self.catalog_calls += 1
        return CatalogOut(
            generated_at=datetime.now(UTC),
            courses=[],
            sections=[],
            periods=[],
            offerings=[],
            faculty=[],
        )

    def templates(self, since=None) -> TemplatesOut:
        self._gate()
        self.template_calls += 1
        return TemplatesOut(
            generated_at=datetime.now(UTC),
            model_version=MODEL,
            calibrated=False,
            threshold=None,
            margin=None,
            full=since is None,
            students=[],
            deleted_usns=[],
        )

    def roster(self, section_id: int):
        raise AssertionError("no sections in this fake catalog")

    def create_session(self, payload) -> SessionOut:
        self._gate()
        self.sessions.setdefault(str(payload.id), payload.model_dump())
        return SessionOut(id=payload.id, status="live")

    def end_session(self, session_id, payload) -> SessionEndOut:
        self._gate()
        self.ends.append(str(session_id))
        return SessionEndOut(id=session_id, status="ended", present=0, absent_marked=0)

    def attendance_batch(self, payload) -> AttendanceBatchOut:
        self._gate()
        self.batches.append(len(payload.events))
        accepted, duplicates, rejected = [], [], {}
        for event in payload.events:
            key = str(event.event_uuid)
            if key in self.events:
                duplicates.append(event.event_uuid)
            elif event.usn == "REJECT":
                rejected[event.event_uuid] = "unknown student"
            elif str(event.session_id) not in self.sessions:
                rejected[event.event_uuid] = "unknown session"
            else:
                self.events[key] = event.model_dump()
                accepted.append(event.event_uuid)
        return AttendanceBatchOut(accepted=accepted, duplicates=duplicates, rejected=rejected)


@pytest.fixture
def store(tmp_path):
    s = DeviceStore(tmp_path / "device.db")
    s.put_catalog("courses", [{"id": 1, "code": "22EC71", "name": "VLSI"}])
    s.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}, {"id": 2, "name": "ECE-7B"}])
    s.put_catalog(
        "periods",
        [{"id": 1, "ordinal": 1, "name": "P1", "start_time": "09:00", "end_time": "10:00"}],
    )
    s.put_catalog("offerings", [{"id": 1, "course_id": 1, "section_id": 1, "faculty_id": 7}])
    s.put_catalog(
        "roster:1", [{"id": i, "usn": f"1BM22EC00{i}", "name": f"S{i}"} for i in range(1, 6)]
    )
    for i in range(1, 4):
        s.add_template(f"1BM22EC00{i}", f"S{i}", _vec(i), MODEL, section_id=1)
    yield s
    s.close()


def _manager(store, clock_synced=True):
    config = DeviceConfig()
    return SessionManager(store, config, clock_synced=lambda: clock_synced)


def test_session_manager_offline_start_record_resume_end(store):
    manager = _manager(store, clock_synced=False)
    assert manager.blocker(2) == NO_TEMPLATES_MESSAGE
    with pytest.raises(RuntimeError, match="No faces enrolled"):
        manager.start(course_id=1, section_id=2, period_id=None, faculty_id=7)
    assert [c["code"] for c in manager.course_options()] == ["22EC71"]
    assert [s["name"] for s in manager.section_options(1)] == ["ECE-7A"]
    assert manager.faculty_for_offering(1, 1) == 7 and manager.roster_size(1) == 5

    session = manager.start(course_id=1, section_id=1, period_id=1, faculty_id=7)
    assert session.course_label == "22EC71 VLSI" and session.section_label == "ECE-7A"
    assert session.period_label.startswith("P1")
    recognizer, calibrated = manager.build_recognizer(session)
    assert recognizer.index.n_students == 3 and calibrated is False

    manager.record_match(session, "1BM22EC001", 0.8)
    manager.record_match(session, "1BM22EC002", 0.7)
    assert manager.present_count(session) == 2
    assert [e["usn"] for e in manager.recent(session)] == ["1BM22EC002", "1BM22EC001"]
    pending = store.pending_events()
    assert len(pending) == 2 and all(p["clock_synced"] == 0 for p in pending)

    # App restarts mid-class: the open session is resumable and the cooldown is restored.
    resumed = manager.resume()
    assert resumed is not None and resumed.uuid == session.uuid
    recognizer2, _ = manager.build_recognizer(resumed)
    assert recognizer2.marked == {"1BM22EC001", "1BM22EC002"}

    manager.end(session)
    assert manager.resume() is None
    assert store.get_session(session.uuid)["ended_at"] is not None


def test_drain_uploads_sessions_then_events_then_ends_without_duplicates(store):
    server = FakeServer()
    manager = _manager(store)
    session = manager.start(course_id=1, section_id=1, period_id=1, faculty_id=7)
    for i in range(1, 4):
        manager.record_match(session, f"1BM22EC00{i}", 0.9)
    store.add_event(
        str(uuid.uuid4()),
        session_uuid=session.uuid,
        usn="REJECT",
        score=None,
        captured_at=datetime.now(UTC),
        clock_synced=True,
    )
    manager.end(session)

    result = drain_outbox(store, server, batch_size=2)  # type: ignore[arg-type]
    assert (result.sessions, result.events, result.duplicates, result.rejected, result.ends) == (
        1,
        3,
        0,
        1,
        1,
    )
    assert server.batches == [2, 2]  # batches respect the size limit
    assert set(server.sessions) == {session.uuid} and server.ends == [session.uuid]
    assert store.pending_count() == 0

    # Draining again sends nothing new; events remain unique on the server.
    again = drain_outbox(store, server)  # type: ignore[arg-type]
    assert (again.sessions, again.events, again.ends) == (0, 0, 0)
    assert len(server.events) == 3 and len(server.ends) == 1


def test_worker_backs_off_offline_and_recovers_with_no_duplicates(store):
    server = FakeServer()
    server.offline = True
    statuses = []
    sync = SyncSection(
        interval_s=5, heartbeat_s=30, prefetch_interval_s=600, backoff_max_s=300, batch_size=200
    )
    worker = SyncWorker(
        store,
        server,
        sync,
        MODEL,  # type: ignore[arg-type]
        lambda: HeartbeatIn(device_id="pi", app_version="0.1.0", queue_len=store.pending_count()),
        on_status=statuses.append,
        clock=lambda: 0.0,
    )
    manager = _manager(store)
    session = manager.start(course_id=1, section_id=1, period_id=None, faculty_id=7)
    manager.record_match(session, "1BM22EC001", 0.9)

    s0 = worker.tick(0.0)
    assert s0.online is False and s0.queue_len == 1 and s0.retry_in_s == 10.0  # 5 * 2**1
    assert worker.tick(5.0).retry_in_s == 5.0  # still waiting, nothing sent
    assert server.heartbeats == 0
    s1 = worker.tick(10.0)
    assert s1.retry_in_s == 20.0  # 5 * 2**2: exponential
    for t in (30.0, 70.0, 150.0, 310.0):
        worker.tick(t)
    assert worker.tick(610.0).retry_in_s == 300.0  # capped at backoff_max_s

    server.offline = False
    s_online = worker.tick(910.0)
    assert s_online.online is True and s_online.queue_len == 0 and s_online.last_error is None
    assert server.heartbeats == 1 and server.catalog_calls == 1 and server.template_calls == 1
    assert len(server.events) == 1 and set(server.sessions) == {session.uuid}

    # Steady state: heartbeat every 30 s, prefetch every 600 s, uploads only when queued.
    worker.tick(911.0)
    assert server.heartbeats == 1
    manager.record_match(session, "1BM22EC002", 0.8)
    worker.tick(916.0)
    assert len(server.events) == 2
    worker.tick(941.0)
    assert server.heartbeats == 2 and server.catalog_calls == 1
    worker.tick(1511.0)
    assert server.catalog_calls == 2
    assert len(server.events) == 2  # no duplicates after all those ticks


def test_revoked_device_wipes_cache_and_stops(store):
    server = FakeServer()
    server.revoked = True
    worker = SyncWorker(
        store,
        server,
        SyncSection(),
        MODEL,  # type: ignore[arg-type]
        lambda: HeartbeatIn(device_id="pi", app_version="0.1.0", queue_len=0),
        clock=lambda: 0.0,
    )
    status = worker.tick(0.0)
    assert status.revoked is True and status.online is False
    assert (
        store.load_templates() == []
        and store.get_catalog("sections") == []
        and store.faculty_pins() == []
    )
    server.revoked = False
    assert worker.tick(100.0).revoked is True  # stays stopped until the app is reconfigured
    assert server.heartbeats == 0


def test_request_sync_now_resets_the_schedule(store):
    server = FakeServer()
    worker = SyncWorker(
        store,
        server,
        SyncSection(prefetch_interval_s=600),
        MODEL,  # type: ignore[arg-type]
        lambda: HeartbeatIn(device_id="pi", app_version="0.1.0", queue_len=0),
        clock=lambda: 0.0,
    )
    worker.tick(0.0)
    worker.tick(100.0)
    assert server.catalog_calls == 1
    worker.request_sync_now()
    worker.tick(101.0)
    assert server.catalog_calls == 2
