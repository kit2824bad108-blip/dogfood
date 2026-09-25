"""Organizer console: the z-scored leaderboard, integrity review, audit trail and archive."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import archive, audit, zscore
from ..config import settings
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import Assignment, AuditLog, Score, Submission, Team, User
from ..schemas import JudgeCreateRequest
from ..security import hash_password
from ..services import assign_submission_to_new_judge, score_counts
from .submissions import serialize_submission

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_ROLES = ("admin",)


def _records(db: Session) -> list[zscore.ScoreRecord]:
    rows = db.execute(
        select(Score.judge_id, Score.submission_id, Score.technical_score).where(
            Score.technical_score.isnot(None)
        )
    ).all()
    return [zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t)) for j, s, t in rows]


def _title_map(db: Session) -> dict[int, dict]:
    rows = db.execute(
        select(Submission.id, Submission.title, Submission.repo_url, Team.name)
        .join(Team, Team.id == Submission.team_id)
    ).all()
    return {sid: {"title": title, "repo_url": repo, "team": team} for sid, title, repo, team in rows}


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))) -> dict:
    counts = score_counts(db)
    submissions = db.scalars(select(Submission)).all()
    return {
        "event": {
            "name": settings.event_name,
            "starts_at": settings.event_start.isoformat(),
            "ends_at": settings.event_end.isoformat(),
        },
        "totals": {
            "participants": len(db.scalars(select(User).where(User.role == "participant")).all()),
            "judges": len(db.scalars(select(User).where(User.role == "judge")).all()),
            "teams": len(db.scalars(select(Team)).all()),
            "submissions": len(submissions),
            "flagged_for_review": sum(1 for s in submissions if s.integrity_flagged),
            "assignments": len(db.scalars(select(Assignment.id)).all()),
            **counts,
        },
    }


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))) -> dict:
    records = _records(db)
    titles = _title_map(db)
    normalized = zscore.leaderboard(records)
    raw = {r.submission_id: r for r in zscore.raw_leaderboard(records)}
    movement = zscore.rank_changes(records)
    stats = zscore.judge_statistics(records)

    judges = db.scalars(select(User).where(User.role == "judge")).all()
    judge_rows = [
        {
            "id": judge.id,
            "name": judge.name or judge.email,
            "verdicts": stats[judge.id].n if judge.id in stats else 0,
            "raw_mean": round(stats[judge.id].raw_mean, 2) if judge.id in stats else None,
            "raw_sigma": round(stats[judge.id].raw_sigma, 2) if judge.id in stats else None,
            "discriminative": stats[judge.id].discriminative if judge.id in stats else None,
        }
        for judge in judges
    ]

    rows = []
    for result in normalized:
        meta = titles.get(result.submission_id, {})
        raw_row = raw.get(result.submission_id)
        rows.append(
            {
                "submission_id": result.submission_id,
                "title": meta.get("title"),
                "team": meta.get("team"),
                "repo_url": meta.get("repo_url"),
                "axion_score": result.display,
                "z_score": result.z,
                "axion_rank": result.rank,
                "raw_average": raw_row.display if raw_row else None,
                "raw_rank": raw_row.rank if raw_row else None,
                "rank_movement": movement.get(result.submission_id, 0),
                "judges": result.judges,
            }
        )

    return {
        "leaderboard": rows,
        "judges": judge_rows,
        "methodology": {
            "prior_strength": zscore.PRIOR_STRENGTH,
            "prior_variance": zscore.PRIOR_VARIANCE,
            "sigma_floor": zscore.SIGMA_FLOOR,
            "display_mapping": "50 + 10z, clamped to [0, 100]",
            "ranking_basis": "technical verdicts only; presentation scores are retained, not ranked",
        },
        "coverage_warnings": zscore.coverage_flags(records),
        "verdict_count": len(records),
    }


@router.get("/flagged")
def flagged(db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))) -> dict:
    rows = db.execute(
        select(Submission, Team)
        .join(Team, Team.id == Submission.team_id)
        .where(Submission.integrity_flagged.is_(True))
        .order_by(Submission.integrity_pct_in_window)
    ).all()
    return {"flagged": [serialize_submission(s, t) for s, t in rows]}


@router.get("/audit")
def audit_trail(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    limit = max(1, min(limit, 500))
    entries = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return {
        "entries": [
            {
                "id": entry.id,
                "action": entry.action,
                "actor": entry.actor_email,
                "entity": entry.entity,
                "entity_id": entry.entity_id,
                "ip": entry.ip,
                "details": entry.details,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in entries
        ]
    }


@router.post("/judges")
def create_judge(
    payload: JudgeCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    if db.scalar(select(User).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    judge = User(
        email=payload.email,
        name=payload.name or payload.email.split("@")[0],
        role="judge",
        password_hash=hash_password(payload.password),
    )
    db.add(judge)
    db.flush()
    assigned = assign_submission_to_new_judge(db, judge.id)
    audit.record(
        db,
        "judge.created",
        actor=user,
        entity="user",
        entity_id=judge.id,
        ip=client_ip(request),
        details={"assignments_created": assigned},
    )
    db.commit()
    return {"judge": {"id": judge.id, "email": judge.email, "name": judge.name}, "assigned": assigned}


@router.post("/assignments/backfill")
def backfill_assignments(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    judges = db.scalars(select(User).where(User.role == "judge")).all()
    created = sum(assign_submission_to_new_judge(db, judge.id) for judge in judges)
    audit.record(
        db, "assignments.backfilled", actor=user, ip=client_ip(request), details={"created": created}
    )
    db.commit()
    return {"created": created}


@router.post("/archive")
def make_archive(
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
):
    bundle = archive.build_bundle(db)
    markdown = archive.to_markdown(bundle)
    audit.record(
        db,
        "event.archived",
        actor=user,
        entity="event",
        ip=client_ip(request),
        details={"bundle_version": bundle["bundle_version"], "results": len(bundle["results"])},
    )
    db.commit()

    if download:
        import json

        return JSONResponse(
            content=json.loads(json.dumps(bundle)),
            headers={"Content-Disposition": 'attachment; filename="axion-archive.json"'},
        )
    return {"bundle": bundle, "markdown": markdown}


@router.post("/archive/markdown")
def make_archive_markdown(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> PlainTextResponse:
    bundle = archive.build_bundle(db)
    audit.record(db, "event.archived_markdown", actor=user, entity="event", ip=client_ip(request))
    db.commit()
    return PlainTextResponse(
        archive.to_markdown(bundle),
        headers={"Content-Disposition": 'attachment; filename="RESULTS.md"'},
    )
