"""The webhook outbox (T4): signed, retryable, never in the writer's way.

The claims being tested are the ones a subscriber would care about:

* a delivery is signed, and the receiver can check it without asking us;
* a retry carries the same bytes and the same id as the first attempt, so a
  receiver can dedupe instead of double-counting;
* an endpoint that is down for the afternoon is retried, and one that is gone is
  `dead` and replayable rather than silently lost;
* nothing here can fail a participant's write — emission is a row insert, and the
  HTTP call happens later.

Transport is stubbed wherever the retry policy is under test: the interesting
behaviour is what happens between attempts, not what a socket does.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import webhooks
from app.models import WebhookDelivery, WebhookEndpoint


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class Recorder:
    """Stands in for the network, and can be made to fail on demand."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses) or [200]
        self.calls: list[dict] = []

    def __call__(self, url: str, body: bytes, headers: dict) -> FakeResponse:
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        status = self.statuses[min(len(self.calls) - 1, len(self.statuses) - 1)]
        if status == 0:
            raise ConnectionError("receiver is unreachable")
        return FakeResponse(status)


def an_endpoint(db, *, url="https://receiver.example.org/hooks", events=None, active=True):
    endpoint = WebhookEndpoint(
        url=url,
        description="test receiver",
        secret=webhooks.new_secret(),
        events=events or [],
        active=active,
        created_by="organiser@test.dev",
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return endpoint


def    admin_headers(client, make_user, auth) -> dict:

    user = make_user("admin", email="organiser@test.dev")
    auth(user.email)
    return {}


# ── the contract ────────────────────────────────────────────────────────────


def test_the_catalogue_and_the_event_constants_are_the_same_set():
    constants = {
        value
        for name, value in vars(webhooks).items()
        if name.startswith("EVENT_") and isinstance(value, str)
    }

    assert constants == set(webhooks.EVENT_CATALOGUE)
    assert all(description.strip() for description in webhooks.EVENT_CATALOGUE.values())


def test_emitting_an_unknown_event_is_an_error_not_a_silent_delivery(db):
    with pytest.raises(ValueError):
        webhooks.emit(db, "submission.exploded", {})


def test_a_subscriber_only_hears_the_events_it_asked_for(db):
    subscribed = an_endpoint(db, url="https://a.example.org/hooks", events=[webhooks.EVENT_VOTE_CAST])
    everything = an_endpoint(db, url="https://b.example.org/hooks")

    webhooks.emit(db, webhooks.EVENT_VOTE_CAST, {"submission_id": 1})
    webhooks.emit(db, webhooks.EVENT_COMMENT_CREATED, {"comment_id": 2})
    db.commit()

    rows = db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.id)).all()
    assert [(row.endpoint_id, row.event) for row in rows] == [
        (subscribed.id, webhooks.EVENT_VOTE_CAST),
        (everything.id, webhooks.EVENT_VOTE_CAST),
        (everything.id, webhooks.EVENT_COMMENT_CREATED),
    ]


def test_a_deactivated_endpoint_is_not_sent_anything(db):
    an_endpoint(db, active=False)

    assert webhooks.emit(db, webhooks.EVENT_VOTE_CAST, {}) == []


def test_the_signature_covers_the_body_and_the_timestamp(db):
    endpoint = an_endpoint(db)
    delivery = webhooks.emit(db, webhooks.EVENT_VOTE_CAST, {"submission_id": 7})[0]
    db.commit()

    payload = delivery.payload
    body = webhooks.body_bytes(payload)
    stamp = payload["timestamp"]

    assert webhooks.sign(endpoint.secret, stamp, body) == delivery.signature
    assert webhooks.verify_signature(endpoint.secret, stamp, body, delivery.signature)["valid"]

    # A different endpoint's secret must not validate it: the signature identifies
    # the sender, not merely that *some* Axion deployment produced the body.
    other = webhooks.verify_signature("nope", stamp, body, delivery.signature)
    assert other["valid"] is False
    assert "does not match" in other["reason"]

    # And an old delivery cannot be replayed forever.
    stale = webhooks.verify_signature(
        endpoint.secret,
        stamp,
        body,
        delivery.signature,
        now=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    assert stale["valid"] is False
    assert "tolerance" in stale["reason"]


# ── delivering ──────────────────────────────────────────────────────────────


def test_a_delivery_reports_the_receivers_status_and_clears_its_failure_streak(db):
    endpoint = an_endpoint(db)
    recorder = Recorder(200)
    webhooks.emit(db, webhooks.EVENT_VOTE_CAST, {"submission_id": 1})
    db.commit()

    result = webhooks.dispatch(db, sender=recorder)
    db.commit()

    assert result["attempted"] == 1
    assert result["outcomes"][0]["status"] == "delivered"
    assert recorder.calls[0]["url"] == endpoint.url
    assert recorder.calls[0]["headers"]["X-Axion-Event"] == webhooks.EVENT_VOTE_CAST
    assert recorder.calls[0]["headers"]["X-Axion-Signature"].startswith("sha256=")
    db.refresh(endpoint)
    assert endpoint.failure_count == 0
    assert endpoint.last_delivered_at is not None


def test_a_retry_carries_the_same_id_and_the_same_bytes(db):
    an_endpoint(db)
    recorder = Recorder(503, 200)
    webhooks.emit(db, webhooks.EVENT_VOTE_CAST, {"submission_id": 1})
    db.commit()

    webhooks.dispatch(db, sender=recorder)
    db.commit()
    delivery = db.scalars(select(WebhookDelivery)).one()
    first_body, first_signature, first_id = recorder.calls[0]["body"], delivery.signature, delivery.payload["id"]
    assert delivery.status == "pending", "a 5xx is a fact to retry, not a failure to accept"

    # The backoff window has to be honoured, or a receiver that is down gets
    # hammered instead of retried.
    assert webhooks.dispatch(db, sender=recorder)["attempted"] == 0

    later = datetime.now(timezone.utc) + webhooks.backoff_for(1) + timedelta(seconds=1)
    webhooks.dispatch(db, sender=recorder, now=later)
    db.commit()

    assert recorder.calls[1]["body"] == first_body
    assert delivery.payload["id"] == first_id
    assert delivery.attempts == 2
    assert delivery.status == "delivered"


def test_an_endpoint_that_never_answers_goes_dead_rather_than_being_lost(db):
    endpoint = an_endpoint(db)
    recorder = Recorder(0)  # unreachable
    webhooks.emit(db, webhooks.EVENT_COMMENT_CREATED, {"comment_id": 1})
    db.commit()

    moment = datetime.now(timezone.utc)
    for _ in range(webhooks.MAX_ATTEMPTS):
        webhooks.dispatch(db, sender=recorder, now=moment)
        moment += timedelta(hours=2)
    db.commit()

    delivery = db.scalars(select(WebhookDelivery)).one()
    assert delivery.status == "dead"
    assert delivery.attempts == webhooks.MAX_ATTEMPTS
    assert "unreachable" in delivery.error
    db.refresh(endpoint)
    assert endpoint.failure_count == webhooks.MAX_ATTEMPTS

    # Dead letters are listed rather than hidden, and replayable.
    webhooks.redeliver(db, delivery)
    db.commit()
    assert delivery.status == "pending"
    assert delivery.attempts == 0

    webhooks.dispatch(db, sender=Recorder(200))
    db.commit()
    assert delivery.status == "delivered"
    db.refresh(endpoint)
    assert endpoint.failure_count == 0, "a working endpoint is not permanently marked"


def test_the_backoff_grows_then_holds():
    delays = [webhooks.backoff_for(attempt).total_seconds() for attempt in range(1, 8)]

    assert delays[0] < delays[1] < delays[2]
    assert delays[-1] == delays[-2], "retries must not back off forever"


# ── the console ─────────────────────────────────────────────────────────────


def test_only_an_organiser_can_manage_endpoints(client, make_user, auth):
    participant = make_user("participant", email="p@test.dev")
    auth(participant.email)
    assert client.get("/api/admin/webhooks").status_code == 403
    assert client.post("/api/admin/webhooks", json={"url": "https://x.example.org"}).status_code == 403


def test_registering_an_endpoint_returns_the_secret_and_refuses_a_non_http_url(client, make_user, auth, db):
    admin_headers(client, make_user, auth)

    bad = client.post("/api/admin/webhooks", json={"url": "ftp://receiver.example.org"})
    assert bad.status_code == 400

    unknown = client.post(
        "/api/admin/webhooks",
        json={"url": "https://receiver.example.org/hooks", "events": ["submission.melted"]},
    )
    assert unknown.status_code == 400
    assert "Unknown events" in unknown.json()["detail"]

    created = client.post(
        "/api/admin/webhooks",
        json={"url": "https://receiver.example.org/hooks", "events": ["vote.cast"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()["endpoint"]
    assert len(body["secret"]) == 48
    assert body["events"] == ["vote.cast"]

    # A secret nobody can read is a secret nobody can configure a receiver with,
    # and the signature scheme travels beside it so the docs cannot drift.
    listed = client.get("/api/admin/webhooks").json()
    assert listed["endpoints"][0]["secret"] == body["secret"]
    assert "sha256=" in listed["signature"]["scheme"]
    assert listed["queue"]["pending"] == 0


def test_a_test_delivery_goes_to_one_endpoint_only(client, make_user, auth, db):
    admin_headers(client, make_user, auth)
    first = client.post("/api/admin/webhooks", json={"url": "https://a.example.org/h"}).json()["endpoint"]
    client.post("/api/admin/webhooks", json={"url": "https://b.example.org/h"})

    response = client.post(f"/api/admin/webhooks/{first['id']}/test")
    assert response.status_code == 200, response.text

    rows = db.scalars(select(WebhookDelivery)).all()
    assert [(row.endpoint_id, row.event) for row in rows] == [(first["id"], "webhook.test")]


def test_the_console_can_flush_the_queue_with_a_stubbed_receiver(client, make_user, auth, db, monkeypatch):
    admin_headers(client, make_user, auth)
    recorder = Recorder(200)
    monkeypatch.setattr(webhooks, "default_sender", recorder)
    endpoint = client.post("/api/admin/webhooks", json={"url": "https://a.example.org/h"}).json()["endpoint"]
    client.post(f"/api/admin/webhooks/{endpoint['id']}/test")

    dispatched = client.post("/api/admin/webhooks/dispatch").json()
    assert dispatched["attempted"] == 1
    assert dispatched["queue"]["pending"] == 0

    detail = client.get(f"/api/admin/webhooks/deliveries/{dispatched['outcomes'][0]['delivery_id']}").json()[
        "delivery"
    ]
    assert detail["payload"]["event"] == "webhook.test"
    assert detail["signature"].startswith("sha256=")


def test_deleting_an_endpoint_deactivates_it_and_keeps_its_history(client, make_user, auth, db):
    admin_headers(client, make_user, auth)
    endpoint = client.post("/api/admin/webhooks", json={"url": "https://a.example.org/h"}).json()["endpoint"]
    client.post(f"/api/admin/webhooks/{endpoint['id']}/test")

    removed = client.delete(f"/api/admin/webhooks/{endpoint['id']}").json()
    assert removed["purged"] is False
    assert removed["endpoint"]["active"] is False
    assert db.scalars(select(WebhookDelivery)).all(), "the evidence of the failure is the point of keeping it"

    purged = client.delete(f"/api/admin/webhooks/{endpoint['id']}?purge=true").json()
    assert purged["purged"] is True
    assert db.scalars(select(WebhookDelivery)).all() == []


def test_a_participants_write_emits_a_webhook(client, make_user, auth, db):
    """The wiring, end to end: a submission is announced, and a draft stays a draft."""
    admin_headers(client, make_user, auth)
    endpoint = client.post(
        "/api/admin/webhooks",
        json={"url": "https://a.example.org/h", "events": ["submission.created", "submission.submitted"]},
    ).json()["endpoint"]

    from app.models import Team, TeamMember

    participant = make_user("participant", email="p@test.dev")
    team = Team(name="Webhook Team", invite_code="HOOK0001", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))
    db.commit()
    auth(participant.email)

    body = {
        "title": "Hooked Project",
        "repo_url": "https://github.com/hooked/project",
        "summary": "A project that announces itself.",
        "status": "draft",
    }

    draft = client.post("/api/submissions", json=body)
    assert draft.status_code == 200, draft.text
    submitted = client.post("/api/submissions", json={**body, "status": "submitted"})
    assert submitted.status_code == 200, submitted.text

    rows = db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.id)).all()
    assert [row.event for row in rows] == ["submission.created", "submission.submitted"]
    assert all(row.endpoint_id == endpoint["id"] for row in rows)
    assert rows[1].payload["data"]["title"] == "Hooked Project"
