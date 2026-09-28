"""Submission intake, including the Commit Integrity check."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, webhooks
from ..db import get_db
from ..deps import client_ip, current_user, require_role
from ..github import parse_repo
from ..models import Submission, Team, Track, User
from ..schemas import SubmissionUpsertRequest
from ..services import assign_judges, event_window, run_integrity_check, submission_window_closed
from ..timeutil import iso
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
        # `iso()` pins the offset: SQLite hands back naive datetimes, and a bare
        # string would be read as local time by the browser.
        "submitted_at": iso(submission.submitted_at),
        "commit_integrity": {
            "flagged": submission.integrity_flagged,
            "pct_in_window": submission.integrity_pct_in_window,
            "source": submission.integrity_source,
            "reason": (submission.integrity_details or {}).get("reason"),
            "details": submission.integrity_details or {},
            "checked_at": iso(submission.integrity_checked_at),
        },
        "source_ref": submission.source_ref,
        "created_at": iso(submission.created_at),
        "updated_at": iso(submission.updated_at),
    }


async def require_open_window(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    """Refuse a write from a closed event before its body is even validated.

    Declared at the route level so it runs ahead of body validation. For a closed
    event the answer is always "the deadline has passed" — not a schema error
    about a field nobody is allowed to write anyway. It is also what makes the
    acceptance probe `POST {"title", "summary"}` report the deadline rather than
    a 422: the refusal is the deadline's, which is the claim being checked.

    Authentication still comes first, because it is a dependency of this one: an
    anonymous write is 401 whatever the state of the clock.

    The body is read here anyway, unsafely and without validating it, so the audit
    trail still records what the participant was trying to do. A refusal that
    cannot say what it refused is a smaller trail than this feature deserves.
    """
    if not submission_window_closed():
        return

    attempted_status: str | None = None
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a body we cannot read is not a reason to 500
        body = None
    if isinstance(body, dict):
        raw_status = body.get("status")
        attempted_status = str(raw_status) if raw_status is not None else None

    audit.record(
        db,
        "submission.rejected_after_deadline",
        actor=user,
        ip=client_ip(request),
        details={
            "attempted_status": attempted_status,
            "deadline": event_window()["closes_at"],
            "refused_before_body_validation": True,
        },
    )
    db.commit()
    raise HTTPException(
        status_code=403,
        detail="The submission window is closed — no further edits are accepted",
    )


@router.post("", dependencies=[Depends(require_open_window)])
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
    # A team may hold a second submission that is marked as a duplicate of the
    # first (an imported dataset can contain one). Edits address the canonical row
    # only: a participant saving their project must not be quietly editing the
    # duplicate instead, and a duplicate is never allowed to become canonical by
    # being written over.
    submission = db.scalar(
        select(Submission)
        .where(
            Submission.team_id == member.team_id,
            Submission.duplicate_of_submission_id.is_(None),
        )
        .order_by(Submission.id)
    )
    created = submission is None

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
        # A draft is announced too, because a team's own tooling (a status board, a
        # Discord notifier) wants to know that work exists before it is finished.
        # What it must never carry is a draft's *content* to anyone else: this is
        # addressed to the organiser's subscribers, and drafts stay unlisted.
        webhooks.emit(
            db,
            webhooks.EVENT_SUBMISSION_CREATED,
            {
                "submission_id": submission.id,
                "team": team.name,
                "title": submission.title,
                "status": "draft",
                "track_id": submission.track_id,
            },
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
    webhooks.emit(
        db,
        webhooks.EVENT_SUBMISSION_SUBMITTED,
        {
            "submission_id": submission.id,
            "team": team.name,
            "title": submission.title,
            "track_id": submission.track_id,
            "repo_url": submission.repo_url,
            "submitted_at": iso(submission.submitted_at),
            "commit_integrity_pct_in_window": submission.integrity_pct_in_window,
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
    submitted = db.scalars(
        select(Submission).where(Submission.team_id == member.team_id).order_by(Submission.id)
    ).all()
    canonical = next(
        (row for row in submitted if row.duplicate_of_submission_id is None), None
    )
    # Named explicitly rather than silently dropped: a team looking at their one
    # submission should be able to see that a second, duplicate-looking one exists
    # and that an organiser is reviewing it.
    duplicates = [row for row in submitted if row.duplicate_of_submission_id is not None]
    return {
        "submission": serialize_submission(canonical, team) if canonical else None,
        "team": {"id": team.id, "name": team.name, "invite_code": team.invite_code} if team else None,
        "duplicates": [
            {
                "id": row.id,
                "title": row.title,
                "repo_url": row.repo_url,
                "submitted_at": iso(row.submitted_at),
                "duplicate_of_submission_id": row.duplicate_of_submission_id,
                "note": "Under review as a possible duplicate; nothing was deleted.",
            }
            for row in duplicates
        ],
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
