"""Device-local SQLite: template cache, catalog, PIN hashes, settings, sessions, outbox.

Only embeddings are ever stored for faces, never images. ``sqlite3`` from the standard
library keeps the Pi footprint small; WAL mode lets the sync thread write while the
pipeline reads. Sessions and attendance events are created here first (with UUIDs) and
uploaded later, which is what makes the device work through WiFi drops.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from common.face.embedder import bytes_to_embedding, embedding_to_bytes
from common.face.matcher import Template

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    usn TEXT NOT NULL,
    name TEXT NOT NULL,
    section_id INTEGER,
    template_idx INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (usn, template_idx)
);
CREATE INDEX IF NOT EXISTS ix_templates_section ON templates(section_id);
CREATE TABLE IF NOT EXISTS catalog (
    kind TEXT NOT NULL,
    id INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS faculty_pins (
    faculty_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    pin_hash TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_uuid TEXT PRIMARY KEY,
    course_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL,
    period_id INTEGER,
    faculty_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    clock_synced INTEGER NOT NULL DEFAULT 1,
    synced INTEGER NOT NULL DEFAULT 0,
    end_synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
    event_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    usn TEXT NOT NULL,
    score REAL,
    captured_at TEXT NOT NULL,
    clock_synced INTEGER NOT NULL DEFAULT 1,
    synced_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox(synced_at) WHERE synced_at IS NULL;
"""

CACHE_SETTING_KEYS = ("threshold", "margin", "calibration_model_version")


@dataclass(frozen=True)
class StoreCounts:
    templates: int
    students: int
    pending_events: int


class DeviceStore:
    """Thread-safe (one re-entrant lock) wrapper over the device database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns older device databases lack (SQLite has no ALTER ... IF NOT EXISTS)."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "end_synced" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN end_synced INTEGER NOT NULL DEFAULT 0"
            )
        if "period_id" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN period_id INTEGER")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def calibration(self) -> tuple[float, float] | None:
        """(threshold, margin) delivered by the server, or None while uncalibrated."""
        threshold = self.get_setting("threshold")
        margin = self.get_setting("margin")
        if threshold is None or margin is None:
            return None
        return float(threshold), float(margin)

    def set_calibration(self, threshold: float, margin: float, model_version: str) -> None:
        self.set_setting("threshold", repr(threshold))
        self.set_setting("margin", repr(margin))
        self.set_setting("calibration_model_version", model_version)

    def clear_calibration(self) -> None:
        marks = ",".join("?" for _ in CACHE_SETTING_KEYS)
        with self._lock:
            self._conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", CACHE_SETTING_KEYS)

    # ------------------------------------------------------------------ templates
    def load_templates(self, section_id: int | None = None) -> list[Template]:
        query = "SELECT usn, name, embedding FROM templates"
        params: tuple[Any, ...] = ()
        if section_id is not None:
            query += " WHERE section_id = ?"
            params = (section_id,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY usn, template_idx", params).fetchall()
        return [
            Template(
                usn=row["usn"], name=row["name"], embedding=bytes_to_embedding(row["embedding"])
            )
            for row in rows
        ]

    def template_counts_by_section(self) -> dict[int | None, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT section_id, COUNT(DISTINCT usn) AS n FROM templates GROUP BY section_id"
            ).fetchall()
        return {row["section_id"]: int(row["n"]) for row in rows}

    def replace_all_templates(
        self, rows: Iterable[tuple[str, str, int | None, int, np.ndarray, str]]
    ) -> int:
        """Full snapshot from the server: wipe the cache and insert every row."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM templates")
            count = 0
            for usn, name, section_id, idx, embedding, model_version in rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO templates"
                    "(usn, name, section_id, template_idx, embedding, model_version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (usn, name, section_id, idx, embedding_to_bytes(embedding), model_version),
                )
                count += 1
            self._conn.execute("COMMIT")
        return count

    def put_student_templates(
        self,
        usn: str,
        name: str,
        section_id: int | None,
        templates: Iterable[tuple[int, np.ndarray, str]],
    ) -> int:
        """Incremental sync: the templates of one student are replaced as a unit."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM templates WHERE usn = ?", (usn,))
            count = 0
            for idx, embedding, model_version in templates:
                self._conn.execute(
                    "INSERT INTO templates"
                    "(usn, name, section_id, template_idx, embedding, model_version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (usn, name, section_id, idx, embedding_to_bytes(embedding), model_version),
                )
                count += 1
            self._conn.execute("COMMIT")
        return count

    def replace_section_templates(
        self, section_id: int, rows: Iterable[tuple[str, str, int, np.ndarray, str]]
    ) -> int:
        """Overwrite one section with ``(usn, name, idx, embedding, model_version)`` rows."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM templates WHERE section_id = ?", (section_id,))
            count = 0
            for usn, name, idx, embedding, model_version in rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO templates"
                    "(usn, name, section_id, template_idx, embedding, model_version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (usn, name, section_id, idx, embedding_to_bytes(embedding), model_version),
                )
                count += 1
            self._conn.execute("COMMIT")
        return count

    def add_template(
        self,
        usn: str,
        name: str,
        embedding: np.ndarray,
        model_version: str,
        section_id: int | None = None,
    ) -> int:
        """Append one template for a student (used by the simulator's test enrolment)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(template_idx), -1) + 1 AS nxt FROM templates WHERE usn = ?",
                (usn,),
            ).fetchone()
            idx = int(row["nxt"])
            self._conn.execute(
                "INSERT INTO templates"
                "(usn, name, section_id, template_idx, embedding, model_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (usn, name, section_id, idx, embedding_to_bytes(embedding), model_version),
            )
        return idx

    def delete_student_templates(self, usns: Sequence[str]) -> int:
        if not usns:
            return 0
        marks = ",".join("?" for _ in usns)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM templates WHERE usn IN ({marks})", tuple(usns)
            )
        return int(cursor.rowcount)

    # ------------------------------------------------------------------ catalog
    def put_catalog(self, kind: str, items: Iterable[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM catalog WHERE kind = ?", (kind,))
            for item in items:
                self._conn.execute(
                    "INSERT INTO catalog(kind, id, data) VALUES (?, ?, ?)",
                    (kind, int(item["id"]), json.dumps(item)),
                )
            self._conn.execute("COMMIT")

    def get_catalog(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM catalog WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    # ------------------------------------------------------------------ faculty pins
    def replace_faculty_pins(self, rows: Iterable[tuple[int, str, str, str | None]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM faculty_pins")
            self._conn.executemany(
                "INSERT INTO faculty_pins(faculty_id, name, role, pin_hash) VALUES (?, ?, ?, ?)",
                list(rows),
            )
            self._conn.execute("COMMIT")

    def faculty_pins(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT faculty_id, name, role, pin_hash FROM faculty_pins ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ sessions
    def create_session(
        self,
        session_uuid: str,
        *,
        course_id: int,
        section_id: int,
        period_id: int | None,
        faculty_id: int,
        started_at: datetime,
        clock_synced: bool,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(session_uuid, course_id, section_id, period_id, "
                "faculty_id, started_at, clock_synced) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_uuid,
                    course_id,
                    section_id,
                    period_id,
                    faculty_id,
                    started_at.isoformat(),
                    int(clock_synced),
                ),
            )

    def get_session(self, session_uuid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_uuid = ?", (session_uuid,)
            ).fetchone()
        return dict(row) if row else None

    def open_session(self) -> dict[str, Any] | None:
        """The most recent session that was never ended (e.g. the app restarted mid-class)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def end_session(self, session_uuid: str, ended_at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_uuid = ? AND ended_at IS NULL",
                (ended_at.isoformat(), session_uuid),
            )

    def unsynced_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE synced = 0 ORDER BY started_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_session_synced(self, session_uuid: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET synced = 1 WHERE session_uuid = ?", (session_uuid,)
            )

    def pending_session_ends(self) -> list[dict[str, Any]]:
        """Ended sessions whose end has not reached the server (and whose start has)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE ended_at IS NOT NULL AND synced = 1 "
                "AND end_synced = 0 ORDER BY ended_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_session_end_synced(self, session_uuid: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET end_synced = 1 WHERE session_uuid = ?", (session_uuid,)
            )

    # ------------------------------------------------------------------ outbox
    def add_event(
        self,
        event_uuid: str,
        *,
        session_uuid: str,
        usn: str,
        score: float | None,
        captured_at: datetime,
        clock_synced: bool,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO outbox(event_uuid, session_uuid, usn, score, captured_at, "
                "clock_synced) VALUES (?, ?, ?, ?, ?, ?)",
                (event_uuid, session_uuid, usn, score, captured_at.isoformat(), int(clock_synced)),
            )

    def pending_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE synced_at IS NULL ORDER BY captured_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_events_synced(self, event_uuids: Sequence[str], synced_at: datetime) -> None:
        if not event_uuids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE outbox SET synced_at = ? WHERE event_uuid = ?",
                [(synced_at.isoformat(), uuid) for uuid in event_uuids],
            )

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE synced_at IS NULL"
            ).fetchone()
        return int(row[0])

    def session_marked_usns(self, session_uuid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT usn FROM outbox WHERE session_uuid = ? ORDER BY usn",
                (session_uuid,),
            ).fetchall()
        return [str(row["usn"]) for row in rows]

    def session_events(self, session_uuid: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE session_uuid = ? "
                "ORDER BY captured_at DESC, rowid DESC LIMIT ?",
                (session_uuid, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ housekeeping
    def wipe_cache(self) -> None:
        """Device revoked: drop everything synced from the server (attendance history stays)."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            for table in ("templates", "catalog", "faculty_pins", "settings"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.execute("COMMIT")

    def counts(self) -> StoreCounts:
        with self._lock:
            templates = self._conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
            students = self._conn.execute("SELECT COUNT(DISTINCT usn) FROM templates").fetchone()[0]
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE synced_at IS NULL"
            ).fetchone()[0]
        return StoreCounts(
            templates=int(templates), students=int(students), pending_events=int(pending)
        )
