"""Identity, sessions and role boundaries."""
from __future__ import annotations


def test_register_creates_a_session_cookie(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "new@test.dev", "password": "supersecret", "name": "New Person"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "participant"
    assert "axion_session" in response.cookies

    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is True


def test_duplicate_registration_is_rejected(client):
    payload = {"email": "dupe@test.dev", "password": "supersecret"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_login_rejects_a_bad_password(client, make_user):
    make_user("participant", email="pw@test.dev")
    response = client.post("/api/auth/login", json={"email": "pw@test.dev", "password": "wrong-password"})
    assert response.status_code == 401


def test_logout_clears_the_session(client, make_user, auth):
    make_user("participant", email="bye@test.dev")
    auth("bye@test.dev")
    assert client.get("/api/auth/me").json()["authenticated"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_anonymous_users_are_not_authenticated(client):
    assert client.get("/api/auth/me").json() == {"authenticated": False, "user": None}


def test_password_hash_is_never_serialized(client, make_user, auth):
    make_user("participant", email="private@test.dev")
    auth("private@test.dev")
    body = client.get("/api/auth/me").text
    assert "password" not in body.lower()
    assert "pbkdf2" not in body


def test_participants_cannot_reach_admin_routes(client, make_user, auth):
    make_user("participant", email="nobody@test.dev")
    auth("nobody@test.dev")
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/api/admin/leaderboard").status_code == 403


def test_judges_cannot_reach_admin_routes(client, make_user, auth):
    make_user("judge", email="judge.only@test.dev")
    auth("judge.only@test.dev")
    assert client.get("/api/admin/overview").status_code == 403


def test_admin_can_reach_the_console(client, make_user, auth):
    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")
    assert client.get("/api/admin/overview").status_code == 200


def test_team_lifecycle_and_invite_codes(client, make_user, auth, db):
    make_user("participant", email="captain@test.dev")
    auth("captain@test.dev")
    created = client.post("/api/teams", json={"name": "Alpha Squad"})
    assert created.status_code == 200
    invite = created.json()["team"]["invite_code"]

    # A second member joins with the invite code.
    second = make_user("participant", email="rookie@test.dev")
    client.post("/api/auth/login", json={"email": second.email, "password": "password123"})
    joined = client.post("/api/teams/join", json={"invite_code": invite})
    assert joined.status_code == 200
    assert len(joined.json()["team"]["members"]) == 2


def test_double_team_membership_is_rejected(client, make_user, auth):
    make_user("participant", email="solo@test.dev")
    auth("solo@test.dev")
    assert client.post("/api/teams", json={"name": "Only Team"}).status_code == 200
    assert client.post("/api/teams", json={"name": "Second Team"}).status_code == 400


def test_bad_invite_code_is_a_404(client, make_user, auth):
    make_user("participant", email="lost@test.dev")
    auth("lost@test.dev")
    assert client.post("/api/teams/join", json={"invite_code": "NOPE1234"}).status_code == 404


def test_submission_requires_a_team(client, make_user, auth):
    make_user("participant", email="teamless@test.dev")
    auth("teamless@test.dev")
    response = client.post(
        "/api/submissions",
        json={"title": "Orphan", "repo_url": "https://github.com/axion-demo/orphan"},
    )
    assert response.status_code == 400


def test_submission_rejects_a_non_github_url(client, make_user, auth):
    make_user("participant", email="gitlab@test.dev")
    auth("gitlab@test.dev")
    client.post("/api/teams", json={"name": "URL Police"})
    response = client.post(
        "/api/submissions",
        json={"title": "Not GitHub", "repo_url": "https://gitlab.com/somebody/thing"},
    )
    assert response.status_code == 400


def test_submission_records_commit_integrity(client, make_user, auth):
    make_user("participant", email="submitter@test.dev")
    auth("submitter@test.dev")
    client.post("/api/teams", json={"name": "Integrity Squad"})
    response = client.post(
        "/api/submissions",
        json={"title": "Fresh Code", "repo_url": "https://github.com/axion-demo/fresh-code"},
    )
    assert response.status_code == 200
    integrity = response.json()["submission"]["commit_integrity"]
    assert integrity["source"] == "mock"
    assert integrity["pct_in_window"] is not None


def test_admin_can_create_a_judge_who_is_assigned_everywhere(client, make_user, auth, db):
    from app.models import Assignment, Team, Submission

    admin = make_user("admin", email="root@test.dev")
    auth(admin.email)

    team = Team(name="Assignment Team", invite_code="ASSIGN01", created_by=admin.id)
    db.add(team)
    db.flush()
    db.add(
        Submission(
            team_id=team.id,
            title="Assigned Project",
            repo_url="https://github.com/axion-demo/assigned",
        )
    )
    db.commit()

    response = client.post(
        "/api/admin/judges",
        json={"email": "fresh.judge@test.dev", "password": "judgepassword", "name": "Fresh Judge"},
    )
    assert response.status_code == 200
    assert response.json()["assigned"] == 1

    assignments = db.execute(select_all(Assignment)).all()
    assert len(assignments) == 1


def select_all(model):
    from sqlalchemy import select

    return select(model)


def test_health_endpoint_is_open(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
