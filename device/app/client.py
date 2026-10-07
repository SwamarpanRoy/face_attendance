"""Thin HTTP client for the device API (httpx), injected into whatever needs the server.

Keeping all requests here means timeouts, the bearer header and the "revoked" signal
are handled in one place, and tests can swap in a fake with the same methods.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from common.schemas import (
    AttendanceBatchIn,
    AttendanceBatchOut,
    CatalogOut,
    EnrolmentCapturesIn,
    EnrolmentCapturesOut,
    HeartbeatIn,
    HeartbeatOut,
    PinAuthOut,
    RosterOut,
    SessionEndIn,
    SessionEndOut,
    SessionIn,
    SessionOut,
    TemplatesOut,
)

log = logging.getLogger(__name__)


class DeviceRevokedError(RuntimeError):
    """The server says this device was revoked: the caller must wipe the local cache."""


class ServerUnavailableError(RuntimeError):
    """Network problem or 5xx; the caller should back off and retry later."""


class EnrolmentRejectedError(RuntimeError):
    """The server refused an enrolment (no consent, bad crop, wrong section)."""

    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ServerStatus:
    reachable: bool
    detail: str


class ServerClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float = 5.0,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if http is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s, headers=headers)
        else:
            # Tests inject Starlette's TestClient (an httpx.Client talking ASGI in-process).
            self._client = http
            self._client.headers.update(headers)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServerUnavailableError(str(exc)) from exc
        if response.status_code == 401 and response.headers.get("x-device-status") == "revoked":
            raise DeviceRevokedError(response.text)
        if response.status_code >= 500:
            raise ServerUnavailableError(f"{response.status_code} from {path}")
        return response

    # ------------------------------------------------------------------ endpoints
    def health(self) -> ServerStatus:
        try:
            response = self._request("GET", "/healthz")
        except ServerUnavailableError as exc:
            return ServerStatus(False, str(exc))
        return ServerStatus(response.status_code == 200, f"HTTP {response.status_code}")

    def heartbeat(self, payload: HeartbeatIn) -> HeartbeatOut:
        response = self._request(
            "POST", "/api/v1/devices/heartbeat", json=payload.model_dump(mode="json")
        )
        response.raise_for_status()
        return HeartbeatOut.model_validate(response.json())

    def catalog(self) -> CatalogOut:
        response = self._request("GET", "/api/v1/catalog")
        response.raise_for_status()
        return CatalogOut.model_validate(response.json())

    def roster(self, section_id: int) -> RosterOut:
        response = self._request("GET", "/api/v1/roster", params={"section_id": section_id})
        response.raise_for_status()
        return RosterOut.model_validate(response.json())

    def verify_pin(self, pin: str) -> PinAuthOut | None:
        response = self._request("POST", "/api/v1/auth/pin", json={"pin": pin})
        if response.status_code == 401:
            return None
        response.raise_for_status()
        return PinAuthOut.model_validate(response.json())

    def templates(self, since: datetime | None = None) -> TemplatesOut:
        params = {"since": since.isoformat()} if since else None
        response = self._request("GET", "/api/v1/templates", params=params)
        response.raise_for_status()
        return TemplatesOut.model_validate(response.json())

    def upload_captures(self, usn: str, payload: EnrolmentCapturesIn) -> EnrolmentCapturesOut:
        """Upload aligned crops; a 4xx becomes ``EnrolmentRejectedError`` with the reason."""
        response = self._request(
            "POST", f"/api/v1/enrolment/{usn}/captures", json=payload.model_dump(mode="json")
        )
        if 400 <= response.status_code < 500:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise EnrolmentRejectedError(str(detail), response.status_code)
        response.raise_for_status()
        return EnrolmentCapturesOut.model_validate(response.json())

    # ------------------------------------------------------------------ sessions / outbox
    def create_session(self, payload: SessionIn) -> SessionOut:
        response = self._request("POST", "/api/v1/sessions", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return SessionOut.model_validate(response.json())

    def end_session(self, session_id: uuid.UUID, payload: SessionEndIn) -> SessionEndOut:
        response = self._request(
            "POST", f"/api/v1/sessions/{session_id}/end", json=payload.model_dump(mode="json")
        )
        response.raise_for_status()
        return SessionEndOut.model_validate(response.json())

    def attendance_batch(self, payload: AttendanceBatchIn) -> AttendanceBatchOut:
        response = self._request(
            "POST", "/api/v1/attendance/batch", json=payload.model_dump(mode="json")
        )
        response.raise_for_status()
        return AttendanceBatchOut.model_validate(response.json())
