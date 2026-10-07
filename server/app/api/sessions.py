"""Device-created sessions and the idempotent attendance batch endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from common.schemas import (
    AttendanceBatchIn,
    AttendanceBatchOut,
    SessionEndIn,
    SessionEndOut,
    SessionIn,
    SessionOut,
)
from server.app.auth import CurrentDevice, DbSession
from server.app.models import AttendanceSession
from server.app.services import attendance

router = APIRouter()


@router.post("/sessions", response_model=SessionOut)
def create_session(payload: SessionIn, db: DbSession, device: CurrentDevice) -> SessionOut:
    try:
        session = attendance.upsert_session(db, device, payload)
    except attendance.SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return SessionOut(id=session.id, status=session.status.value)


@router.post("/sessions/{session_id}/end", response_model=SessionEndOut)
def end_session(
    session_id: uuid.UUID, payload: SessionEndIn, db: DbSession, device: CurrentDevice
) -> SessionEndOut:
    session = db.get(AttendanceSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    try:
        present, absent_marked = attendance.end_session(db, device, session, payload.ended_at)
    except attendance.SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return SessionEndOut(
        id=session.id,
        status=session.status.value,
        present=present,
        absent_marked=absent_marked,
    )


@router.post("/attendance/batch", response_model=AttendanceBatchOut)
def attendance_batch(
    payload: AttendanceBatchIn, db: DbSession, device: CurrentDevice
) -> AttendanceBatchOut:
    result = attendance.ingest_batch(db, device, payload.events)
    return AttendanceBatchOut(
        accepted=result.accepted, duplicates=result.duplicates, rejected=result.rejected
    )
