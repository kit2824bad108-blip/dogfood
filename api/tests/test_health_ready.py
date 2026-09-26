"""Readiness: it must fail for the right reasons, not only pass when happy.

The test schema is built by `create_all`, so alembic has never run here; the
tests that want a ready instance put `alembic_version` at head explicitly.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app import readiness
from app.models import Submission, Team


@pytest.fixture(autouse=True)
def _no_stale_alembic_version(db):
    """`alembic_version` is not in Base.metadata, so the shared in-memory
    database keeps it across tests; each test starts without it."""
    db.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.commit()
    yield


def _mark_migrated(db) -> str:
    head = readiness._script_head()
    assert head, "the alembic version directory must be readable in tests"
    db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head}
    )
    db.commit()
    return head


def _seed_a_project(db, make_user) -> None:
    admin = make_user("admin", email="ready@test.dev")
    team = Team(name="Ready Team", invite_code="READY001", created_by=admin.id)
    db.add(team)
    db.flush()
    db.add(
        Submission(
            team_id=team.id,
            title="Ready Project",
            repo_url="https://github.com/ready/project",
            status="submitted",
        )
    )
    db.commit()


def test_live_is_200_even_on_an_empty_database(client):
    assert client.get("/api/health/live").json() == {"status": "alive"}


def test_ready_names_every_missing_piece(client):
    response = client.get("/api/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["database"] == "ok"
    assert "migrations" in body["failed"]
    assert "dataset" in body["failed"]
    assert "no dataset initialised (0 users, 0 submissions)" in body["checks"]["dataset"]


def test_ready_is_200_when_migrated_and_seeded(client, db, make_user):
    _mark_migrated(db)
    _seed_a_project(db, make_user)

    response = client.get("/api/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["migrations"] == "ok"
    assert body["checks"]["dataset"] == "1 users, 1 submissions"
    assert body["failed"] == []


def test_ready_fails_when_the_schema_is_behind_head(client, db, make_user):
    db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(
        text("INSERT INTO alembic_version (version_num) VALUES ('0002_event_and_rubrics')")
    )
    _seed_a_project(db, make_user)

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["failed"] == ["migrations"]
    assert "schema is at 0002_event_and_rubrics" in response.json()["checks"]["migrations"]


def test_ready_marks_everything_unproven_when_the_database_is_unreachable(client, monkeypatch):
    class Unreachable:
        def __call__(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(readiness, "SessionLocal", Unreachable())

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert "unreachable" in body["checks"]["database"]
    assert set(body["failed"]) == {"database", "migrations", "dataset"}
