"""The math is the product. These tests are the receipt."""
from __future__ import annotations

import math

from app import zscore


def rec(judge: int, project: int, score: float) -> zscore.ScoreRecord:
    return zscore.ScoreRecord(judge_id=judge, submission_id=project, score=score)


def base_records():
    """Two judges scoring five shared projects, then one unshared project each.

    Judge 1 grades on a 3–6 band, judge 2 on a 7–9 band.
    """
    records = []
    harsh = [3, 4, 4, 5, 4]
    generous = [7, 8, 8, 9, 8]
    for index, (h, g) in enumerate(zip(harsh, generous), start=1):
        records.append(rec(1, index, h))
        records.append(rec(2, index, g))
    records.append(rec(1, 6, 6))  # "6 from the harsh judge"
    records.append(rec(2, 7, 8))  # "8 from the generous judge"
    return records


def test_harsh_six_outranks_generous_eight():
    """The headline claim of the whole project."""
    records = base_records()
    table = zscore.leaderboard(records)
    ranks = {row.submission_id: row.rank for row in table}

    assert ranks[6] < ranks[7], "A 6 from a harsh judge must beat an 8 from a generous one"

    raw_ranks = {row.submission_id: row.rank for row in zscore.raw_leaderboard(records)}
    assert raw_ranks[7] == 1, "the naive average still puts the raw 8 on top"
    assert raw_ranks[6] > raw_ranks[7]

    harsh_six = next(r for r in table if r.submission_id == 6)
    generous_eight = next(r for r in table if r.submission_id == 7)
    assert harsh_six.z > generous_eight.z
    assert harsh_six.z > 0.0, "a 6 from a judge who averages ~4 is still an endorsement"
    assert harsh_six.raw_mean == 6.0 and generous_eight.raw_mean == 8.0


def test_z_scores_are_bounded_by_construction_not_by_chance():
    table = zscore.normalize(base_records())
    for result in table.values():
        assert math.isfinite(result.z)
        assert 0.0 <= result.display <= 100.0


def test_rank_change_reports_the_flip():
    changes = zscore.rank_changes(base_records())
    # Project 6 climbs from last-ish on raw average to the top under Axion.
    assert changes[6] > 0


def test_uniform_judge_contributes_zero_instead_of_dividing_by_zero():
    records = [rec(1, project, 7) for project in range(1, 6)]
    results = zscore.normalize(records)
    assert len(results) == 5
    for result in results.values():
        assert result.z == 0.0
        assert result.display == 50.0

    stats = zscore.judge_statistics(records)
    assert stats[1].discriminative is False


def test_single_verdict_is_shrunk_toward_the_pool():
    records = [rec(1, p, value) for p, value in zip(range(1, 6), [4, 5, 6, 5, 4])]
    records.append(rec(2, 6, 9))  # a judge with exactly one verdict

    stats = zscore.judge_statistics(records)
    assert stats[2].n == 1
    assert stats[2].weight_on_self < 0.5, "a single verdict must not define its own scale"

    results = zscore.normalize(records)
    assert math.isfinite(results[6].z)
    assert abs(results[6].z) < 5, "shrinkage keeps one-score judges from minting extreme z"


def test_display_mapping_clamps():
    assert zscore.to_display(0) == 50.0
    assert zscore.to_display(100) == 100.0
    assert zscore.to_display(-100) == 0.0
    assert zscore.to_display(2) == 70.0


def test_empty_input_is_empty_output():
    assert zscore.normalize([]) == {}
    assert zscore.leaderboard([]) == []
    assert zscore.judge_statistics([]) == {}


def test_ties_are_broken_deterministically_by_raw_then_id():
    records = [rec(1, 1, 6), rec(1, 2, 6), rec(1, 3, 6)]
    table = zscore.leaderboard(records)
    assert [row.submission_id for row in table] == [1, 2, 3]
    assert [row.rank for row in table] == [1, 2, 3]


def test_coverage_flags_singletons():
    records = [rec(1, 1, 5), rec(1, 2, 5), rec(2, 2, 6)]
    assert zscore.coverage_flags(records) == {1: 1}


def test_shrinkage_is_symmetric_when_sample_sizes_match():
    """Equal n means equal shrinkage, so the ordering cannot be an artifact."""
    stats = zscore.judge_statistics(base_records())
    assert stats[1].weight_on_self == stats[2].weight_on_self
