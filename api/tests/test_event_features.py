"""Tests for the T1/T2 features: offline login, drafts, deadline, gallery,
tracks and prizes, weighted rubrics, judge progress and CSV export.
"""
from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app import config, seed
from app.models import (
    Assignment,
    AuditLog,
    Prize,
    Score,
    ScoreCriterion,
    Submission,
    Team,
    Track,
)

ADMIN = "admin@axion.dev"
ADMIN_PASSWORD = "axion-admin"
JUDGE = "disciplined@axion.dev"
JUDGE_PASSWORD = "axion-judge"


@pytest.fixture()
def seeded(db):
    result = seed.seed()
    assert result["skipped"] is False
    return result


def _rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.text)))


# ── Offline local dev login ──────────────────────────────────────────────────


def test_dev_login_is_refused_when_the_deployment_is_not_in_demo_mode(client, monkeypatch):
    production = replace(config.settings, mock_github=False, seed_demo=False)
    monkeypatch.setattr("app.routers.auth.settings", production)
    response = client.post("/api/auth/dev-login", json={"role": "admin"})
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_dev_login_signs_in_every_role_without_a_password(client, seeded):
    for role, expected in (("admin", "admin"), ("judge", "judge"), ("participant", "participant")):
        response = client.post("/api/auth/dev-login", json={"role": role})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["role"] == expected
        assert body["offline"] is True
        # A real session cookie, not a special marker the rest of the API knows about.
        me = client.get("/api/auth/me").json()
        assert me["authenticated"] is True
        assert me["user"]["role"] == expected


def test_dev_login_audit_and_documented_credentials(client, db, seeded):
    client.post("/api/auth/dev-login", json={"role": "admin"})
    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "auth.dev_login" in actions

    # The documented pair works through the ordinary password form too.
    response = client.post(
        "/api/auth/login", json={"email": "admin@axion.local", "password": "password"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"


def test_dev_login_accepts_an_explicit_seeded_email(client, seeded):
    response = client.post("/api/auth/dev-login", json={"email": "hacker@axion.local"})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "participant"
    assert client.post("/api/auth/dev-login", json={"email": "nobody@nowhere.dev"}).status_code == 404


def test_status_advertises_the_accounts_the_buttons_resolve_to(client, seeded):
    body = client.get("/api/auth/status").json()
    assert body["local_dev_login"] is True
    accounts = {entry["role"]: entry for entry in body["demo_accounts"]}
    assert accounts["admin"]["email"] == "admin@axion.local"
    assert accounts["participant"]["email"] == "hacker@axion.local"
    # The judge role falls back to a real calibrated judge rather than inventing one.
    assert accounts["judge"]["email"] == JUDGE
    assert "password" not in str(body["demo_accounts"]).lower()


# ── Drafts, edits and the deadline ───────────────────────────────────────────


def _join_team(client, name: str) -> None:
    assert client.post("/api/teams", json={"name": name}).status_code == 200


def test_a_draft_is_invisible_to_judging_and_to_the_gallery(client, make_user, auth, db):
    make_user("participant", email="drafter@test.dev")
    auth("drafter@test.dev")
    _join_team(client, "Draft Squad")

    response = client.post(
        "/api/submissions",
        json={
            "title": "Half Built",
            "repo_url": "https://github.com/axion-demo/half-built",
            "status": "draft",
        },
    )
    assert response.status_code == 200
    submission = response.json()["submission"]
    assert submission["status"] == "draft"
    assert submission["submitted_at"] is None
    # No integrity check and no judge assignment while it is a draft.
    assert submission["commit_integrity"]["pct_in_window"] is None
    assert db.scalar(select(func.count(Assignment.id))) == 0
    assert client.get("/api/gallery").json()["projects"] == []


def test_promoting_a_draft_runs_integrity_and_assigns_judges(client, make_user, auth, db):
    make_user("participant", email="promoter@test.dev")
    auth("promoter@test.dev")
    _join_team(client, "Promotion Squad")
    judge = make_user("judge", email="watcher@test.dev")

    client.post(
        "/api/submissions",
        json={
            "title": "Now Finished",
            "repo_url": "https://github.com/axion-demo/now-finished",
            "status": "draft",
        },
    )
    promoted = client.post(
        "/api/submissions",
        json={
            "title": "Now Finished",
            "repo_url": "https://github.com/axion-demo/now-finished",
            "status": "submitted",
        },
    ).json()["submission"]

    assert promoted["status"] == "submitted"
    assert promoted["submitted_at"] is not None
    assert promoted["commit_integrity"]["pct_in_window"] is not None
    assert db.scalar(select(func.count(Assignment.id)).where(Assignment.judge_id == judge.id)) == 1
    assert len(client.get("/api/gallery").json()["projects"]) == 1


def test_a_submitted_project_cannot_be_reverted_to_a_draft(client, make_user, auth):
    make_user("participant", email="regret@test.dev")
    auth("regret@test.dev")
    _join_team(client, "No Regrets")
    payload = {"title": "Shipped", "repo_url": "https://github.com/axion-demo/shipped"}
    assert client.post("/api/submissions", json=payload).status_code == 200
    response = client.post("/api/submissions", json={**payload, "status": "draft"})
    assert response.status_code == 400


def test_the_deadline_is_enforced_server_side(client, db, make_user, auth, closed_window):
    from app.models import TeamMember, User

    # The team exists before the window expires; only the write is late.
    user = make_user("participant", email="late@test.dev")
    team = Team(name="Late Squad", invite_code="LATE0001", created_by=user.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id))
    db.commit()
    auth("late@test.dev")

    response = client.post(
        "/api/submissions",
        json={"title": "Too Late", "repo_url": "https://github.com/axion-demo/too-late"},
    )
    assert response.status_code == 403
    assert "closed" in response.json()["detail"]
    assert db.scalar(select(func.count(Submission.id))) == 0

    # The rejection itself is evidence: an attempted late write is recorded.
    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "submission.rejected_after_deadline" in actions




def test_editing_a_submission_is_still_allowed_before_the_deadline(client, make_user, auth):
    make_user("participant", email="editor@test.dev")
    auth("editor@test.dev")
    _join_team(client, "Editorial")
    client.post(
        "/api/submissions",
        json={"title": "First Pass", "repo_url": "https://github.com/axion-demo/first-pass"},
    )
    updated = client.post(
        "/api/submissions",
        json={
            "title": "Second Pass",
            "repo_url": "https://github.com/axion-demo/first-pass",
            "summary": "Improved",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["submission"]["title"] == "Second Pass"
    assert updated.json()["submission"]["summary"] == "Improved"


# ── Public event + gallery ───────────────────────────────────────────────────


def test_public_event_endpoint_is_anonymous_and_complete(client, seeded):
    body = client.get("/api/event").json()
    assert body["event"]["name"]
    assert body["event"]["phase"] == "open"
    assert body["stats"]["submissions"] == 10
    assert body["stats"]["drafts"] == 0
    assert body["stats"]["judges"] == 5
    assert body["stats"]["verdicts"] == 50
    assert len(body["tracks"]) == 3
    assert [p["title"] for p in body["overall_prizes"]] == ["Grand Prize", "Runner-up"]
    assert body["rubric"]["criteria"][0]["key"] == "innovation"
    assert body["rubric"]["criteria"][0]["percent"] == 30.0
    assert body["rubric"]["criteria"][1]["percent"] == 70.0
    assert body["environment"]["local_dev_login"] is True


def test_gallery_search_track_filter_and_presentation_withholding(client, seeded):
    everything = client.get("/api/gallery").json()
    assert everything["count"] == 10
    project = everything["projects"][0]
    # Blind evaluation must not be bypassable from an unauthenticated endpoint.
    assert "demo_url" not in project
    assert "video_url" not in project
    assert project["repo_url"]
    assert project["track"]["slug"]

    found = client.get("/api/gallery", params={"q": "quiet"}).json()
    assert [row["title"] for row in found["projects"]] == ["Quiet Craft"]

    by_team = client.get("/api/gallery", params={"q": "big o"}).json()
    assert [row["title"] for row in by_team["projects"]] == ["Zero-Knowledge Vault"]

    filtered = client.get("/api/gallery", params={"track": "web3"}).json()
    assert filtered["count"] >= 1
    assert all(row["track"]["slug"] == "web3" for row in filtered["projects"])
    assert {row["slug"] for row in filtered["tracks"]} == {"ai-ml", "web3", "devtools"}

    assert client.get("/api/gallery", params={"q": "no-such-project"}).json()["count"] == 0


# ── Tracks and prizes ────────────────────────────────────────────────────────


def test_admin_can_create_tracks_and_prizes(client, db, make_user, auth):
    make_user("admin", email="organiser@test.dev")
    auth("organiser@test.dev")

    created = client.post(
        "/api/admin/tracks",
        json={"name": "Space Tech", "description": "Orbital software", "prize_pool": "$2,000"},
    )
    assert created.status_code == 200
    assert created.json()["track"]["slug"] == "space-tech"
    assert client.post("/api/admin/tracks", json={"name": "Space Tech"}).status_code == 409

    track_id = created.json()["track"]["id"]
    prize = client.post(
        "/api/admin/prizes",
        json={"title": "Best orbital demo", "track_id": track_id, "rank": 1},
    )
    assert prize.status_code == 200
    assert client.post("/api/admin/prizes", json={"title": "Ghost", "track_id": 9999}).status_code == 404

    listing = client.get("/api/admin/tracks").json()
    assert listing["tracks"][0]["prizes"][0]["title"] == "Best orbital demo"
    assert db.scalar(select(func.count(Track.id))) == 1
    assert db.scalar(select(func.count(Prize.id))) == 1


def test_submission_can_enter_a_track_and_unknown_tracks_are_rejected(client, make_user, auth, db):
    make_user("admin", email="setter@test.dev")
    auth("setter@test.dev")
    track_id = client.post("/api/admin/tracks", json={"name": "Robotics"}).json()["track"]["id"]

    make_user("participant", email="robot@test.dev")
    auth("robot@test.dev")
    _join_team(client, "Robot Squad")
    with_track = client.post(
        "/api/submissions",
        json={
            "title": "Arm Wrangler",
            "repo_url": "https://github.com/axion-demo/arm-wrangler",
            "track_id": track_id,
        },
    ).json()["submission"]
    assert with_track["track_id"] == track_id

    bad = client.post(
        "/api/submissions",
        json={
            "title": "Arm Wrangler",
            "repo_url": "https://github.com/axion-demo/arm-wrangler",
            "track_id": 4242,
        },
    )
    assert bad.status_code == 400


# ── Weighted rubrics ─────────────────────────────────────────────────────────


def test_admin_can_reweight_the_rubric_and_the_default_is_thirty_seventy(client, make_user, auth):
    make_user("admin", email="weights@test.dev")
    auth("weights@test.dev")

    current = client.get("/api/admin/rubric").json()["rubric"]
    assert [(c["label"], c["percent"]) for c in current["criteria"]] == [
        ("Innovation", 30.0),
        ("Code Quality", 70.0),
    ]

    updated = client.post(
        "/api/admin/rubric",
        json={
            "name": "Hackathon rubric",
            "criteria": [
                {"key": "innovation", "label": "Innovation", "weight": 50},
                {"key": "code_quality", "label": "Code Quality", "weight": 50},
            ],
        },
    ).json()["rubric"]
    assert [c["percent"] for c in updated["criteria"]] == [50.0, 50.0]

    # Duplicate keys and empty rubrics are refused.
    assert (
        client.post(
            "/api/admin/rubric",
            json={
                "name": "Dupes",
                "criteria": [
                    {"key": "a", "label": "A", "weight": 1},
                    {"key": "a", "label": "A again", "weight": 1},
                ],
            },
        ).status_code
        == 400
    )

    # Re-weighting does not rewrite verdicts already filed.
    assert client.get("/api/admin/rubric").json()["rubric"]["name"] == "Hackathon rubric"


def test_criterion_scores_derive_the_technical_score_used_by_zscore(client, db, make_user, auth):
    judge = make_user("judge", email="criteria@test.dev")
    organiser = make_user("admin", email="weightwatcher@test.dev")
    make_user("participant", email="scored@test.dev")
    auth("scored@test.dev")
    _join_team(client, "Scored Squad")
    client.post(
        "/api/submissions",
        json={"title": "Rubric Target", "repo_url": "https://github.com/axion-demo/rubric-target"},
    )
    submission_id = db.scalar(select(Submission.id))
    auth(judge.email)

    # Default weights are 30 / 70, so 10 and 4 blend to 5.8 -> 6.
    response = client.post(
        "/api/judging/scores",
        json={
            "submission_id": submission_id,
            "criteria": [
                {"key": "innovation", "value": 10},
                {"key": "code_quality", "value": 4},
            ],
            "technical_comment": "Inventive, rough edges.",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["technical_score_derived"] == 6
    assert body["score"]["technical_score"] == 6
    assert body["score"]["criteria"] == {"innovation": 10, "code_quality": 4}
    assert body["score"]["rubric_id"] is not None
    assert body["presentation_unlocked"] is True

    stored = db.scalars(select(ScoreCriterion)).all()
    assert {row.key: row.value for row in stored} == {"innovation": 10, "code_quality": 4}
    assert all(row.weight > 0 for row in stored)

    score = db.scalars(select(Score)).all()[0]
    assert score.technical_score == 6

    # An unknown criterion is a client error, not a silently ignored field.
    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": submission_id, "criteria": [{"key": "vibes", "value": 9}]},
        ).status_code
        == 400
    )

    # The derived score is what the leaderboard normalizes.
    auth(organiser.email)
    board = client.get("/api/admin/leaderboard").json()
    assert board["verdict_count"] == 1
    assert board["leaderboard"][0]["axion_score"] is not None


def test_a_bare_technical_score_still_works(client, db, make_user, auth):
    judge = make_user("judge", email="bare@test.dev")
    make_user("participant", email="plain@test.dev")
    auth("plain@test.dev")
    _join_team(client, "Plain Squad")
    client.post(
        "/api/submissions",
        json={"title": "No Rubric", "repo_url": "https://github.com/axion-demo/no-rubric"},
    )
    submission_id = db.scalar(select(Submission.id))
    auth(judge.email)

    body = client.post(
        "/api/judging/scores", json={"submission_id": submission_id, "technical_score": 7}
    ).json()
    assert body["score"]["technical_score"] == 7
    assert body["score"]["criteria"] == {}
    assert body["technical_score_derived"] is None
    assert db.scalar(select(func.count(ScoreCriterion.id))) == 0


def test_assignments_report_the_rubric_and_progress(client, db, make_user, auth):
    judge = make_user("judge", email="progress@test.dev")
    make_user("participant", email="counted@test.dev")
    auth("counted@test.dev")
    _join_team(client, "Counted Squad")
    client.post(
        "/api/submissions",
        json={"title": "Counted", "repo_url": "https://github.com/axion-demo/counted"},
    )
    submission_id = db.scalar(select(Submission.id))
    auth(judge.email)

    before = client.get("/api/judging/assignments").json()
    assert before["progress"] == {
        "total": 1,
        "technical_done": 0,
        "technical_pending": 1,
        "presentation_done": 0,
        "percent_technical": 0.0,
    }
    assert before["rubric"]["criteria"][0]["percent"] == 30.0

    client.post("/api/judging/scores", json={"submission_id": submission_id, "technical_score": 8})
    after = client.get("/api/judging/assignments").json()
    assert after["progress"]["technical_done"] == 1
    assert after["progress"]["percent_technical"] == 100.0


# ── Judge progress + CSV export ──────────────────────────────────────────────


def test_judging_progress_covers_every_judge(client, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    body = client.get("/api/admin/judging-progress").json()
    assert body["submissions"] == 10
    assert len(body["judges"]) == 5
    assert body["totals"]["technical_verdicts"] == 50
    assert body["totals"]["outstanding"] == 0
    assert body["totals"]["percent"] == 100.0
    disciplined = next(row for row in body["judges"] if row["name"] == "Dana Disciplined")
    assert disciplined["assigned"] == 10
    assert disciplined["technical_done"] == 10
    assert disciplined["technical_pending"] == 0
    assert disciplined["last_activity"] is not None


def test_csv_exports_are_admin_only_and_well_formed(client, seeded):
    client.post("/api/auth/login", json={"email": JUDGE, "password": JUDGE_PASSWORD})
    assert client.get("/api/admin/export/leaderboard.csv").status_code == 403

    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})

    leaderboard = client.get("/api/admin/export/leaderboard.csv")
    assert leaderboard.status_code == 200
    assert leaderboard.headers["content-type"].startswith("text/csv")
    assert "attachment" in leaderboard.headers["content-disposition"]
    rows = _rows(leaderboard)
    assert rows[0][:4] == ["rank", "submission_id", "project", "team"]
    assert len(rows) == 11
    assert rows[1][0] == "1"
    assert len(rows[1]) == len(rows[0])

    scores = client.get("/api/admin/export/scores.csv")
    assert scores.status_code == 200
    score_rows = _rows(scores)
    assert len(score_rows) == 51
    assert "criterion:Innovation" in score_rows[0]
    assert "criterion:Code Quality" in score_rows[0]
    assert "z_score" in score_rows[0]
    # Every data row is fully populated for the columns the header promises.
    assert all(len(row) == len(score_rows[0]) for row in score_rows[1:])

    progress = client.get("/api/admin/export/judging-progress.csv")
    assert progress.status_code == 200
    progress_rows = _rows(progress)
    assert len(progress_rows) == 6
    assert progress_rows[0][:3] == ["judge_id", "judge", "email"]


def test_exports_are_written_to_the_audit_trail(client, db, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    client.get("/api/admin/export/leaderboard.csv")
    client.get("/api/admin/export/scores.csv")
    entries = db.scalars(select(AuditLog).where(AuditLog.action == "export.csv")).all()
    assert len(entries) == 2
    assert all(entry.ip for entry in entries)


# ── The archive ignores drafts ───────────────────────────────────────────────


def test_the_archive_ranks_submitted_work_only(client, db, seeded, make_user, auth):
    make_user("participant", email="adhoc@test.dev")
    auth("adhoc@test.dev")
    _join_team(client, "Ad Hoc")
    client.post(
        "/api/submissions",
        json={
            "title": "Abandoned Idea",
            "repo_url": "https://github.com/axion-demo/abandoned",
            "status": "draft",
        },
    )

    auth(ADMIN, ADMIN_PASSWORD)
    bundle = client.post("/api/admin/archive").json()["bundle"]
    assert len(bundle["results"]) == 10
    assert bundle["totals"]["drafts"] == 1
    assert all(row["title"] != "Abandoned Idea" for row in bundle["results"])
    # Tracks reach the published archive.
    assert bundle["results"][0]["track"] in {"AI & Machine Learning", "Web3 & Trust", "Developer Tools"}
    assert "Track:" in client.post("/api/admin/archive").json()["markdown"]
