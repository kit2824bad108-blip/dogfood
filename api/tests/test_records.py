"""Signed participation records and certificates (T4).

A record is a claim someone else can check: *this judge filed fourteen verdicts*,
*this team placed third*. So the tests here are about what makes that checkable —
the signature is over the stored bytes, the numbers agree with the leaderboard the
results page uses, revocation is a dated fact rather than an edit, and the key that
lets a stranger verify is only published once publishing it can no longer help
anyone forge a result.

The event window is monkeypatched rather than waited for: whether the key is public
is a property of the clock, and the clock is the one thing a test can move.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config, records
from app.models import ParticipationRecord

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture(autouse=True)
def seeded(db):
    from app import seed as seed_module

    seed_module.seed()
    return None


def as_admin(client, make_user, auth) -> None:
    user = make_user("admin", email="organiser@test.dev")
    auth(user.email)


def issue(client) -> dict:
    response = client.post("/api/admin/records/issue", json={"judges": True, "teams": True})
    assert response.status_code == 201, response.text
    return response.json()


# ── issuing ─────────────────────────────────────────────────────────────────


def test_only_an_organiser_can_issue_or_list_records(client, make_user, auth):
    judge = make_user("judge", email="j@test.dev")
    auth(judge.email)

    assert client.get("/api/admin/records").status_code == 403
    assert client.post("/api/admin/records/issue", json={}).status_code == 403


def test_issuing_covers_every_judge_and_team_and_is_idempotent(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    first = issue(client)

    assert first["totals"]["issued"] > 0
    assert first["skipped"] == []
    assert all(entry["code"].startswith("AXN-") for entry in first["issued"])

    # A record is a statement about a moment. Re-issuing one because a verdict
    # changed afterwards would invalidate every copy already in someone's hands.
    second = issue(client)
    assert second["totals"]["issued"] == 0
    assert second["totals"]["already_issued"] == first["totals"]["issued"]
    assert db.scalar(select(ParticipationRecord).where(ParticipationRecord.code == first["issued"][0]["code"]))


def test_a_judges_record_carries_the_same_numbers_the_leaderboard_uses(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    body = issue(client)

    judge_record = next(
        row
        for row in db.scalars(select(ParticipationRecord).where(ParticipationRecord.subject_kind == "judge")).all()
    )
    summary = judge_record.payload["summary"]

    assert summary["verdicts_filed"] > 0
    assert summary["effective_sigma"] is not None
    assert "z-score" in summary["normalization"]
    # The signed payload carries the claim, and the row's columns agree with it:
    # a mismatch would mean the certificate and the database disagreed.
    assert judge_record.payload["code"] == judge_record.code
    assert judge_record.payload["algorithm"] == judge_record.algorithm
    assert judge_record.payload["key_fingerprint"] == records.key_fingerprint()
    assert body["issued"], "an empty event has nothing to attest, but this one is seeded"


def test_a_winner_record_quotes_the_placement_from_the_ranking(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    client.post("/api/admin/records/issue", json={"judges": False, "teams": False, "winners": 2})

    winners = db.scalars(
        select(ParticipationRecord).where(ParticipationRecord.role == "winner").order_by(ParticipationRecord.id)
    ).all()
    assert [row.payload["summary"]["placement"] for row in winners] == [1, 2]

    ranking = records.ranked_results(db, limit=2)
    assert winners[0].payload["summary"]["project"] == ranking[0]["title"]
    assert winners[0].payload["summary"]["axion_score"] == ranking[0]["axion_score"]


# ── verification ────────────────────────────────────────────────────────────


def test_a_stranger_can_verify_a_record_without_authenticating(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]

    verified = client.get(f"/api/records/{code}")
    assert verified.status_code == 200
    body = verified.json()

    assert body["verification"]["verified"] is True
    assert body["record"]["code"] == code
    assert body["verification"]["key_fingerprint"] == records.key_fingerprint()


def test_a_lowercase_code_still_verifies_and_an_unknown_one_is_404(client, make_user, auth):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]

    assert client.get(f"/api/records/{code.lower()}").status_code == 200
    missing = client.get("/api/records/AXN-0000-0000")
    assert missing.status_code == 404
    assert "was issued" in missing.json()["detail"]


def test_editing_the_stored_payload_breaks_the_signature(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]
    record = records.find(db, code)

    assert records.verify(record)["verified"] is True

    # The signature is over the bytes as stored, so a single edited number is
    # caught — this is why the payload is stored instead of rebuilt from columns.
    tampered = dict(record.payload)
    tampered["summary"] = {**tampered.get("summary", {}), "verdicts_filed": 999}
    record.payload = tampered
    db.commit()

    check = records.verify(record)
    assert check["verified"] is False
    assert "does not match" in check["reason"]
    assert client.get(f"/api/records/{code}").json()["verification"]["verified"] is False


def test_revocation_is_a_dated_fact_beside_the_signature_not_an_edit(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]
    before = client.get(f"/api/records/{code}").json()["record"]["signature"]

    revoked = client.post(f"/api/admin/records/{code}/revoke", json={"reason": "wrong team name"}).json()
    assert revoked["revoked"] is True
    assert revoked["already_revoked"] is False

    after = client.get(f"/api/records/{code}").json()
    assert after["record"]["signature"] == before, "the record itself is never altered"
    assert after["verification"]["verified"] is True, "the signature stays valid; the revocation is the new fact"
    assert after["revocation"]["revoked"] is True
    assert after["revocation"]["reason"] == "wrong team name"

    # Revoking twice is not an error, it is a fact that was already true.
    assert client.post(f"/api/admin/records/{code}/revoke", json={"reason": "again"}).json()["already_revoked"] is True


def test_the_verification_key_is_published_only_after_the_event_closes(client, make_user, auth, db, monkeypatch):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]
    now = datetime.now(timezone.utc)

    still_running = replace(config.settings, event_end=now + timedelta(hours=6))
    monkeypatch.setattr(records, "settings", still_running)
    open_key = client.get(f"/api/records/{code}").json()["key"]
    assert open_key["published"] is False
    assert open_key["key"] is None
    assert "still running" in open_key["reason"]
    assert open_key["fingerprint"] == records.key_fingerprint()

    closed = replace(config.settings, event_end=now - timedelta(minutes=1))
    monkeypatch.setattr(records, "settings", closed)
    published = client.get(f"/api/records/{code}").json()["key"]
    assert published["published"] is True
    assert published["key"] == config.settings.record_signing_key
    assert "anyone can verify" in published["reason"]

    # And the published key is the one that actually verifies the record.
    check = records.verify(db.scalar(select(ParticipationRecord).where(ParticipationRecord.code == code)), published["key"])
    assert check["verified"] is True


def test_the_certificate_is_self_contained_and_names_the_code(client, make_user, auth):
    as_admin(client, make_user, auth)
    code = issue(client)["issued"][0]["code"]

    response = client.get(f"/api/records/{code}/certificate")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    page = response.text
    assert code in page
    assert "Certificate of participation" in page
    assert records.ALGORITHM in page
    # Offline-first: no font, stylesheet or script is fetched from anywhere.
    assert "<script" not in page
    assert "https://fonts." not in page
    assert page.count("<link") == 0

    assert client.get("/api/records/AXN-0000-0000/certificate").status_code == 404


def test_the_public_overview_counts_records_without_naming_anyone(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    issue(client)

    body = client.get("/api/records").json()
    issued = db.scalars(select(ParticipationRecord)).all()
    assert body["issued"]["total"] == len(issued) > 0
    assert body["issued"]["total"] == client.get("/api/admin/records").json()["counts"]["total"]
    assert set(body["issued"]["by_kind"]) <= {"judge", "team", "participant"}

    # A verified record is public, the roster behind it is not: an address in a
    # public listing is a privacy leak that no amount of signing fixes.
    assert "records" not in body
    assert "@" not in str(body["issued"])
    assert body["key"]["published"] is False, "the tests run inside an open event window"
