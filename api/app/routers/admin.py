"""Organizer console: the z-scored leaderboard, integrity review, audit trail and archive."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import archive, audit, zscore
from ..config import settings
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import (
    Assignment,
    AuditLog,
    Prize,
    Rubric,
    Score,
    ScoreCriterion,
    Submission,
    Team,
    Track,
    User,
)
from ..schemas import JudgeCreateRequest, PrizeCreateRequest, RubricUpdateRequest, TrackCreateRequest
from ..security import hash_password
from ..timeutil import iso
from ..services import (
    active_rubric,
    assign_submission_to_new_judge,
    normalized_criteria,
    overall_prizes,
    score_counts,
    tracks_with_prizes,
)
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


def _track_map(db: Session) -> dict[int, str]:
    rows = db.execute(
        select(Submission.id, Track.name)
        .join(Track, Track.id == Submission.track_id)
    ).all()
    return {sid: name for sid, name in rows}


def _expected_coverage(db: Session) -> dict[int, int]:
    """{submission_id: judges assigned}. The denominator for coverage."""
    rows = db.execute(
        select(Assignment.submission_id, func.count(Assignment.id))
        .join(Submission, Submission.id == Assignment.submission_id)
        .where(Submission.status == "submitted")
        .group_by(Assignment.submission_id)
    ).all()
    return {submission_id: count for submission_id, count in rows}


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
            "tracks": len(db.scalars(select(Track)).all()),
            "submissions": sum(1 for s in submissions if s.status == "submitted"),
            "drafts": sum(1 for s in submissions if s.status == "draft"),
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
    coverage = zscore.coverage_report(
        records, expected_by_submission=_expected_coverage(db)
    )

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
                # Coverage is reported next to the score, never instead of it: a
                # project rated by one judge is a provisional number, and saying so
                # is more honest than quietly ranking it beside a project rated six.
                "reviews_filed": coverage[result.submission_id].judges,
                "reviews_expected": coverage[result.submission_id].expected,
                "provisional": coverage[result.submission_id].provisional,
            }
        )

    unranked = [
        {
            "submission_id": submission_id,
            "title": titles.get(submission_id, {}).get("title"),
            "team": titles.get(submission_id, {}).get("team"),
            "reviews_expected": entry.expected,
            "reason": "no technical verdicts filed yet",
        }
        for submission_id, entry in sorted(coverage.items())
        if entry.judges == 0 and entry.expected > 0
    ]
    provisional = [row["submission_id"] for row in rows if row["provisional"]]

    return {
        "leaderboard": rows,
        "unranked": unranked,
        "coverage_summary": {
            "minimum_judges": zscore.MINIMUM_JUDGES,
            "ranked": len(rows),
            "provisional": len(provisional),
            "provisional_ids": provisional,
            "unranked": len(unranked),
            "coverage_percent": round((len(rows) - len(provisional)) / len(rows) * 100, 1)
            if rows
            else 0.0,
        },
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
                "created_at": iso(entry.created_at),
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


# ── Event configuration: tracks, prizes and rubric weights ────────────────────


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "track"


@router.get("/tracks")
def list_tracks(db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))) -> dict:
    return {"tracks": tracks_with_prizes(db), "overall_prizes": overall_prizes(db)}


@router.post("/tracks")
def create_track(
    payload: TrackCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    if db.scalar(select(Track).where(Track.name == payload.name)) is not None:
        raise HTTPException(status_code=409, detail="A track with that name already exists")
    slug = _slugify(payload.slug or payload.name)
    if db.scalar(select(Track).where(Track.slug == slug)) is not None:
        raise HTTPException(status_code=409, detail="A track with that slug already exists")

    track = Track(
        name=payload.name,
        slug=slug,
        description=payload.description,
        prize_pool=payload.prize_pool,
        display_order=payload.display_order,
    )
    db.add(track)
    db.flush()
    audit.record(
        db,
        "track.created",
        actor=user,
        entity="track",
        entity_id=track.id,
        ip=client_ip(request),
        details={"slug": slug},
    )
    db.commit()
    return {"track": {"id": track.id, "name": track.name, "slug": track.slug}}


@router.post("/prizes")
def create_prize(
    payload: PrizeCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    if payload.track_id is not None and db.get(Track, payload.track_id) is None:
        raise HTTPException(status_code=404, detail="Track not found")
    prize = Prize(
        track_id=payload.track_id,
        rank=payload.rank,
        title=payload.title,
        description=payload.description,
    )
    db.add(prize)
    db.flush()
    audit.record(
        db,
        "prize.created",
        actor=user,
        entity="prize",
        entity_id=prize.id,
        ip=client_ip(request),
        details={"track_id": payload.track_id, "rank": payload.rank},
    )
    db.commit()
    return {"prize": {"id": prize.id, "title": prize.title, "rank": prize.rank}}


@router.get("/rubric")
def get_rubric(db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))) -> dict:
    rubric = active_rubric(db)
    return {
        "rubric": {
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
    }


@router.post("/rubric")
def update_rubric(
    payload: RubricUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Replace the active rubric.

    Existing verdicts keep the rubric they were filed against (`scores.rubric_id`),
    so re-weighting never silently rewrites history.
    """
    keys = [entry.key for entry in payload.criteria]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=400, detail="Criterion keys must be unique")

    for existing in db.scalars(select(Rubric).where(Rubric.is_active.is_(True))).all():
        existing.is_active = False

    rubric = Rubric(
        name=payload.name,
        criteria=[
            {"key": entry.key, "label": entry.label, "weight": float(entry.weight)}
            for entry in payload.criteria
        ],
        is_active=True,
    )
    db.add(rubric)
    db.flush()
    audit.record(
        db,
        "rubric.updated",
        actor=user,
        entity="rubric",
        entity_id=rubric.id,
        ip=client_ip(request),
        details={"criteria": rubric.criteria},
    )
    db.commit()
    return {
        "rubric": {
            "id": rubric.id,
            "name": rubric.name,
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
    }


# ── Judge progress ───────────────────────────────────────────────────────────


def _judging_progress(db: Session) -> list[dict]:
    submissions = list(
        db.scalars(select(Submission.id).where(Submission.status == "submitted")).all()
    )
    total = len(submissions)
    rows = []
    for judge in db.scalars(select(User).where(User.role == "judge").order_by(User.id)).all():
        assigned = (
            db.scalar(
                select(func.count(Assignment.id))
                .join(Submission, Submission.id == Assignment.submission_id)
                .where(Assignment.judge_id == judge.id, Submission.status == "submitted")
            )
            or 0
        )
        scores = db.scalars(select(Score).where(Score.judge_id == judge.id)).all()
        technical = sum(1 for score in scores if score.technical_score is not None)
        presentation = sum(1 for score in scores if score.presentation_score is not None)
        stamps = [
            stamp
            for score in scores
            for stamp in (score.technical_submitted_at, score.presentation_submitted_at)
            if stamp is not None
        ]
        rows.append(
            {
                "judge_id": judge.id,
                "name": judge.name or judge.email,
                "email": judge.email,
                "assigned": assigned,
                "technical_done": technical,
                "technical_pending": max(0, assigned - technical),
                "presentation_done": presentation,
                "percent": round(technical / assigned * 100, 1) if assigned else 0.0,
                "last_activity": iso(max(stamps)) if stamps else None,
            }
        )
    done = sum(row["technical_done"] for row in rows)
    expected = sum(row["assigned"] for row in rows)
    return {
        "submissions": total,
        "judges": rows,
        "totals": {
            "expected_technical_verdicts": expected,
            "technical_verdicts": done,
            "outstanding": max(0, expected - done),
            "percent": round(done / expected * 100, 1) if expected else 0.0,
        },
    }


@router.get("/judging-progress")
def judging_progress(
    db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))
) -> dict:
    return _judging_progress(db)


@router.get("/judges")
def judge_calibration(
    db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))
) -> dict:
    """How each judge scores, and whether their verdicts discriminate at all.

    A judge who gives every project the same number contributes no ranking
    information — the model gives them z = 0 for everything — and this is where an
    organiser finds that out, rather than after the awards.
    """
    records = _records(db)
    stats = zscore.judge_statistics(records)
    judges = db.scalars(select(User).where(User.role == "judge").order_by(User.id)).all()
    rows = []
    for judge in judges:
        entry = stats.get(judge.id)
        if entry is None or entry.n == 0:
            reliability = "no verdicts filed"
        elif entry.n == 1:
            reliability = "single verdict: dispersion borrowed from the pool"
        elif not entry.discriminative:
            reliability = "non-discriminative: identical verdicts across every project"
        else:
            reliability = "discriminative"
        rows.append(
            {
                "judge_id": judge.id,
                "name": judge.name or judge.email,
                "email": judge.email,
                "verdicts": entry.n if entry else 0,
                "raw_mean": round(entry.raw_mean, 2) if entry else None,
                "raw_sigma": round(entry.raw_sigma, 2) if entry else None,
                "effective_mean": round(entry.effective_mean, 2) if entry else None,
                "effective_sigma": round(entry.effective_sigma, 3) if entry else None,
                "discriminative": entry.discriminative if entry else None,
                "reliability": reliability,
            }
        )
    return {
        "judges": rows,
        "totals": {
            "judges": len(rows),
            "verdicts": len(records),
            "non_discriminative": sum(1 for row in rows if row["discriminative"] is False),
            "single_verdict": sum(1 for row in rows if row["verdicts"] == 1),
            "no_verdicts": sum(1 for row in rows if row["verdicts"] == 0),
        },
    }


# ── CSV export ──────────────────────────────────────────────────────────────


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _audit_export(db: Session, user: User, request: Request, name: str, rows: int) -> None:
    audit.record(
        db,
        "export.csv",
        actor=user,
        entity="export",
        entity_id=name,
        ip=client_ip(request),
        details={"rows": rows},
    )
    db.commit()


@router.get("/export/leaderboard.csv")
def export_leaderboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> Response:
    records = _records(db)
    titles = _title_map(db)
    tracks = _track_map(db)
    normalized = {r.submission_id: r for r in zscore.leaderboard(records)}
    raw = {r.submission_id: r for r in zscore.raw_leaderboard(records)}
    movement = zscore.rank_changes(records)
    coverage = zscore.coverage_report(
        records, expected_by_submission=_expected_coverage(db)
    )

    ordered = sorted(normalized.values(), key=lambda row: row.rank)
    rows = []
    for result in ordered:
        meta = titles.get(result.submission_id, {})
        raw_row = raw.get(result.submission_id)
        rows.append(
            [
                result.rank,
                result.submission_id,
                meta.get("title") or "",
                meta.get("team") or "",
                tracks.get(result.submission_id) or "",
                meta.get("repo_url") or "",
                result.display,
                round(result.z, 4),
                raw_row.display if raw_row else "",
                raw_row.rank if raw_row else "",
                movement.get(result.submission_id, 0),
                result.judges,
                coverage[result.submission_id].judges,
                coverage[result.submission_id].expected,
                "provisional" if coverage[result.submission_id].provisional else "final",
            ]
        )

    _audit_export(db, user, request, "leaderboard.csv", len(rows))
    return _csv_response(
        "axion-leaderboard.csv",
        [
            "rank",
            "submission_id",
            "project",
            "team",
            "track",
            "repo_url",
            "axion_score",
            "z_score",
            "raw_average",
            "raw_rank",
            "rank_movement",
            "judges",
            "reviews_filed",
            "reviews_expected",
            "confidence",
        ],
        rows,
    )


@router.get("/export/scores.csv")
def export_scores(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> Response:
    """One row per judge x submission: raw verdict, that judge's calibration, z."""
    records = _records(db)
    titles = _title_map(db)
    tracks = _track_map(db)
    stats = zscore.judge_statistics(records)

    judges = {judge.id: judge for judge in db.scalars(select(User)).all()}
    criteria_labels = [entry["label"] for entry in normalized_criteria(active_rubric(db))]
    criteria_rows: dict[int, dict[str, int]] = {}
    for row in db.scalars(select(ScoreCriterion)).all():
        criteria_rows.setdefault(row.score_id, {})[row.label or row.key] = row.value

    header = [
        "submission_id",
        "project",
        "team",
        "track",
        "judge_id",
        "judge_name",
        "judge_email",
        "technical_score",
        "judge_raw_mean",
        "judge_raw_sigma",
        "z_score",
        *[f"criterion:{label}" for label in criteria_labels],
        "presentation_score",
        "technical_submitted_at",
        "presentation_submitted_at",
    ]

    rows = []
    scores = db.scalars(select(Score).order_by(Score.submission_id, Score.judge_id)).all()
    for score in scores:
        if score.technical_score is None:
            continue
        meta = titles.get(score.submission_id, {})
        judge = judges.get(score.judge_id)
        stat = stats.get(score.judge_id)
        values = criteria_rows.get(score.id, {})
        rows.append(
            [
                score.submission_id,
                meta.get("title") or "",
                meta.get("team") or "",
                tracks.get(score.submission_id) or "",
                score.judge_id,
                (judge.name or judge.email) if judge else "",
                judge.email if judge else "",
                score.technical_score,
                round(stat.raw_mean, 4) if stat else "",
                round(stat.raw_sigma, 4) if stat else "",
                round(zscore.judge_z(float(score.technical_score), stat), 4) if stat else "",
                *[values.get(label, "") for label in criteria_labels],
                score.presentation_score if score.presentation_score is not None else "",
                score.technical_submitted_at.isoformat() if score.technical_submitted_at else "",
                score.presentation_submitted_at.isoformat() if score.presentation_submitted_at else "",
            ]
        )

    _audit_export(db, user, request, "scores.csv", len(rows))
    return _csv_response("axion-scores.csv", header, rows)


@router.get("/export/judging-progress.csv")
def export_judging_progress(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> Response:
    progress = _judging_progress(db)
    rows = [
        [
            row["judge_id"],
            row["name"],
            row["email"],
            row["assigned"],
            row["technical_done"],
            row["technical_pending"],
            row["presentation_done"],
            row["percent"],
            row["last_activity"] or "",
        ]
        for row in progress["judges"]
    ]
    _audit_export(db, user, request, "judging-progress.csv", len(rows))
    return _csv_response(
        "axion-judging-progress.csv",
        [
            "judge_id",
            "judge",
            "email",
            "assigned",
            "technical_done",
            "technical_pending",
            "presentation_done",
            "percent_technical",
            "last_activity",
        ],
        rows,
    )
