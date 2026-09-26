"""Coverage accounting and balanced judge assignment.

Axion's default model is **full coverage**: every judge sees every project. At
hackathon scale (10 projects × 5 judges = 50 verdicts) that is the right call,
because per-judge Z-scores are only comparable when their verdicts overlap — and
every number published in README.md and JUDGING.md was computed against it.

It does not scale. At 500 projects × 40 judges, full coverage is 20,000
judgments, which no one is going to complete. This module adds the other model:
a configurable target number of reviews per project, with balanced judge load.
It is an explicit organiser action (`POST /api/admin/assignments/balance`), never
something that silently rewrites a running event.

The greedy order is deliberately boring:

  1. projects with the fewest existing assignments first, so thin coverage is
     addressed before already-well-covered work;
  2. for each project, the eligible judges with the smallest current load;
  3. never exceeding `max_projects_per_judge`.

What it refuses to do is claim more than it can prove. The plan reports the
coverage and load distribution it *would* produce, plus the number of connected
components in the judge/project overlap graph — the honest measure of whether
verdicts from different judges can be compared at all.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, pvariance
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import zscore
from .models import Assignment, Score, Submission, Team, User


def submitted_submissions(db: Session) -> list[tuple[int, str, str]]:
    """(submission_id, title, team name) for everything actually in the event."""
    rows = db.execute(
        select(Submission.id, Submission.title, Team.name)
        .join(Team, Team.id == Submission.team_id)
        .where(Submission.status == "submitted")
        .order_by(Submission.id)
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def judge_ids(db: Session) -> list[int]:
    return list(db.scalars(select(User.id).where(User.role == "judge").order_by(User.id)).all())


def assignment_map(db: Session) -> dict[int, set[int]]:
    """{submission_id: {judge_id}} for every assignment in the event."""
    rows = db.execute(
        select(Assignment.submission_id, Assignment.judge_id)
        .join(Submission, Submission.id == Assignment.submission_id)
        .where(Submission.status == "submitted")
    ).all()
    mapping: dict[int, set[int]] = defaultdict(set)
    for submission_id, judge_id in rows:
        mapping[submission_id].add(judge_id)
    return mapping


def verdict_counts(db: Session) -> dict[int, int]:
    """{submission_id: number of technical verdicts actually filed}."""
    rows = db.execute(
        select(Score.submission_id, func.count(Score.id))
        .where(Score.technical_score.isnot(None))
        .group_by(Score.submission_id)
    ).all()
    return {submission_id: count for submission_id, count in rows}


def judge_loads(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(Assignment.judge_id, func.count(Assignment.id))
        .join(Submission, Submission.id == Assignment.submission_id)
        .where(Submission.status == "submitted")
        .group_by(Assignment.judge_id)
    ).all()
    return {judge_id: count for judge_id, count in rows}


def _components(pairs: Iterable[tuple[int, int]]) -> int:
    """Connected components of the bipartite judge/project overlap graph.

    One component means every judge's verdicts can be calibrated against every
    other judge's through some chain of shared projects. Several components mean
    the ranking is really several separate rankings, and saying so is more useful
    than reporting a single confident number.
    """
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: tuple[str, int], right: tuple[str, int]) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    nodes: set[tuple[str, int]] = set()
    for judge_id, submission_id in pairs:
        union(("judge", judge_id), ("project", submission_id))
        nodes.add(("judge", judge_id))
        nodes.add(("project", submission_id))
    return len({find(node) for node in nodes})


def _spread(values: list[int]) -> dict[str, float]:
    if not values:
        return {"min": 0, "max": 0, "mean": 0.0, "variance": 0.0}
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(mean(values), 2),
        "variance": round(pvariance(values), 3) if len(values) > 1 else 0.0,
    }


def coverage_snapshot(
    db: Session, *, expected: Optional[int] = None, minimum: int = zscore.MINIMUM_JUDGES
) -> dict:
    """Per-project evidence, including projects whose judges filed nothing."""
    assignments = assignment_map(db)
    verdicts = verdict_counts(db)
    rows = []
    for submission_id, title, team in submitted_submissions(db):
        assigned = len(assignments.get(submission_id, ()))
        filed = verdicts.get(submission_id, 0)
        target = expected if expected is not None else assigned
        rows.append(
            {
                "submission_id": submission_id,
                "title": title,
                "team": team,
                "assigned": assigned,
                "reviews": filed,
                "expected": target,
                "missing": max(target - filed, 0),
                "provisional": filed < minimum,
                "scoreable": filed > 0,
            }
        )
    provisional = [row for row in rows if row["provisional"]]
    return {
        "submissions": rows,
        "minimum_judges": minimum,
        "totals": {
            "submissions": len(rows),
            "assignments": sum(row["assigned"] for row in rows),
            "verdicts": sum(row["reviews"] for row in rows),
            "provisional": len(provisional),
            "without_verdicts": sum(1 for row in rows if row["reviews"] == 0),
            "coverage_percent": round(
                (len(rows) - len(provisional)) / len(rows) * 100, 1
            )
            if rows
            else 0.0,
        },
    }


def plan_balanced_assignment(
    db: Session,
    *,
    reviews_per_project: int = 3,
    max_projects_per_judge: Optional[int] = None,
) -> dict:
    """Compute a balanced assignment plan without writing anything."""
    judges = judge_ids(db)
    if not judges:
        raise ValueError("There are no judges to assign")

    submissions = submitted_submissions(db)
    if not submissions:
        raise ValueError("There are no submitted projects to assign")

    assignments = assignment_map(db)
    loads = defaultdict(int, judge_loads(db))
    for judge_id in judges:
        loads.setdefault(judge_id, 0)

    ordered = sorted(submissions, key=lambda row: (len(assignments.get(row[0], ())), row[0]))
    plan: list[dict] = []
    projected = {submission_id: set(members) for submission_id, members in assignments.items()}

    for submission_id, title, _team in ordered:
        current = projected.get(submission_id, set())
        needed = max(0, reviews_per_project - len(current))
        if needed == 0:
            continue
        candidates = [judge for judge in judges if judge not in current]
        candidates.sort(key=lambda judge: (loads[judge], judge))
        chosen: list[int] = []
        for judge_id in candidates:
            if len(chosen) == needed:
                break
            if max_projects_per_judge is not None and loads[judge_id] >= max_projects_per_judge:
                continue
            chosen.append(judge_id)
        if not chosen:
            continue
        for judge_id in chosen:
            loads[judge_id] += 1
        projected.setdefault(submission_id, set()).update(chosen)
        plan.append({"submission_id": submission_id, "title": title, "add_judges": chosen})

    # ── what the plan would produce ─────────────────────────────────────────
    per_project = [len(projected.get(row[0], ())) for row in submissions]
    load_values = [loads[judge_id] for judge_id in judges]
    pairs = [
        (judge_id, submission_id)
        for submission_id, members in projected.items()
        for judge_id in members
    ]
    before_pairs = [
        (judge_id, submission_id)
        for submission_id, members in assignments.items()
        for judge_id in members
    ]
    new_assignments = sum(len(entry["add_judges"]) for entry in plan)

    return {
        "plan": plan,
        "reviews_per_project": reviews_per_project,
        "max_projects_per_judge": max_projects_per_judge,
        "projected": {
            "new_assignments": new_assignments,
            "judgments_after": len(before_pairs) + new_assignments,
            "judgments_before": len(before_pairs),
            "reviews_per_project": _spread(per_project),
            "judge_load": _spread(load_values),
            "components_before": _components(before_pairs),
            "components_after": _components(pairs),
            "projects_without_assignments": sum(1 for value in per_project if value == 0),
            "coverage_percent": round(
                sum(1 for value in per_project if value >= reviews_per_project)
                / len(submissions)
                * 100,
                1,
            )
            if submissions
            else 0.0,
        },
        "judges": len(judges),
        "submissions": len(submissions),
    }


def apply_plan(db: Session, plan: list[dict]) -> int:
    """Create the assignments a plan describes. Idempotent per judge/project pair."""
    existing = assignment_map(db)
    created = 0
    for entry in plan:
        submission_id = entry["submission_id"]
        already = existing.get(submission_id, set())
        for judge_id in entry["add_judges"]:
            if judge_id in already:
                continue
            db.add(Assignment(judge_id=judge_id, submission_id=submission_id))
            already.add(judge_id)
            created += 1
    if created:
        db.flush()
    return created


def dispersion_note(value: float) -> str:
    """A one-line reading of a load distribution, for the organiser report."""
    if value <= 0.5:
        return "evenly balanced"
    if value <= 2.0:
        return "mildly uneven"
    return "uneven: consider a lower max_projects_per_judge"


__all__ = [
    "submitted_submissions",
    "judge_ids",
    "assignment_map",
    "verdict_counts",
    "judge_loads",
    "coverage_snapshot",
    "plan_balanced_assignment",
    "apply_plan",
    "dispersion_note",
]
