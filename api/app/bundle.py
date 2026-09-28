"""A whole event, in and out (T4).

The brief asks for "bulk import and export", and the operability criterion is about
whether a stranger could move their event in and out of this deployment. So this is
one JSON document holding everything the event *is*, and an importer that is a round
trip rather than a best effort: export from one instance and import into a fresh one
produces the same counts, the same rankings and the same signed records.

Three decisions worth stating:

**No credentials travel.** The bundle carries identities — addresses, names, roles,
GitHub ids — and never a password hash or a session. A deployment can be moved
without moving the ability to sign in as anyone in it, which is the difference
between an export and a leak.

**The import is idempotent, and dry by default.** Every row is matched on the key it
is *identified* by — a source ref where the dataset has one, otherwise an address, a
team, a judge/project pair — so importing twice updates rather than duplicates, and
running the dry run tells you what would change before anything does.

**Nothing is invented.** A bundle that references a team it does not contain is
refused as a whole, in the same spirit as the fixture importer: half an event is worse
than none, and a silently repaired bundle is a bundle nobody can trust.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services, voting
from .config import settings
from .models import (
    Assignment,
    Comment,
    ParticipationRecord,
    Prize,
    Rubric,
    Score,
    ScoreCriterion,
    Submission,
    Team,
    TeamMember,
    Track,
    User,
    Vote,
    Voter,
)
from .timeutil import iso

BUNDLE_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_bytes(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def checksum(tables: dict) -> str:
    return hashlib.sha256(canonical_bytes(tables)).hexdigest()


# ── export ──────────────────────────────────────────────────────────────────


def export_bundle(db: Session, *, include_community: bool = True) -> dict:
    """Everything the event is, as one document.

    Ordered by primary key throughout so two exports of an unchanged event are
    byte-identical — which is what makes the checksum worth publishing.
    """
    rubric = services.active_rubric(db)

    tables: dict[str, list[dict]] = {
        "tracks": [
            {
                "source_ref": t.slug,
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "prize_pool": t.prize_pool,
                "display_order": t.display_order,
            }
            for t in db.scalars(select(Track).order_by(Track.id)).all()
        ],
        "prizes": [
            {
                "track": (db.get(Track, p.track_id).slug if p.track_id else None),
                "rank": p.rank,
                "title": p.title,
                "description": p.description,
            }
            for p in db.scalars(select(Prize).order_by(Prize.id)).all()
        ],
        "users": [
            {
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "source_ref": u.source_ref,
                "github_id": u.github_id,
                "github_login": u.github_login,
                # password_hash is deliberately absent: see the module docstring.
            }
            for u in db.scalars(select(User).order_by(User.id)).all()
        ],
        "teams": [
            {
                "source_ref": t.source_ref,
                "name": t.name,
                "invite_code": t.invite_code,
                "created_by": (db.get(User, t.created_by).email if t.created_by else None),
                "members": [
                    db.get(User, member.user_id).email
                    for member in db.scalars(
                        select(TeamMember).where(TeamMember.team_id == t.id).order_by(TeamMember.id)
                    ).all()
                    if db.get(User, member.user_id) is not None
                ],
            }
            for t in db.scalars(select(Team).order_by(Team.id)).all()
        ],
        "submissions": [
            {
                "source_ref": s.source_ref,
                "team": (db.get(Team, s.team_id).source_ref or db.get(Team, s.team_id).name),
                "track": (db.get(Track, s.track_id).slug if s.track_id else None),
                "title": s.title,
                "repo_url": s.repo_url,
                "docs_url": s.docs_url,
                "demo_url": s.demo_url,
                "video_url": s.video_url,
                "summary": s.summary,
                "status": s.status,
                "submitted_at": iso(s.submitted_at),
                "duplicate_of": (
                    db.get(Submission, s.duplicate_of_submission_id).source_ref
                    if s.duplicate_of_submission_id
                    else None
                ),
                "integrity": {
                    "pct_in_window": s.integrity_pct_in_window,
                    "flagged": s.integrity_flagged,
                    "source": s.integrity_source,
                    "checked_at": iso(s.integrity_checked_at),
                },
            }
            for s in db.scalars(select(Submission).order_by(Submission.id)).all()
        ],
        "assignments": [
            {
                "judge": db.get(User, a.judge_id).email,
                "submission": (
                    db.get(Submission, a.submission_id).source_ref
                    or f"id:{a.submission_id}"
                ),
            }
            for a in db.scalars(select(Assignment).order_by(Assignment.id)).all()
        ],
        "scores": [
            {
                "judge": db.get(User, s.judge_id).email,
                "submission": (
                    db.get(Submission, s.submission_id).source_ref
                    or f"id:{s.submission_id}"
                ),
                "technical_score": s.technical_score,
                "technical_comment": s.technical_comment,
                "presentation_score": s.presentation_score,
                "presentation_comment": s.presentation_comment,
                "technical_submitted_at": iso(s.technical_submitted_at),
                "presentation_submitted_at": iso(s.presentation_submitted_at),
                "rubric": rubric.name if s.rubric_id == (rubric.id if rubric else None) else None,
                "criteria": [
                    {
                        "key": row.key,
                        "label": row.label,
                        "weight": row.weight,
                        "value": row.value,
                    }
                    for row in db.scalars(
                        select(ScoreCriterion)
                        .where(ScoreCriterion.score_id == s.id)
                        .order_by(ScoreCriterion.key)
                    ).all()
                ],
            }
            for s in db.scalars(select(Score).order_by(Score.id)).all()
        ],
        "rubric": {
            "name": rubric.name if rubric else None,
            "criteria": list(rubric.criteria or []) if rubric else [],
        },
        "records": [
            {
                "code": r.code,
                "subject_kind": r.subject_kind,
                "subject_ref": r.subject_ref,
                "subject_name": r.subject_name,
                "subject_email": r.subject_email,
                "role": r.role,
                "payload": r.payload,
                "signature": r.signature,
                "algorithm": r.algorithm,
                "issued_at": iso(r.issued_at),
                "revoked_at": iso(r.revoked_at),
                "revoked_reason": r.revoked_reason,
            }
            for r in db.scalars(select(ParticipationRecord).order_by(ParticipationRecord.id)).all()
        ],
    }
    if include_community:
        tables["voters"] = [
            {
                "email": v.email,
                "display_name": v.display_name,
                "verified": v.verified_at is not None,
                # The ballot token digest is *not* exported: an export must not hand
                # someone a working ballot.
                "blocked": v.blocked,
                "blocked_reason": v.blocked_reason,
            }
            for v in db.scalars(select(Voter).order_by(Voter.id)).all()
        ]
        tables["votes"] = [
            {
                "voter": db.get(Voter, v.voter_id).email if db.get(Voter, v.voter_id) else None,
                "submission": (
                    db.get(Submission, v.submission_id).source_ref
                    or f"id:{v.submission_id}"
                ),
                "score": v.score,
                "status": v.status,
                "cast_at": iso(v.cast_at),
                "struck_reason": v.struck_reason,
            }
            for v in db.scalars(select(Vote).order_by(Vote.id)).all()
        ]
        tables["comments"] = [
            {
                "submission": (
                    db.get(Submission, c.submission_id).source_ref
                    or f"id:{c.submission_id}"
                ),
                "author_email": c.author_email,
                "author_name": c.author_name,
                "body": c.body,
                "status": c.status,
                "created_at": iso(c.created_at),
                "moderated_reason": c.moderated_reason,
            }
            for c in db.scalars(select(Comment).order_by(Comment.id)).all()
        ]

    return {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": _now().isoformat(),
        "generated_by": settings.web_url,
        "event": {
            "name": settings.event_name,
            "starts_at": settings.event_start.isoformat(),
            "ends_at": settings.event_end.isoformat(),
            "voting_opens_at": settings.voting_start.isoformat(),
            "voting_closes_at": settings.voting_end.isoformat(),
        },
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
        "checksum": checksum(tables),
    }


# ── import ──────────────────────────────────────────────────────────────────


def _index(bundle: dict) -> dict:
    tables = bundle.get("tables") or {}
    return {name: list(rows or []) for name, rows in tables.items()}


def validate_bundle(bundle: dict) -> list[str]:
    """Structural problems, named. An import with any of these is refused whole."""
    problems: list[str] = []
    if not isinstance(bundle, dict):
        return ["the bundle is not an object"]
    version = bundle.get("bundle_version")
    if version != BUNDLE_VERSION:
        problems.append(f"bundle_version {version!r} is not {BUNDLE_VERSION}")
    tables = bundle.get("tables")
    if not isinstance(tables, dict):
        return problems + ["the bundle has no `tables`"]

    team_keys = {row.get("source_ref") or row.get("name") for row in tables.get("teams") or []}
    emails = {row.get("email") for row in tables.get("users") or []}
    submission_keys = {
        row.get("source_ref") or f"id:{row.get('title')}" for row in tables.get("submissions") or []
    }
    for row in tables.get("submissions") or []:
        if row.get("team") not in team_keys:
            problems.append(f"submission {row.get('title')!r} names an unknown team {row.get('team')!r}")
    for name in ("assignments", "scores"):
        for row in tables.get(name) or []:
            if row.get("judge") not in emails:
                problems.append(f"{name}: unknown judge {row.get('judge')!r}")
            if row.get("submission") not in submission_keys and not str(
                row.get("submission", "")
            ).startswith("id:"):
                problems.append(f"{name}: unknown submission {row.get('submission')!r}")
    if bundle.get("checksum") and checksum(tables) != bundle["checksum"]:
        problems.append("the checksum does not match the tables: the bundle was edited in transit")
    return problems


def summarise_counts(bundle: dict) -> dict:
    """How much of an event a bundle contains, for a validate call to quote."""
    if not isinstance(bundle, dict):
        return {}
    tables = bundle.get("tables") if isinstance(bundle.get("tables"), dict) else {}
    declared = bundle.get("counts") if isinstance(bundle.get("counts"), dict) else {}
    summary: dict[str, int] = {}
    for name, rows in tables.items():
        summary[name] = len(rows or [])
    for name, value in declared.items():
        summary.setdefault(name, value)
    return dict(sorted(summary.items()))


def import_bundle(db: Session, bundle: dict, *, mode: str = "dry_run") -> dict:
    """Load a bundle. Dry run by default; idempotent on the keys rows are known by."""
    problems = validate_bundle(bundle)
    if problems:
        return {
            "mode": mode,
            "refused": True,
            "problems": problems,
            "created": {},
            "updated": {},
        }

    tables = _index(bundle)
    created: dict[str, int] = {}
    updated: dict[str, int] = {}

    def bump(bucket: dict, name: str) -> None:
        bucket[name] = bucket.get(name, 0) + 1

    def as_dt(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    # ── tracks and prizes ───────────────────────────────────────────────────
    track_by_slug: dict[str, Track] = {}
    for row in tables.get("tracks", []):
        slug = row.get("slug") or row.get("source_ref")
        track = db.scalar(select(Track).where(Track.slug == slug))
        if track is None:
            track = Track(name=row.get("name") or slug, slug=slug)
            db.add(track)
            bump(created, "tracks")
        else:
            bump(updated, "tracks")
        track.name = row.get("name") or track.name
        track.description = row.get("description")
        track.prize_pool = row.get("prize_pool")
        track.display_order = int(row.get("display_order") or 0)
        db.flush()
        track_by_slug[slug] = track

    for row in tables.get("prizes", []):
        track = track_by_slug.get(row.get("track")) if row.get("track") else None
        existing = db.scalar(
            select(Prize).where(
                Prize.track_id == (track.id if track else None),
                Prize.rank == int(row.get("rank") or 1),
                Prize.title == (row.get("title") or ""),
            )
        )
        if existing is None:
            db.add(
                Prize(
                    track_id=track.id if track else None,
                    rank=int(row.get("rank") or 1),
                    title=row.get("title") or "",
                    description=row.get("description"),
                )
            )
            bump(created, "prizes")
        else:
            existing.description = row.get("description")
            bump(updated, "prizes")

    # ── people ──────────────────────────────────────────────────────────────
    user_by_email: dict[str, User] = {}
    for row in tables.get("users", []):
        email = (row.get("email") or "").strip().lower()
        if not email:
            continue
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, role=row.get("role") or "participant", password_hash=None)
            db.add(user)
            bump(created, "users")
        else:
            bump(updated, "users")
        user.name = row.get("name") or user.name
        user.role = row.get("role") or user.role
        user.source_ref = row.get("source_ref") or user.source_ref
        user.github_id = row.get("github_id") or user.github_id
        user.github_login = row.get("github_login") or user.github_login
        db.flush()
        user_by_email[email] = user

    # ── teams and members ───────────────────────────────────────────────────
    team_by_key: dict[str, Team] = {}
    for row in tables.get("teams", []):
        key = row.get("source_ref") or row.get("name")
        team = None
        if row.get("source_ref"):
            team = db.scalar(select(Team).where(Team.source_ref == row["source_ref"]))
        if team is None:
            team = db.scalar(
                select(Team).where(Team.name == row.get("name"), Team.source_ref.is_(None))
            )
        if team is None:
            team = Team(
                name=row.get("name") or "Unnamed team",
                # An imported team keeps its own code when the bundle has one, and
                # otherwise gets a fresh one from the same generator the console
                # uses — never a guessable `import-1`.
                invite_code=row.get("invite_code") or services.unique_invite_code(db),
            )
            db.add(team)
            bump(created, "teams")
        else:
            bump(updated, "teams")
        team.name = row.get("name") or team.name
        team.source_ref = row.get("source_ref") or team.source_ref
        if row.get("invite_code"):
            team.invite_code = row["invite_code"]
        creator = user_by_email.get((row.get("created_by") or "").lower())
        team.created_by = creator.id if creator else team.created_by
        db.flush()
        team_by_key[key] = team

        for email in row.get("members") or []:
            member = user_by_email.get(str(email).lower())
            if member is None:
                continue
            link = db.scalar(
                select(TeamMember).where(
                    TeamMember.team_id == team.id, TeamMember.user_id == member.id
                )
            )
            if link is None:
                # One team per person, so a member already on another team is moved
                # only if they are on none: the bundle is not allowed to evict anyone.
                elsewhere = db.scalar(
                    select(TeamMember).where(TeamMember.user_id == member.id)
                )
                if elsewhere is not None:
                    continue
                db.add(TeamMember(team_id=team.id, user_id=member.id))
                bump(created, "team_members")
            else:
                bump(updated, "team_members")

    # ── rubric ──────────────────────────────────────────────────────────────
    rubric_row = bundle.get("tables", {}).get("rubric") or {}
    rubric = db.scalar(select(Rubric).where(Rubric.is_active.is_(True)))
    if rubric is None and rubric_row.get("criteria"):
        rubric = Rubric(
            name=rubric_row.get("name") or "Imported rubric",
            criteria=list(rubric_row["criteria"]),
            is_active=True,
        )
        db.add(rubric)
        db.flush()
        bump(created, "rubrics")
    elif rubric is not None:
        bump(updated, "rubrics")

    # ── submissions ─────────────────────────────────────────────────────────
    submission_by_key: dict[str, Submission] = {}
    by_id: dict[str, Submission] = {}
    for row in tables.get("submissions", []):
        team = team_by_key.get(row.get("team"))
        if team is None:
            continue
        submission = None
        if row.get("source_ref"):
            submission = db.scalar(
                select(Submission).where(Submission.source_ref == row["source_ref"])
            )
        if submission is None:
            submission = db.scalar(
                select(Submission).where(
                    Submission.team_id == team.id, Submission.duplicate_of_submission_id.is_(None)
                )
            )
        if submission is None:
            submission = Submission(
                team_id=team.id,
                title=row.get("title") or "Untitled",
                repo_url=row.get("repo_url") or "",
            )
            db.add(submission)
            bump(created, "submissions")
        else:
            bump(updated, "submissions")
        submission.title = row.get("title") or submission.title
        submission.repo_url = row.get("repo_url") or submission.repo_url
        submission.docs_url = row.get("docs_url")
        submission.demo_url = row.get("demo_url")
        submission.video_url = row.get("video_url")
        submission.summary = row.get("summary")
        submission.status = row.get("status") or submission.status
        submission.source_ref = row.get("source_ref") or submission.source_ref
        submission.submitted_at = as_dt(row.get("submitted_at"))
        track = track_by_slug.get(row.get("track"))
        submission.track_id = track.id if track else None
        integrity = row.get("integrity") or {}
        submission.integrity_pct_in_window = integrity.get("pct_in_window")
        submission.integrity_flagged = bool(integrity.get("flagged"))
        submission.integrity_source = integrity.get("source")
        submission.integrity_checked_at = as_dt(integrity.get("checked_at"))
        db.flush()
        submission_by_key[row.get("source_ref") or f"id:{row.get('title')}"] = submission
        by_id[f"id:{submission.id}"] = submission

    # Duplicate markers, second pass: the row they point at may come later.
    for row in tables.get("submissions", []):
        if not row.get("duplicate_of"):
            continue
        submission = submission_by_key.get(row.get("source_ref") or f"id:{row.get('title')}")
        target = submission_by_key.get(row["duplicate_of"])
        if submission is not None and target is not None:
            submission.duplicate_of_submission_id = target.id

    def lookup_submission(key) -> Optional[Submission]:
        return submission_by_key.get(key) or by_id.get(str(key))

    # ── assignments and verdicts ────────────────────────────────────────────
    for row in tables.get("assignments", []):
        judge = user_by_email.get((row.get("judge") or "").lower())
        submission = lookup_submission(row.get("submission"))
        if judge is None or submission is None:
            continue
        found = db.scalar(
            select(Assignment).where(
                Assignment.judge_id == judge.id, Assignment.submission_id == submission.id
            )
        )
        if found is None:
            db.add(Assignment(judge_id=judge.id, submission_id=submission.id))
            bump(created, "assignments")
        else:
            bump(updated, "assignments")

    for row in tables.get("scores", []):
        judge = user_by_email.get((row.get("judge") or "").lower())
        submission = lookup_submission(row.get("submission"))
        if judge is None or submission is None:
            continue
        score = db.scalar(
            select(Score).where(
                Score.judge_id == judge.id, Score.submission_id == submission.id
            )
        )
        if score is None:
            score = Score(judge_id=judge.id, submission_id=submission.id)
            db.add(score)
            bump(created, "scores")
        else:
            bump(updated, "scores")
        score.technical_score = row.get("technical_score")
        score.technical_comment = row.get("technical_comment")
        score.presentation_score = row.get("presentation_score")
        score.presentation_comment = row.get("presentation_comment")
        score.technical_submitted_at = as_dt(row.get("technical_submitted_at"))
        score.presentation_submitted_at = as_dt(row.get("presentation_submitted_at"))
        score.rubric_id = rubric.id if (rubric and row.get("rubric")) else None
        db.flush()

        for entry in row.get("criteria") or []:
            criterion = db.scalar(
                select(ScoreCriterion).where(
                    ScoreCriterion.score_id == score.id, ScoreCriterion.key == entry.get("key")
                )
            )
            if criterion is None:
                criterion = ScoreCriterion(score_id=score.id, key=entry.get("key") or "criterion")
                db.add(criterion)
                bump(created, "score_criteria")
            else:
                bump(updated, "score_criteria")
            criterion.label = entry.get("label")
            criterion.weight = float(entry.get("weight") or 0.0)
            criterion.value = int(entry.get("value") or 1)

    # ── community ───────────────────────────────────────────────────────────
    voter_by_email: dict[str, Voter] = {}
    for row in tables.get("voters", []):
        email = (row.get("email") or "").strip().lower()
        if not email:
            continue
        voter = db.scalar(select(Voter).where(Voter.email == email))
        if voter is None:
            # A fresh digest, never the exported one: an export must not carry a
            # working ballot (see `export_bundle`).
            _token, digest = voting.mint_token()
            voter = Voter(email=email, token_hash=digest, display_name=row.get("display_name"))
            db.add(voter)
            bump(created, "voters")
        else:
            bump(updated, "voters")
        voter.display_name = row.get("display_name") or voter.display_name
        voter.blocked = bool(row.get("blocked"))
        voter.blocked_reason = row.get("blocked_reason")
        if row.get("verified") and voter.verified_at is None:
            voter.verified_at = _now()
        db.flush()
        voter_by_email[email] = voter

    for row in tables.get("votes", []):
        voter = voter_by_email.get((row.get("voter") or "").lower())
        submission = lookup_submission(row.get("submission"))
        if voter is None or submission is None:
            continue
        found = db.scalar(
            select(Vote).where(Vote.voter_id == voter.id, Vote.submission_id == submission.id)
        )
        if found is None:
            db.add(
                Vote(
                    voter_id=voter.id,
                    submission_id=submission.id,
                    score=int(row.get("score") or 3),
                    status=row.get("status") or "cast",
                    cast_at=as_dt(row.get("cast_at")),
                    struck_reason=row.get("struck_reason"),
                )
            )
            bump(created, "votes")
        else:
            # A vote is immutable, so an existing one is left exactly as it is.
            bump(updated, "votes")

    for row in tables.get("comments", []):
        submission = lookup_submission(row.get("submission"))
        if submission is None:
            continue
        body = row.get("body") or ""
        author = user_by_email.get((row.get("author_email") or "").lower())
        found = db.scalar(
            select(Comment).where(
                Comment.submission_id == submission.id, Comment.body == body
            )
        )
        if found is None:
            db.add(
                Comment(
                    submission_id=submission.id,
                    author_id=author.id if author else None,
                    author_name=row.get("author_name"),
                    author_email=row.get("author_email"),
                    body=body,
                    status=row.get("status") or "visible",
                    created_at=as_dt(row.get("created_at")),
                    moderated_reason=row.get("moderated_reason"),
                )
            )
            bump(created, "comments")
        else:
            bump(updated, "comments")

    # ── records ─────────────────────────────────────────────────────────────
    for row in tables.get("records", []):
        code = row.get("code")
        if not code or db.scalar(select(ParticipationRecord).where(ParticipationRecord.code == code)):
            bump(updated, "records")
            continue
        db.add(
            ParticipationRecord(
                code=code,
                subject_kind=row.get("subject_kind") or "participant",
                subject_ref=row.get("subject_ref"),
                subject_name=row.get("subject_name") or "Unknown",
                subject_email=row.get("subject_email"),
                event_name=(bundle.get("event") or {}).get("name") or settings.event_name,
                role=row.get("role"),
                payload=row.get("payload"),
                signature=row.get("signature") or "",
                algorithm=row.get("algorithm") or "hmac-sha256",
                issued_at=as_dt(row.get("issued_at")),
                revoked_at=as_dt(row.get("revoked_at")),
                revoked_reason=row.get("revoked_reason"),
            )
        )
        bump(created, "records")

    if mode != "dry_run":
        db.commit()
    else:
        db.rollback()

    return {
        "mode": mode,
        "refused": False,
        "created": created,
        "updated": updated,
        "checksum": bundle.get("checksum"),
        "event": bundle.get("event"),
        "note": (
            "Dry run: nothing was written. Credentials are never imported — the bundle "
            "carries identities, not the ability to sign in as them."
            if mode == "dry_run"
            else "Applied. Importing the same bundle again updates rather than duplicates."
        ),
    }


__all__ = [
    "BUNDLE_VERSION",
    "checksum",
    "export_bundle",
    "import_bundle",
    "summarise_counts",
    "validate_bundle",
]
