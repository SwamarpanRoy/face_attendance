"""Authentication and authorisation for the admin UI and the device API.

Three separate credentials exist on purpose:

* **Admin password** (argon2id) unlocks the web dashboard. A session is a signed,
  HttpOnly cookie carrying the faculty id and a per-session CSRF token.
* **Device PIN** (argon2id, digits only) unlocks actions on a device. PIN hashes are
  synced to devices for offline checks, so a stolen device could brute-force them;
  that is why a PIN can never log in to the dashboard.
* **Device token** (32 random bytes, stored as SHA-256) authenticates a device to the
  JSON API as a bearer token. It is shown once at registration and never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.config import Settings
from server.app.db import get_db
from server.app.models import Device, Faculty, Role

SESSION_SALT = "admin-session"
LOGIN_CSRF_SALT = "login-csrf"
LOGIN_CSRF_COOKIE = "fa_login_csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def get_settings(request: Request) -> Settings:
    """The immutable settings object attached by the app factory."""
    settings: Settings = request.app.state.settings
    return settings


# --------------------------------------------------------------------------- hashing
class Hasher:
    """argon2id for passwords and PINs. Parameters come from Settings (lower in tests)."""

    def __init__(self, settings: Settings) -> None:
        self._ph = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Verified against when the account has no hash, so "unknown email" and
        # "wrong password" take the same time.
        self._dummy = self._ph.hash(secrets.token_hex(8))

    def hash(self, secret: str) -> str:
        return self._ph.hash(secret)

    def verify(self, hashed: str | None, secret: str) -> bool:
        target = hashed or self._dummy
        try:
            ok = self._ph.verify(target, secret)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return bool(ok) and hashed is not None

    def needs_rehash(self, hashed: str) -> bool:
        return self._ph.check_needs_rehash(hashed)


def get_hasher(request: Request) -> Hasher:
    hasher: Hasher = request.app.state.hasher
    return hasher


def pin_problem(pin: str, settings: Settings) -> str | None:
    """Return a plain-English reason the PIN is unacceptable, or None if it is fine."""
    if not pin.isdigit():
        return "PIN must contain digits only."
    if not settings.pin_min_length <= len(pin) <= settings.pin_max_length:
        return f"PIN must be {settings.pin_min_length} to {settings.pin_max_length} digits."
    if len(set(pin)) == 1:
        return "PIN must not be a single repeated digit."
    return None


# --------------------------------------------------------------------------- login throttling
class LoginThrottle:
    """In-memory failure counter per email and per client IP with a lockout window.

    Good enough for one server process on a campus LAN; it resets on restart, which is
    acceptable because the lockout only slows down guessing, argon2 does the real work.
    """

    def __init__(self, max_failures: int, lockout_seconds: float) -> None:
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        window = self._failures.get(key, [])
        window = [t for t in window if now - t < self.lockout_seconds]
        self._failures[key] = window
        return window

    def seconds_blocked(self, *keys: str, now: float | None = None) -> float:
        """Zero when allowed, otherwise how long until the oldest failure expires."""
        current = time.monotonic() if now is None else now
        with self._lock:
            worst = 0.0
            for key in keys:
                window = self._prune(key, current)
                if len(window) >= self.max_failures:
                    worst = max(worst, self.lockout_seconds - (current - window[0]))
            return worst

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            for key in keys:
                self._prune(key, current).append(current)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


def get_login_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


# --------------------------------------------------------------------------- device tokens
def generate_device_token() -> tuple[str, str]:
    """Return ``(token, sha256_hex)``. Store only the hash; show the token once."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- sessions
@dataclass(frozen=True)
class AdminSession:
    """What the signed session cookie carries. Roles are re-read from the DB each request."""

    user_id: int
    csrf: str


class SessionCodec:
    """Signs and reads the admin session cookie and the pre-login CSRF cookie."""

    def __init__(self, settings: Settings) -> None:
        self._sessions = URLSafeTimedSerializer(settings.secret_key, salt=SESSION_SALT)
        self._login = URLSafeTimedSerializer(settings.secret_key, salt=LOGIN_CSRF_SALT)
        self.cookie_name = settings.session_cookie_name
        self.max_age = settings.session_max_age_seconds
        self.secure = settings.session_cookie_secure

    # -- admin session
    def new_session(self, user_id: int) -> AdminSession:
        return AdminSession(user_id=user_id, csrf=secrets.token_urlsafe(32))

    def read(self, request: Request) -> AdminSession | None:
        raw = request.cookies.get(self.cookie_name)
        if not raw:
            return None
        try:
            data = self._sessions.loads(raw, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return None
        try:
            return AdminSession(user_id=int(data["uid"]), csrf=str(data["csrf"]))
        except (KeyError, TypeError, ValueError):
            return None

    def write(self, response: Response, session: AdminSession) -> None:
        response.set_cookie(
            self.cookie_name,
            self._sessions.dumps({"uid": session.user_id, "csrf": session.csrf}),
            max_age=self.max_age,
            httponly=True,
            samesite="lax",
            secure=self.secure,
            path="/",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie(self.cookie_name, path="/")

    # -- pre-login CSRF (double submit, signed so it cannot be forged)
    def issue_login_csrf(self, response: Response, token: str | None = None) -> str:
        token = token or secrets.token_urlsafe(32)
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            self._login.dumps(token),
            max_age=3600,
            httponly=True,
            samesite="lax",
            secure=self.secure,
            path="/admin/login",
        )
        return token

    def read_login_csrf(self, request: Request) -> str | None:
        raw = request.cookies.get(LOGIN_CSRF_COOKIE)
        if not raw:
            return None
        try:
            value = self._login.loads(raw, max_age=3600)
        except (BadSignature, SignatureExpired):
            return None
        return str(value)


def get_session_codec(request: Request) -> SessionCodec:
    codec: SessionCodec = request.app.state.session_codec
    return codec


def read_admin_session(
    request: Request, codec: Annotated[SessionCodec, Depends(get_session_codec)]
) -> AdminSession | None:
    return codec.read(request)


def current_user_optional(
    session: Annotated[AdminSession | None, Depends(read_admin_session)],
    db: Annotated[Session, Depends(get_db)],
) -> Faculty | None:
    """The logged-in faculty member, or None. Inactive accounts are treated as logged out."""
    if session is None:
        return None
    user = db.get(Faculty, session.user_id)
    if user is None or not user.active:
        return None
    return user


def require_user(
    request: Request, user: Annotated[Faculty | None, Depends(current_user_optional)]
) -> Faculty:
    """Redirect anonymous browsers to the login page, remembering where they were going."""
    if user is None:
        target = f"/admin/login?next={quote(request.url.path)}"
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": target})
    return user


def require_admin(user: Annotated[Faculty, Depends(require_user)]) -> Faculty:
    if user.role is not Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only.")
    return user


async def verify_csrf(
    request: Request,
    session: Annotated[AdminSession | None, Depends(read_admin_session)],
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
) -> None:
    """Double-submit CSRF check for every state-changing admin request.

    The expected token is the one in the admin session, or, before login, the one in
    the signed login cookie. It may arrive as a form field or (for HTMX) as a header.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = session.csrf if session else codec.read_login_csrf(request)
    supplied: str | None = request.headers.get(CSRF_HEADER)
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FORM_FIELD)
            supplied = value if isinstance(value, str) else None
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Form expired or CSRF token invalid. Reload the page and try again.",
        )


# --------------------------------------------------------------------------- device API
def require_device(request: Request, db: Annotated[Session, Depends(get_db)]) -> Device:
    """Resolve the bearer token to an active device, or 401."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    device = db.scalar(select(Device).where(Device.token_hash == hash_token(token)))
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown device token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not device.active:
        # The device app treats this exact detail as "wipe the local cache".
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device revoked.",
            headers={"WWW-Authenticate": "Bearer", "X-Device-Status": "revoked"},
        )
    return device


CurrentUser = Annotated[Faculty, Depends(require_user)]
AdminUser = Annotated[Faculty, Depends(require_admin)]
CurrentDevice = Annotated[Device, Depends(require_device)]
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
PasswordHasherDep = Annotated[Hasher, Depends(get_hasher)]
