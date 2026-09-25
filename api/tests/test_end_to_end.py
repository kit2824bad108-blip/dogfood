"""End-to-end smoke test: the walkthrough a judge would actually perform.

Seeds the real demo dataset, then drives the live API — login, blind judgement,
normalized leaderboard, integrity queue and archive — against a real database.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import seed
from app.models import AuditLog, Score, Submission

ADMIN = "admin@axion.dev"
ADMIN_PASSWORD = "axion-admin"
JUDGE = "disciplined@axion.dev"
JUDGE_PASSWORD = "axion-judge"


@pytest.fixture()
def seeded(db):
    result = seed.seed()
    assert result["skipped"] is False
    return result


def by_title(rows: list[dict], title: str) -> dict:
    for row in rows:
        if row["title"] == title:
            return row
    raise AssertionError(f"{title} missing from leaderboard: {[r['title'] for r in rows]}")


def test_seed_populates_a_full_event(seeded):
    assert seeded["teams"] == 10
    assert seeded["participants"] == 50
    assert seeded["submissions"] == 10
    assert seeded["verdicts"] == 50


def test_seed_is_idempotent():
    seed.seed()
    assert seed.seed()["skipped"] is True


def test_every_project_has_a_verdict_from_every_judge(db, seeded):
    for submission in db.scalars(select(Submission)).all():
        count = db.scalar(
            select(func.count(Score.id)).where(
                Score.submission_id == submission.id, Score.technical_score.isnot(None)
            )
        )
        assert count == 5, "full coverage is what makes per-judge z-scores comparable"


def test_leaderboard_flips_the_crafted_pair(client, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    body = client.get("/api/admin/leaderboard").json()
    rows = body["leaderboard"]

    quiet = by_title(rows, "Quiet Craft")
    flashy = by_title(rows, "Flashy Demo")

    # The naive average prefers the demo...
    assert flashy["raw_average"] > quiet["raw_average"]
    assert flashy["raw_rank"] < quiet["raw_rank"]
    # ...the Axion score prefers the craft.
    assert quiet["axion_rank"] < flashy["axion_rank"]
    assert quiet["axion_score"] > flashy["axion_score"]
    assert quiet["rank_movement"] > 0
    assert flashy["rank_movement"] < 0


def test_leaderboard_reports_grader_calibration(client, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    body = client.get("/api/admin/leaderboard").json()

    judges = {judge["name"]: judge for judge in body["judges"]}
    assert len(judges) == 5
    disciplined = judges["Dana Disciplined"]
    expansive = judges["Enzo Expansive"]
    assert disciplined["verdicts"] == 10
    # The narrow-band grader is the more informative one.
    assert disciplined["raw_sigma"] < expansive["raw_sigma"]
    assert body["methodology"]["prior_strength"] > 0
    assert body["coverage_warnings"] == {}
    assert body["verdict_count"] == 50


def test_seeded_preexisting_repository_is_in_the_review_queue(client, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    flagged = client.get("/api/admin/flagged").json()["flagged"]
    assert flagged, "the seed should produce at least one flagged submission"
    assert any(row["commit_integrity"]["flagged"] for row in flagged)
    assert all(row["commit_integrity"]["pct_in_window"] < 50 for row in flagged)


def test_blind_judging_round_trip(client, db, seeded):
    # Seeded judges already have verdicts, so the lock is already open for them.
    # Create a fresh judge instead — the path an organiser would actually take.
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    created = client.post(
        "/api/admin/judges",
        json={"email": "fresh.judge@axion.dev", "password": "judgepassword", "name": "Fresh Judge"},
    )
    assert created.status_code == 200
    assert created.json()["assigned"] == 10

    client.post(
        "/api/auth/login", json={"email": "fresh.judge@axion.dev", "password": "judgepassword"}
    )

    assignments = client.get("/api/judging/assignments").json()
    assert assignments["progress"]["total"] == 10
    assert assignments["progress"]["technical_done"] == 0
    target = assignments["assignments"][0]["submission_id"]

    # The presentation tier is gated by the API, not the interface.
    assert client.get(f"/api/judging/submissions/{target}/presentation").status_code == 403

    verdict = client.post(
        "/api/judging/scores",
        json={"submission_id": target, "technical_score": 9, "technical_comment": "Read the code first."},
    )
    assert verdict.status_code == 200
    assert verdict.json()["presentation_unlocked"] is True

    unlocked = client.get(f"/api/judging/submissions/{target}/presentation")
    assert unlocked.status_code == 200
    assert unlocked.json()["presentation"]["demo_url"]

    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": target, "presentation_score": 7},
        ).status_code
        == 200
    )

    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "score.technical_submitted" in actions
    assert "score.presentation_submitted" in actions


def test_every_seeded_score_is_audited(db, seeded):
    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "seed.run" in actions
    assert db.scalar(select(func.count(Score.id))) == 50


def test_archive_bundle_is_publishable(client, seeded):
    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    body = client.post("/api/admin/archive").json()

    bundle = body["bundle"]
    assert bundle["event"]["name"]
    assert len(bundle["results"]) == 10
    assert bundle["totals"]["judge_verdicts"] == 50
    assert "technical verdicts only" in bundle["methodology"]["ranking_basis"]
    assert bundle["results"][0]["axion_rank"] == 1
    assert len(bundle["verdicts"]) == 50

    markdown = body["markdown"]
    assert bundle["event"]["name"] in markdown
    assert "| Rank | Project |" in markdown
    assert "Quiet Craft" in markdown
    assert "Methodology" in markdown


def test_admin_console_and_role_boundaries(client, seeded):
    client.post("/api/auth/login", json={"email": JUDGE, "password": JUDGE_PASSWORD})
    assert client.get("/api/admin/overview").status_code == 403

    client.post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PASSWORD})
    overview = client.get("/api/admin/overview").json()
    assert overview["totals"]["teams"] == 10
    assert overview["totals"]["judges"] == 5
    assert overview["totals"]["technical_scores"] == 50
    assert overview["totals"]["flagged_for_review"] >= 1
