"""Public, unauthenticated reads: the event itself and the project gallery.

Everything here is safe to expose to a browser that has never signed in. Two
decisions are deliberate and are defended in THREAT-MODEL.md:

  * the gallery never returns `demo_url` or `video_url`. Those artifacts are the
    presentation tier, and blind evaluation only works if a judge cannot read
    them from a second, unauthenticated endpoint before filing a technical
    verdict. The archive publishes them after judging closes;
  * the gallery never exposes judge-written scores or comments, only the
    participants' own words about their own work.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Assignment, Score, Submission, Team, Track, User
from ..services import (
    active_rubric,
    event_window,
    normalized_criteria,
    overall_prizes,
    tracks_with_prizes,
)

router = APIRouter(prefix="/api", tags=["event"])

GALLERY_LIMIT = 200


@router.get("/event")
def public_event(db: Session = Depends(get_db)) -> dict:
    """One call that gives the shell everything the landing page needs."""
    window = event_window()
    rubric = active_rubric(db)

    submitted = db.scalar(
        select(func.count(Submission.id)).where(Submission.status == "submitted")
    ) or 0
    drafts = db.scalar(
        select(func.count(Submission.id)).where(Submission.status == "draft")
    ) or 0
    verdicts = db.scalar(
        select(func.count(Score.id)).where(Score.technical_score.isnot(None))
    ) or 0

    criteria = normalized_criteria(rubric)
    return {
        "event": {
            "name": settings.event_name,
            "starts_at": window["opens_at"],
            "ends_at": window["closes_at"],
            "phase": "upcoming" if window["not_yet_open"] else ("closed" if window["closed"] else "open"),
            "submission_window": window,
        },
        "environment": {
            "github_oauth_enabled": settings.github_oauth_enabled,
            "mock_github": settings.mock_github,
            "local_dev_login": settings.local_dev_login,
            "commit_integrity_source": "mock" if settings.mock_github else "github",
        },
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
                for entry in criteria
            ],
        },
        "tracks": tracks_with_prizes(db),
        "overall_prizes": overall_prizes(db),
        "stats": {
            "teams": db.scalar(select(func.count(Team.id))) or 0,
            "submissions": submitted,
            "drafts": drafts,
            "judges": db.scalar(select(func.count(User.id)).where(User.role == "judge")) or 0,
            "verdicts": verdicts,
            "assignments": db.scalar(select(func.count(Assignment.id))) or 0,
        },
    }


@router.get("/gallery")
def gallery(
    q: str | None = Query(default=None, max_length=200),
    track: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
) -> dict:
    """Searchable public gallery of submitted projects."""
    statement = (
        select(Submission, Team, Track)
        .join(Team, Team.id == Submission.team_id)
        .outerjoin(Track, Track.id == Submission.track_id)
        .where(Submission.status == "submitted")
        .order_by(Submission.id)
        .limit(GALLERY_LIMIT)
    )

    term = (q or "").strip()
    if term:
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Submission.title.ilike(pattern),
                Team.name.ilike(pattern),
                Submission.summary.ilike(pattern),
            )
        )

    slug = (track or "").strip()
    if slug:
        statement = statement.where(Track.slug == slug)

    rows = db.execute(statement).all()

    tracks = db.scalars(select(Track).order_by(Track.display_order, Track.id)).all()

    return {
        "query": term,
        "track": slug or None,
        "count": len(rows),
        "tracks": [{"slug": t.slug, "name": t.name} for t in tracks],
        "projects": [
            {
                "id": submission.id,
                "title": submission.title,
                "team": team.name,
                "summary": submission.summary,
                "repo_url": submission.repo_url,
                "docs_url": submission.docs_url,
                "track": {"slug": found.slug, "name": found.name} if found else None,
                "submitted_at": submission.submitted_at.isoformat()
                if submission.submitted_at
                else None,
                # demo_url / video_url intentionally omitted — see module docstring.
            }
            for submission, team, found in rows
        ],
    }
