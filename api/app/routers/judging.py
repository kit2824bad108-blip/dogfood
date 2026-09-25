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
from ..models import Assignment, Score, Submission, Team, User
from ..schemas import ScoreUpsertRequest

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


def _technical_view(submission: Submission, team: Team | None, score: Score | None) -> dict:
    """Everything a judge may see before their technical verdict is filed."""
    return {
        "id": submission.id,
        "title": submission.title,
        "team_name": team.name if team else None,
        "repo_url": submission.repo_url,
        "docs_url": submission.docs_url,
        "summary": submission.summary,
        "presentation_unlocked": bool(score and score.technical_score is not None),
        "score": serialize_score(score),
        "commit_integrity": {
            "flagged": submission.integrity_flagged,
            "pct_in_window": submission.integrity_pct_in_window,
        },
    }


def serialize_score(score: Score | None) -> dict | None:
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
        .where(Assignment.judge_id == user.id)
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

    return {
        "assignments": assignments,
        "progress": {
            "total": len(assignments),
            "technical_done": sum(1 for a in assignments if a["technical_submitted"]),
            "presentation_done": sum(1 for a in assignments if a["presentation_submitted"]),
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
    return {"submission": _technical_view(submission, team, _score(db, user.id, submission_id))}


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
            "score": serialize_score(score),
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
    if payload.technical_score is None and payload.presentation_score is None:
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
        "score": serialize_score(score),
        "presentation_unlocked": score.technical_score is not None,
    }
