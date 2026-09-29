"""Outbound webhooks (T4): a durable outbox with signed, retryable deliveries.

Why an outbox rather than a fire-and-forget POST inside the request:

* **A webhook must never be able to fail a participant's write.** Emitting a
  delivery is a row insert in the same transaction as the thing that happened, so
  either both are recorded or neither is. The HTTP call happens later, from
  `dispatch`, and a receiver that is down cannot turn a successful submission into
  a 500.
* **Retries have to be byte-identical.** The envelope is built once, at emit time,
  and never rebuilt, so a retry carries the same `id` and the same bytes. A
  receiver can dedupe on `X-Axion-Delivery` and verify the signature without
  re-deriving the body — both of which are impossible if the payload is
  re-serialised per attempt.
* **No hosted worker.** There is nothing scheduling these, on purpose: the rules
  forbid a cloud dependency, so `POST /api/admin/webhooks/dispatch` flushes the
  queue and the offline workflow calls it. A deployment that is entirely offline
  still completes every write; it simply has deliveries pending, which is visible
  in the console.

Every delivery is signed with the endpoint's own secret, using the scheme in
`sign()`. `verify_signature()` is the receiver's side of the same function, kept
here so the algorithm has exactly one implementation and the documentation cannot
drift from it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import WebhookDelivery, WebhookEndpoint

API_VERSION = "1"

# Five attempts over roughly 70 minutes. A receiver that is briefly down (a
# deploy, a restart) recovers without an organiser touching anything; one that is
# gone for an hour is `dead`, listed, and replayable rather than silently lost.
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (30, 120, 600, 3600)
TIMEOUT_SECONDS = 5.0
RESPONSE_BODY_LIMIT = 500

# The catalogue, and the constants call sites use. Webhook consumers subscribe to
# these names, so they are an interface: renaming one is a breaking change and the
# test suite pins the set.
EVENT_SUBMISSION_CREATED = "submission.created"
EVENT_SUBMISSION_SUBMITTED = "submission.submitted"
EVENT_SCORE_SUBMITTED = "score.submitted"
EVENT_SCORE_MODIFIED = "score.modified"
EVENT_PRESENTATION_SUBMITTED = "presentation.submitted"
EVENT_VOTE_CAST = "vote.cast"
EVENT_COMMENT_CREATED = "comment.created"
EVENT_DUPLICATE_CONFIRMED = "duplicate.confirmed"
EVENT_EVENT_ARCHIVED = "event.archived"
# The one event about the event itself: a deadline moved, or the clock handed back
# to the deployment's configuration. A subscriber's status board has as much need
# of this as of a submission, and finding out by watching a submission bounce is
# not how a team should learn that the deadline moved.
EVENT_SETTINGS_UPDATED = "event.settings_updated"
EVENT_WEBHOOK_TEST = "webhook.test"

EVENT_CATALOGUE: dict[str, str] = {
    EVENT_SUBMISSION_CREATED: "A project was saved as a draft. Drafts are not public.",
    EVENT_SUBMISSION_SUBMITTED: "A project entered the event: integrity check ran, judges were assigned.",
    EVENT_SCORE_SUBMITTED: "A judge filed a technical verdict.",
    EVENT_SCORE_MODIFIED: "A judge changed a verdict they had already filed.",
    EVENT_PRESENTATION_SUBMITTED: "A judge filed a presentation verdict, having filed the technical one first.",
    EVENT_VOTE_CAST: "A community vote was cast.",
    EVENT_COMMENT_CREATED: "Someone commented on a project.",
    EVENT_DUPLICATE_CONFIRMED: "An organiser confirmed a suspected duplicate. Nothing was deleted.",
    EVENT_EVENT_ARCHIVED: "The results bundle was generated.",
    EVENT_SETTINGS_UPDATED: "An organiser changed the event's window or identity.",
    EVENT_WEBHOOK_TEST: "A test delivery an organiser triggered from the console.",
}


def new_secret() -> str:
    return secrets.token_hex(24)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── the wire format ─────────────────────────────────────────────────────────


def envelope(delivery_id: int, event: str, data: dict, *, at: Optional[datetime] = None) -> dict:
    """The body of a delivery. Built once, then stored and reused verbatim."""
    moment = at or now_utc()
    return {
        "id": f"dlv_{delivery_id}",
        "event": event,
        "api_version": API_VERSION,
        # Seconds, so a receiver can reject a replayed old delivery without parsing
        # the ISO string; both are signed.
        "timestamp": int(moment.timestamp()),
        "created_at": moment.isoformat(),
        "data": data,
    }


def body_bytes(payload: dict) -> bytes:
    """Canonical JSON: sorted keys, no incidental whitespace.

    A signature is over bytes, so two serialisations of the same payload must be
    the same bytes. This is the one place that decides how a delivery body is
    written.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """`sha256=<hex>` over `timestamp.body`, the scheme in `docs/` and in the README.

    The timestamp is inside the signed material on purpose: a signature that
    covered only the body would validate forever, so a delivery captured once could
    be replayed at any time. Receivers should also reject anything older than a few
    minutes — `verify_signature` does, and says so.
    """
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify_signature(
    secret: str,
    timestamp: int | str,
    body: bytes,
    signature: str,
    *,
    tolerance_seconds: int | None = 300,
    now: Optional[datetime] = None,
) -> dict:
    """The receiver's half of `sign`, with the timestamp check included.

    Returned as a dict rather than a bool so a receiver can log *why* it refused:
    "the signature is wrong" and "the signature is right but nine hours old" are
    different incidents.
    """
    result = {"valid": False, "reason": None, "age_seconds": None}
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        result["reason"] = "timestamp is not an integer"
        return result

    moment = now or now_utc()
    age = int(moment.timestamp()) - stamp
    result["age_seconds"] = age
    if tolerance_seconds is not None and abs(age) > tolerance_seconds:
        result["reason"] = f"timestamp outside the {tolerance_seconds}s tolerance"
        return result

    expected = sign(secret, stamp, body)
    if not hmac.compare_digest(expected, signature or ""):
        result["reason"] = "signature does not match the body"
        return result
    result["valid"] = True
    return result


# ── emitting ────────────────────────────────────────────────────────────────


def emit(
    db: Session,
    event: str,
    data: dict,
    *,
    at: Optional[datetime] = None,
    only_endpoint_id: Optional[int] = None,
) -> list[WebhookDelivery]:
    """Record one event for every subscribed endpoint.

    The name is validated against the catalogue: a typo here would create a
    delivery nobody subscribed to and that nothing documents, which is worse than
    an error during a test. Every call site uses the constants above, so the
    failure mode is unreachable in practice — and `test_webhooks.py` proves the
    constants and the catalogue are the same set.
    """
    if event not in EVENT_CATALOGUE:
        raise ValueError(f"unknown webhook event: {event!r}")

    statement = select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))
    if only_endpoint_id is not None:
        statement = statement.where(WebhookEndpoint.id == only_endpoint_id)
    endpoints = db.scalars(statement).all()
    created: list[WebhookDelivery] = []
    for endpoint in endpoints:
        subscribed = list(endpoint.events or [])
        if subscribed and event not in subscribed:
            continue
        delivery = WebhookDelivery(endpoint_id=endpoint.id, event=event, status="pending")
        db.add(delivery)
        db.flush()  # for the id, which is part of the envelope
        payload = envelope(delivery.id, event, data, at=at)
        delivery.payload = payload
        delivery.signature = sign(endpoint.secret, payload["timestamp"], body_bytes(payload))
        delivery.next_attempt_at = at or now_utc()
        created.append(delivery)
    if created:
        db.flush()
    return created


# ── delivering ──────────────────────────────────────────────────────────────


def backoff_for(attempts: int) -> timedelta:
    index = max(0, min(attempts - 1, len(BACKOFF_SECONDS) - 1))
    return timedelta(seconds=BACKOFF_SECONDS[index])


def default_sender(url: str, body: bytes, headers: dict[str, str]):
    """POST the delivery. Raises only on a transport failure, never on a 4xx/5xx.

    A 500 from a receiver is a fact to record and retry, not an exception to
    propagate: the retry policy needs the status code.
    """
    import httpx

    return httpx.post(url, content=body, headers=headers, timeout=TIMEOUT_SECONDS)


Sender = Callable[[str, bytes, dict], object]


def attempt(db: Session, delivery: WebhookDelivery, sender: Sender, *, at: Optional[datetime] = None) -> str:
    """One delivery attempt. Returns the delivery's resulting status."""
    moment = at or now_utc()
    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None:
        delivery.status = "dead"
        delivery.error = "the endpoint was removed"
        return delivery.status

    payload = dict(delivery.payload or {})
    body = body_bytes(payload)
    signature = delivery.signature or sign(endpoint.secret, payload["timestamp"], body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Axion-Webhooks/1",
        "X-Axion-Event": delivery.event,
        "X-Axion-Delivery": str(payload.get("id")),
        "X-Axion-Timestamp": str(payload.get("timestamp")),
        "X-Axion-Signature": signature,
    }

    delivery.attempts += 1
    delivery.signature = signature
    try:
        response = sender(endpoint.url, body, headers)
        status_code = getattr(response, "status_code", None)
        text = (getattr(response, "text", "") or "")[:RESPONSE_BODY_LIMIT]
    except Exception as exc:  # noqa: BLE001 - any transport failure is retryable
        status_code = None
        text = ""
        delivery.error = f"{type(exc).__name__}: {exc}"[:500]

    delivered = status_code is not None and 200 <= int(status_code) < 300
    if delivered:
        delivery.status = "delivered"
        delivery.delivered_at = moment
        delivery.response_status = int(status_code)
        delivery.response_body = text
        delivery.error = None
        delivery.next_attempt_at = None
        endpoint.last_delivered_at = moment
        # A working endpoint clears its streak: the count means "currently failing",
        # not "has ever failed".
        endpoint.failure_count = 0
    else:
        delivery.response_status = int(status_code) if status_code is not None else None
        delivery.response_body = text or None
        delivery.next_attempt_at = moment + backoff_for(delivery.attempts)
        endpoint.last_failed_at = moment
        endpoint.failure_count = (endpoint.failure_count or 0) + 1
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = "dead"
        else:
            delivery.status = "pending"
    db.flush()
    return delivery.status


def dispatch(
    db: Session,
    *,
    limit: int = 20,
    sender: Optional[Sender] = None,
    now: Optional[datetime] = None,
    endpoint_id: Optional[int] = None,
) -> dict:
    """Flush due deliveries. Safe to call as often as you like; it is idempotent.

    `sender` is injectable so the test suite can exercise the retry policy — the
    interesting behaviour — without a socket.
    """
    moment = now or now_utc()
    send = sender or default_sender

    statement = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == "pending",
            (WebhookDelivery.next_attempt_at.is_(None))
            | (WebhookDelivery.next_attempt_at <= moment),
        )
        .order_by(WebhookDelivery.id)
        .limit(limit)
    )
    if endpoint_id is not None:
        statement = statement.where(WebhookDelivery.endpoint_id == endpoint_id)

    due = db.scalars(statement).all()
    outcomes: list[dict] = []
    for delivery in due:
        status = attempt(db, delivery, send, at=moment)
        outcomes.append(
            {
                "delivery_id": delivery.id,
                "endpoint_id": delivery.endpoint_id,
                "event": delivery.event,
                "status": status,
                "attempts": delivery.attempts,
                "response_status": delivery.response_status,
                "error": delivery.error,
            }
        )

    pending = db.scalar(
        select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "pending")
    ) or 0
    dead = db.scalar(
        select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "dead")
    ) or 0
    return {
        "attempted": len(outcomes),
        "outcomes": outcomes,
        "queue": {"pending": pending, "dead": dead},
        "backoff_seconds": list(BACKOFF_SECONDS),
        "max_attempts": MAX_ATTEMPTS,
    }


def redeliver(db: Session, delivery: WebhookDelivery, *, at: Optional[datetime] = None) -> WebhookDelivery:
    """Put a dead or delivered delivery back in the queue, keeping its id and body.

    Replay exists because the alternative is manual: an organiser who fixes their
    receiver should be able to resend what was missed, and a receiver that dedupes
    on the delivery id will ignore the copy it already processed.
    """
    delivery.status = "pending"
    delivery.next_attempt_at = at or now_utc()
    delivery.attempts = 0
    delivery.error = None
    db.flush()
    return delivery


# ── serialisation ───────────────────────────────────────────────────────────


def serialize_endpoint(endpoint: WebhookEndpoint, *, reveal_secret: bool = False) -> dict:
    return {
        "id": endpoint.id,
        "url": endpoint.url,
        "description": endpoint.description,
        "events": list(endpoint.events or []) or "all",
        "active": endpoint.active,
        "created_by": endpoint.created_by,
        "failure_count": endpoint.failure_count,
        "last_delivered_at": endpoint.last_delivered_at.isoformat() if endpoint.last_delivered_at else None,
        "last_failed_at": endpoint.last_failed_at.isoformat() if endpoint.last_failed_at else None,
        "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
        # Shown once at creation, and on demand to the organiser who created it: a
        # secret nobody can read is a secret nobody can configure a receiver with.
        "secret": endpoint.secret if reveal_secret else None,
        "signature_header": "X-Axion-Signature",
        "signature_scheme": "sha256=<hex> over '<timestamp>.<body>' with the endpoint secret",
    }


def serialize_delivery(delivery: WebhookDelivery, *, include_payload: bool = False) -> dict:
    row = {
        "id": delivery.id,
        "endpoint_id": delivery.endpoint_id,
        "event": delivery.event,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "response_status": delivery.response_status,
        "error": delivery.error,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
        "next_attempt_at": delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None,
        "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
    }
    if include_payload:
        row["payload"] = delivery.payload
        row["signature"] = delivery.signature
        row["response_body"] = delivery.response_body
    return row


__all__ = [
    "API_VERSION",
    "BACKOFF_SECONDS",
    "EVENT_CATALOGUE",
    "EVENT_COMMENT_CREATED",
    "EVENT_DUPLICATE_CONFIRMED",
    "EVENT_EVENT_ARCHIVED",
    "EVENT_PRESENTATION_SUBMITTED",
    "EVENT_SCORE_MODIFIED",
    "EVENT_SCORE_SUBMITTED",
    "EVENT_SUBMISSION_CREATED",
    "EVENT_SUBMISSION_SUBMITTED",
    "EVENT_VOTE_CAST",
    "EVENT_WEBHOOK_TEST",
    "MAX_ATTEMPTS",
    "attempt",
    "backoff_for",
    "body_bytes",
    "default_sender",
    "dispatch",
    "emit",
    "envelope",
    "new_secret",
    "redeliver",
    "serialize_delivery",
    "serialize_endpoint",
    "sign",
    "verify_signature",
]
