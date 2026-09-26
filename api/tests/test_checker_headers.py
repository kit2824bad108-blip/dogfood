"""The acceptance credentials a header-only checker relies on.

`.dogfood.toml` points its `auth.token_source` at `GET /api/dev/checker-headers`
instead of embedding a token, so the indirection has a contract: the same four
roles every time, tokens that do not rotate between calls, and a 403 the moment
the deployment stops being an explicit demo.

The header authentication itself lives in `deps.py`; these tests pin the
seeding, the stability and the gate that make it usable by a checker.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app import config, devtokens
from app.models import Team, TeamMember


@pytest.fixture()
def accounts(db, make_user):
    """The four identities a checker needs, with roles as the fixture seed sets them."""
    organizer = make_user("admin", email="organizer@fixture.test")
    judge_a = make_user("judge", email="judge.a@fixture.test")
    judge_b = make_user("judge", email="judge.b@fixture.test")
    participant = make_user("participant", email="hacker@fixture.test")
    team = Team(name="Checker Team", invite_code="CHECK001", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))
    db.commit()
    return {
        "organizer": organizer,
        "judge_a": judge_a,
        "judge_b": judge_b,
        "participant": participant,
    }


def test_all_four_roles_with_distinct_identities(accounts):
    headers = devtokens.checker_headers()
    assert set(headers) == set(devtokens.ROLE_ORDER) == {
        "organizer",
        "judge_a",
        "judge_b",
        "participant",
    }
    assert headers["organizer"]["email"] == "organizer@fixture.test"
    assert headers["judge_a"]["email"] == "judge.a@fixture.test"
    assert headers["judge_b"]["email"] == "judge.b@fixture.test"
    assert headers["participant"]["email"] == "hacker@fixture.test"
    assert len({record["email"] for record in headers.values()}) == 4


def test_tokens_are_byte_identical_across_calls(accounts):
    """A report has to stay reproducible: no rotation between two reads."""
    first = devtokens.checker_headers()
    second = devtokens.checker_headers()
    assert first == second
    for record in first.values():
        assert record["Authorization"].startswith("Bearer ")
        assert record["X-Axion-Session"] == record["Authorization"][len("Bearer ") :]


def test_each_header_authenticates_with_its_own_role(client, accounts):
    body = client.get("/api/dev/checker-headers").json()
    assert body["auth_mode"] == "bearer"
    assert set(body["roles"]) == {"organizer", "judge_a", "judge_b", "participant"}
    for role, record in body["roles"].items():
        response = client.get(
            "/api/auth/me", headers={"Authorization": record["Authorization"]}
        )
        assert response.status_code == 200, (role, response.text)
        assert response.json()["authenticated"] is True
        assert response.json()["user"]["role"] == record["role"]
        assert response.json()["user"]["email"] == record["email"]


def test_the_headers_also_work_via_the_explicit_session_header(client, accounts):
    record = client.get("/api/dev/checker-headers").json()["roles"]["judge_b"]
    response = client.get(
        "/api/auth/me", headers={"X-Axion-Session": record["X-Axion-Session"]}
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "judge"


def test_the_endpoint_is_403_when_the_deployment_is_not_a_demo(client, accounts, monkeypatch):
    production = replace(
        config.settings,
        mock_github=False,
        seed_demo=False,
        local_dev_login_override=False,
        dogfood_fixture_mode=False,
    )
    monkeypatch.setattr(config, "settings", production)
    monkeypatch.setattr("app.routers.devtools.settings", production)
    response = client.get("/api/dev/checker-headers")
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_the_alias_selects_the_fixture_window_and_the_same_gate(monkeypatch):
    """DOGFOOD_FIXTURE_MODE is one flag for the dataset, its window and the gate.

    Settings are read from the environment once at import time, so this builds a
    settings object directly rather than reloading the module.
    """
    monkeypatch.setenv("DOGFOOD_FIXTURE_MODE", "true")
    monkeypatch.delenv("EVENT_SOURCE", raising=False)
    monkeypatch.setenv("EVENT_START", "")
    monkeypatch.setenv("EVENT_END", "")

    settings = config.Settings.from_env()

    assert settings.dogfood_fixture_mode is True
    assert settings.local_dev_login is True
    # The fixture's own deadline (2026-08-04) is the one that applies, so the
    # event is closed on any machine running this suite after it was authored.
    assert settings.event_end == datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert settings.event_window_closed is True


def test_the_alias_does_not_override_an_explicit_event_source(monkeypatch):
    monkeypatch.setenv("DOGFOOD_FIXTURE_MODE", "true")
    monkeypatch.setenv("EVENT_SOURCE", "env")
    monkeypatch.setenv("EVENT_START", "2030-01-01T00:00:00+00:00")
    monkeypatch.setenv("EVENT_END", "2030-01-08T00:00:00+00:00")

    settings = config.Settings.from_env()

    assert settings.event_end == datetime(2030, 1, 8, tzinfo=timezone.utc)
    # The window follows EVENT_SOURCE; the flag still means "this is a demo".
    assert settings.local_dev_login is True
