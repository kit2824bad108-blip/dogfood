"""Authorization on PostgreSQL, exercised at the API boundary.

Every request here carries a real credential — the deterministic header token the
official checker uses (`app.devtokens.stable_token`) — and no frontend is
involved. That is the point: the role boundary has to hold when the caller goes
straight to the URL, which is what a curious participant with the network tab open
actually does.

The matrix the brief asks for is read as: each role reaches its own surface, and
is refused on every other one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.devtokens import stable_token
from app.models import Score, Submission, Team, TeamMember, User
from app.security import sign_payload
from app.services import assign_judges

pytestmark = pytest.mark.postgres

ORGANIZER_ONLY = (
    "/api/admin/leaderboard",
    "/api/admin/audit",
    "/api/admin/flagged",
    "/api/admin/export/leaderboard.csv",
    "/api/admin/export/scores.csv",
    "/api/admin/export/judging-progress.csv",
    "/api/admin/judging-progress",
)

JUDGE_ONLY = ("/api/judging/assignments",)


def headers_for(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {stable_token(user)}"}


@pytest.fixture()
def world(db, make_user):
    judge_a = make_user("judge", email="judge.a.authz@test.dev")
    judge_b = make_user("judge", email="judge.b.authz@test.dev")
    organizer = make_user("admin", email="organizer.authz@test.dev")
    participant = make_user("participant", email="hacker.authz@test.dev")

    projects = {}
    for label, judge in (("a", judge_a), ("b", judge_b)):
        owner = make_user("participant", email=f"owner.{label}.authz@test.dev")
        team = Team(
            name=f"Authz Team {label.upper()}",
            invite_code=f"AUTHZ{label.upper()}1",
            created_by=owner.id,
        )
        db.add(team)
        db.flush()
        db.add(TeamMember(team_id=team.id, user_id=owner.id))
        submission = Submission(
            team_id=team.id,
            title=f"Authz Project {label.upper()}",
            repo_url=f"https://github.com/authz/project-{label}",
            demo_url=f"https://demo-{label}.authz.dev",
            status="submitted",
        )
        db.add(submission)
        db.flush()
        # One judge per project: the assignment is the boundary under test.
        assign_judges(db, submission.id, only_judge_ids=[judge.id])
        projects[label] = submission

    own_team = Team(name="Authz Participant Team", invite_code="AUTHZPART", created_by=participant.id)
    db.add(own_team)
    db.flush()
    db.add(TeamMember(team_id=own_team.id, user_id=participant.id))
    db.commit()

    return {
        "judge_a": judge_a,
        "judge_b": judge_b,
        "organizer": organizer,
        "participant": participant,
        "a": projects["a"],
        "b": projects["b"],
    }


# ── judge A / judge B ────────────────────────────────────────────────────────


def test_judge_a_reaches_only_their_own_assignment(client, world):
    headers = headers_for(world["judge_a"])

    assignments = client.get("/api/judging/assignments", headers=headers)
    assert assignments.status_code == 200
    assert [row["submission_id"] for row in assignments.json()["assignments"]] == [world["a"].id]

    assert client.get(f"/api/judging/submissions/{world['a'].id}", headers=headers).status_code == 200
    assert client.get(f"/api/judging/submissions/{world['b'].id}", headers=headers).status_code == 403


def test_judge_a_cannot_file_a_verdict_on_judge_bs_project(client, db, world):
    response = client.post(
        "/api/judging/scores",
        json={"submission_id": world["b"].id, "technical_score": 10},
        headers=headers_for(world["judge_a"]),
    )

    assert response.status_code == 403
    assert (
        db.scalar(select(Score.id).where(Score.judge_id == world["judge_a"].id)) is None
    ), "a refused write is not a stored score"


def test_judge_b_is_the_mirror_image(client, world):
    headers = headers_for(world["judge_b"])

    assert client.get(f"/api/judging/submissions/{world['b'].id}", headers=headers).status_code == 200
    assert client.get(f"/api/judging/submissions/{world['a'].id}", headers=headers).status_code == 403
    refused = client.post(
        "/api/judging/scores",
        json={"submission_id": world["a"].id, "technical_score": 10},
        headers=headers,
    )
    assert refused.status_code == 403


def test_judges_cannot_read_each_others_presentation_tier(client, db, world):
    """Even with a verdict filed, judge B's unlock does not unlock judge A's view."""
    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": world["b"].id, "technical_score": 7},
            headers=headers_for(world["judge_b"]),
        ).status_code
        == 200
    )

    assert (
        client.get(
            f"/api/judging/submissions/{world['b'].id}/presentation",
            headers=headers_for(world["judge_a"]),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/judging/submissions/{world['b'].id}/presentation",
            headers=headers_for(world["judge_b"]),
        ).status_code
        == 200
    )


# ── participant ──────────────────────────────────────────────────────────────


def test_a_participant_reaches_their_own_resources(client, world):
    headers = headers_for(world["participant"])

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    own = client.get("/api/submissions/me", headers=headers)
    assert own.status_code == 200, own.text
    assert client.get("/api/teams/me", headers=headers).status_code in {200, 404}


def test_a_participant_is_refused_on_every_organizer_route(client, world):
    headers = headers_for(world["participant"])

    for path in ORGANIZER_ONLY:
        assert client.get(path, headers=headers).status_code == 403, path

    refused = client.post(
        "/api/admin/judges",
        json={"email": "smuggled.judge@test.dev", "password": "password123", "name": "Smuggled"},
        headers=headers,
    )
    assert refused.status_code == 403


def test_a_participant_is_refused_on_the_judging_surface(client, world):
    headers = headers_for(world["participant"])

    for path in JUDGE_ONLY:
        assert client.get(path, headers=headers).status_code == 403, path
    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": world["a"].id, "technical_score": 10},
            headers=headers,
        ).status_code
        == 403
    )


# ── organizer ────────────────────────────────────────────────────────────────


def test_the_organizer_reaches_every_organizer_route(client, db, world):
    headers = headers_for(world["organizer"])

    for path in ORGANIZER_ONLY:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)

    created = client.post(
        "/api/admin/judges",
        json={
            "email": "created.by.organizer@test.dev",
            "password": "password123",
            "name": "Created Judge",
        },
        headers=headers,
    )
    assert created.status_code in {200, 201}, created.text
    created_user = db.scalars(
        select(User).where(User.email == "created.by.organizer@test.dev")
    ).first()
    assert created_user is not None and created_user.role == "judge"


# ── nobody ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ORGANIZER_ONLY + JUDGE_ONLY + ("/api/submissions/me",))
def test_an_anonymous_request_is_turned_away(client, path):
    response = client.get(path)
    assert response.status_code == 401, (path, response.status_code)


def test_a_forged_token_is_not_a_credential(client, world):
    """Two different refusals, and the difference is the point.

    A token the server did not sign is not a session at all (401), however
    well-formed it looks. A token the server *did* sign is whoever it names — so a
    token for the participant role is the participant, and the participant is
    refused on the organizer's leaderboard (403). `purpose` is descriptive rather
    than a privilege boundary: the checker's tokens are ordinary session tokens in
    every way that matters, which is exactly why `.dogfood.toml` can use one to
    authenticate as the organiser.
    """
    wrong_secret = sign_payload(
        {"uid": world["participant"].id, "exp": 4_102_444_800, "purpose": "checker"},
        "not-the-secret",
    )

    for token in (wrong_secret, "garbage", "not-even-a-token"):
        response = client.get(
            "/api/admin/leaderboard", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, token[:20]

    renamed_purpose = sign_payload(
        {"uid": world["participant"].id, "exp": 4_102_444_800, "purpose": "other"},
        "test-secret-key",
    )
    assert (
        client.get(
            "/api/admin/leaderboard", headers={"Authorization": f"Bearer {renamed_purpose}"}
        ).status_code
        == 403
    )


def test_a_real_participant_token_cannot_buy_an_organizer_surface(client, world):
    """A valid credential for the wrong role is still the wrong role."""
    response = client.get(
        "/api/admin/leaderboard", headers=headers_for(world["participant"])
    )
    assert response.status_code == 403
