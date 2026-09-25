"""Ephemeral Archive — snapshot the event, then spin the database down.

`build_bundle` produces a self-contained JSON document; `to_markdown` renders the
same content as a human-readable results page that can be committed to GitHub
Pages. Nothing here mutates state, so archiving is always safe to run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import zscore
from .config import settings
from .models import Assignment, AuditLog, Score, Submission, Team, TeamMember, Track, User

BUNDLE_VERSION = 1


def _records(db: Session) -> list[zscore.ScoreRecord]:
    rows = db.execute(
        select(Score.judge_id, Score.submission_id, Score.technical_score).where(
            Score.technical_score.isnot(None)
        )
    ).all()
    return [
        zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t))
        for j, s, t in rows
    ]


def build_bundle(db: Session) -> dict:
    records = _records(db)

    teams = {team.id: team for team in db.scalars(select(Team)).all()}
    all_submissions = db.scalars(select(Submission)).all()
    # Drafts never entered the event, so they are counted but not ranked.
    submissions = {s.id: s for s in all_submissions if s.status == "submitted"}
    draft_count = len(all_submissions) - len(submissions)
    tracks = {track.id: track.name for track in db.scalars(select(Track)).all()}
    members: dict[int, list[str]] = {}
    for member in db.scalars(select(TeamMember)).all():
        user = db.get(User, member.user_id)
        members.setdefault(member.team_id, []).append(user.name or user.email if user else "")

    judges = {
        judge.id: judge for judge in db.scalars(select(User).where(User.role == "judge")).all()
    }

    normalized = {r.submission_id: r for r in zscore.leaderboard(records)}
    raw = {r.submission_id: r for r in zscore.raw_leaderboard(records)}
    movement = zscore.rank_changes(records)

    results = []
    for submission_id, submission in sorted(submissions.items()):
        norm = normalized.get(submission_id)
        team = teams.get(submission.team_id)
        results.append(
            {
                "submission_id": submission_id,
                "title": submission.title,
                "team": team.name if team else None,
                "members": members.get(submission.team_id, []),
                "track": tracks.get(submission.track_id),
                "repo_url": submission.repo_url,
                "docs_url": submission.docs_url,
                "demo_url": submission.demo_url,
                "video_url": submission.video_url,
                "summary": submission.summary,
                "commit_integrity": {
                    "pct_in_window": submission.integrity_pct_in_window,
                    "flagged_for_review": submission.integrity_flagged,
                    "source": submission.integrity_source,
                },
                "judges": norm.judges if norm else 0,
                "axion_score": norm.display if norm else None,
                "z_score": norm.z if norm else None,
                "axion_rank": norm.rank if norm else None,
                "raw_average": raw[submission_id].display if submission_id in raw else None,
                "raw_rank": raw[submission_id].rank if submission_id in raw else None,
                "rank_movement": movement.get(submission_id, 0),
            }
        )

    results.sort(key=lambda row: (row["axion_rank"] is None, row["axion_rank"] or 0))

    verdicts = []
    if records:
        stats = zscore.judge_statistics(records)
        scores_by_pair = {
            (row.judge_id, row.submission_id): float(row.technical_score)
            for row in db.execute(
                select(Score.judge_id, Score.submission_id, Score.technical_score).where(
                    Score.technical_score.isnot(None)
                )
            ).all()
        }
        for (judge_id, submission_id), value in sorted(scores_by_pair.items()):
            judge = judges.get(judge_id)
            stat = stats[judge_id]
            verdicts.append(
                {
                    "judge": judge.name or judge.email if judge else f"judge:{judge_id}",
                    "submission_id": submission_id,
                    "technical_score": value,
                    "judge_raw_mean": round(stat.raw_mean, 4),
                    "judge_raw_sigma": round(stat.raw_sigma, 4),
                    "z_score": round(zscore.judge_z(value, stat), 4),
                }
            )

    return {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event": {
            "name": settings.event_name,
            "starts_at": settings.event_start.isoformat(),
            "ends_at": settings.event_end.isoformat(),
        },
        "methodology": {
            "aggregation": "mean of per-judge z-scores",
            "ranking_basis": "technical verdicts only; presentation scores are retained but not ranked",
            "z_score": "(raw - shrunk_judge_mean) / shrunk_judge_sigma",
            "prior_strength": zscore.PRIOR_STRENGTH,
            "prior_variance": zscore.PRIOR_VARIANCE,
            "sigma_floor": zscore.SIGMA_FLOOR,
            "display_mapping": "50 + 10 * z, clamped to [0, 100]",
            "note": "Commit integrity is an advisory signal, not a disqualification.",
        },
        "totals": {
            "teams": len(teams),
            "submissions": len(submissions),
            "drafts": draft_count,
            "judges": len(judges),
            "judge_verdicts": len(records),
            "assignments": len(db.scalars(select(Assignment.id)).all()),
            "audit_entries": len(db.scalars(select(AuditLog.id)).all()),
        },
        "results": results,
        "verdicts": verdicts,
    }


def to_markdown(bundle: dict) -> str:
    event = bundle["event"]
    lines = [
        f"# {event['name']} — Results",
        "",
        f"_Archived {bundle['generated_at']}_",
        "",
        "Scores are **Z-score normalized** per judge, so a hard grader is not",
        "penalized for their scale. `Rank ±` shows movement versus a naive average.",
        "",
        "| Rank | Project | Team | Axion score | Raw avg | Rank ± | Judges |",
        "| ---- | ------- | ---- | ----------- | ------- | ------ | ------ |",
    ]
    for row in bundle["results"]:
        movement = row["rank_movement"] or 0
        arrow = f"+{movement}" if movement > 0 else str(movement)
        lines.append(
            f"| {row['axion_rank']} | {row['title']} | {row['team']} | "
            f"{row['axion_score']} | {row['raw_average']} | {arrow} | {row['judges']} |"
        )

    lines += [
        "",
        "## Methodology",
        "",
        f"- Z-score: `{bundle['methodology']['z_score']}`",
        f"- Aggregation: {bundle['methodology']['aggregation']}",
        f"- Display mapping: `{bundle['methodology']['display_mapping']}`",
        f"- Shrinkage prior strength: {bundle['methodology']['prior_strength']}",
        "",
        f"> {bundle['methodology']['note']}",
        "",
        "## Submissions",
        "",
    ]
    for row in bundle["results"]:
        integrity = row["commit_integrity"]
        flag = " ⚠️ pre-existing history" if integrity["flagged_for_review"] else ""
        lines += [
            f"### {row['axion_rank']}. {row['title']}{flag}",
            "",
            f"- Team: {row['team']}",
            f"- Track: {row.get('track') or 'unassigned'}",
            f"- Repository: {row['repo_url']}",
        ]
        if row["demo_url"]:
            lines.append(f"- Demo: {row['demo_url']}")
        if row["video_url"]:
            lines.append(f"- Video: {row['video_url']}")
        if row["summary"]:
            lines.append(f"- Summary: {row['summary']}")
        lines.append("")
    return "\n".join(lines)
