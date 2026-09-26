"""Server-side authorization, role by role.

The frontend hides what a role cannot do; these tests call the API directly, the
way a curious user with devtools would, and require the server to say no. Every
assertion here is about the HTTP boundary, never about a hidden button.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Assignment, Score, Submission, Team, TeamMember


@pytest.fixture()
def arena(db, make_user):
    """Two teams, two judges, and one project both judges are assigned to.

    Deliberately asymmetric: judge A and judge B share Alpha, and *neither* is
    assigned to Beta, so "unassigned" and "the other judge's verdict" are both
    reachable in the same scenario.
    """
    organizer = make_user("admin", email="organizer@authz.test")
    judge_a = make_user("judge", email="judge.a@authz.test")
    judge_b = make_user("judge", email="judge.b@authz.test")
    participant_a = make_user("participant", email="alice@authz.test")
    participant_b = make_user("participant", email="bob@authz.test")

    team_a = Team(name="Team Alpha", invite_code="ALPHA001", created_by=participant_a.id)
    team_b = Team(name="Team Beta", invite_code="BETA0001", created_by=participant_b.id)
    db.add_all([team_a, team_b])
    db.flush()
    db.add_all(
        [
            TeamMember(team_id=team_a.id, user_id=participant_a.id),
            TeamMember(team_id=team_b.id, user_id=participant_b.id),
        ]
    )
    submission_a = Submission(
        team_id=team_a.id,
        title="Alpha Mesh",
        repo_url="https://github.com/authz-test/alpha-mesh",
        summary="Alpha's private summary",
        status="submitted",
    )
    submission_b = Submission(
        team_id=team_b.id,
        title="Beta Mesh",
        repo_url="https://github.com/authz-test/beta-mesh",
        summary="Beta's private summary",
        status="submitted",
    )
    db.add_all([submission_a, submission_b])
    db.flush()
    db.add_all(
        [
            Assignment(judge_id=judge_a.id, submission_id=submission_a.id),
            Assignment(judge_id=judge_b.id, submission_id=submission_a.id),
        ]
    )
    db.commit()
    return {
        "organizer": organizer,
        "judge_a": judge_a,
        "judge_b": judge_b,
        "participant_a": participant_a,
        "participant_b": participant_b,
        "submission_a": submission_a,
        "submission_b": submission_b,
    }


# ── organizer ────────────────────────────────────────────────────────────────


def test_organizer_reaches_the_management_and_export_surface(client, auth, arena):
    auth("organizer@authz.test")
    for path in (
        "/api/admin/overview",
        "/api/admin/leaderboard",
        "/api/admin/judging-progress",
        "/api/admin/audit",
        "/api/admin/import/diagnostics",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
    for path in (
        "/api/admin/export/leaderboard.csv",
        "/api/admin/export/scores.csv",
        "/api/admin/export/judging-progress.csv",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert "text/csv" in response.headers.get("content-type", "")


# ── judge A ──────────────────────────────────────────────────────────────────


def test_judge_a_works_their_assignment(client, auth, arena):
    auth("judge.a@authz.test")
    assignments = client.get("/api/judging/assignments")
    assert assignments.status_code == 200
    assert [row["submission_id"] for row in assignments.json()["assignments"]] == [
        arena["submission_a"].id
    ]

    detail = client.get(f"/api/judging/submissions/{arena['submission_a'].id}")
    assert detail.status_code == 200

    scored = client.post(
        "/api/judging/scores",
        json={
            "submission_id": arena["submission_a"].id,
            "technical_score": 7,
            "technical_comment": "A files a verdict",
        },
    )
    assert scored.status_code == 200
    assert scored.json()["score"]["technical_score"] == 7


def test_judge_a_cannot_read_judge_b_s_verdict(client, auth, arena):
    auth("judge.b@authz.test")
    filed = client.post(
        "/api/judging/scores",
        json={
            "submission_id": arena["submission_a"].id,
            "technical_score": 4,
            "technical_comment": "B-only-note",
        },
    )
    assert filed.status_code == 200

    auth("judge.a@authz.test")
    detail = client.get(f"/api/judging/submissions/{arena['submission_a'].id}")
    assert detail.status_code == 200
    assert "B-only-note" not in detail.text
    assert detail.json()["submission"]["score"] is None

    assignments = client.get("/api/judging/assignments")
    assert "B-only-note" not in assignments.text
    assert assignments.json()["assignments"][0]["technical_score"] is None


def test_judge_a_cannot_reach_an_unassigned_project(client, auth, arena):
    auth("judge.a@authz.test")
    assert (
        client.get(f"/api/judging/submissions/{arena['submission_b'].id}").status_code == 403
    )
    assert (
        client.get(
            f"/api/judging/submissions/{arena['submission_b'].id}/presentation"
        ).status_code
        == 403
    )
    response = client.post(
        "/api/judging/scores",
        json={"submission_id": arena["submission_b"].id, "technical_score": 9},
    )
    assert response.status_code == 403


def test_a_posted_judge_id_cannot_impersonate_another_judge(client, auth, db, arena):
    auth("judge.a@authz.test")
    response = client.post(
        "/api/judging/scores",
        json={
            "submission_id": arena["submission_a"].id,
            # The body names judge B; the server must write the session's row.
            "judge_id": arena["judge_b"].id,
            "technical_score": 6,
        },
    )
    assert response.status_code == 200
    rows = db.execute(
        select(Score.judge_id, Score.technical_score).where(
            Score.submission_id == arena["submission_a"].id
        )
    ).all()
    assert rows == [(arena["judge_a"].id, 6)]


def test_judges_cannot_reach_the_organizer_surface(client, auth, arena):
    for email in ("judge.a@authz.test", "judge.b@authz.test"):
        auth(email)
        assert client.get("/api/admin/overview").status_code == 403
        assert client.get("/api/admin/leaderboard").status_code == 403
        assert client.get("/api/admin/export/scores.csv").status_code == 403
        assert client.get("/api/admin/import/diagnostics").status_code == 403


# ── judge B ──────────────────────────────────────────────────────────────────


def test_judge_b_has_the_same_isolation_rules(client, auth, arena):
    auth("judge.a@authz.test")
    assert (
        client.post(
            "/api/judging/scores",
            json={
                "submission_id": arena["submission_a"].id,
                "technical_score": 8,
                "technical_comment": "A-only-note",
            },
        ).status_code
        == 200
    )

    auth("judge.b@authz.test")
    detail = client.get(f"/api/judging/submissions/{arena['submission_a'].id}")
    assert detail.status_code == 200
    assert "A-only-note" not in detail.text
    assert detail.json()["submission"]["score"] is None

    assert (
        client.get(f"/api/judging/submissions/{arena['submission_b'].id}").status_code == 403
    )
    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": arena["submission_b"].id, "technical_score": 5},
        ).status_code
        == 403
    )
    assert client.get("/api/admin/leaderboard").status_code == 403
    assert client.get("/api/judging/assignments").status_code == 200


# ── participant ──────────────────────────────────────────────────────────────


def test_participant_reaches_only_their_own_team_and_submission(client, auth, arena):
    auth("alice@authz.test")
    team = client.get("/api/teams/me")
    assert team.status_code == 200
    assert team.json()["team"]["name"] == "Team Alpha"

    mine = client.get("/api/submissions/me")
    assert mine.status_code == 200
    assert mine.json()["submission"]["id"] == arena["submission_a"].id
    assert "Beta's private summary" not in mine.text


def test_participant_cannot_touch_another_team_s_submission(client, auth, db, arena):
    auth("alice@authz.test")
    response = client.post(
        "/api/submissions",
        json={
            "title": "Hijack attempt",
            "repo_url": "https://github.com/authz-test/alpha-mesh",
            "status": "submitted",
        },
    )
    assert response.status_code == 200

    # The write landed on Alice's own team; Beta's row is untouched.
    alpha = db.get(Submission, arena["submission_a"].id)
    beta = db.get(Submission, arena["submission_b"].id)
    db.refresh(alpha)
    db.refresh(beta)
    assert alpha.title == "Hijack attempt"
    assert beta.title == "Beta Mesh"
    assert beta.summary == "Beta's private summary"


def test_participant_cannot_reach_judging_or_admin(client, auth, arena):
    auth("alice@authz.test")
    assert client.get("/api/judging/assignments").status_code == 403
    assert (
        client.get(f"/api/judging/submissions/{arena['submission_a'].id}").status_code
        == 403
    )
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/api/admin/leaderboard").status_code == 403
    assert client.get("/api/admin/export/leaderboard.csv").status_code == 403
    assert client.get("/api/submissions").status_code == 403
