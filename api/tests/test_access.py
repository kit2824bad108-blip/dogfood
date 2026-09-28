"""The four literal credentials, and the gate that keeps them out of production.

The organisers' checker attaches a header string and never logs in, so the
credentials it uses have to be literal, committed, and stable across machines.
That is a deliberate weakening of an auth boundary, and the whole justification is
the gate: they are accepted only while the deployment has explicitly declared
itself a demo. These tests are what make that claim checkable rather than a
sentence in a document.
"""
from __future__ import annotations

import pytest

from app import access, devtokens


def test_every_role_the_checker_uses_gets_a_header_it_can_carry():
    """Four roles, four `Cookie:` headers, each naming its own token."""
    headers = access.headers()

    assert set(headers) == set(access.ROLE_ORDER) == set(devtokens.ROLE_ORDER)
    for role, header in headers.items():
        assert header == f"Cookie: {access.LITERAL_COOKIE}={access.LITERAL_TOKENS[role]}"
    # Distinct tokens: a copy-paste that left two roles sharing one string would
    # otherwise silently make every isolation check pass for the wrong reason.
    assert len(set(access.LITERAL_TOKENS.values())) == len(access.LITERAL_TOKENS)


def test_the_tokens_are_readable_rather_than_random():
    """A header in a bug report should say which role it proves."""
    for role, token in access.LITERAL_TOKENS.items():
        assert role.replace("_", "-") in token


def test_a_literal_token_does_not_resolve_once_the_gate_is_off(db, monkeypatch):
    """The whole justification for a committed credential is this gate."""
    monkeypatch.setattr(access, "enabled", lambda: False)

    assert access.resolve(access.LITERAL_TOKENS["organizer"], db) is None


def test_a_literal_cookie_stops_authenticating_once_the_gate_is_off(
    client, organiser_dataset, monkeypatch
):
    """And the boundary is the API's, not the caller's good manners.

    `/api/auth/me` answers 200 to anybody and reports whether it recognised you,
    so identification is asserted through the payload and authorisation through an
    organiser-only route: with the gate off the same cookie is an anonymous 401,
    not an admin session.
    """
    headers = {"Cookie": f"{access.LITERAL_COOKIE}={access.LITERAL_TOKENS['organizer']}"}
    assert client.get("/api/auth/me", headers=headers).json()["user"] is not None
    assert client.get("/api/admin/leaderboard", headers=headers).status_code == 200

    monkeypatch.setattr(access, "enabled", lambda: False)

    assert client.get("/api/auth/me", headers=headers).json()["user"] is None
    assert client.get("/api/admin/leaderboard", headers=headers).status_code == 401


def test_an_invented_literal_is_not_a_session(client, organiser_dataset):
    """Only the four issued tokens are credentials; the shape is not the secret."""
    headers = {"Cookie": f"{access.LITERAL_COOKIE}=axion-organizer-2"}

    assert client.get("/api/auth/me", headers=headers).json()["user"] is None
    assert client.get("/api/admin/leaderboard", headers=headers).status_code == 401


def test_the_literal_headers_are_the_four_fixture_actors(client, organiser_dataset):
    """Who each header signs in as, named, against the organisers' own dataset."""
    expected = {
        "organizer": ("admin", "organiser@sample-hack-2026.dogfood"),
        "judge_a": ("judge", "tomas.varga@example.org"),
        "judge_b": ("judge", "wei.lindqvist@example.org"),
        "participant": ("participant", "priya1@example.org"),
    }

    for role, (api_role, email) in expected.items():
        header = {"Cookie": f"{access.LITERAL_COOKIE}={access.LITERAL_TOKENS[role]}"}
        response = client.get("/api/auth/me", headers=header)
        assert response.status_code == 200, (role, response.text)
        body = response.json()["user"]
        assert body["role"] == api_role, role
        assert body["email"] == email, role


def test_the_organiser_account_is_created_by_the_import(db, organiser_dataset):
    """The organisers' file declares no accounts, so one has to be derived.

    Not invented silently: it is named after the event it organises, which is what
    makes the boot banner and the database agree about who the organizer is.
    """
    from app.models import User

    organiser = db.query(User).filter(User.role == "admin").one()

    assert organiser.email == "organiser@sample-hack-2026.dogfood"
    assert "Sample Hack 2026" in organiser.name


@pytest.mark.parametrize("role", ["organizer", "judge_a", "judge_b", "participant"])
def test_the_signed_bearer_form_still_works(client, organiser_dataset, role):
    """The literal headers are an addition, not a replacement.

    Axion's own deeper self-check fetches signed tokens from
    `/api/dev/checker-headers`; that path has to keep working, and this is what
    says so.
    """
    payload = client.get("/api/dev/checker-headers").json()
    header = {"Authorization": payload["roles"][role]["Authorization"]}

    response = client.get("/api/auth/me", headers=header)

    assert response.status_code == 200, (role, response.text)
