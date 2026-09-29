"""One clock for the whole deployment.

The event identity and window used to live only in configuration, read once at
import. That made the deadline a fact about the process rather than about the
event: extending it meant editing the environment and restarting, and nothing in
the database recorded that it had moved.

This module is the single place that answers "when does this event open, close,
and count votes", from two inputs:

  * **the deployment default** — `EVENT_*` / `VOTING_*`, or the imported dataset's
    window when `EVENT_SOURCE=fixtures`. This is authoritative whenever the
    `event_settings` table is empty, which is the state a fresh deployment is in.
    The acceptance brief's "a closed event refuses submissions" check therefore
    keeps passing on a cold boot with no console visit;
  * **the organiser's row**, if one exists. One row, enforced by a check
    constraint, carrying a revision, an actor, a note and a timestamp.

Every reader goes through here. `services.event_window()`, the community ballot,
the signed-record key publication, the archive, the whole-event bundle and the
public `/api/event` payload all resolve the window the same way, because a
deployment where the submission form and the certificate disagree about the
event's dates is worse than one that refuses to guess.

**The read path does not cache across requests.** `active(db)` resolves from the
session it is given, so a change made a millisecond ago is visible to the next
request and there is no staleness window to reason about. The one exception is a
caller with no session at all (a property, an offline script): those get the last
resolution, primed at startup and refreshed on every write. Readers inside a
request always pass their session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import EventSettings, Submission
from .timeutil import as_utc

# The singleton's id. The database enforces it (see migration 0010); this constant
# is how the application says so out loud.
SETTINGS_ID = 1

MAX_NAME = 160
MAX_NOTE = 500

# The deployment's own defaults, spelled once here rather than imported from
# `config`, so the console can say *why* a value is what it is.
DEPLOYMENT = "deployment"
ORGANISER = "organiser"

# The last resolution, for callers that cannot pass a session. Never used by a
# request that has a session — see the module docstring.
_CACHE: Optional["EventConfig"] = None


class InvalidWindow(ValueError):
    """A window that cannot be stored. The message is the user-facing reason."""

    def __init__(self, detail: str, field: str = "window") -> None:
        super().__init__(detail)
        self.detail = detail
        self.field = field


@dataclass(frozen=True)
class EventConfig:
    """The effective event clock, and where each part of it came from."""

    name: str
    starts_at: datetime
    ends_at: datetime
    voting_opens_at: datetime
    voting_closes_at: datetime
    source: str = DEPLOYMENT
    revision: int = 0
    note: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    @property
    def organiser_set(self) -> bool:
        return self.source == ORGANISER

    @property
    def length(self) -> timedelta:
        return self.ends_at - self.starts_at

    @property
    def voting_length(self) -> timedelta:
        return self.voting_closes_at - self.voting_opens_at

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "voting_opens_at": self.voting_opens_at.isoformat(),
            "voting_closes_at": self.voting_closes_at.isoformat(),
            "source": self.source,
            "revision": self.revision,
            "note": self.note,
            "updated_at": iso_or_none(self.updated_at),
            "updated_by": self.updated_by,
        }


def iso_or_none(value: Optional[datetime]) -> Optional[str]:
    moment = as_utc(value)
    return moment.isoformat() if moment is not None else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── the two inputs ───────────────────────────────────────────────────────────


def deployment_default() -> EventConfig:
    """The configured window: environment variables, or the dataset in fixture mode.

    Read from `config.settings` *at call time* rather than bound at import, so a
    test (or a rotated environment) that replaces the settings object is honoured
    by every reader.
    """
    settings = config.settings
    return EventConfig(
        name=settings.event_name,
        starts_at=settings.event_start,
        ends_at=settings.event_end,
        voting_opens_at=settings.voting_start,
        voting_closes_at=settings.voting_end,
        source=DEPLOYMENT,
    )


def _from_row(row: EventSettings, default: EventConfig) -> EventConfig:
    """The organiser's row, with the deployment's values as a last resort.

    The columns are NOT NULL, so the fallbacks here are belt-and-braces for a row
    written by something other than `apply()`; they are not a partial-override
    mechanism, deliberately, because "which field did the organiser actually
    take over" is not a question a deadline should raise.
    """
    return EventConfig(
        name=(row.name or "").strip() or default.name,
        starts_at=as_utc(row.starts_at) or default.starts_at,
        ends_at=as_utc(row.ends_at) or default.ends_at,
        voting_opens_at=as_utc(row.voting_opens_at) or default.voting_opens_at,
        voting_closes_at=as_utc(row.voting_closes_at) or default.voting_closes_at,
        source=ORGANISER,
        revision=int(row.revision or 1),
        note=row.note,
        updated_at=as_utc(row.updated_at),
        updated_by=row.updated_by_email,
    )


def row_for(db: Session) -> Optional[EventSettings]:
    """The singleton row, or None. One primary-key lookup: nothing to cache."""
    return db.get(EventSettings, SETTINGS_ID)


def resolve(db: Session) -> EventConfig:
    """The effective clock, read through this session."""
    default = deployment_default()
    row = row_for(db)
    return _from_row(row, default) if row is not None else default


def active(db: Optional[Session] = None) -> EventConfig:
    """The effective clock.

    With a session, this is always fresh. Without one it returns the last
    resolution — primed at startup and refreshed after every write — because a
    reader that has no session has no way to open one safely in the middle of a
    request (see the module docstring).
    """
    global _CACHE
    if db is not None:
        _CACHE = resolve(db)
        return _CACHE
    if _CACHE is None:
        return refresh()
    return _CACHE


def refresh(db: Optional[Session] = None) -> EventConfig:
    """Re-read the clock, using `db` if one is given.

    Called at startup, after every write, and by tests between cases. Any failure
    falls back to the deployment default rather than taking the process down: a
    database that cannot answer this question must not turn a health check into a
    crash.
    """
    global _CACHE
    if db is not None:
        _CACHE = resolve(db)
        return _CACHE
    try:
        from .db import SessionLocal

        with SessionLocal() as session:
            _CACHE = resolve(session)
    except Exception:  # noqa: BLE001 - see the docstring
        _CACHE = deployment_default()
    return _CACHE


def reset_cache() -> None:
    """Forget the cached resolution. Used by tests between schemas."""
    global _CACHE
    _CACHE = None


# ── the three questions every reader asks ────────────────────────────────────


def window(db: Optional[Session] = None) -> dict:
    """The submission window, in the shape `/api/event` has always published."""
    clock = active(db)
    now = now_utc()
    return {
        "now": now.isoformat(),
        "opens_at": clock.starts_at.isoformat(),
        "closes_at": clock.ends_at.isoformat(),
        "closed": now > clock.ends_at,
        "not_yet_open": now < clock.starts_at,
        "source": clock.source,
        "revision": clock.revision,
    }


def event_window_closed(db: Optional[Session] = None) -> bool:
    return now_utc() > active(db).ends_at


def voting_window(db: Optional[Session] = None) -> dict:
    """The community window. Its own clock, exactly as T3 requires."""
    clock = active(db)
    now = now_utc()
    opens_at, closes_at = clock.voting_opens_at, clock.voting_closes_at
    if now < opens_at:
        phase = "upcoming"
    elif now > closes_at:
        phase = "closed"
    else:
        phase = "open"
    return {
        "now": now.isoformat(),
        "opens_at": opens_at.isoformat(),
        "closes_at": closes_at.isoformat(),
        "phase": phase,
        "open": phase == "open",
        "closed": phase == "closed",
        # Results are published only after the window closes: before it opens
        # there is nothing to publish, and while it is open publishing would
        # measure the crowd rather than the projects.
        "results_visible": phase == "closed",
        "source": clock.source,
        "revision": clock.revision,
    }


def voting_results_visible(db: Optional[Session] = None) -> bool:
    return voting_window(db)["results_visible"]


def records_verifiable_publicly(db: Optional[Session] = None) -> bool:
    """Whether the record key may be published (T4). Follows the effective clock.

    Moving the close is therefore also how an organiser publishes the verification
    key early or holds it back — which is why the console warns before a change
    that would do it.
    """
    return event_window_closed(db)


# ── validation ───────────────────────────────────────────────────────────────


def validate(candidate: EventConfig) -> None:
    """Refuse a window that cannot mean anything. Raises `InvalidWindow`."""
    name = (candidate.name or "").strip()
    if not name:
        raise InvalidWindow("The event needs a name.", "name")
    if len(name) > MAX_NAME:
        raise InvalidWindow(f"The event name must be at most {MAX_NAME} characters.", "name")
    if candidate.note is not None and len(candidate.note) > MAX_NOTE:
        raise InvalidWindow(f"The note must be at most {MAX_NOTE} characters.", "note")
    if candidate.ends_at <= candidate.starts_at:
        raise InvalidWindow(
            "Submissions must close after they open.", "ends_at"
        )
    if candidate.voting_closes_at <= candidate.voting_opens_at:
        raise InvalidWindow(
            "Community voting must close after it opens.", "voting_closes_at"
        )
    # The two windows are independent on purpose (see JUDGING.md §8): an event is
    # entitled to keep the crowd voting after the submission deadline, and to open
    # the ballot before it. Only each window's own ordering is enforced, plus the
    # one relationship nobody can justify: a deadline more than a year before it
    # opens.
    if candidate.length > timedelta(days=366):
        raise InvalidWindow(
            "That submission window is longer than a year — check the dates.", "starts_at"
        )


def impact(db: Session, candidate: EventConfig) -> dict:
    """What changing the clock to `candidate` would do, in sentences an organiser reads.

    Deliberately *descriptive*, not a gate: every one of these is a situation an
    organiser may be trying to create (re-opening a deadline, publishing the
    community tally early, closing immediately). Warnings exist so the change is
    made knowingly, not so the console refuses it.
    """
    before = active(db)
    now = now_utc()
    warnings: list[str] = []

    was_open = now <= before.ends_at
    will_be_open = now <= candidate.ends_at
    if was_open and not will_be_open:
        warnings.append(
            "Submissions close immediately: the next submission or edit is refused."
        )
    if not was_open and will_be_open:
        warnings.append(
            "This re-opens the submission window — work may be submitted again."
        )
    if candidate.ends_at != before.ends_at and will_be_open:
        remaining = candidate.ends_at - now
        warnings.append(
            f"Submissions will remain open for another {_humanise(remaining)}."
        )
    if candidate.starts_at != before.starts_at:
        warnings.append(
            "Commit Integrity compares each repository against this window; existing "
            "checks are not re-run automatically."
        )

    before_results = now > before.voting_closes_at
    after_results = now > candidate.voting_closes_at
    if not before_results and after_results:
        warnings.append(
            "Community results become public immediately, and the signed-record key is published."
        )
    if before_results and not after_results:
        warnings.append(
            "Community results stop being public until the new voting close."
        )

    # Counted in Python rather than in SQL: SQLite hands back naive timestamps and
    # Postgres aware ones, and a comparison that means two different things on two
    # backends is exactly the bug this feature must not have. The event is small
    # by construction — this is a hackathon, not an archive — so the loop is the
    # honest implementation.
    late = [
        submission_id
        for submission_id, submitted_at in db.execute(
            select(Submission.id, Submission.submitted_at).where(
                Submission.status == "submitted",
                Submission.submitted_at.isnot(None),
            )
        ).all()
        if (as_utc(submitted_at) or now) > candidate.ends_at
    ]
    if late:
        warnings.append(
            f"{len(late)} already-submitted project{'' if len(late) == 1 else 's'} "
            f"carr{'ies' if len(late) == 1 else 'y'} a submitted-at after the new deadline."
        )

    return {
        "submissions_open_before": was_open,
        "submissions_open_after": will_be_open,
        "results_public_before": before_results,
        "results_public_after": after_results,
        "submissions_after_deadline": len(late),
        "submissions_after_deadline_ids": late[:20],
        "warnings": warnings,
    }


def _humanise(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} day{'s' if days != 1 else ''} and {hours} hour{'s' if hours != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


# ── writing ──────────────────────────────────────────────────────────────────


def apply(
    db: Session,
    *,
    name: Optional[str] = None,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
    voting_opens_at: Optional[datetime] = None,
    voting_closes_at: Optional[datetime] = None,
    note: Optional[str] = None,
    expected_revision: Optional[int] = None,
    actor=None,
) -> tuple[EventConfig, dict, EventConfig]:
    """Take the clock over (or change it). Returns (before, impact, after).

    A PATCH is partial in the ordinary sense: fields left out keep their current
    *effective* value, so "move the deadline to Friday" is one field and cannot
    silently reset the ballot window. The route is the caller; validation lives
    here so a script or a test cannot write a state the console would refuse.
    """
    before = active(db)
    if expected_revision is not None and expected_revision != before.revision:
        raise InvalidWindow(
            "Somebody else changed the event window while this form was open. "
            "Reload and re-apply your change.",
            "revision",
        )

    candidate = EventConfig(
        name=(name if name is not None else before.name),
        starts_at=_coerce(starts_at) or before.starts_at,
        ends_at=_coerce(ends_at) or before.ends_at,
        voting_opens_at=_coerce(voting_opens_at) or before.voting_opens_at,
        voting_closes_at=_coerce(voting_closes_at) or before.voting_closes_at,
        note=note if note is not None else before.note,
        source=ORGANISER,
    )
    validate(candidate)
    preview = impact(db, candidate)

    row = row_for(db)
    if row is None:
        row = EventSettings(id=SETTINGS_ID, revision=0)
        db.add(row)
    else:
        row.revision = int(row.revision or 1) + 1
    if row.revision == 0:
        row.revision = 1

    row.name = candidate.name.strip()
    row.starts_at = candidate.starts_at
    row.ends_at = candidate.ends_at
    row.voting_opens_at = candidate.voting_opens_at
    row.voting_closes_at = candidate.voting_closes_at
    row.note = candidate.note
    row.updated_by_id = getattr(actor, "id", None)
    row.updated_by_email = getattr(actor, "email", None)
    db.flush()
    # `updated_at` is a server default (and an `onupdate`), so the value the row
    # now holds is the database's, not Python's. Read it back rather than
    # reporting the timestamp of whatever this process happened to think.
    db.refresh(row)

    after = _from_row(row, deployment_default())
    _prime(after)
    return before, preview, after


def clear(db: Session, *, actor=None) -> EventConfig:
    """Hand the clock back to the deployment's configuration."""
    row = row_for(db)
    if row is not None:
        db.delete(row)
        db.flush()
    after = deployment_default()
    _prime(after)
    return after


def _prime(clock: EventConfig) -> None:
    global _CACHE
    _CACHE = clock


def _coerce(value: Optional[datetime]) -> Optional[datetime]:
    """Any datetime the API is given becomes aware UTC.

    A naive value is read as UTC (`timeutil`'s rule) rather than as local time,
    because the alternative is a deadline that depends on the server's timezone
    table — the class of bug this project keeps writing tests against.
    """
    if value is None:
        return None
    return as_utc(value)


__all__ = [
    "DEPLOYMENT",
    "EventConfig",
    "InvalidWindow",
    "MAX_NAME",
    "MAX_NOTE",
    "ORGANISER",
    "SETTINGS_ID",
    "active",
    "apply",
    "clear",
    "deployment_default",
    "event_window_closed",
    "impact",
    "now_utc",
    "records_verifiable_publicly",
    "refresh",
    "reset_cache",
    "resolve",
    "row_for",
    "validate",
    "voting_results_visible",
    "voting_window",
    "window",
]
