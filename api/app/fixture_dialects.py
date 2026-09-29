"""Two fixture dialects, one canonical shape.

Axion owns a generated demo dataset. The organisers publish a different file
with the same job: fake hackathon data to seed a portal with. They disagree about
almost every field name, and both have to work.

    organisers (dogfood)                 Axion (axion)
    ─────────────────────────────────    ─────────────────────────────────
    event.submissions_close              event.starts_at / event.ends_at
    tracks[].name                        tracks[].slug + .name
    judges[].tracks  (coverage)          projects[].assigned_judges
    teams[].members  (emails only)       participants[].team_id
    projects[].team / .track   (ids)     projects[].team_id / .track_id
    scores[].judge / .project            reviews[].judge_id / .project_id
    scores[].criteria{fn,quality,innov}  reviews[].technical (a verdict)
    scores[].comment                     reviews[].technical_comment

Rather than teach every consumer about both spellings, the organisers' file is
translated once, here, into the canonical shape the importer already reads. That
keeps `validate`, `diagnose`, `duplicate_candidates` and `apply_fixture` as the
single implementation of "what a dataset is", and it makes the translation itself
reviewable in one place — which matters, because the translation is where a
faithful import can go wrong.

Three deliberate consequences of the organiser file's shape, each handled rather
than papered over:

  * **It names only a close date.** `event.submissions_close` is in the past, so
    the window is derived backwards from it (72 hours; the app's own default
    length). What the acceptance check needs is the close, and the close is kept
    exactly as given.
  * **Participants have no names.** `teams[].members` is a list of email
    addresses. Accounts are created for them and their display names are derived
    from the address, because inventing names would be inventing data.
  * **Judges declare tracks, not projects.** Assignment is derived from
    `judges[].tracks` — every judge covers every submission in a track they
    declared. The fixture's missing review batches are then preserved, not
    manufactured: the file scores fewer projects than its own assignments imply.

Nothing here writes to a database.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

DOGFOOD = "dogfood"
AXION = "axion"

# The organiser file names a close date and no start; the app's own default event
# length is 72 hours (config.DEFAULT_WINDOW), so the derived window matches the
# length of an event the app would create itself.
DERIVED_WINDOW = timedelta(hours=72)

# The fixture's own lower bound, in `fixtures.py`, for "enough reviews to be worth
# ranking". Used as the coverage expectation so the file's thin batches are
# reported rather than silently accepted.
MINIMUM_COVERAGE = 3

ADMIN_EMAIL_TEMPLATE = "organiser@{slug}.dogfood"
ADMIN_NAME_TEMPLATE = "{name} organiser"


# ── dialect detection ────────────────────────────────────────────────────────


def detect_dialect(payload: dict[str, Any]) -> str:
    """Which file is this? Read the shape, not a label.

    A marker key is checked first because it is unambiguous, and the project
    records are the fallback: the organiser file gives a project `team`/`track`
    (string ids) while Axion's gives it `team_id`/`track_id`.
    """
    if payload.get("reviews") is not None and "scores" not in payload:
        return AXION
    if payload.get("scores") is not None:
        return DOGFOOD
    projects = [project for project in payload.get("projects") or [] if isinstance(project, dict)]
    if projects and any("team" in project or "track" in project for project in projects):
        return DOGFOOD
    return AXION


# ── small helpers ────────────────────────────────────────────────────────────


def slugify(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def display_name_from_email(email: str) -> str:
    """A readable name for an account the dataset gave only an address for.

    `priya1@example.org` → `Priya1`; `member1_2@example.org` → `Member1 2`. The
    digits stay attached deliberately: they are what distinguishes three phantom
    team-mates from each other, and dropping them would make a roster ambiguous.
    """
    local = str(email or "").split("@", 1)[0]
    parts = [part for part in re.split(r"[._-]+", local) if part]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or local


def _label_for(key: str) -> str:
    return re.sub(r"[_\s]+", " ", key).strip().title()


# ── the organisers' file ─────────────────────────────────────────────────────


def _dogfood_rubric(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """One rubric from every criterion key the file scores against.

    The file carries values but no weights and no rubric, so the weights are
    equal and the keys are taken in first-seen order — the fixture's own order,
    not an alphabetical one that would silently reorder a published rubric. The
    choice is documented in JUDGING.md; changing it changes the ranking, which is
    exactly why it is written down.
    """
    keys: list[str] = []
    for score in scores:
        for key in (score.get("criteria") or {}):
            if key not in keys:
                keys.append(str(key))
    if not keys:
        return {"name": "Imported rubric", "criteria": []}
    weight = round(100.0 / len(keys), 4)
    return {
        "name": "Imported rubric — " + ", ".join(keys),
        "criteria": [
            {"key": key, "label": _label_for(key), "weight": weight} for key in keys
        ],
    }


def _dogfood_event(event: dict[str, Any]) -> dict[str, Any]:
    close = _parse_utc(event.get("submissions_close"))
    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "starts_at": _iso(close - DERIVED_WINDOW) if close else None,
        "ends_at": _iso(close),
        "submissions_close": event.get("submissions_close"),
    }


def _dogfood_participants(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    seen: set[str] = set()
    for team in teams:
        for email in team.get("members") or []:
            address = str(email or "").strip().lower()
            if not address or address in seen:
                continue
            seen.add(address)
            participants.append(
                {
                    "id": address,
                    "name": display_name_from_email(address),
                    "email": address,
                    "team_id": str(team.get("id")),
                }
            )
    return participants


def _dogfood_assignments(
    projects: list[dict[str, Any]], judges: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """{project id: [judge ids]} from judges' declared track coverage."""
    by_track: dict[str, list[str]] = {}
    for judge in judges:
        for track in judge.get("tracks") or []:
            by_track.setdefault(str(track), []).append(str(judge.get("id")))
    return {
        str(project.get("id")): by_track.get(str(project.get("track")), [])
        for project in projects
    }


def _from_dogfood(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") or {}
    tracks = [track for track in payload.get("tracks") or [] if isinstance(track, dict)]
    judges = [judge for judge in payload.get("judges") or [] if isinstance(judge, dict)]
    teams = [team for team in payload.get("teams") or [] if isinstance(team, dict)]
    projects = [row for row in payload.get("projects") or [] if isinstance(row, dict)]
    scores = [row for row in payload.get("scores") or [] if isinstance(row, dict)]

    # Imported here rather than at module scope: `services` reads `config`, and
    # `config` reads this module to find the event window it must enforce. The
    # lazy import is what keeps that from being a cycle.
    from .services import weighted_technical_score

    rubric = _dogfood_rubric(scores)
    criteria = rubric["criteria"]
    assignments = _dogfood_assignments(projects, judges)

    reviews: list[dict[str, Any]] = []
    for score in scores:
        values = {
            str(key): int(value)
            for key, value in (score.get("criteria") or {}).items()
            if isinstance(value, (int, float))
        }
        # The verdict is the app's own weighted mean of the criteria, not a
        # second opinion invented here: the same function the judging API uses,
        # so an imported score and a live one mean the same thing.
        reviews.append(
            {
                "judge_id": str(score.get("judge")),
                "project_id": str(score.get("project")),
                "criteria": values,
                "technical": weighted_technical_score(criteria, values),
                "presentation": None,
                "comment": score.get("comment"),
                "technical_comment": score.get("comment"),
                "presentation_comment": None,
                "submitted_at": None,
            }
        )

    slug = slugify(event.get("name")) or "dogfood"
    name = str(event.get("name") or "Imported event")

    return {
        "fixture_version": payload.get("fixture_version"),
        "generator": payload.get("generator") or "organisers' fixtures.json",
        "note": payload.get("note")
        or (
            "Organiser-provided fixture dataset, loaded as published. Nothing is "
            "added to it and nothing is dropped from it."
        ),
        "dialect": DOGFOOD,
        "event": _dogfood_event(event),
        "tracks": [
            {
                "id": str(track.get("id")),
                "slug": slugify(track.get("name")) or str(track.get("id")),
                "name": track.get("name"),
                "description": None,
                "prize_pool": None,
                "prizes": [],
            }
            for track in tracks
        ],
        "judges": [
            {
                "id": str(judge.get("id")),
                "name": judge.get("name") or display_name_from_email(judge.get("email") or ""),
                "email": str(judge.get("email") or "").strip().lower(),
                "tracks": [str(track) for track in judge.get("tracks") or []],
            }
            for judge in judges
        ],
        "accounts": {
            "admin": {
                "email": ADMIN_EMAIL_TEMPLATE.format(slug=slug),
                "name": ADMIN_NAME_TEMPLATE.format(name=name),
                "password": "password",
            },
            "judge": {"password": "password"},
            "participant": {"password": "password"},
        },
        "teams": [
            {
                "id": str(team.get("id")),
                "source_ref": str(team.get("id")),
                "name": team.get("name"),
            }
            for team in teams
        ],
        "participants": _dogfood_participants(teams),
        "projects": [
            {
                "id": str(project.get("id")),
                "team_id": str(project.get("team")),
                "track_id": str(project.get("track")),
                "title": project.get("title"),
                "summary": project.get("summary"),
                "repo_url": project.get("repo_url"),
                "docs_url": None,
                "demo_url": None,
                "video_url": None,
                "status": "submitted",
                "submitted_at": project.get("submitted_at"),
                "assigned_judges": assignments.get(str(project.get("id")), []),
                "commit_integrity": {},
            }
            for project in projects
        ],
        "reviews": reviews,
        "rubric": rubric,
        "minimum_coverage": MINIMUM_COVERAGE,
    }


# ── Axion's own file ─────────────────────────────────────────────────────────


def _from_axion(payload: dict[str, Any]) -> dict[str, Any]:
    """Already canonical; this only fills in what older revisions left implicit."""
    tracks = []
    for track in payload.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        entry = dict(track)
        entry.setdefault("slug", slugify(entry.get("name")) or str(entry.get("id")))
        tracks.append(entry)

    teams = []
    for team in payload.get("teams") or []:
        if not isinstance(team, dict):
            continue
        entry = dict(team)
        # Identity for every dialect: the dataset's own id, never the name.
        entry["source_ref"] = str(entry.get("source_ref") or entry.get("id"))
        teams.append(entry)

    event = dict(payload.get("event") or {})
    event.setdefault("submissions_close", event.get("ends_at"))

    return {
        **payload,
        "dialect": AXION,
        "event": event,
        "tracks": tracks,
        "teams": teams,
        "rubric": payload.get("rubric") or {"name": None, "criteria": []},
    }


# ── entry point ──────────────────────────────────────────────────────────────


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a fixture file into the canonical shape. Idempotent.

    Already-canonical input is passed through, so `diagnose` and `apply_fixture`
    can call this defensively without a flag day for their existing callers.
    """
    if not isinstance(payload, dict):
        return {}
    if payload.get("dialect") in {DOGFOOD, AXION}:
        return payload
    if detect_dialect(payload) == DOGFOOD:
        return _from_dogfood(payload)
    return _from_axion(payload)


def window_from_fixture(payload: dict[str, Any]) -> tuple[Optional[datetime], Optional[datetime]]:
    """The event window a fixture declares, for `config.py` to enforce.

    Reads both dialects without needing the rest of the normalisation, because it
    runs at import time on a file that may be either.
    """
    if not isinstance(payload, dict):
        return None, None
    event = payload.get("event") or {}
    if not isinstance(event, dict):
        return None, None
    close = event.get("submissions_close") or event.get("closes_for_submissions_at")
    if close and not event.get("ends_at"):
        close_at = _parse_utc(close)
        return (close_at - DERIVED_WINDOW if close_at else None), close_at
    return _parse_utc(event.get("starts_at")), _parse_utc(
        event.get("ends_at") or event.get("closes_for_submissions_at")
    )


def event_name_from_fixture(payload: dict[str, Any]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") or {}
    if not isinstance(event, dict):
        return None
    name = str(event.get("name") or "").strip()
    return name or None


__all__ = [
    "AXION",
    "DOGFOOD",
    "detect_dialect",
    "display_name_from_email",
    "event_name_from_fixture",
    "normalize",
    "slugify",
    "window_from_fixture",
]
