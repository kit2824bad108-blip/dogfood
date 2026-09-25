"""Axion Core — Z-score normalization.

Pure functions with no framework or database imports, so the whole scoring model
is provable in isolation (see `tests/test_zscore.py`).

The problem
-----------
A raw average punishes projects for *which judge* happened to score them. If
Judge A grades on a 3–5 band and Judge B on a 7–9 band, an identical project
scores two points lower purely from grader drift.

The fix
-------
Standardize each judge against **their own** scoring distribution, then average
the standardized verdicts:

    z = (raw - judge_mean) / judge_sigma

A 6 from a judge who averages 4 with sigma 1.2 (z = +1.67) is a stronger
endorsement than an 8 from a judge who averages 8 (z = 0.00).

Three guard rails make this defensible at hackathon scale, where a judge sees
only a handful of projects:

1. **Mean shrinkage.** A judge with two verdicts has a meaningless mean, so each
   judge's mean is blended toward the global pool with a pseudo-count
   `PRIOR_STRENGTH`: `w = n / (n + k)`. With n=1 the judge is 75% global pool.
2. **Weak variance shrinkage + a dispersion gate.** The *spread* is shrunk with a
   deliberately weaker prior (`PRIOR_VARIANCE`): a judge who grades inside a
   narrow band is not noisy, they are informative — a 6 from someone who grades
   everything 4 or 5 is a strong signal, and shrinking their sigma away would
   discard exactly that signal. Two degenerate cases are handled explicitly
   rather than blended: a judge who gave *every* project the same number carries
   no ranking information at all (`z = 0`), and a judge with a single verdict has
   no measurable dispersion, so the pool's sigma is borrowed.
3. **Sigma floor.** Guards the final division against near-zero dispersion.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from statistics import mean, pvariance
from typing import Iterable, Optional

PRIOR_STRENGTH = 3.0  # mean shrinkage
PRIOR_VARIANCE = 1.0  # variance shrinkage (deliberately weak)
MIN_RAW_SIGMA = 0.05  # below this a judge gave identical verdicts
SIGMA_FLOOR = 0.05
DISPLAY_MIDPOINT = 50.0
DISPLAY_SCALE = 10.0
DISPLAY_MIN = 0.0
DISPLAY_MAX = 100.0

FLAG_COVERAGE_BELOW = 50.0


@dataclass(frozen=True)
class ScoreRecord:
    """One judge's technical verdict on one submission."""

    judge_id: int
    submission_id: int
    score: float


@dataclass(frozen=True)
class JudgeStats:
    judge_id: int
    n: int
    raw_mean: float
    raw_sigma: float
    effective_mean: float
    effective_sigma: float
    weight_on_self: float
    discriminative: bool


@dataclass(frozen=True)
class ProjectResult:
    submission_id: int
    z: float
    display: float
    raw_mean: float
    judges: int
    rank: int = 0


def _sigma(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return sqrt(pvariance(values))


def judge_statistics(
    records: Iterable[ScoreRecord], *, prior_strength: float = PRIOR_STRENGTH
) -> dict[int, JudgeStats]:
    """Per-judge normalized stats, shrunk toward the global pool."""
    records = list(records)
    by_judge: dict[int, list[float]] = defaultdict(list)
    for record in records:
        by_judge[record.judge_id].append(float(record.score))

    if not by_judge:
        return {}

    all_scores = [float(r.score) for r in records]
    global_mean = mean(all_scores)
    global_sigma = _sigma(all_scores) if len(all_scores) > 1 else 0.0

    stats: dict[int, JudgeStats] = {}
    for judge_id, scores in by_judge.items():
        n = len(scores)
        raw_mean = mean(scores)
        raw_sigma = _sigma(scores)
        weight = n / (n + prior_strength) if n + prior_strength > 0 else 0.0
        effective_mean = weight * raw_mean + (1.0 - weight) * global_mean

        if n == 1:
            # A single observation has no measurable dispersion: borrow the
            # pool's, so one verdict cannot mint an arbitrarily large z.
            effective_sigma = global_sigma
        elif raw_sigma < MIN_RAW_SIGMA:
            # Identical verdicts for every project: no ranking information.
            effective_sigma = 0.0
        else:
            weight_var = n / (n + PRIOR_VARIANCE)
            effective_sigma = sqrt(
                weight_var * (raw_sigma**2) + (1.0 - weight_var) * (global_sigma**2)
            )

        stats[judge_id] = JudgeStats(
            judge_id=judge_id,
            n=n,
            raw_mean=raw_mean,
            raw_sigma=raw_sigma,
            effective_mean=effective_mean,
            effective_sigma=effective_sigma,
            weight_on_self=weight,
            discriminative=effective_sigma >= SIGMA_FLOOR,
        )
    return stats


def judge_z(score: float, stats: JudgeStats) -> float:
    """Standardize one verdict. Non-discriminative judges contribute 0."""
    if not stats.discriminative:
        return 0.0
    return (float(score) - stats.effective_mean) / stats.effective_sigma


def to_display(z: float) -> float:
    """Map an unbounded z onto a human-readable 0–100 scale."""
    value = DISPLAY_MIDPOINT + DISPLAY_SCALE * z
    return round(min(DISPLAY_MAX, max(DISPLAY_MIN, value)), 2)


def normalize(
    records: Iterable[ScoreRecord], *, prior_strength: float = PRIOR_STRENGTH
) -> dict[int, ProjectResult]:
    """Returns {submission_id: ProjectResult} with z, display score and raw mean."""
    records = list(records)
    if not records:
        return {}

    stats = judge_statistics(records, prior_strength=prior_strength)

    z_by_project: dict[int, list[float]] = defaultdict(list)
    raw_by_project: dict[int, list[float]] = defaultdict(list)
    for record in records:
        z_by_project[record.submission_id].append(judge_z(record.score, stats[record.judge_id]))
        raw_by_project[record.submission_id].append(float(record.score))

    results: dict[int, ProjectResult] = {}
    for submission_id, zs in z_by_project.items():
        z = mean(zs)
        raw = raw_by_project[submission_id]
        results[submission_id] = ProjectResult(
            submission_id=submission_id,
            z=round(z, 6),
            display=to_display(z),
            raw_mean=round(mean(raw), 4),
            judges=len(raw),
        )
    return results


def leaderboard(
    records: Iterable[ScoreRecord], *, prior_strength: float = PRIOR_STRENGTH
) -> list[ProjectResult]:
    """Projects ranked by normalized score. Ties break on raw mean, then id."""
    results = normalize(records, prior_strength=prior_strength)
    ordered = sorted(
        results.values(),
        key=lambda r: (-r.z, -r.raw_mean, r.submission_id),
    )
    return [
        ProjectResult(
            submission_id=r.submission_id,
            z=r.z,
            display=r.display,
            raw_mean=r.raw_mean,
            judges=r.judges,
            rank=index + 1,
        )
        for index, r in enumerate(ordered)
    ]


def raw_leaderboard(records: Iterable[ScoreRecord]) -> list[ProjectResult]:
    """The naive average, kept alongside so the difference is visible."""
    records = list(records)
    by_project: dict[int, list[float]] = defaultdict(list)
    for record in records:
        by_project[record.submission_id].append(float(record.score))
    ordered = sorted(
        by_project.items(),
        key=lambda item: (-mean(item[1]), item[0]),
    )
    out = []
    for index, (submission_id, scores) in enumerate(ordered):
        out.append(
            ProjectResult(
                submission_id=submission_id,
                z=0.0,
                display=round(mean(scores), 2),
                raw_mean=round(mean(scores), 4),
                judges=len(scores),
                rank=index + 1,
            )
        )
    return out


def rank_changes(
    records: Iterable[ScoreRecord], *, prior_strength: float = PRIOR_STRENGTH
) -> dict[int, int]:
    """{submission_id: rank movement} from raw average to normalized ranking.

    Positive means the project climbs under Axion normalization — the "quiet
    craftsmanship beats a flashy demo" signal.
    """
    records = list(records)
    raw_ranks = {r.submission_id: r.rank for r in raw_leaderboard(records)}
    norm_ranks = {r.submission_id: r.rank for r in leaderboard(records, prior_strength=prior_strength)}
    return {
        submission_id: raw_ranks[submission_id] - norm_rank
        for submission_id, norm_rank in norm_ranks.items()
    }


def coverage_flags(records: Iterable[ScoreRecord], minimum_judges: int = 2) -> dict[int, int]:
    """{submission_id: judge_count} for projects below `minimum_judges`.

    A project rated by one judge carries an unreliable estimate; admins should
    see which projects are thinly covered before finalizing.
    """
    counts: dict[int, int] = defaultdict(int)
    for record in records:
        counts[record.submission_id] += 1
    return {sid: n for sid, n in counts.items() if n < minimum_judges}


__all__ = [
    "ScoreRecord",
    "JudgeStats",
    "ProjectResult",
    "judge_statistics",
    "judge_z",
    "to_display",
    "normalize",
    "leaderboard",
    "raw_leaderboard",
    "rank_changes",
    "coverage_flags",
    "PRIOR_STRENGTH",
    "PRIOR_VARIANCE",
    "MIN_RAW_SIGMA",
    "SIGMA_FLOOR",
    "FLAG_COVERAGE_BELOW",
]
