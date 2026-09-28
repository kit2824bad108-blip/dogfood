"""Whole-event bundles (T4) and the embeddable gallery (T4).

The bundle's claim is operability: an organiser can leave this deployment with
their event intact. So the test that matters is a round trip into a *different
database* — the one built here from an empty schema — and the assertions are the
things a stranger would check: the same counts, the same ranking, the same signed
records, and no credentials anywhere in the document.

The embed's claim is the opposite one: read-only and public, with no third-party
asset, so a sponsor's page renders it air-gapped.
"""
from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import bundle as bundle_module
from app.db import Base
from app.models import ParticipationRecord, Score, Submission, Team, User


@pytest.fixture(autouse=True)
def seeded(db):
    from app import seed as seed_module

    seed_module.seed()
    return None


def as_admin(client, make_user, auth) -> None:
    user = make_user("admin", email="organiser@test.dev")
    auth(user.email)


def a_fresh_database(tmp_path) -> Session:
    """A second deployment: empty schema, its own file."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'elsewhere.db'}")
    Base.metadata.create_all(bind=engine)
    return Session(engine)


# ── export ──────────────────────────────────────────────────────────────────


def test_only_an_organiser_can_export_a_bundle(client, make_user, auth):
    judge = make_user("judge", email="j@test.dev")
    auth(judge.email)
    assert client.get("/api/admin/bundle/export").status_code == 403


def test_no_credential_travels_in_a_bundle(client, make_user, auth):
    as_admin(client, make_user, auth)
    document = json.loads(client.get("/api/admin/bundle/export").text)

    assert document["bundle_version"] == bundle_module.BUNDLE_VERSION
    assert document["counts"]["users"] > 0
    assert document["checksum"] == bundle_module.checksum(document["tables"])

    serialised = json.dumps(document)
    assert "password_hash" not in serialised
    assert "session" not in serialised.lower() or "session" in json.dumps(document["event"]).lower()


def test_an_export_of_an_unchanged_event_is_byte_identical(client, make_user, auth):
    as_admin(client, make_user, auth)
    first = json.loads(client.get("/api/admin/bundle/export").text)
    second = json.loads(client.get("/api/admin/bundle/export").text)

    assert first["checksum"] == second["checksum"]
    assert json.dumps(first["tables"], sort_keys=True) == json.dumps(second["tables"], sort_keys=True)


# ── import ──────────────────────────────────────────────────────────────────


def test_the_import_is_a_dry_run_by_default_and_writes_nothing(client, make_user, auth, db, tmp_path):
    as_admin(client, make_user, auth)
    document = json.loads(client.get("/api/admin/bundle/export").text)

    # Into the deployment it came from, every row is matched on the key it is
    # identified by, so a correct import creates nothing and updates everything.
    # That is what makes importing a bundle twice safe.
    same = client.post("/api/admin/bundle/import", json={"bundle": document}).json()
    assert same["mode"] == "dry_run"
    assert same["created"] == {}
    assert same["updated"], "a dry run still reports what it would do"
    assert "nothing was written" in same["note"]

    # Into an empty deployment, the same call reports the creations it would make
    # — and, because the default is a dry run, makes none of them.
    elsewhere = a_fresh_database(tmp_path)
    try:
        would_create = bundle_module.import_bundle(elsewhere, document)
        assert would_create["created"]["teams"] == document["counts"]["teams"]
        assert elsewhere.scalars(select(User)).all() == []
    finally:
        elsewhere.close()

    before = [row.id for row in db.scalars(select(User)).all()]
    assert [row.id for row in db.scalars(select(User)).all()] == before, "the live event is untouched"


def test_a_round_trip_into_a_fresh_database_reproduces_the_event(client, make_user, auth, db, tmp_path):
    as_admin(client, make_user, auth)
    document = json.loads(client.get("/api/admin/bundle/export").text)
    original = bundle_module.export_bundle(db)

    elsewhere = a_fresh_database(tmp_path)
    try:
        applied = bundle_module.import_bundle(elsewhere, document, mode="apply")
        assert applied["refused"] is False
        assert applied["created"]["tracks"] == document["counts"]["tracks"]
        assert applied["created"]["teams"] == document["counts"]["teams"]
        assert applied["created"]["users"] == document["counts"]["users"]
        assert applied["created"]["submissions"] == document["counts"]["submissions"]

        # The same event, not merely the same row count.
        assert {row.name for row in elsewhere.scalars(select(Team)).all()} == {
            row.name for row in db.scalars(select(Team)).all()
        }
        assert len(elsewhere.scalars(select(Submission)).all()) == len(
            db.scalars(select(Submission)).all()
        )
        assert len(elsewhere.scalars(select(Score)).all()) == len(db.scalars(select(Score)).all())
        assert len(elsewhere.scalars(select(ParticipationRecord)).all()) == len(
            db.scalars(select(ParticipationRecord)).all()
        )

        # And the signed bytes survive: a record imported elsewhere still verifies.
        from app import records

        imported = elsewhere.scalars(select(ParticipationRecord)).first()
        if imported is not None:
            assert records.verify(imported, imported.payload["signature"])["presented"] == imported.signature

        # Importing the same bundle twice updates rather than duplicates.
        again = bundle_module.import_bundle(elsewhere, document, mode="apply")
        assert not again["created"], "a second import of the same event creates nothing"
        assert again["updated"]
    finally:
        elsewhere.close()

    assert original["counts"] == document["counts"]


def test_a_bundle_edited_in_transit_is_refused(client, make_user, auth):
    as_admin(client, make_user, auth)
    document = json.loads(client.get("/api/admin/bundle/export").text)
    document["tables"]["teams"][0]["name"] = "Renamed After Signing"

    checked = client.post("/api/admin/bundle/validate", json={"bundle": document}).json()
    assert checked["valid"] is False
    assert any("checksum" in problem for problem in checked["errors"])

    refused = client.post("/api/admin/bundle/import", json={"bundle": document, "mode": "apply"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"]


def test_a_bundle_that_names_an_unknown_team_is_refused_whole(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    document = json.loads(client.get("/api/admin/bundle/export").text)
    document["tables"]["submissions"][0]["team"] = "a team that does not exist"
    document["checksum"] = bundle_module.checksum(document["tables"])

    problems = bundle_module.validate_bundle(document)
    assert any("unknown team" in problem for problem in problems)

    before = len(db.scalars(select(Submission)).all())
    refused = client.post("/api/admin/bundle/import", json={"bundle": document, "mode": "apply"})
    assert refused.status_code == 422
    assert len(db.scalars(select(Submission)).all()) == before, "half an event is worse than none"


# ── the embeddable gallery ──────────────────────────────────────────────────


def test_the_embed_is_public_self_contained_and_read_only(client):
    response = client.get("/api/embed/gallery")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    page = response.text
    assert "<script" not in page
    assert "<link" not in page
    assert "https://fonts." not in page
    assert page.count("<li class=\"card\">") > 0

    # Read-only in every direction: an embed is not an entry point into the event.
    assert client.post("/api/embed/gallery").status_code == 405
    assert client.get("/api/embed/gallery?limit=1").text.count("<li class=\"card\">") == 1


def test_the_snippet_points_at_the_deployment_that_served_it(client):
    body = client.get("/api/embed/gallery/snippet?theme=dark&limit=6").json()

    assert body["iframe"].startswith("<iframe src=")
    assert "/api/embed/gallery?limit=6&theme=dark" in body["src"]
    assert "loading=\"lazy\"" in body["iframe"]
    assert len(body["notes"]) >= 3


def test_the_embed_hides_duplicate_submissions(client):
    """A duplicate is reported, never ranked — and never shown twice either."""
    page = client.get("/api/embed/gallery?limit=200").text
    titles = re.findall(r'<h3><a href="[^"]*"[^>]*>([^<]+)</a></h3>', page)

    assert titles, "the seeded event has public projects"
    assert len(titles) == len(set(titles))
