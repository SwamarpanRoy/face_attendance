"""Offline PIN verification against the argon2 hashes synced from the server.

PINs unlock device actions only. Because the hashes live on the device, a short
lockout after repeated failures is the main defence against someone tapping through
the number pad; the server also audits PIN changes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from device.app.store import DeviceStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FacultyRef:
    id: int
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class PinLockedError(RuntimeError):
    def __init__(self, seconds_left: float) -> None:
        super().__init__(f"Too many wrong PINs. Try again in {int(seconds_left) + 1} s.")
        self.seconds_left = seconds_left


class PinVerifier:
    def __init__(
        self, store: DeviceStore, *, max_failures: int = 5, lockout_s: float = 60.0
    ) -> None:
        self.store = store
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self._hasher = PasswordHasher()
        self._failures = 0
        self._locked_until = 0.0

    def verify(self, pin: str, *, now: float | None = None) -> FacultyRef | None:
        """Return the matching faculty member, None for a wrong PIN; raises when locked."""
        current = time.monotonic() if now is None else now
        if current < self._locked_until:
            raise PinLockedError(self._locked_until - current)
        for row in self.store.faculty_pins():
            pin_hash = row.get("pin_hash")
            if not pin_hash:
                continue
            try:
                if self._hasher.verify(str(pin_hash), pin):
                    self._failures = 0
                    return FacultyRef(int(row["faculty_id"]), str(row["name"]), str(row["role"]))
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                continue
        self._failures += 1
        if self._failures >= self.max_failures:
            self._failures = 0
            self._locked_until = current + self.lockout_s
            log.warning("PIN pad locked for %.0f s after repeated failures", self.lockout_s)
        return None

    @property
    def has_pins(self) -> bool:
        return any(row.get("pin_hash") for row in self.store.faculty_pins())
