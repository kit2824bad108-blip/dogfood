"""Blind evaluation.

Judges see the repository and documentation first. The presentation artifacts
(demo and video links) are withheld — and the withholding is enforced here, in
the API, not by blurring a tab in the browser. A judge who calls the endpoint
directly still gets a 403 until their technical verdict exists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import Assignment, Score, ScoreCriterion, Submission, Team, User
from ..schemas import ScoreUpsertRequest
from ..services import (
    active_rubric,
    ensure_default_rubric,
    normalized_criteria,
    weighted_technical_score,
)

router = APIRouter(prefix="/api/judging", tags=["judging"])

JUDGE_ROLES = ("judge", "admin")


def _assignment(db: Session, judge_id: int, submission_id: int) -> Assignment | None:
    return db.scalar(
        select(Assignment).where(
            Assignment.judge_id == judge_id, Assignment.submission_id == submission_id
        )
    )


def _score(db: Session, judge_id: int, submission_id: int) -> Score | None:
    return db.scalar(
        select(Score).where(Score.judge_id == judge_id, Score.submission_id == submission_id)
    )


def _technical_view(
    submission: Submission, team: Team | None, score: Score | None, criteria: dict[str, int] | None = None
) -> dict:
    """Everything a judge may see before their technical verdict is filed."""
    return {
        "id": submission.id,
        "title": submission.title,
        "team_name": team.name if team else None,
        "repo_url": submission.repo_url,
        "docs_url": submission.docs_url,
        "summary": submission.summary,
        "presentation_unlocked": bool(score and score.technical_score is not None),
        "score": serialize_score(score, criteria),
        "commit_integrity": {
            "flagged": submission.integrity_flagged,
            "pct_in_window": submission.integrity_pct_in_window,
        },
    }


def _criteria_values(db: Session, score: Score | None) -> dict[str, int]:
    if score is None:
        return {}
    rows = db.scalars(
        select(ScoreCriterion).where(ScoreCriterion.score_id == score.id)
    ).all()
    return {row.key: row.value for row in rows}


def serialize_score(score: Score | None, criteria: dict[str, int] | None = None) -> dict | None:
    if score is None:
        return None
    return {
        "technical_score": score.technical_score,
        "technical_comment": score.technical_comment,
        "presentation_score": score.presentation_score,
        "presentation_comment": score.presentation_comment,
        "technical_submitted_at": score.technical_submitted_at.isoformat()
        if score.technical_submitted_at
        else None,
        "presentation_submitted_at": score.presentation_submitted_at.isoformat()
        if score.presentation_submitted_at
        else None,
        "rubric_id": score.rubric_id,
        "criteria": criteria or {},
    }


def _public_rubric(db: Session) -> dict:
    """The rubric as a judge needs it.

    Every rubric payload in the API carries the same four fields (key, label,
    weight, percent) so a client cannot end up with a shape that is only *mostly*
    the same — a missing `weight` here once made the weighted-score preview
    render NaN in the browser.
    """
    rubric = active_rubric(db)
    return {
        "id": rubric.id if rubric else None,
        "name": rubric.name if rubric else None,
        "criteria": [
            {
                "key": entry["key"],
                "label": entry["label"],
                "weight": entry["weight"],
                "percent": round(entry["fraction"] * 100, 2),
            }
            for entry in normalized_criteria(rubric)
        ],
    }


@router.get("/assignments")
def my_assignments(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*JUDGE_ROLES)),
) -> dict:
    rows = db.execute(
        select(Assignment, Submission, Team, Score)
        .join(Submission, Submission.id == Assignment.submission_id)
        .join(Team, Team.id == Submission.team_id)
        .outerjoin(
            Score,
            (Score.submission_id == Assignment.submission_id) & (Score.judge_id == Assignment.judge_id),
        )
        .where(Assignment.judge_id == user.id, Submission.status == "submitted")
        .order_by(Submission.id)
    ).all()

    assignments = []
    for assignment, submission, team, score in rows:
        assignments.append(
            {
                "submission_id": submission.id,
                "title": submission.title,
                "team_name": team.name,
                "repo_url": submission.repo_url,
                "technical_submitted": bool(score and score.technical_score is not None),
                "presentation_submitted": bool(score and score.presentation_score is not None),
                "technical_score": score.technical_score if score else None,
                "presentation_score": score.presentation_score if score else None,
                "commit_integrity_flagged": submission.integrity_flagged,
            }
        )

    technical_done = sum(1 for a in assignments if a["technical_submitted"])
    return {
        "assignments": assignments,
        "rubric": _public_rubric(db),
        "progress": {
            "total": len(assignments),
            "technical_done": technical_done,
            "technical_pending": len(assignments) - technical_done,
            "presentation_done": sum(1 for a in assignments if a["presentation_submitted"]),
            "percent_technical": round(technical_done / len(assignments) * 100, 1) if assignments else 0.0,
        },
    }


@router.get("/submissions/{submission_id}")
def submission_detail(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*JUDGE_ROLES)),
) -> dict:
    if user.role != "admin" and _assignment(db, user.id, submission_id) is None:
        raise HTTPException(status_code=403, detail="You are not assigned to this submission")
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    team = db.get(Team, submission.team_id)
    mine = _score(db, user.id, submission_id)
    return {
        "submission": _technical_view(submission, team, mine, _criteria_values(db, mine)),
        "rubric": _public_rubric(db),
    }


@router.get("/submissions/{submission_id}/presentation")
def presentation_detail(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*JUDGE_ROLES)),
) -> dict:
    """Gated on the technical verdict. This is the blind evaluation boundary."""
    if user.role != "admin" and _assignment(db, user.id, submission_id) is None:
        raise HTTPException(status_code=403, detail="You are not assigned to this submission")

    score = _score(db, user.id, submission_id)
    if user.role != "admin" and (score is None or score.technical_score is None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Submit the technical evaluation before the presentation is unlocked",
        )

    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    team = db.get(Team, submission.team_id)
    return {
        "presentation": {
            "submission_id": submission.id,
            "title": submission.title,
            "team_name": team.name if team else None,
            "demo_url": submission.demo_url,
            "video_url": submission.video_url,
            "summary": submission.summary,
            "score": serialize_score(score, _criteria_values(db, score)),
        }
    }


@router.post("/scores")
def upsert_score(
    payload: ScoreUpsertRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*JUDGE_ROLES)),
) -> dict:
    if user.role != "admin" and _assignment(db, user.id, payload.submission_id) is None:
        raise HTTPException(status_code=403, detail="You are not assigned to this submission")
    if (
        payload.technical_score is None
        and payload.presentation_score is None
        and not payload.criteria
    ):
        raise HTTPException(status_code=400, detail="Provide a technical or presentation score")

    submission = db.get(Submission, payload.submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    score = _score(db, user.id, payload.submission_id)
    if score is None:
        score = Score(submission_id=payload.submission_id, judge_id=user.id)
        db.add(score)
        db.flush()

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    derived_technical: int | None = None
    if payload.criteria:
        # Materialise the default rubric if the organiser never configured one,
        # so every derived score carries the weights that produced it.
        rubric = active_rubric(db) or ensure_default_rubric(db)
        criteria = normalized_criteria(rubric)
        known = {entry["key"]: entry for entry in criteria}
        unknown = [entry.key for entry in payload.criteria if entry.key not in known]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown rubric criteria: {', '.join(sorted(unknown))}",
            )
        values = {entry.key: entry.value for entry in payload.criteria}
        derived_technical = weighted_technical_score(criteria, values)
        if derived_technical is None:
            raise HTTPException(status_code=400, detail="Score at least one rubric criterion")

        existing = {
            row.key: row
            for row in db.scalars(
                select(ScoreCriterion).where(ScoreCriterion.score_id == score.id)
            ).all()
        }
        for entry in criteria:
            if entry["key"] not in values:
                continue
            row = existing.pop(entry["key"], None)
            if row is None:
                row = ScoreCriterion(score_id=score.id, key=entry["key"])
                db.add(row)
            row.label = entry["label"]
            row.weight = entry["weight"]
            row.value = values[entry["key"]]
        for orphan in existing.values():
            db.delete(orphan)
        score.rubric_id = rubric.id
        db.flush()

    effective_technical_request = (
        derived_technical if derived_technical is not None else payload.technical_score
    )

    if effective_technical_request is not None:
        payload = payload.model_copy(update={"technical_score": effective_technical_request})

    if payload.technical_score is not None:
        previous = score.technical_score
        score.technical_score = payload.technical_score
        if payload.technical_comment is not None:
            score.technical_comment = payload.technical_comment
        score.technical_submitted_at = now
        audit.record(
            db,
            "score.technical_modified" if previous is not None else "score.technical_submitted",
            actor=user,
            entity="score",
            entity_id=score.id,
            ip=client_ip(request),
            details={
                "submission_id": submission.id,
                "previous": previous,
                "value": payload.technical_score,
                "rubric_id": score.rubric_id,
                # The per-criterion inputs are recorded so a later change to the
                # rubric cannot retroactively rewrite how a verdict was reached.
                "criteria": {entry.key: entry.value for entry in (payload.criteria or [])},
                "technical_score_derived": derived_technical is not None,
            },
        )
    elif payload.technical_comment is not None and score.technical_score is not None:
        score.technical_comment = payload.technical_comment

    if payload.presentation_score is not None:
        effective_technical = (
            payload.technical_score if payload.technical_score is not None else score.technical_score
        )
        if effective_technical is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Submit the technical evaluation before scoring the presentation",
            )
        previous = score.presentation_score
        score.presentation_score = payload.presentation_score
        if payload.presentation_comment is not None:
            score.presentation_comment = payload.presentation_comment
        score.presentation_submitted_at = now
        audit.record(
            db,
            "score.presentation_modified" if previous is not None else "score.presentation_submitted",
            actor=user,
            entity="score",
            entity_id=score.id,
            ip=client_ip(request),
            details={
                "submission_id": submission.id,
                "previous": previous,
                "value": payload.presentation_score,
            },
        )
    elif payload.presentation_comment is not None and score.presentation_score is not None:
        score.presentation_comment = payload.presentation_comment

    db.commit()
    return {
        "score": serialize_score(score, _criteria_values(db, score)),
        "technical_score_derived": derived_technical,
        "presentation_unlocked": score.technical_score is not None,
    }
