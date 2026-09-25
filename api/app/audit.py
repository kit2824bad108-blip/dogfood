"""Append-only audit trail.

Every score submission, alteration, role change and archive action lands here
with a timestamp and the client IP.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditLog, User


def record(
    db: Session,
    action: str,
    *,
    actor: Optional[User] = None,
    entity: Optional[str] = None,
    entity_id: Optional[Any] = None,
    ip: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        ip=ip,
        details=details or {},
    )
    db.add(entry)
    db.flush()
    return entry
