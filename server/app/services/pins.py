"""Device PIN helpers.

PINs are verified on the device against synced argon2 hashes, so two faculty with the
same PIN would be indistinguishable there. ``pin_in_use`` checks a candidate PIN
against every other active account's hash when a PIN is set; ``faculty_for_pin``
does the same lookup for the online ``/api/v1/auth/pin`` endpoint.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.auth import Hasher
from server.app.models import Faculty


def _candidates(db: Session, exclude_id: int | None = None) -> list[Faculty]:
    stmt = select(Faculty).where(Faculty.active, Faculty.pin_hash.is_not(None))
    if exclude_id is not None:
        stmt = stmt.where(Faculty.id != exclude_id)
    return list(db.scalars(stmt))


def pin_in_use(db: Session, hasher: Hasher, pin: str, *, exclude_id: int | None = None) -> bool:
    return any(hasher.verify(f.pin_hash, pin) for f in _candidates(db, exclude_id))


def faculty_for_pin(db: Session, hasher: Hasher, pin: str) -> Faculty | None:
    """The account whose PIN matches, or None. Linear in the number of faculty."""
    for faculty in _candidates(db):
        if hasher.verify(faculty.pin_hash, pin):
            return faculty
    return None
