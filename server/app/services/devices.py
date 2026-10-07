"""Device registration, section assignment and revocation (admin UI)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from server.app.auth import generate_device_token
from server.app.models import Device, DeviceSection, Section
from server.app.services import audit


def register(db: Session, name: str, *, actor_id: int) -> tuple[Device, str]:
    """Create a device and return it with its plaintext token (shown once). Commits."""
    token, token_hash = generate_device_token()
    device = Device(name=name, token_hash=token_hash)
    db.add(device)
    db.flush()
    audit.record(
        db,
        actor_id=actor_id,
        action="device.register",
        entity="device",
        entity_id=device.id,
        after={"name": name},
    )
    db.commit()
    return device, token


def rotate_token(db: Session, device: Device, *, actor_id: int) -> str:
    """New token (old one stops working immediately); also re-activates. Commits."""
    token, token_hash = generate_device_token()
    device.token_hash = token_hash
    device.active = True
    device.revoked_at = None
    audit.record(
        db, actor_id=actor_id, action="device.rotate_token", entity="device", entity_id=device.id
    )
    db.commit()
    return token


def revoke(db: Session, device: Device, *, actor_id: int, reason: str) -> None:
    device.active = False
    device.revoked_at = datetime.now(UTC)
    audit.record(
        db,
        actor_id=actor_id,
        action="device.revoke",
        entity="device",
        entity_id=device.id,
        reason=reason or None,
    )
    db.commit()


def assign_sections(
    db: Session, device: Device, section_ids: list[int], *, actor_id: int
) -> list[int]:
    valid = (
        set(db.scalars(select(Section.id).where(Section.id.in_(section_ids))))
        if section_ids
        else set()
    )
    before = sorted(
        db.scalars(select(DeviceSection.section_id).where(DeviceSection.device_id == device.id))
    )
    db.execute(delete(DeviceSection).where(DeviceSection.device_id == device.id))
    for section_id in sorted(valid):
        db.add(DeviceSection(device_id=device.id, section_id=section_id))
    db.flush()
    after = sorted(valid)
    audit.record(
        db,
        actor_id=actor_id,
        action="device.assign_sections",
        entity="device",
        entity_id=device.id,
        before={"sections": before},
        after={"sections": after},
    )
    db.commit()
    return after
