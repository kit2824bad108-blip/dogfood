"""Community voting and comments: the shared rules for the T3 surface.

Three decisions are made here rather than in a router, because each one has to be
true everywhere it is asked about:

**The window has two halves.** Voting opens and closes on its own clock, and
*results are hidden until it closes*. That is not a display setting: while the
window is open, no public endpoint returns a tally, a rank or an average, because
publishing running totals is how a community vote turns into a bandwagon. An
organiser can read them at any point, which is what makes "hidden" checkable
rather than a claim.

**Ballot order is determined, not shuffled per request.** Each voter gets a stable
pseudo-random order derived from an HMAC over (voter token digest, project id) with
the deployment's secret. Two properties follow, and both matter: the ballot does
not reshuffle every time the page reloads (which would be unusable), and two
voters do not see the same order (which is the point — a fixed alphabetical list
gives the projects at the top of it an advantage that has nothing to do with
quality). It is deterministic, so an organiser can reproduce any voter's ballot
when investigating a complaint.

**A vote is immutable; a moderator strikes it.** The unique constraint makes a
second vote an error rather than an update, so changing one's mind is not a way to
vote twice. Removal leaves the row, marked and reasoned, with an audit entry.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import eventconfig
from .config import VOTING_TAIL, settings
from .models import Comment, Submission, Vote, Voter

VOTE_MIN, VOTE_MAX = 1, 5


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def voting_window(db: Session | None = None) -> dict:
    """The community window, resolved server-side. Client clocks are never trusted.

    Delegated to `eventconfig`, which is the one place that decides whether the
    deployment's configuration or the organiser's row is authoritative. The
    semantics are unchanged from T3: the community clock is its own clock, and
    results are published only once it has closed.
    """
    return eventconfig.voting_window(db)


def results_visible(db: Session | None = None) -> bool:
    return eventconfig.voting_results_visible(db)


def normalize_email(value: str) -> str:
    """Lowercase and strip, because `Ada@Example.org` and `ada@example.org` vote once."""
    return (value or "").strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token() -> tuple[str, str]:
    """A fresh ballot token and the digest that gets stored. Returns (token, hash)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def voter_by_token(db: Session, token: str | None) -> Voter | None:
    if not token:
        return None
    return db.scalar(select(Voter).where(Voter.token_hash == hash_token(token.strip())))


def ballot_order(voter: Voter, submission_ids: list[int]) -> list[int]:
    """A stable per-voter ordering of the given projects.

    The key is the voter's token digest (not their id), so the order cannot be
    reproduced by anyone who can merely count rows, and the secret is mixed in so
    it cannot be reproduced off-deployment either. Ties in the digest are
    impossible in practice, and the id is used as the final tiebreak anyway.
    """
    secret = settings.secret_key.encode("utf-8")

    def sort_key(submission_id: int) -> bytes:
        message = f"{voter.token_hash}:{submission_id}".encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).digest()

    return sorted(submission_ids, key=lambda sid: (sort_key(sid), sid))


def castable_submission_ids(db: Session) -> list[int]:
    """Projects a stranger may vote on: submitted, and not a marked duplicate."""
    return list(
        db.scalars(
            select(Submission.id)
            .where(
                Submission.status == "submitted",
                Submission.duplicate_of_submission_id.is_(None),
            )
            .order_by(Submission.id)
        ).all()
    )


def voter_votes(db: Session, voter: Voter) -> dict[int, Vote]:
    rows = db.scalars(select(Vote).where(Vote.voter_id == voter.id)).all()
    return {row.submission_id: row for row in rows}


def aggregate(db: Session, *, include_struck: bool = False) -> dict:
    """Community tallies per project.

    The headline numbers are **cast votes only**, always. A struck vote is one an
    organiser removed, so letting it into `votes`/`average` — even under a flag
    named "include struck" — would mean the figure an organiser reads changed
    depending on which request produced it, which is the one thing a tally must
    never do.

    `include_struck=True` therefore *adds* a separate figure per project
    (`struck_votes`) rather than mixing one in, so an organiser can see both what
    counts and what their own decision removed. Nothing is ever deleted, so the
    difference is always recoverable.
    """
    statement = (
        select(
            Vote.submission_id,
            func.count(Vote.id).label("votes"),
            func.avg(Vote.score).label("average"),
            func.min(Vote.score).label("lowest"),
            func.max(Vote.score).label("highest"),
        )
        .where(Vote.status == "cast")
        .group_by(Vote.submission_id)
        .order_by(func.avg(Vote.score).desc(), Vote.submission_id)
    )
    rows = db.execute(statement).all()

    seen: set[int] = set()
    struck_counts: dict[int, int] = {}
    if include_struck:
        struck_counts = {
            submission_id: count
            for submission_id, count in db.execute(
                select(Vote.submission_id, func.count(Vote.id))
                .where(Vote.status == "struck")
                .group_by(Vote.submission_id)
            ).all()
        }

    distribution_rows = db.execute(
        select(Vote.submission_id, Vote.score, func.count(Vote.id))
        .where(Vote.status == "cast")
        .group_by(Vote.submission_id, Vote.score)
    ).all()
    distributions: dict[int, dict[str, int]] = {}
    for submission_id, score, count in distribution_rows:
        distributions.setdefault(submission_id, {})[str(score)] = count

    titles = dict(
        db.execute(select(Submission.id, Submission.title)).all()  # type: ignore[arg-type]
    )

    results = []
    for row in rows:
        entry = {
            "submission_id": row.submission_id,
            "title": titles.get(row.submission_id),
            "votes": row.votes,
            "average": round(float(row.average), 3) if row.average is not None else None,
            "lowest": row.lowest,
            "highest": row.highest,
            "distribution": distributions.get(row.submission_id, {}),
        }
        if include_struck:
            entry["struck_votes"] = struck_counts.get(row.submission_id, 0)
        results.append(entry)
        seen.add(row.submission_id)

    if include_struck:
        # A project whose every vote was struck has no cast rows, so the loop above
        # never mentions it — and it is exactly the project an organiser asking for
        # struck votes wants to find. It is reported with zero cast votes rather
        # than being quietly missing from the list.
        for submission_id, struck in sorted(struck_counts.items()):
            if submission_id in seen:
                continue
            results.append(
                {
                    "submission_id": submission_id,
                    "title": titles.get(submission_id),
                    "votes": 0,
                    "average": None,
                    "lowest": None,
                    "highest": None,
                    "distribution": {},
                    "struck_votes": struck,
                }
            )

    totals = {
        "votes": sum(entry["votes"] for entry in results),
        "projects_with_votes": sum(1 for entry in results if entry["votes"]),
    }
    if include_struck:
        totals["votes_struck"] = sum(struck_counts.values())
    return {
        "window": voting_window(db),
        "results": results,
        "totals": totals,
    }


def participation(db: Session) -> dict:
    """How much of the community actually voted. Read by organisers only."""
    voters_total = db.scalar(select(func.count(Voter.id))) or 0
    verified = db.scalar(
        select(func.count(Voter.id)).where(Voter.verified_at.isnot(None))
    ) or 0
    blocked = db.scalar(select(func.count(Voter.id)).where(Voter.blocked.is_(True))) or 0
    voted = db.scalar(select(func.count(func.distinct(Vote.voter_id)))) or 0
    cast = db.scalar(select(func.count(Vote.id)).where(Vote.status == "cast")) or 0
    struck = db.scalar(select(func.count(Vote.id)).where(Vote.status == "struck")) or 0
    comments_visible = db.scalar(
        select(func.count(Comment.id)).where(Comment.status == "visible")
    ) or 0
    comments_hidden = db.scalar(
        select(func.count(Comment.id)).where(Comment.status == "hidden")
    ) or 0
    return {
        "voters": voters_total,
        "verified": verified,
        "blocked": blocked,
        "unverified": voters_total - verified,
        "voters_who_voted": voted,
        "votes_cast": cast,
        "votes_struck": struck,
        "comments_visible": comments_visible,
        "comments_hidden": comments_hidden,
        # A ballot is not finished until it is submitted; this is the share of
        # verified voters who cast at least one vote, which is the number an
        # organiser can act on.
        "turnout": round(voted / verified, 4) if verified else 0.0,
    }


def integrity_signals(db: Session, *, ip_threshold: int = 3, burst_seconds: int = 300) -> dict:
    """Signals an organiser should see before trusting a community result.

    Deliberately descriptive rather than verdict-shaped: this reports clusters, it
    does not accuse anyone. The spec asks for duplicate detection and an audit
    trail, and both of those decide what *counts*; what is left for a human is
    noticing a pattern, so the pattern is what gets surfaced.
    """
    cluster_rows = db.execute(
        select(Vote.ip, func.count(Vote.id), func.count(func.distinct(Vote.voter_id)))
        .where(Vote.status == "cast", Vote.ip.isnot(None))
        .group_by(Vote.ip)
        .having(func.count(func.distinct(Vote.voter_id)) >= ip_threshold)
        .order_by(func.count(Vote.id).desc())
    ).all()

    recent = db.execute(
        select(Vote.ip, Vote.voter_id, Vote.submission_id, Vote.score, Vote.cast_at)
        .where(Vote.status == "cast")
        .order_by(Vote.cast_at.desc())
        .limit(200)
    ).all()
    bursts: dict[str, int] = {}
    for _ip, _voter_id, _submission_id, _score, cast_at in recent:
        if cast_at is None:
            continue
        moment = cast_at if cast_at.tzinfo else cast_at.replace(tzinfo=timezone.utc)
        bucket = moment.replace(
            minute=(moment.minute // max(1, burst_seconds // 60)) * max(1, burst_seconds // 60),
            second=0,
            microsecond=0,
        )
        bursts[bucket.isoformat()] = bursts.get(bucket.isoformat(), 0) + 1

    top_bursts = sorted(bursts.items(), key=lambda pair: pair[1], reverse=True)[:5]

    return {
        "ip_clusters": [
            {"ip": row[0], "votes": row[1], "distinct_voters": row[2]} for row in cluster_rows
        ],
        "ip_threshold": ip_threshold,
        "burst_seconds": burst_seconds,
        "busiest_windows": [
            {"window_start": start, "votes": count} for start, count in top_bursts
        ],
        "notes": [
            "A cluster is a signal, not a finding: shared campus, office or mobile "
            "networks legitimately put many voters behind one address.",
            "Distinct voters behind one address (not raw vote count) is what an "
            "organiser should read first, since a single voter may rate many projects.",
        ],
    }


def serialize_vote(vote: Vote) -> dict:
    return {
        "id": vote.id,
        "submission_id": vote.submission_id,
        "score": vote.score,
        "status": vote.status,
        "cast_at": vote.cast_at.isoformat() if vote.cast_at else None,
        "struck_reason": vote.struck_reason,
    }


def serialize_voter(voter: Voter) -> dict:
    return {
        "id": voter.id,
        "email": voter.email,
        "display_name": voter.display_name,
        "verified": voter.verified_at is not None,
        "blocked": voter.blocked,
        "blocked_reason": voter.blocked_reason,
    }


def serialize_comment(comment: Comment, *, viewer_is_organiser: bool = False) -> dict:
    return {
        "id": comment.id,
        "submission_id": comment.submission_id,
        "author": comment.author_name or comment.author_email,
        # The address is only exposed to an organiser: a comment is public, the
        # commenter's identity is not a thing to publish on their behalf.
        "author_email": comment.author_email if viewer_is_organiser else None,
        "author_kind": "user" if comment.author_id else ("voter" if comment.voter_id else "unknown"),
        "body": comment.body,
        "status": comment.status if viewer_is_organiser else "visible",
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "moderated_reason": comment.moderated_reason if viewer_is_organiser else None,
    }


__all__ = [
    "VOTING_TAIL",
    "VOTE_MAX",
    "VOTE_MIN",
    "aggregate",
    "ballot_order",
    "castable_submission_ids",
    "hash_token",
    "integrity_signals",
    "mint_token",
    "normalize_email",
    "participation",
    "results_visible",
    "serialize_comment",
    "serialize_vote",
    "serialize_voter",
    "voter_by_token",
    "voter_votes",
    "voting_window",
]
