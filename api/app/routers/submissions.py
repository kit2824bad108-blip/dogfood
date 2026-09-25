"""Submission intake, including the Commit Integrity check."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import client_ip, current_user, require_role
from ..github import parse_repo
from ..models import Submission, Team, Track, User
from ..schemas import SubmissionUpsertRequest
from ..services import assign_judges, event_window, run_integrity_check, submission_window_closed
from .teams import membership

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


def serialize_submission(submission: Submission, team: Team | None = None) -> dict:
    return {
        "id": submission.id,
        "team_id": submission.team_id,
        "team_name": team.name if team else None,
        "title": submission.title,
        "repo_url": submission.repo_url,
        "docs_url": submission.docs_url,
        "demo_url": submission.demo_url,
        "video_url": submission.video_url,
        "summary": submission.summary,
        "track_id": submission.track_id,
        "status": submission.status,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "commit_integrity": {
            "flagged": submission.integrity_flagged,
            "pct_in_window": submission.integrity_pct_in_window,
            "source": submission.integrity_source,
            "reason": (submission.integrity_details or {}).get("reason"),
            "details": submission.integrity_details or {},
            "checked_at": submission.integrity_checked_at.isoformat()
            if submission.integrity_checked_at
            else None,
        },
        "created_at": submission.created_at.isoformat() if submission.created_at else None,
        "updated_at": submission.updated_at.isoformat() if submission.updated_at else None,
    }


@router.post("")
def upsert_submission(
    payload: SubmissionUpsertRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Create or edit this team's single submission.

    `status="draft"` saves work in progress: no Commit Integrity check, no judge
    assignment, invisible to judging and to the public gallery. Flipping to
    `submitted` runs both and enters the event.

    The deadline is enforced here, server-side, against the server clock. The
    browser is never asked whether the window is open.
    """
    member = membership(db, user.id)
    if member is None:
        raise HTTPException(status_code=400, detail="Create or join a team before submitting")
    if parse_repo(payload.repo_url) is None:
        raise HTTPException(status_code=400, detail="Enter a GitHub repository URL (github.com/owner/repo)")

    track = None
    if payload.track_id is not None:
        track = db.get(Track, payload.track_id)
        if track is None:
            raise HTTPException(status_code=400, detail="That track does not exist")

    team = db.get(Team, member.team_id)
    submission = db.scalar(select(Submission).where(Submission.team_id == member.team_id))
    created = submission is None

    if submission_window_closed():
        audit.record(
            db,
            "submission.rejected_after_deadline",
            actor=user,
            entity="submission",
            entity_id=submission.id if submission else None,
            ip=client_ip(request),
            details={"attempted_status": payload.status, "deadline": event_window()["closes_at"]},
        )
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="The submission window is closed — no further edits are accepted",
        )

    if submission is not None and submission.status == "submitted" and payload.status == "draft":
        raise HTTPException(
            status_code=400,
            detail="This project is already submitted, so it can no longer be reverted to a draft",
        )

    if submission is None:
        submission = Submission(team_id=member.team_id, title=payload.title, repo_url=payload.repo_url)
        db.add(submission)
        db.flush()

    was_draft = submission.status == "draft"
    submission.title = payload.title
    submission.repo_url = payload.repo_url
    submission.docs_url = payload.docs_url
    submission.demo_url = payload.demo_url
    submission.video_url = payload.video_url
    submission.summary = payload.summary
    submission.track_id = track.id if track else None
    submission.status = payload.status
    db.flush()

    if payload.status == "draft":
        audit.record(
            db,
            "submission.draft_saved",
            actor=user,
            entity="submission",
            entity_id=submission.id,
            ip=client_ip(request),
            details={"track_id": submission.track_id},
        )
        db.commit()
        return {"submission": serialize_submission(submission, team)}

    report = run_integrity_check(db, submission)
    submission.submitted_at = submission.submitted_at or datetime.now(timezone.utc)
    assigned = 0
    if created or was_draft:
        assigned = assign_judges(db, submission.id)

    audit.record(
        db,
        "submission.created" if created else ("submission.submitted" if was_draft else "submission.updated"),
        actor=user,
        entity="submission",
        entity_id=submission.id,
        ip=client_ip(request),
        details={
            "flagged": report.get("flagged"),
            "pct_in_window": report.get("pct_in_window"),
            "track_id": submission.track_id,
            "judges_assigned": assigned,
        },
    )
    db.commit()
    return {"submission": serialize_submission(submission, team)}


@router.get("/me")
def my_submission(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    member = membership(db, user.id)
    if member is None:
        return {"submission": None, "team": None}
    team = db.get(Team, member.team_id)
    submission = db.scalar(select(Submission).where(Submission.team_id == member.team_id))
    return {
        "submission": serialize_submission(submission, team) if submission else None,
        "team": {"id": team.id, "name": team.name, "invite_code": team.invite_code} if team else None,
        "window": event_window(),
    }


@router.get("")
def list_submissions(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    rows = db.execute(
        select(Submission, Team).join(Team, Team.id == Submission.team_id).order_by(Submission.id)
    ).all()
    return {"submissions": [serialize_submission(s, t) for s, t in rows]}


@router.post("/{submission_id}/recheck")
def recheck_integrity(
    submission_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    report = run_integrity_check(db, submission)
    audit.record(
        db,
        "submission.integrity_rechecked",
        actor=user,
        entity="submission",
        entity_id=submission.id,
        ip=client_ip(request),
        details={"flagged": report.get("flagged"), "pct_in_window": report.get("pct_in_window")},
    )
    db.commit()
    return {"submission": serialize_submission(submission)}
