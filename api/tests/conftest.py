"""Test bootstrap.

Environment variables must be set *before* `app.config` is imported, because
settings are read once at import time.
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["MOCK_GITHUB"] = "true"
os.environ["WEB_URL"] = "http://localhost:3000"
os.environ["EVENT_START"] = "2026-09-20T00:00:00+00:00"
os.environ["EVENT_END"] = "2026-09-23T00:00:00+00:00"

from datetime import datetime, timezone  # noqa: E402

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
