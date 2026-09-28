"""The embeddable gallery (T4).

A hackathon's projects page is the thing everyone wants on their own site — the
sponsor's landing page, the organiser's blog, the event's Discord status. The usual
answer is "copy the JSON and build it yourself", which means every embed drifts from
the portal the moment the portal changes.

This serves the two halves of a one-line embed instead:

* `GET /api/embed/gallery` — a complete, self-contained HTML document, suitable
  directly as an `<iframe src>`. No build step, no external asset, no script tag
  from a third party: it renders on a page with the network firewalled except for
  this deployment, which is the same rule the rest of Axion follows.
* `GET /api/embed/gallery/snippet` — the exact `<iframe>`/`<script>` markup to paste,
  generated from the request's own origin so the snippet cannot point at the wrong
  host.

It is public because a gallery is public, and it is read-only in every direction: an
embed can never be an entry point into the event.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Submission, Team, Track

router = APIRouter(prefix="/api/embed", tags=["embed"])


def _rows(db: Session, track: str | None, limit: int, canonical_only: bool = True) -> list[dict]:
    from .. import services

    statement = (
        select(Submission, Team, Track)
        .join(Team, Team.id == Submission.team_id)
        .outerjoin(Track, Track.id == Submission.track_id)
        .where(Submission.status == "submitted")
        .order_by(Submission.id)
        .limit(limit)
    )
    if canonical_only:
        statement = services.canonical_only(statement)
    if track:
        statement = statement.where(Track.slug == track)

    out: list[dict] = []
    for submission, team, row_track in db.execute(statement).all():
        out.append(
            {
                "title": submission.title,
                "team": team.name,
                "track": row_track.name if row_track else None,
                "summary": submission.summary,
                "repo_url": submission.repo_url,
            }
        )
    return out


@router.get("/gallery", response_class=Response)
def gallery(
    request: Request,
    track: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=24, ge=1, le=200),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    db: Session = Depends(get_db),
) -> Response:
    """The gallery as an iframe-ready document."""
    rows = _rows(db, track, limit)
    cards = "\n".join(
        f"""    <li class="card">
      <h3><a href="{html.escape(row['repo_url'] or '#')}" target="_blank" rel="noopener">{html.escape(row['title'])}</a></h3>
      <p class="team">{html.escape(row['team'])}</p>
      <p class="track">{html.escape(row['track'] or 'No track')}</p>
      <p class="summary">{html.escape(row['summary'] or '')}</p>
    </li>"""
        for row in rows
    )
    if not rows:
        cards = '    <li class="empty">No projects are published yet.</li>'

    background, foreground, border, muted, accent = (
        ("#ffffff", "#10201c", "#dbe8e4", "#5b6f69", "#0f766e")
        if theme == "light"
        else ("#0c1613", "#e8f2ef", "#22332e", "#9fb4ad", "#4fd1c5")
    )
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(settings.event_name)} — projects</title>
<style>
  :root {{ color-scheme: {theme}; }}
  body {{ margin: 0; padding: 16px; background: {background}; color: {foreground};
         font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  header {{ display: flex; align-items: baseline; gap: 10px; margin: 0 0 14px; }}
  header h1 {{ margin: 0; font-size: 17px; }}
  header p {{ margin: 0; color: {muted}; font-size: 12.5px; }}
  ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 12px;
        grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }}
  .card {{ border: 1px solid {border}; border-radius: 10px; padding: 14px 16px; background: {background}; }}
  .card h3 {{ margin: 0 0 6px; font-size: 15px; }}
  .card h3 a {{ color: {foreground}; text-decoration: none; border-bottom: 2px solid {accent}; }}
  .team, .track, .summary {{ margin: 0 0 4px; font-size: 13px; color: {muted}; }}
  .track {{ color: {accent}; font-weight: 600; }}
  .empty {{ color: {muted}; }}
  footer {{ margin-top: 14px; font-size: 12px; color: {muted}; }}
  footer a {{ color: {accent}; }}
</style>
</head>
<body>
  <header>
    <h1>{html.escape(settings.event_name)}</h1>
    <p>{len(rows)} project{'' if len(rows) == 1 else 's'}{f' · {html.escape(track)}' if track else ''}</p>
  </header>
  <ul>
{cards}
  </ul>
  <footer>
    Served by <a href="{html.escape(settings.web_url)}" target="_blank" rel="noopener">Axion</a>
    · live from the event's own submissions.
  </footer>
</body>
</html>
"""
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        # An embed is read constantly and changes rarely; a minute of caching keeps a
        # sponsor's landing page from hammering the event's API without ever showing
        # a stale roster for long.
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.get("/gallery/snippet")
def snippet(
    request: Request,
    track: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=24, ge=1, le=200),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    height: int = Query(default=520, ge=180, le=2000),
) -> dict:
    """The exact markup to paste, built from the origin the caller reached us on."""
    query = f"?limit={limit}&theme={theme}" + (f"&track={track}" if track else "")
    origin = settings.web_url or str(request.base_url).rstrip("/")
    src = f"{origin}/api/embed/gallery{query}"
    return {
        "iframe": (
            f'<iframe src="{src}" title="{settings.event_name} projects" '
            f'width="100%" height="{height}" loading="lazy" '
            f'style="border:1px solid #dbe8e4;border-radius:12px"></iframe>'
        ),
        "src": src,
        "params": {"track": track, "limit": limit, "theme": theme, "height": height},
        "notes": [
            "Public and read-only: no session, no cookie, no write endpoint.",
            "Self-contained HTML with no third-party asset, so it renders air-gapped.",
            "Cached for 60 seconds; the roster never lags a live event by more than that.",
        ],
    }
