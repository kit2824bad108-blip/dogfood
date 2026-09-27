"""Judging against PostgreSQL: rubrics, persistence, blinding, normalization.

The numbers published on a leaderboard are produced here, so this is where the
scoring path is checked end to end against the database a deployment runs:
rubric inputs are stored beside the verdict they produced, the blind gate is the
API's rather than the browser's, coverage is reported rather than hidden, and the
normalization the endpoint returns matches the mapping `JUDGING.md` documents.

On precision: `weighted_technical_score` deliberately returns an **int** — a
verdict is one number, and the rubric inputs that produced it are kept in
`score_criteria` and in the audit entry, so nothing is lost by storing 8 instead
of 7.999. These tests assert that design rather than quietly changing it: the
stored value is an exact integer, the criterion weights are floats, and the
normalized display is a 2-decimal float off `50 + 10z`.
"""
from __future__ import annotations

import math

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Score, ScoreCriterion, Submission, Team, TeamMember
from app.services import (
    active_rubric,
    assign_judges,
    ensure_default_rubric,
    normalized_criteria,
    weighted_technical_score,
)
from app.devtokens import stable_token

pytestmark = pytest.mark.postgres

JUDGES = 3
PROJECTS = 4


def headers_for(user) -> dict[str, str]:
    """The deterministic header credential the official checker uses."""
    return {"Authorization": f"Bearer {stable_token(user)}"}


@pytest.fixture()
def world(db, make_user):
    """Three judges, four projects, every judge assigned to every project."""
    judges = [make_user("judge", email=f"judge{j}.pgjudge@test.dev") for j in range(JUDGES)]
    organizer = make_user("admin", email="organizer.pgjudge@test.dev")
    submissions = []
    for n in range(PROJECTS):
        participant = make_user("participant", email=f"hacker{n}.pgjudge@test.dev")
        team = Team(
            name=f"PG Judging Team {n}",
            invite_code=f"PGJUDGE{n}",
            created_by=participant.id,
        )
        db.add(team)
        db.flush()
        db.add(TeamMember(team_id=team.id, user_id=participant.id))
        submission = Submission(
            team_id=team.id,
            title=f"PG Judging Project {n}",
            repo_url=f"https://github.com/pg-judging/project-{n}",
            demo_url=f"https://demo-{n}.pg-judging.dev",
            video_url=f"https://youtu.be/pg-judging-{n}",
            status="submitted",
        )
        db.add(submission)
        db.flush()
        assign_judges(db, submission.id)
        submissions.append(submission)
    db.commit()
    return {"judges": judges, "organizer": organizer, "submissions": submissions}


def file_verdict(client, judge, submission_id, **payload):
    return client.post(
        "/api/judging/scores",
        json={"submission_id": submission_id, **payload},
        headers=headers_for(judge),
    )


def leaderboard(client, organizer) -> dict:
    response = client.get("/api/admin/leaderboard", headers=headers_for(organizer))
    assert response.status_code == 200, response.text
    return response.json()


# ── the rubric ───────────────────────────────────────────────────────────────


def test_a_rubric_verdict_is_derived_stored_and_audited(client, db, world):
    judge = world["judges"][0]
    submission = world["submissions"][0]
    rubric = active_rubric(db) or ensure_default_rubric(db)
    db.commit()  # the request runs in its own session; an uncommitted rubric is invisible
    criteria = normalized_criteria(rubric)
    values = {entry["key"]: (10 if index % 2 == 0 else 6) for index, entry in enumerate(criteria)}
    expected = weighted_technical_score(criteria, values)

    response = file_verdict(
        client, judge, submission.id, criteria=[{"key": k, "value": v} for k, v in values.items()]
    )

    assert response.status_code == 200, response.text
    score = db.scalars(
        select(Score).where(Score.judge_id == judge.id, Score.submission_id == submission.id)
    ).one()
    assert score.technical_score == expected, "the rubric weights, not the raw average"
    assert isinstance(score.technical_score, int)
    assert score.rubric_id == rubric.id

    rows = {
        row.key: row
        for row in db.scalars(select(ScoreCriterion).where(ScoreCriterion.score_id == score.id)).all()
    }
    assert {key: row.value for key, row in rows.items()} == values
    assert {key: row.weight for key, row in rows.items()} == {
        entry["key"]: entry["weight"] for entry in criteria
    }

    entry = db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "score.technical_submitted")
        .order_by(AuditLog.id.desc())
    ).first()
    assert entry.details["criteria"] == values, "the inputs are recorded, not just the total"
    assert entry.details["technical_score_derived"] is True
    assert entry.details["rubric_id"] == rubric.id


def test_a_repeated_verdict_updates_one_row_and_keeps_both_entries(client, db, world):
    judge = world["judges"][0]
    submission = world["submissions"][0]

    first = file_verdict(client, judge, submission.id, technical_score=7)
    second = file_verdict(client, judge, submission.id, technical_score=9)
    assert (first.status_code, second.status_code) == (200, 200)

    rows = db.scalars(
        select(Score).where(Score.judge_id == judge.id, Score.submission_id == submission.id)
    ).all()
    assert len(rows) == 1 and rows[0].technical_score == 9

    actions = [
        entry.action
        for entry in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        if entry.action in {"score.technical_submitted", "score.technical_modified"}
    ]
    assert actions == ["score.technical_submitted", "score.technical_modified"]


# ── the blind gate and assignment ────────────────────────────────────────────


def test_presentation_stays_locked_until_that_judge_files_a_verdict(client, db, world):
    judge, other = world["judges"][0], world["judges"][1]
    submission = world["submissions"][0]

    locked = client.get(
        f"/api/judging/submissions/{submission.id}/presentation", headers=headers_for(judge)
    )
    assert locked.status_code == 403
    refused = file_verdict(client, judge, submission.id, presentation_score=9)
    assert refused.status_code == 403, "the API refuses it, not the order of the screens"

    assert file_verdict(client, judge, submission.id, technical_score=8).status_code == 200
    unlocked = client.get(
        f"/api/judging/submissions/{submission.id}/presentation", headers=headers_for(judge)
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["presentation"]["demo_url"] == submission.demo_url

    still_locked = client.get(
        f"/api/judging/submissions/{submission.id}/presentation", headers=headers_for(other)
    )
    assert still_locked.status_code == 403, "unlocking is per judge, not per project"


def test_the_technical_view_never_carries_presentation_links(client, world):
    judge = world["judges"][0]
    submission = world["submissions"][0]

    body = client.get(
        f"/api/judging/submissions/{submission.id}", headers=headers_for(judge)
    ).json()

    assert body["submission"]["presentation_unlocked"] is False
    assert "demo_url" not in body["submission"]
    assert submission.demo_url not in str(body)


def test_an_unassigned_judge_is_refused_by_the_api(client, db, world, make_user):
    outsider = make_user("judge", email="outsider.pgjudge@test.dev")
    submission = world["submissions"][0]

    assert (
        client.get(
            f"/api/judging/submissions/{submission.id}", headers=headers_for(outsider)
        ).status_code
        == 403
    )
    assert file_verdict(client, outsider, submission.id, technical_score=10).status_code == 403
    assert (
        db.scalar(select(func.count(Score.id)).where(Score.judge_id == outsider.id)) == 0
    ), "a refused verdict is not a stored one"


def test_an_admin_may_score_any_project(client, db, world):
    """`upsert_score` exempts admins from the assignment rule; asserted, not assumed."""
    organizer = world["organizer"]
    submission = world["submissions"][0]

    response = file_verdict(client, organizer, submission.id, technical_score=6)

    assert response.status_code == 200, response.text


# ── normalization, coverage and the leaderboard ──────────────────────────────


def test_identical_verdicts_all_land_in_the_middle(client, db, world):
    """Zero variance cannot be standardized; the sigma floor keeps it at 50.0."""
    for judge in world["judges"]:
        for submission in world["submissions"]:
            assert file_verdict(client, judge, submission.id, technical_score=7).status_code == 200

    body = leaderboard(client, world["organizer"])

    assert len(body["leaderboard"]) == PROJECTS
    for row in body["leaderboard"]:
        assert row["z_score"] == 0.0
        assert row["axion_score"] == 50.0
        assert row["reviews_filed"] == JUDGES and row["provisional"] is False
    assert body["coverage_summary"]["coverage_percent"] == 100.0


def test_normalization_separates_projects_a_judge_rated_differently(client, db, world):
    pattern = [9, 7, 5, 3]
    for judge in world["judges"]:
        for submission, value in zip(world["submissions"], pattern):
            assert file_verdict(client, judge, submission.id, technical_score=value).status_code == 200

    body = leaderboard(client, world["organizer"])
    rows = body["leaderboard"]

    assert [row["axion_rank"] for row in rows] == [1, 2, 3, 4]
    assert [row["title"] for row in rows] == [
        f"PG Judging Project {index}" for index in range(PROJECTS)
    ], "a project every judge rates highest must rank first"
    scores = [row["axion_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    for row in rows:
        expected = round(min(100.0, max(0.0, 50 + 10 * row["z_score"])), 2)
        assert row["axion_score"] == expected, "the documented display mapping"
        assert math.isfinite(row["z_score"])
    assert body["methodology"]["display_mapping"] == "50 + 10z, clamped to [0, 100]"
    assert body["verdict_count"] == JUDGES * PROJECTS


def test_a_zero_variance_judge_is_reported_and_does_not_break_the_maths(client, db, world):
    """`judge_11` in the fixture dataset: one grader who works a single value."""
    pattern = [9, 7, 5, 3]
    for judge in world["judges"][:2]:
        for submission, value in zip(world["submissions"], pattern):
            file_verdict(client, judge, submission.id, technical_score=value)
    flat = world["judges"][2]
    for submission in world["submissions"]:
        file_verdict(client, flat, submission.id, technical_score=8)

    body = leaderboard(client, world["organizer"])

    calibration = {row["id"]: row for row in body["judges"]}[flat.id]
    assert calibration["raw_sigma"] == 0.0
    assert calibration["discriminative"] is False
    assert calibration["verdicts"] == PROJECTS
    for row in body["leaderboard"]:
        assert math.isfinite(row["z_score"]) and math.isfinite(row["axion_score"])
        assert 0.0 <= row["axion_score"] <= 100.0


def test_incomplete_coverage_is_marked_provisional_never_silently_ranked(client, db, world):
    partial, untouched = world["submissions"][0], world["submissions"][1]
    for submission in world["submissions"][2:]:
        for judge in world["judges"]:
            file_verdict(client, judge, submission.id, technical_score=7)
    file_verdict(client, world["judges"][0], partial.id, technical_score=9)

    body = leaderboard(client, world["organizer"])
    rows = {row["submission_id"]: row for row in body["leaderboard"]}

    assert rows[partial.id]["reviews_filed"] == 1
    assert rows[partial.id]["reviews_expected"] == JUDGES
    assert rows[partial.id]["provisional"] is True
    assert partial.id in body["coverage_summary"]["provisional_ids"]
    assert body["coverage_summary"]["provisional"] == 1
    assert body["coverage_summary"]["minimum_judges"] == 3
    assert [row["submission_id"] for row in body["unranked"]] == [untouched.id]
    assert body["unranked"][0]["reason"] == "no technical verdicts filed yet"


def test_extreme_verdicts_survive_the_round_trip_without_drift(client, db, world):
    judge = world["judges"][0]
    submission = world["submissions"][0]

    assert file_verdict(client, judge, submission.id, technical_score=10).status_code == 200
    stored = db.scalars(select(Score.technical_score)).one()
    assert stored == 10 and float(stored).is_integer(), "stored exactly, not 9.999"

    body = leaderboard(client, world["organizer"])
    row = body["leaderboard"][0]
    assert row["raw_average"] == 10.0
    assert row["axion_score"] == round(row["axion_score"], 2)
    assert isinstance(row["reviews_expected"], int) and row["reviews_expected"] == JUDGES
