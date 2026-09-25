"""Blind evaluation is enforced at the API boundary.

These tests deliberately bypass the UI: they call the presentation endpoint
directly, the way a curious judge with devtools would.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Assignment, Score, Submission, Team, TeamMember, User


@pytest.fixture()
def scenario(db, make_user):
    judge = make_user("judge", email="judge.blind@test.dev")
    participant = make_user("participant", email="hacker.blind@test.dev")
    admin = make_user("admin", email="admin.blind@test.dev")

    team = Team(name="Blind Test Team", invite_code="BLIND001", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))

    submission = Submission(
        team_id=team.id,
        title="Blind Candidate",
        repo_url="https://github.com/axion-demo/blind-candidate",
        docs_url="https://github.com/axion-demo/blind-candidate#readme",
        demo_url="https://blind.axion-demo.dev",
        video_url="https://youtu.be/blind",
        summary="Should not be visible before the technical verdict.",
    )
    db.add(submission)
    db.flush()
    db.add(Assignment(judge_id=judge.id, submission_id=submission.id))
    db.commit()

    return {"judge": judge, "participant": participant, "admin": admin, "submission": submission}


def test_presentation_is_forbidden_before_the_technical_verdict(client, auth, scenario):
    auth(scenario["judge"].email)
    submission_id = scenario["submission"].id

    response = client.get(f"/api/judging/submissions/{submission_id}/presentation")
    assert response.status_code == 403
    assert "technical" in response.json()["detail"].lower()


def test_technical_view_hides_presentation_artifacts(client, auth, scenario):
    auth(scenario["judge"].email)
    response = client.get(f"/api/judging/submissions/{scenario['submission'].id}")
    assert response.status_code == 200
    payload = response.json()["submission"]

    assert payload["repo_url"].endswith("blind-candidate")
    assert payload["presentation_unlocked"] is False
    serialized = str(payload)
    assert "blind.axion-demo.dev" not in serialized
    assert "youtu.be/blind" not in serialized


def test_presentation_cannot_be_scored_first(client, auth, scenario):
    auth(scenario["judge"].email)
    response = client.post(
        "/api/judging/scores",
        json={"submission_id": scenario["submission"].id, "presentation_score": 9},
    )
    assert response.status_code == 403


def test_submitting_technical_unlocks_presentation(client, auth, scenario):
    auth(scenario["judge"].email)
    submission_id = scenario["submission"].id

    technical = client.post(
        "/api/judging/scores",
        json={
            "submission_id": submission_id,
            "technical_score": 8,
            "technical_comment": "Clean abstractions.",
        },
    )
    assert technical.status_code == 200
    assert technical.json()["presentation_unlocked"] is True

    unlocked = client.get(f"/api/judging/submissions/{submission_id}/presentation")
    assert unlocked.status_code == 200
    assert unlocked.json()["presentation"]["demo_url"] == "https://blind.axion-demo.dev"


def test_assignment_list_never_leaks_presentation_links(client, auth, scenario):
    auth(scenario["judge"].email)
    response = client.get("/api/judging/assignments")
    assert response.status_code == 200
    body = response.json()
    assert len(body["assignments"]) == 1
    assert "demo_url" not in body["assignments"][0]
    assert "blind.axion-demo.dev" not in response.text


def test_unassigned_judge_gets_403(client, auth, db, scenario, make_user):
    outsider = make_user("judge", email="outsider@test.dev")
    auth(outsider.email)
    response = client.get(f"/api/judging/submissions/{scenario['submission'].id}")
    assert response.status_code == 403


def test_participants_cannot_reach_the_judging_surface(client, auth, scenario):
    auth(scenario["participant"].email)
    assert client.get("/api/judging/assignments").status_code == 403


def test_admins_can_inspect_the_presentation_tier(client, auth, scenario):
    auth(scenario["admin"].email)
    response = client.get(f"/api/judging/submissions/{scenario['submission'].id}/presentation")
    assert response.status_code == 200


def test_score_writes_are_audited(client, auth, db, scenario):
    auth(scenario["judge"].email)
    client.post(
        "/api/judging/scores",
        json={"submission_id": scenario["submission"].id, "technical_score": 7},
    )
    from app.models import AuditLog

    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "score.technical_submitted" in actions


def test_editing_a_score_is_recorded_as_a_modification(client, auth, db, scenario):
    auth(scenario["judge"].email)
    submission_id = scenario["submission"].id
    client.post("/api/judging/scores", json={"submission_id": submission_id, "technical_score": 7})
    client.post("/api/judging/scores", json={"submission_id": submission_id, "technical_score": 9})

    from app.models import AuditLog

    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert "score.technical_modified" in actions

    score = db.scalar(
        select(Score).where(Score.submission_id == submission_id, Score.judge_id == scenario["judge"].id)
    )
    assert score.technical_score == 9


def test_out_of_range_scores_are_rejected(client, auth, scenario):
    auth(scenario["judge"].email)
    response = client.post(
        "/api/judging/scores",
        json={"submission_id": scenario["submission"].id, "technical_score": 11},
    )
    assert response.status_code == 422
