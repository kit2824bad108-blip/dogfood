"""Test bootstrap.

Environment variables must be set *before* `app.config` is imported, because
settings are read once at import time.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# The event window has to be relative to *now*: the API rejects writes once
# `EVENT_END` has passed, so a hard-coded date would silently start testing the
# closed-window path instead of the open one.
_NOW = datetime.now(timezone.utc)

# `AXION_TEST_DATABASE_URL` switches the whole suite onto a real PostgreSQL
# server (`tests/pg` requires it); unset, the fast path stays in-memory SQLite and
# needs no service at all. This has to happen before `app.config` is imported
# below, because settings are read once.
_TEST_DATABASE_URL = os.environ.get("AXION_TEST_DATABASE_URL", "").strip()
USING_POSTGRES = _TEST_DATABASE_URL.startswith("postgresql")
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL or "sqlite+pysqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["MOCK_GITHUB"] = "true"
os.environ["WEB_URL"] = "http://localhost:3000"
os.environ["SEED_DEMO"] = "false"
os.environ["EVENT_START"] = (_NOW - timedelta(hours=48)).isoformat()
os.environ["EVENT_END"] = (_NOW + timedelta(hours=24)).isoformat()
# Pinned explicitly: `app.config` loads a developer's `.env` if one exists, and a
# stray `LOCAL_DEV_LOGIN=true` there would quietly enable the passwordless login
# that the gating tests assert is *off*. Tests must not depend on local state.
os.environ["LOCAL_DEV_LOGIN"] = "false"
os.environ.pop("EVENT_SOURCE", None)
# Same reason as EVENT_SOURCE: a developer's .env opting into fixture mode must
# not flip the dataset or the event window inside the tests.
os.environ.pop("DOGFOOD_FIXTURE_MODE", None)
os.environ["SEED_MODE"] = "demo"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

DEFAULT_PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Rebuild the schema per test so fixtures are isolated."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


# The two datasets that ship with this repository. `fixtures.json` at the root is
# the organisers' published file and is what `docker compose up` seeds;
# `data/axion-fixtures.json` is Axion's own generated dataset, which every number
# in README.md and JUDGING.md was computed against.
ORGANISER_FIXTURE = "fixtures.json"
AXION_FIXTURE = "data/axion-fixtures.json"


@pytest.fixture()
def organiser_dataset(db):
    """Import the organisers' published fixtures.json into an empty database.

    Imported through the same loader `docker compose up` uses, so these tests
    exercise the dialect translation rather than a hand-written stand-in for it.
    """
    from app import fixtures as fixtures_module

    payload = fixtures_module.load_fixture(ORGANISER_FIXTURE)
    summary = fixtures_module.apply_fixture(db, payload, mode="apply")
    assert summary.get("applied") is not None, summary.get("invalid")
    db.commit()
    return summary


@pytest.fixture()
def checker_headers() -> dict[str, dict[str, str]]:
    """The four literal `Cookie:` headers the committed .dogfood.toml uses.

    Read from `app.access` rather than written out again here: the manifest and
    the code that accepts the headers must not be able to drift apart, and a test
    that hard-coded its own copy would not notice if they did.
    """
    from app import access

    return {
        role: {"Cookie": f"{access.LITERAL_COOKIE}={access.LITERAL_TOKENS[role]}"}
        for role in access.ROLE_ORDER
    }


@pytest.fixture()
def make_user(db):
    counter = {"n": 0}

    def _make_user(role: str = "participant", email: str | None = None, password: str = DEFAULT_PASSWORD) -> User:
        counter["n"] += 1
        user = User(
            email=email or f"{role}{counter['n']}@test.dev",
            name=f"{role} {counter['n']}",
            role=role,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make_user


@pytest.fixture()
def auth(client):
    def _login(email: str, password: str = DEFAULT_PASSWORD):
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()

    return _login


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture()
def closed_window(monkeypatch):
    """Move the event window into the past for the routers that read settings.

    `Settings` is a frozen dataclass, so the module-level name is replaced
    rather than mutated — routers look up `settings` at call time.
    """
    from dataclasses import replace

    from app import config

    expired = replace(
        config.settings,
        event_start=_NOW - timedelta(hours=96),
        event_end=_NOW - timedelta(hours=1),
    )
    for module in (
        "app.routers.submissions",
        "app.routers.event",
        "app.routers.auth",
        "app.services",
    ):
        monkeypatch.setattr(f"{module}.settings", expired, raising=False)
    monkeypatch.setattr(config, "settings", expired)
    return expired
