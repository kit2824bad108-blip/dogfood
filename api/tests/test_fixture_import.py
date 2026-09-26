"""Fixture import: the messy cases, and the promise that nothing is half-written.

The pure tests run against the real committed `fixtures.json`, so the dataset the
README talks about is the dataset under test. The behavioural tests use a small
inline fixture, because what matters is the *rules* — a missing score is not a
zero, a dry run writes nothing, an invalid record refuses the whole import —
rather than volume.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app import fixtures, zscore
from app.assignment import apply_plan, coverage_snapshot, plan_balanced_assignment
from app.models import ImportBatch, Score, Submission, User
from app.timeutil import iso


# ── the committed dataset, diagnosed ─────────────────────────────────────────


def test_the_committed_fixture_is_structurally_valid():
    payload = fixtures.load_fixture()
    assert fixtures.validate(payload) == []
    assert len(payload["projects"]) == 40
    assert len(payload["judges"]) == 12
    assert len(payload["teams"]) == len(payload["projects"]), "one team per project"


def test_diagnostics_find_every_edge_case_the_dataset_was_built_for():
    diagnostics = fixtures.diagnose(fixtures.load_fixture())

    assert diagnostics["invalid"] == []
    assert diagnostics["zero_variance_judges"] == ["judge_11"]
    assert diagnostics["single_verdict_judges"] == ["judge_12"]
    assert diagnostics["duplicates_detected"] == 2
    assert diagnostics["duplicates_detected"] == diagnostics["duplicates_declared_in_file"]
    assert sorted(
        (row["submission"], row["duplicate_of"]) for row in diagnostics["duplicate_candidates"]
    ) == [("proj_021", "proj_017"), ("proj_031", "proj_007")]
    assert diagnostics["projects_without_reviews"] == ["proj_018"]
    assert diagnostics["assignments_without_scores"] == 7
    assert diagnostics["null_technical_comments"] > 0
    assert diagnostics["null_summaries"] > 0
    assert diagnostics["string_ids_preserved"] > 0
    assert diagnostics["timestamps_normalised_to_utc"] is True
    assert diagnostics["event_window"]["closed"] is True
    assert diagnostics["minimum_coverage"] == 3

    headline = "\n".join(diagnostics["headline"])
    for expected in (
        "40 projects imported",
        "12 judges imported",
        "incomplete review batches detected",
        "2 duplicate candidates detected",
        "1 zero-variance judge detected",
        "0 invalid records",
    ):
        assert expected in headline, expected


def test_validation_reports_what_is_wrong_rather_than_guessing():
    payload = fixtures.load_fixture()
    payload = {**payload}
    payload["projects"] = [
        *payload["projects"],
        {
            "id": "proj_017",  # duplicate id
            "team_id": "team_missing",
            "track_id": "track_missing",
            "title": "",
            "repo_url": "",
            "submitted_at": "not-a-date",
            "assigned_judges": ["judge_missing"],
            "declared_duplicate_of": "proj_absent",
        },
    ]
    payload["judges"] = [*payload["judges"], {"id": "judge_99", "name": "No Email"}]

    kinds = {problem["kind"] for problem in fixtures.validate(payload)}
    assert {
        "duplicate_id",
        "missing_title",
        "missing_repo_url",
        "unknown_team",
        "unknown_track",
        "bad_timestamp",
        "unknown_judge",
        "unknown_duplicate",
        "missing_email",
    } <= kinds


# ── duplicate detection ──────────────────────────────────────────────────────


def test_duplicate_detection_normalises_but_does_not_over_reach():
    projects = [
        {"id": "31", "title": "Mesh Relay", "repo_url": "https://github.com/Orbit-Works/Mesh-Relay/"},
        {"id": "7", "title": "Mesh Relay - resilient chat", "repo_url": "https://github.com/orbit-works/mesh-relay"},
        {"id": "25", "title": "Mesh Scheduler", "repo_url": "https://github.com/fixture-labs/mesh-scheduler"},
    ]
    candidates = fixtures.duplicate_candidates(projects)

    # "31" < "7" as strings; sorting ids naturally means project 7 is canonical.
    assert candidates == [
        {
            "submission": "31",
            "duplicate_of": "7",
            "reason": "same repository after normalisation (github.com/orbit-works/mesh-relay)",
        }
    ]
    # The near-miss shares a word with the others and must not be flagged.
    assert all(candidate["submission"] != "25" for candidate in candidates)


def test_url_normalisation_ignores_scheme_case_suffix_and_slash():
    variants = [
        "https://github.com/owner/repo",
        "http://GitHub.com/Owner/Repo/",
        "git://github.com/owner/repo.git",
        "  github.com/owner/repo  ",
    ]
    keys = {fixtures.normalize_repo_url(value) for value in variants}
    assert keys == {"github.com/owner/repo"}


def test_title_fingerprints_drop_bracketed_suffixes_only():
    assert fixtures.title_fingerprint("Nimbus Mesh (final v2)") == fixtures.title_fingerprint(
        "nimbus mesh"
    )
    assert fixtures.title_fingerprint("Mesh Scheduler") != fixtures.title_fingerprint("Mesh Relay")


# ── behaviour, on a small fixture ────────────────────────────────────────────


def mini_fixture() -> dict:
    """Two projects: one with two verdicts (one of them comment-less), one with
    an assignment and no verdict at all."""
    return {
        "fixture_version": 1,
        "generator": "tests/mini",
        "minimum_coverage": 2,
        "accounts": {
            "admin": {"email": "organiser@mini.test", "password": "mini-admin"},
            "judge": {"password": "mini-judge"},
            "participant": {"password": "mini-hacker"},
        },
        "event": {
            "starts_at": "2020-01-01T00:00:00+00:00",
            "ends_at": "2020-01-02T00:00:00+00:00",
        },
        "rubric": {
            "name": "Mini rubric",
            "criteria": [
                {"key": "innovation", "label": "Innovation", "weight": 50.0},
                {"key": "code_quality", "label": "Code Quality", "weight": 50.0},
            ],
        },
        "tracks": [{"id": "track_a", "name": "Track A", "slug": "track-a", "prizes": []}],
        "judges": [
            {"id": "judge_a", "email": "judge.a@mini.test", "name": "Judge A"},
            {"id": "judge_b", "email": "judge.b@mini.test", "name": "Judge B"},
        ],
        "teams": [
            {"id": "team_1", "name": "Team One"},
            {"id": "team_2", "name": "Team Two"},
        ],
        "participants": [
            {"id": "user_1", "email": "p1@mini.test", "name": "P One", "team_id": "team_1"},
            {"id": "user_2", "email": "p2@mini.test", "name": "P Two", "team_id": "team_2"},
        ],
        "projects": [
            {
                "id": "proj_1",
                "team_id": "team_1",
                "title": "Alpha Mesh",
                "repo_url": "https://github.com/mini/alpha-mesh",
                "track_id": "track_a",
                "status": "submitted",
                "submitted_at": "2020-01-01T10:00:00+00:00",
                "assigned_judges": ["judge_a", "judge_b"],
            },
            {
                "id": "proj_2",
                "team_id": "team_2",
                "title": "Beta Mesh",
                "repo_url": "https://github.com/mini/beta-mesh/",
                "track_id": "track_a",
                "status": "submitted",
                "submitted_at": "2020-01-01T11:00:00+00:00",
                "assigned_judges": ["judge_a"],
            },
        ],
        "reviews": [
            {
                "judge_id": "judge_a",
                "project_id": "proj_1",
                "technical": 7,
                "technical_comment": None,
                "presentation": 8,
                "submitted_at": "2020-01-01T12:00:00+00:00",
            },
            {
                "judge_id": "judge_b",
                "project_id": "proj_1",
                "technical": 5,
                "technical_comment": "Fine.",
                "presentation": None,
                "submitted_at": "2020-01-01T12:30:00+00:00",
            },
        ],
    }


def _counts(db) -> dict[str, int]:
    return {
        "users": db.scalar(select(func.count(User.id))) or 0,
        "submissions": db.scalar(select(func.count(Submission.id))) or 0,
        "scores": db.scalar(select(func.count(Score.id))) or 0,
        "batches": db.scalar(select(func.count(ImportBatch.id))) or 0,
    }


def test_a_dry_run_reports_and_writes_nothing(db):
    summary = fixtures.apply_fixture(db, mini_fixture(), mode="dry_run")

    assert summary["mode"] == "dry_run"
    assert summary["applied"] is None
    assert summary["records"]["projects"] == 2
    assert _counts(db) == {"users": 0, "submissions": 0, "scores": 0, "batches": 1}


def test_an_apply_writes_the_dataset_and_keeps_source_ids(db):
    summary = fixtures.apply_fixture(db, mini_fixture(), mode="apply")

    assert summary["mode"] == "apply"
    assert summary["applied"]["projects_created"] == 2
    assert summary["applied"]["scores_created"] == 2
    db.commit()

    submission = db.scalar(select(Submission).where(Submission.source_ref == "proj_1"))
    assert submission is not None and submission.title == "Alpha Mesh"
    # The stored instant is the UTC one the fixture declared. SQLite hands the
    # datetime back naive, which is why every serializer goes through `iso()`.
    assert submission.submitted_at == datetime(2020, 1, 1, 10, 0)
    assert iso(submission.submitted_at) == "2020-01-01T10:00:00+00:00"
    judge = db.scalar(select(User).where(User.email == "judge.a@mini.test"))
    assert judge is not None and judge.source_ref == "judge_a"


def test_a_missing_verdict_is_missing_and_never_a_zero(db):
    fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()

    # `minimum` is the dataset's own coverage target (2), not the product default
    # of 3 — the fixture declares what "enough verdicts" means for it.
    snapshot = coverage_snapshot(db, expected=2, minimum=2)
    by_id = {row["title"]: row for row in snapshot["submissions"]}

    alpha = by_id["Alpha Mesh"]
    assert (alpha["reviews"], alpha["assigned"]) == (2, 2)
    assert alpha["provisional"] is False

    beta = by_id["Beta Mesh"]
    assert (beta["reviews"], beta["assigned"]) == (0, 1)
    assert beta["provisional"] is True and beta["scoreable"] is False

    # And the leaderboard ranks only the project that actually has verdicts:
    # "no verdicts" is not "a verdict of zero".
    rows = db.execute(
        select(Score.judge_id, Score.submission_id, Score.technical_score).where(
            Score.technical_score.isnot(None)
        )
    ).all()
    records = [zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t)) for j, s, t in rows]
    assert [result.submission_id for result in zscore.leaderboard(records)] == [
        alpha["submission_id"]
    ]


def test_null_comments_survive_the_import(db):
    fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()

    rows = db.execute(
        select(Score.technical_score, Score.technical_comment)
        .where(Score.technical_score.isnot(None))
        .order_by(Score.technical_score)
    ).all()
    assert dict(rows) == {5: "Fine.", 7: None}


def test_re_running_an_import_updates_rather_than_duplicating(db):
    fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()
    before = _counts(db)

    summary = fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()

    assert summary["applied"]["projects_created"] == 0
    assert summary["applied"]["projects_updated"] == 2
    after = _counts(db)
    assert after["users"] == before["users"]
    assert after["submissions"] == before["submissions"]
    assert after["scores"] == before["scores"]


def test_an_invalid_record_refuses_the_whole_import(db):
    broken = mini_fixture()
    broken["projects"] = [
        {**broken["projects"][0], "team_id": "team_nowhere"},
        broken["projects"][1],
    ]
    summary = fixtures.apply_fixture(db, broken, mode="apply")

    assert summary["applied"] is None
    assert summary["refused"] == "records failed validation; nothing was written"
    assert [problem["kind"] for problem in summary["invalid"]] == ["unknown_team"]
    assert _counts(db) == {"users": 0, "submissions": 0, "scores": 0, "batches": 1}


# ── balanced assignment ──────────────────────────────────────────────────────


def test_balance_plan_reaches_the_target_without_touching_the_database(db):
    fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()

    plan = plan_balanced_assignment(db, reviews_per_project=2, max_projects_per_judge=2)

    assert plan["projected"]["coverage_percent"] == 100.0
    assert plan["projected"]["components_after"] == 1
    assert plan["projected"]["new_assignments"] == 1
    assert len(plan["plan"]) == 1
    assert plan["plan"][0]["title"] == "Beta Mesh"
    # judge_a is already at the cap, so the only eligible judge is judge_b.
    judge_b = db.scalar(select(User).where(User.email == "judge.b@mini.test"))
    assert plan["plan"][0]["add_judges"] == [judge_b.id]

    # A plan is only a plan: the database is untouched until it is applied.
    assert coverage_snapshot(db, expected=2, minimum=2)["totals"]["provisional"] == 1


def test_applying_a_plan_is_idempotent(db):
    fixtures.apply_fixture(db, mini_fixture(), mode="apply")
    db.commit()
    plan = plan_balanced_assignment(db, reviews_per_project=2)

    created = apply_plan(db, plan["plan"])
    db.commit()
    assert created == 1
    assert apply_plan(db, plan["plan"]) == 0

    snapshot = coverage_snapshot(db, expected=2, minimum=2)
    beta = next(row for row in snapshot["submissions"] if row["title"] == "Beta Mesh")
    # Assigning a judge is not the same as receiving a verdict. The assignment now
    # exists, the score is still missing, and the project stays provisional — which
    # is exactly the distinction between coverage of assignments and of verdicts.
    assert beta["assigned"] == 2
    assert beta["reviews"] == 0
    assert beta["provisional"] is True


def test_coverage_report_includes_projects_with_no_verdicts_all():
    report = zscore.coverage_report([], expected_by_submission={9: 4})
    assert report[9].judges == 0
    assert report[9].missing == 4
    assert report[9].provisional is True
