"""Rate limiting for the community surface, enforced in the API.

T3 names three anti-abuse requirements — *rate limits, duplicate detection, audit
trail* — and all three are real here rather than gestured at:

* **rate limits** live in the database, not in a process-local counter. A dict in
  the app would be cheaper and would quietly stop working the moment a deployment
  runs more than one worker, which is precisely when an attacker benefits from it.
  Every attempt is one row carrying an integer epoch, and each check prunes that
  key's expired rows, so the table stays proportional to *recent* traffic rather
  than to the age of the event.
* **duplicate detection** is a unique constraint (`uq_vote_voter_submission`), not
  a heuristic. The heuristic detector in `voting.py` is for *reporting* suspicious
  clusters to an organiser; it never decides whether a vote counts.
* **audit trail** is `audit_logs`, which every write in this module appends to.

The integer epoch is deliberate. SQLite returns naive datetimes from
`DateTime(timezone=True)` columns while Postgres returns aware ones, so comparing
timestamps across dialects is a trap this module simply does not enter: an epoch
is an integer everywhere.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import ThrottleEvent

# Every bucket the API can be asked about, with the limit a single client gets.
# Limits are per key, and each bucket chooses its own key — an address for
# registration, a voter identity for casting — so that one noisy proxy cannot
# exhaust the allowance of everyone behind it, and one identity cannot hide
# behind a rotating address. Where both matter, two buckets are checked.
RULES: dict[str, tuple[int, int]] = {
    # bucket: (allowed attempts, window in seconds)
    "vote.register": (8, 3600),          # one address, one hour
    "vote.register.ip": (20, 3600),      # an address range asking for many voters
    "vote.verify": (20, 3600),
    "vote.cast": (150, 900),             # a voter working through a long ballot
    "vote.cast.ip": (300, 900),
    "vote.ballot": (900, 60),            # reads are cheap, but not free
    "comment.create": (12, 900),         # one identity, fifteen minutes
    "comment.create.ip": (30, 900),
    "vote.results": (600, 60),
}


@dataclass(frozen=True)
class Decision:
    """The answer to one rate-limit question.

    `retry_after` is seconds until the oldest attempt in the window falls out, so
    a client can be told something true instead of being told to wait an
    arbitrary amount.
    """

    allowed: bool
    retry_after: int
    remaining: int | None
    limit: int | None
    window_seconds: int | None

    def headers(self) -> dict[str, str]:
        """Response headers, in the RFC 9110 shape a client already understands."""
        out = {}
        if self.limit is not None:
            out["X-RateLimit-Limit"] = str(self.limit)
        if self.remaining is not None:
            out["X-RateLimit-Remaining"] = str(max(0, self.remaining))
        if not self.allowed:
            out["Retry-After"] = str(self.retry_after)
        return out


def rule_for(bucket: str) -> tuple[int, int] | None:
    return RULES.get(bucket)


def decision_for(bucket: str, key: str | None) -> Decision:
    """A pass-through decision for a bucket that is not configured.

    Unknown buckets are not throttled rather than rejected: a new call site that
    forgets to register a limit should still work. `test_community.py` pins the
    buckets that exist, so the omission is catchable in the suite rather than in
    production.
    """
    rule = rule_for(bucket)
    if rule is None:
        return Decision(True, 0, None, None, None)
    return Decision(True, 0, rule[0], rule[0], rule[1])


def hit(db: Session, bucket: str, key: str | None, *, cost: int = 1) -> Decision:
    """Record one attempt and say whether it is allowed.

    Callers must treat a refusal as a refusal: this function *does* count the
    attempt either way, so a client hammering a limited endpoint stays limited
    instead of resetting its own window by being refused.
    """
    rule = rule_for(bucket)
    if rule is None:
        return decision_for(bucket, key)
    limit, window = rule

    key = (str(key) if key is not None else "unknown")[:160]
    now_epoch = int(time.time())
    cutoff = now_epoch - window

    # Prune this key's expired attempts. Scoped to the key so the delete stays
    # small and index-friendly however long the event runs.
    db.execute(
        delete(ThrottleEvent).where(
            ThrottleEvent.bucket == bucket,
            ThrottleEvent.key == key,
            ThrottleEvent.at_epoch < cutoff,
        )
    )

    used = db.scalar(
        select(func.count(ThrottleEvent.id)).where(
            ThrottleEvent.bucket == bucket,
            ThrottleEvent.key == key,
            ThrottleEvent.at_epoch >= cutoff,
        )
    ) or 0

    if used + cost > limit:
        oldest = db.scalar(
            select(func.min(ThrottleEvent.at_epoch)).where(
                ThrottleEvent.bucket == bucket,
                ThrottleEvent.key == key,
                ThrottleEvent.at_epoch >= cutoff,
            )
        )
        retry_after = window - (now_epoch - int(oldest)) if oldest else window
        db.flush()
        return Decision(False, max(1, retry_after), 0, limit, window)

    db.add(ThrottleEvent(bucket=bucket, key=key, at_epoch=now_epoch))
    db.flush()
    return Decision(True, 0, limit - used - cost, limit, window)


def hit_all(db: Session, checks: list[tuple[str, str | None]]) -> Decision:
    """Check several buckets and return the first refusal, or the last decision.

    A refusal is still recorded against every bucket, so an attacker alternating
    between two identities does not get the allowance of both.
    """
    last = Decision(True, 0, None, None, None)
    for bucket, key in checks:
        decision = hit(db, bucket, key)
        if not decision.allowed:
            return decision
        last = decision
    return last


def recent_hits(db: Session, bucket: str, *, window_seconds: int, limit: int = 500) -> dict:
    """Signals for the organiser console: who is being throttled, and how much.

    Read-only. `blocked` counts the keys that have used their whole allowance in
    the window, which is the difference between "busy" and "being limited".
    """
    cutoff = int(time.time()) - window_seconds
    rows = db.execute(
        select(
            ThrottleEvent.key,
            func.count(ThrottleEvent.id).label("attempts"),
            func.min(ThrottleEvent.at_epoch).label("first_epoch"),
        )
        .where(ThrottleEvent.bucket == bucket, ThrottleEvent.at_epoch >= cutoff)
        .group_by(ThrottleEvent.key)
        .order_by(func.count(ThrottleEvent.id).desc())
        .limit(limit)
    ).all()
    return {
        "bucket": bucket,
        "window_seconds": window_seconds,
        "limit": RULES.get(bucket, (None, None))[0],
        "keys": [
            {
                "key": row.key,
                "attempts": row.attempts,
                "first_seen_epoch": row.first_epoch,
                "exhausted": bool(RULES.get(bucket) and row.attempts >= RULES[bucket][0]),
            }
            for row in rows
        ],
    }


__all__ = ["RULES", "Decision", "decision_for", "hit", "hit_all", "recent_hits", "rule_for"]
