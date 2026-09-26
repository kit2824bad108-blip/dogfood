"""Timestamps: one place that decides what a stored datetime means.

SQLite — the backend for the offline and demo paths — returns *naive* datetimes
even from `DateTime(timezone=True)` columns, while Postgres returns aware ones.
Left alone, that difference leaks into the API: a browser reads a bare
"2026-08-04T00:00:00" as **local** time, which can move a submission deadline by
hours for exactly the users who cannot check it.

The rule here is deliberately boring: a naive timestamp is UTC. This application
only ever stores UTC (the importer normalises on the way in, and the event window
is computed in UTC), so there is nothing to guess at. Every serializer that emits
a timestamp goes through `iso()`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Attach UTC to a naive datetime, or convert an aware one to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    """An ISO-8601 string with an explicit offset, or None."""
    moment = as_utc(value)
    return moment.isoformat() if moment is not None else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["as_utc", "iso", "utcnow"]
