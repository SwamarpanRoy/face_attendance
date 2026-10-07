"""Audit trail helpers.

Every change a human makes to attendance, consent or biometric data goes through
``record`` so the before/after snapshot and the reason end up in one place. The DB
``CHECK`` constraint on ``audit_log`` rejects attendance edits without a reason; this
module raises a clearer error first.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from server.app.models import EDIT_ACTIONS, AuditLog


class ReasonRequiredError(ValueError):
    """Raised when an edit action is recorded without a non-empty reason."""


def record(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: int | str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditLog:
    """Add an audit row to the current transaction (the caller commits)."""
    cleaned = reason.strip() if reason else None
    if action in EDIT_ACTIONS and not cleaned:
        raise ReasonRequiredError(f"{action} requires a reason")
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=before,
        after=after,
        reason=cleaned,
    )
    db.add(row)
    return row
