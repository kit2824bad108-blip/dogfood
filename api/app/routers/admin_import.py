"""Organiser data operations: import a dataset, review duplicates, balance load.

Kept apart from `admin.py` (leaderboard, exports, archive) so the code that
*changes* an event's data is in one readable place. Every write here is audited,
and both destructive-looking operations default to a dry run.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import assignment, audit, fixtures
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import DuplicateReview, Submission, Team, User
from ..schemas import BalanceAssignmentRequest, DuplicateDecisionRequest, ImportFixtureRequest

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_ROLES = ("admin",)


# ── duplicate review ─────────────────────────────────────────────────────────


def detected_duplicates(db: Session) -> list[dict]:
    """Duplicate candidates, each with whatever decision an organiser has made."""
    rows = db.execute(
        select(Submission.id, Submission.title, Submission.repo_url, Submission.source_ref, Team.name)
        .join(Team, Team.id == Submission.team_id)
        .where(Submission.status == "submitted")
    ).all()
    projects = [
        {
            "id": str(row.id),
            "title": row.title,
            "repo_url": row.repo_url,
            "source_ref": row.source_ref,
            "team": row.name,
            "internal_id": row.id,
        }
        for row in rows
    ]
    lookup = {project["id"]: project for project in projects}
    decisions = {
        (row.submission_id, row.duplicate_of_submission_id): row
        for row in db.scalars(select(DuplicateReview)).all()
    }

    clusters = []
    for candidate in fixtures.duplicate_candidates(projects):
        other = lookup.get(str(candidate["submission"]))
        canonical = lookup.get(str(candidate["duplicate_of"]))
        if other is None or canonical is None:
            continue
        decision = decisions.get((other["internal_id"], canonical["internal_id"]))
        clusters.append(
            {
                "submission_id": other["internal_id"],
                "title": other["title"],
                "team": other["team"],
                "source_ref": other["source_ref"],
                "duplicate_of_submission_id": canonical["internal_id"],
                "duplicate_of_title": canonical["title"],
                "duplicate_of_team": canonical["team"],
                "duplicate_of_source_ref": canonical["source_ref"],
                "reason": candidate["reason"],
                "decision": decision.decision if decision else "undecided",
                "decided_by": decision.actor_email if decision else None,
                "note": decision.note if decision else None,
            }
        )
    return clusters


@router.get("/duplicates")
def list_duplicates(
    db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))
) -> dict:
    clusters = detected_duplicates(db)
    return {
        "clusters": clusters,
        "totals": {
            "candidates": len(clusters),
            "confirmed": sum(1 for row in clusters if row["decision"] == "duplicate"),
            "dismissed": sum(1 for row in clusters if row["decision"] == "distinct"),
            "undecided": sum(1 for row in clusters if row["decision"] == "undecided"),
        },
    }


@router.post("/duplicates")
def decide_duplicate(
    payload: DuplicateDecisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Record an organiser's decision. Nothing is deleted, ever.

    A confirmed duplicate is flagged, not removed: deleting a participant's work
    on the strength of a string comparison is not a decision software should make.
    """
    if payload.submission_id == payload.duplicate_of_submission_id:
        raise HTTPException(status_code=400, detail="A submission cannot duplicate itself")
    for submission_id in (payload.submission_id, payload.duplicate_of_submission_id):
        if db.get(Submission, submission_id) is None:
            raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")

    existing = db.scalar(
        select(DuplicateReview).where(
            DuplicateReview.submission_id == payload.submission_id,
            DuplicateReview.duplicate_of_submission_id == payload.duplicate_of_submission_id,
        )
    )
    if existing is None:
        existing = DuplicateReview(
            submission_id=payload.submission_id,
            duplicate_of_submission_id=payload.duplicate_of_submission_id,
        )
        db.add(existing)
    existing.decision = payload.decision
    existing.note = payload.note
    existing.actor_id = user.id
    existing.actor_email = user.email
    db.flush()

    audit.record(
        db,
        "duplicate.decided",
        actor=user,
        entity="submission",
        entity_id=payload.submission_id,
        ip=client_ip(request),
        details={
            "duplicate_of": payload.duplicate_of_submission_id,
            "decision": payload.decision,
        },
    )
    db.commit()
    return {
        "decision": {
            "submission_id": existing.submission_id,
            "duplicate_of_submission_id": existing.duplicate_of_submission_id,
            "decision": existing.decision,
            "decided_by": existing.actor_email,
        }
    }


# ── fixture import ───────────────────────────────────────────────────────────


@router.get("/import/diagnostics")
def import_diagnostics(
    db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))
) -> dict:
    """What the last import reported, plus what is true of the database right now."""
    fixture_path = fixtures.default_fixture_path()
    batch = fixtures.latest_batch(db)
    duplicates = detected_duplicates(db)
    coverage = assignment.coverage_snapshot(db)
    return {
        "last_batch": fixtures.batch_to_dict(batch),
        "fixture": {
            "path": str(fixture_path),
            "present": fixture_path.exists(),
            "mode": "fixtures" if fixture_path.exists() else "demo",
        },
        "live": {
            "duplicates": len(duplicates),
            "duplicates_undecided": sum(
                1 for row in duplicates if row["decision"] == "undecided"
            ),
            "coverage": coverage["totals"],
            "provisional_submissions": coverage["totals"]["provisional"],
        },
    }


@router.post("/import")
def import_fixture(
    payload: ImportFixtureRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Import a fixture file. Dry run by default; nothing is written unless asked."""
    try:
        fixture = fixtures.load_fixture(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    summary = fixtures.apply_fixture(
        db, fixture, actor=user, mode="dry_run" if payload.dry_run else "apply"
    )
    audit.record(
        db,
        "fixture.import_checked" if payload.dry_run else "fixture.import_applied",
        actor=user,
        entity="event",
        ip=client_ip(request),
        details={
            "source": summary.get("source"),
            "projects": (summary.get("records") or {}).get("projects"),
            "invalid": len(summary.get("invalid") or []),
            "duplicates_detected": summary.get("duplicates_detected"),
            "refused": summary.get("refused"),
        },
    )
    db.commit()
    return {"import": summary}


# ── balanced assignment ──────────────────────────────────────────────────────


@router.post("/assignments/balance")
def balance_assignments(
    payload: BalanceAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Compute (and optionally apply) a balanced assignment plan.

    The plan is computed first and returned in full, so an organiser can read the
    coverage and load distribution it would produce before it touches anything.
    """
    try:
        plan = assignment.plan_balanced_assignment(
            db,
            reviews_per_project=payload.reviews_per_project,
            max_projects_per_judge=payload.max_projects_per_judge,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    created = 0
    if not payload.dry_run:
        created = assignment.apply_plan(db, plan["plan"])
        audit.record(
            db,
            "assignments.balanced",
            actor=user,
            entity="event",
            ip=client_ip(request),
            details={
                "reviews_per_project": payload.reviews_per_project,
                "max_projects_per_judge": payload.max_projects_per_judge,
                "created": created,
                "components_before": plan["projected"]["components_before"],
                "components_after": plan["projected"]["components_after"],
            },
        )
        db.commit()

    return {
        "mode": "applied" if not payload.dry_run else "dry_run",
        "created": created,
        "plan": plan["plan"],
        "projected": plan["projected"],
        "dispersion": assignment.dispersion_note(plan["projected"]["judge_load"]["variance"]),
        # Stated explicitly because the alternative is a number that looks wrong:
        # the cap limits assignments this plan *adds*, and an existing load above it
        # is reported rather than silently reduced by deleting someone's work.
        "max_projects_per_judge_note": (
            "The cap applies to assignments this plan adds. Existing loads above it are "
            "reported, not reduced: nothing here deletes an assignment."
        ),
        "coverage": assignment.coverage_snapshot(
            db, expected=payload.reviews_per_project
        )["totals"],
    }
