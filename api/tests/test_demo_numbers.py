"""Pin the numbers published in README.md and JUDGING.md.

The demo dataset exists to make one thing visible: a naive average and the
normalized ranking disagree, because a 6 from a disciplined grader is a stronger
signal than a 7 from a generous one. Those exact figures are quoted in README.md,
in JUDGING.md and in the committed acceptance report, so a change to the seed, the
rubric handling or the Z-score engine that quietly moves them would make the
documentation untrue — which is worse than a failing test.
"""
from __future__ import annotations

from sqlalchemy import select

from app import seed as seed_module
from app import zscore
from app.models import Score, Submission


def _records(db) -> tuple[list[zscore.ScoreRecord], dict[int, str]]:
    rows = db.execute(
        select(Score.judge_id, Score.submission_id, Score.technical_score).where(
            Score.technical_score.isnot(None)
        )
    ).all()
    titles = dict(db.execute(select(Submission.id, Submission.title)).all())
    return (
        [zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t)) for j, s, t in rows],
        titles,
    )


def test_the_documented_demo_flip_is_unchanged(db):
    assert seed_module.seed()["skipped"] is False
    records, titles = _records(db)
    assert len(records) == 50, "5 judges x 10 projects"

    raw = {row.submission_id: row for row in zscore.raw_leaderboard(records)}
    normalized = {row.submission_id: row for row in zscore.leaderboard(records)}
    by_title = {title: submission_id for submission_id, title in titles.items()}

    quiet = by_title["Quiet Craft"]
    assert raw[quiet].rank == 7
    assert round(raw[quiet].display, 2) == 6.00
    assert normalized[quiet].rank == 5
    assert round(normalized[quiet].display, 2) == 48.79

    flashy = by_title["Flashy Demo"]
    assert raw[flashy].rank == 5
    assert round(raw[flashy].display, 2) == 6.40
    assert normalized[flashy].rank == 7
    assert round(normalized[flashy].display, 2) == 46.66

    # The whole point of the demo: the naive average prefers the demo, Axion
    # prefers the craft.
    assert raw[flashy].rank < raw[quiet].rank
    assert normalized[quiet].rank < normalized[flashy].rank


def test_the_demo_seed_gives_every_judge_a_distribution_to_calibrate(db):
    seed_module.seed()
    stats = zscore.judge_statistics(_records(db)[0])

    assert len(stats) == 5
    assert all(entry.n == 10 for entry in stats.values())
    # The disciplined grader works a narrow band, the expansive one ranges widely:
    # that difference is what normalization is for.
    disciplined = next(entry for entry in stats.values() if entry.raw_mean < 5)
    expansive = next(entry for entry in stats.values() if entry.raw_mean > 7)
    assert disciplined.effective_sigma < expansive.effective_sigma
