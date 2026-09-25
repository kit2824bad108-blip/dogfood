"""Shared domain services used by several routers."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .github import check_commit_integrity
from .models import Assignment, Submission, User


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
    submission_ids = list(db.scalars(select(Submission.id)).all())
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
    report = check_commit_integrity(
        submission.repo_url,
        event_start=settings.event_start,
        event_end=settings.event_end,
        mock=force_mock,
    )
    submission.integrity_pct_in_window = report.get("pct_in_window")
    submission.integrity_flagged = bool(report.get("flagged"))
    submission.integrity_source = report.get("source")
    submission.integrity_details = report
    submission.integrity_checked_at = datetime.now(timezone.utc)
    db.flush()
    return report


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
