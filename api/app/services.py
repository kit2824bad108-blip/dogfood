"""Shared domain services used by several routers."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import eventconfig
from .github import check_commit_integrity
from .models import Assignment, Prize, Rubric, Score, Submission, Team, Track, User

# The default rubric mirrors the organiser guidance: Innovation 30%, Code
# Quality 70%. Weights are normalised by their sum, so these could equally be
# written 0.3 / 0.7 or 3 / 7 — they are not required to add up to 100.
DEFAULT_CRITERIA: list[dict] = [
    {"key": "innovation", "label": "Innovation", "weight": 30.0},
    {"key": "code_quality", "label": "Code Quality", "weight": 70.0},
]
DEFAULT_RUBRIC_NAME = "Default technical rubric"


# Letters and digits, minus the ones that get misread aloud. An invite code is
# dictated over a call and typed from a screenshot, so O/0 and I/1 are hazards
# rather than aesthetics.
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_LENGTH = 8


def unique_invite_code(db: Session) -> str:
    """An unused invite code. Defined once so a team created in the console and a
    team created by a bundle import cannot end up with differently-shaped codes."""
    while True:
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
        if db.scalar(select(Team).where(Team.invite_code == code)) is None:
            return code


def canonical_only(statement):
    """Rank canonical submissions. Duplicates are reported, never ranked.

    A submission marked as a duplicate of another would otherwise compete against
    the very entry it duplicates — the same work taking two places. The predicate
    lives here, in one place, because the leaderboard, the exports and the signed
    participation records all have to agree about what counts as a result.
    """
    return statement.where(Submission.duplicate_of_submission_id.is_(None))


def score_records(db: Session) -> list:
    """Every technical verdict as a `zscore.ScoreRecord`, duplicates excluded.

    This is *the* input to the normalization engine: one definition, so the
    leaderboard in the console and the ranking inside a signed result record cannot
    disagree.
    """
    from . import zscore

    rows = db.execute(
        canonical_only(
            select(Score.judge_id, Score.submission_id, Score.technical_score)
            .join(Submission, Submission.id == Score.submission_id)
            .where(Score.technical_score.isnot(None))
        )
    ).all()
    return [zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t)) for j, s, t in rows]


def active_rubric(db: Session) -> Rubric | None:
    return db.scalar(select(Rubric).where(Rubric.is_active.is_(True)).order_by(Rubric.id.desc()))


def ensure_default_rubric(db: Session) -> Rubric:
    """Idempotent: the seed and the admin router both rely on this."""
    rubric = active_rubric(db)
    if rubric is not None:
        return rubric
    rubric = Rubric(name=DEFAULT_RUBRIC_NAME, criteria=list(DEFAULT_CRITERIA), is_active=True)
    db.add(rubric)
    db.flush()
    return rubric


def normalized_criteria(rubric: Rubric | None) -> list[dict]:
    """Criteria with weights converted to fractions that sum to 1."""
    raw = list((rubric.criteria if rubric else None) or DEFAULT_CRITERIA)
    clean = []
    for entry in raw:
        key = str(entry.get("key") or "").strip()
        weight = float(entry.get("weight") or 0.0)
        if not key or weight <= 0:
            continue
        clean.append(
            {
                "key": key,
                "label": str(entry.get("label") or key),
                "weight": weight,
            }
        )
    if not clean:
        clean = [dict(entry) for entry in DEFAULT_CRITERIA]
    total = sum(entry["weight"] for entry in clean)
    return [
        {
            "key": entry["key"],
            "label": entry["label"],
            "weight": entry["weight"],
            "fraction": round(entry["weight"] / total, 6),
        }
        for entry in clean
    ]


def weighted_technical_score(criteria: list[dict], values: dict[str, int]) -> int | None:
    """Weight-normalised mean of the criterion values, clamped to 1..10.

    Returning a single integer is deliberate: `zscore` consumes one number per
    verdict, so adding rubrics must not change the normalization contract.
    """
    usable = [entry for entry in criteria if entry["key"] in values]
    if not usable:
        return None
    total = sum(entry["weight"] for entry in usable)
    if total <= 0:
        return None
    blended = sum(entry["weight"] * values[entry["key"]] for entry in usable) / total
    return max(1, min(10, int(round(blended))))


def event_window(db: Session | None = None) -> dict:
    """Server-side view of the submission window. Client clocks are never trusted.

    The boundaries come from `eventconfig` (the organiser's row if there is one,
    otherwise the deployment's configuration). The *clock* stays here: this module
    is where the deadline tests freeze `datetime`, and a server clock read from a
    shared helper would be the one thing those tests could no longer pin.
    """
    active = eventconfig.active(db)
    now = datetime.now(timezone.utc)
    return {
        "now": now.isoformat(),
        "opens_at": active.starts_at.isoformat(),
        "closes_at": active.ends_at.isoformat(),
        "closed": now > active.ends_at,
        "not_yet_open": now < active.starts_at,
        "source": active.source,
        "revision": active.revision,
    }


def submission_window_closed(db: Session | None = None) -> bool:
    return datetime.now(timezone.utc) > eventconfig.active(db).ends_at


def judge_ids(db: Session) -> list[int]:
    return list(db.scalars(select(User.id).where(User.role == "judge")).all())


def assign_judges(db: Session, submission_id: int, only_judge_ids: list[int] | None = None) -> int:
    """Round-robin is unnecessary when every judge sees every project, which is
    the right model at hackathon scale (10 projects x 5 judges = 50 verdicts).

    Z-scores are only comparable across judges when their verdicts overlap, so
    full coverage is the honest default. See MATH.md.
    """
    targets = only_judge_ids if only_judge_ids is not None else judge_ids(db)
    existing = set(
        db.scalars(
            select(Assignment.judge_id).where(Assignment.submission_id == submission_id)
        ).all()
    )
    created = 0
    for judge_id in targets:
        if judge_id in existing:
            continue
        db.add(Assignment(judge_id=judge_id, submission_id=submission_id))
        created += 1
    if created:
        db.flush()
    return created


def assign_submission_to_new_judge(db: Session, judge_id: int) -> int:
    # Drafts are excluded: a work in progress is not part of the event yet.
    submission_ids = list(
        db.scalars(select(Submission.id).where(Submission.status == "submitted")).all()
    )
    existing = set(
        db.scalars(
            select(Assignment.submission_id).where(Assignment.judge_id == judge_id)
        ).all()
    )
    created = 0
    for submission_id in submission_ids:
        if submission_id in existing:
            continue
        db.add(Assignment(judge_id=judge_id, submission_id=submission_id))
        created += 1
    if created:
        db.flush()
    return created


def run_integrity_check(db: Session, submission: Submission, *, force_mock: bool | None = None) -> dict:
    """Refresh a submission's Commit Integrity fields from GitHub (or the mock)."""
    clock = eventconfig.active(db)
    report = check_commit_integrity(
        submission.repo_url,
        event_start=clock.starts_at,
        event_end=clock.ends_at,
        mock=force_mock,
    )
    submission.integrity_pct_in_window = report.get("pct_in_window")
    submission.integrity_flagged = bool(report.get("flagged"))
    submission.integrity_source = report.get("source")
    submission.integrity_details = report
    submission.integrity_checked_at = datetime.now(timezone.utc)
    db.flush()
    return report


def serialize_track(track: Track, prizes: list[Prize] | None = None) -> dict:
    return {
        "id": track.id,
        "name": track.name,
        "slug": track.slug,
        "description": track.description,
        "prize_pool": track.prize_pool,
        "display_order": track.display_order,
        "prizes": [
            {
                "id": prize.id,
                "rank": prize.rank,
                "title": prize.title,
                "description": prize.description,
            }
            for prize in sorted(prizes or [], key=lambda row: row.rank)
        ],
    }


def tracks_with_prizes(db: Session) -> list[dict]:
    tracks = db.scalars(select(Track).order_by(Track.display_order, Track.id)).all()
    prizes = db.scalars(select(Prize).order_by(Prize.rank, Prize.id)).all()
    grouped: dict[int | None, list[Prize]] = {}
    for prize in prizes:
        grouped.setdefault(prize.track_id, []).append(prize)
    return [serialize_track(track, grouped.get(track.id, [])) for track in tracks]


def overall_prizes(db: Session) -> list[dict]:
    prizes = db.scalars(
        select(Prize).where(Prize.track_id.is_(None)).order_by(Prize.rank, Prize.id)
    ).all()
    return [
        {"id": p.id, "rank": p.rank, "title": p.title, "description": p.description}
        for p in prizes
    ]


def score_counts(db: Session) -> dict:
    from .models import Score

    total_scores = db.scalar(select(func.count(Score.id))) or 0
    technical = db.scalar(
        select(func.count(Score.id)).where(Score.technical_score.isnot(None))
    ) or 0
    presentation = db.scalar(
        select(func.count(Score.id)).where(Score.presentation_score.isnot(None))
    ) or 0
    return {
        "score_rows": total_scores,
        "technical_scores": technical,
        "presentation_scores": presentation,
    }
