"""Outbound webhooks, managed from the organiser console (T4).

The receiver's side of the contract is documented where it is implemented — see
`app/webhooks.py` for the signature scheme — and the catalogue is served here so a
subscriber can discover the event names rather than guess them.

Two behaviours are deliberate and will surprise anyone expecting a different
product:

* **Removing an endpoint deactivates it by default.** A `DELETE` that threw away the
  delivery history of an endpoint that was failing would destroy the evidence of the
  failure at exactly the moment someone wants to look at it. `?purge=true` is there
  for a genuine mistake, and says so.
* **Nothing is delivered by a background worker, because there is no worker.**
  `POST /api/admin/webhooks/dispatch` flushes the queue, the offline workflow calls
  it, and a deployment with no network simply accumulates pending deliveries that
  the console lists. That is the one-command rule applied to outbound calls.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, webhooks
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import User, WebhookDelivery, WebhookEndpoint
from ..schemas import WebhookEndpointCreateRequest, WebhookEndpointUpdateRequest

router = APIRouter(prefix="/api/admin/webhooks", tags=["webhooks"])


def _queue(db: Session) -> dict:
    counts = dict(
        db.execute(
            select(WebhookDelivery.status, func.count(WebhookDelivery.id)).group_by(
                WebhookDelivery.status
            )
        ).all()
    )
    return {
        "pending": counts.get("pending", 0),
        "delivered": counts.get("delivered", 0),
        "failed": counts.get("failed", 0),
        "dead": counts.get("dead", 0),
    }


def _endpoint_or_404(db: Session, endpoint_id: int) -> WebhookEndpoint:
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return endpoint


@router.get("")
def list_endpoints(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Every endpoint, the catalogue, and how the queue is doing."""
    endpoints = db.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.id)).all()
    failed = db.scalar(
        select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "dead")
    ) or 0
    return {
        "endpoints": [
            webhooks.serialize_endpoint(endpoint, reveal_secret=True) for endpoint in endpoints
        ],
        "catalogue": [
            {"event": name, "description": description}
            for name, description in sorted(webhooks.EVENT_CATALOGUE.items())
        ],
        "queue": _queue(db),
        "signature": {
            "header": "X-Axion-Signature",
            "timestamp_header": "X-Axion-Timestamp",
            "delivery_header": "X-Axion-Delivery",
            "scheme": "sha256=<hex> over '<timestamp>.<body>' with the endpoint secret",
            "tolerance_seconds": 300,
        },
        "delivery": {
            "attempts": webhooks.MAX_ATTEMPTS,
            "backoff_seconds": list(webhooks.BACKOFF_SECONDS),
            "timeout_seconds": webhooks.TIMEOUT_SECONDS,
            "worker": "none — POST /api/admin/webhooks/dispatch flushes the queue",
        },
        "dead_letters": failed,
    }


@router.get("/catalogue")
def catalogue(user: User = Depends(require_role("admin"))) -> dict:
    return {
        "events": [
            {"event": name, "description": description}
            for name, description in sorted(webhooks.EVENT_CATALOGUE.items())
        ],
        "api_version": webhooks.API_VERSION,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_endpoint(
    payload: WebhookEndpointCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Register a receiver. The secret is returned once, and readable later on demand."""
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="The url must be http:// or https://")

    unknown = [name for name in (payload.events or []) if name not in webhooks.EVENT_CATALOGUE]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown events: {', '.join(sorted(unknown))}. GET /catalogue for the list.",
        )

    endpoint = WebhookEndpoint(
        url=payload.url,
        description=payload.description,
        secret=webhooks.new_secret(),
        events=list(payload.events or []),
        active=payload.active,
        created_by=user.email,
    )
    db.add(endpoint)
    db.flush()
    audit.record(
        db,
        "webhook.endpoint_created",
        actor=user,
        entity="webhook_endpoint",
        entity_id=endpoint.id,
        ip=client_ip(request),
        details={"url": endpoint.url, "events": endpoint.events or "all"},
    )
    db.commit()
    return {
        "endpoint": webhooks.serialize_endpoint(endpoint, reveal_secret=True),
        "note": (
            "Verify deliveries with the secret above: sha256=<hex> over "
            "'<X-Axion-Timestamp>.<raw body>'. Anything else is not from this deployment."
        ),
    }


@router.patch("/{endpoint_id}")
def update_endpoint(
    endpoint_id: int,
    payload: WebhookEndpointUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    endpoint = _endpoint_or_404(db, endpoint_id)
    changes: dict = {}

    if payload.events is not None:
        unknown = [name for name in payload.events if name not in webhooks.EVENT_CATALOGUE]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown events: {', '.join(unknown)}")
        changes["events"] = list(payload.events)
        endpoint.events = list(payload.events)
    if payload.description is not None:
        endpoint.description = payload.description
    if payload.active is not None:
        endpoint.active = payload.active
        changes["active"] = payload.active
    if payload.rotate_secret:
        # Rotation is a repair action: the old secret stops verifying immediately, so
        # the receiver has to be updated in the same breath.
        endpoint.secret = webhooks.new_secret()
        changes["secret_rotated"] = True

    audit.record(
        db,
        "webhook.endpoint_updated",
        actor=user,
        entity="webhook_endpoint",
        entity_id=endpoint.id,
        ip=client_ip(request),
        details=changes,
    )
    db.commit()
    return {"endpoint": webhooks.serialize_endpoint(endpoint, reveal_secret=True), "changes": changes}


@router.delete("/{endpoint_id}")
def remove_endpoint(
    endpoint_id: int,
    request: Request,
    purge: bool = Query(default=False, description="Also discard its delivery history"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Deactivate (default) or delete an endpoint.

    Deactivating keeps the deliveries, which is what an organiser wants when a
    receiver is misbehaving: the evidence stays. `purge=true` removes the endpoint
    and its history together, and is audited as such.
    """
    endpoint = _endpoint_or_404(db, endpoint_id)
    if purge:
        audit.record(
            db,
            "webhook.endpoint_purged",
            actor=user,
            entity="webhook_endpoint",
            entity_id=endpoint.id,
            ip=client_ip(request),
            details={"url": endpoint.url, "deliveries": _queue(db)},
        )
        # The deliveries go first, explicitly. The foreign key declares
        # `ON DELETE CASCADE`, and Postgres would honour it, but SQLite does not
        # enforce foreign keys unless it is asked to — and the offline path is
        # SQLite. A purge that left orphaned rows on one backend and not the other
        # is exactly the kind of divergence this codebase refuses everywhere else.
        for delivery in db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id)
        ).all():
            db.delete(delivery)
        db.delete(endpoint)
        db.commit()
        return {"purged": True, "endpoint_id": endpoint_id}

    endpoint.active = False
    audit.record(
        db,
        "webhook.endpoint_deactivated",
        actor=user,
        entity="webhook_endpoint",
        entity_id=endpoint.id,
        ip=client_ip(request),
        details={"url": endpoint.url},
    )
    db.commit()
    return {
        "purged": False,
        "endpoint": webhooks.serialize_endpoint(endpoint, reveal_secret=True),
        "note": "Deactivated. Its deliveries are kept; delete with ?purge=true to discard them.",
    }


@router.get("/deliveries")
def list_deliveries(
    endpoint_id: int | None = Query(default=None),
    event: str | None = Query(default=None, max_length=80),
    delivery_status: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=500),
    include_payload: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    statement = select(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(limit)
    if endpoint_id is not None:
        statement = statement.where(WebhookDelivery.endpoint_id == endpoint_id)
    if event:
        statement = statement.where(WebhookDelivery.event == event)
    if delivery_status:
        statement = statement.where(WebhookDelivery.status == delivery_status)
    rows = db.scalars(statement).all()
    return {
        "deliveries": [
            webhooks.serialize_delivery(row, include_payload=include_payload) for row in rows
        ],
        "queue": _queue(db),
    }


@router.get("/deliveries/{delivery_id}")
def delivery_detail(
    delivery_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """One delivery, including the exact bytes that were signed."""
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {"delivery": webhooks.serialize_delivery(delivery, include_payload=True)}


@router.post("/deliveries/{delivery_id}/redeliver")
def redeliver(
    delivery_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Requeue a delivery, keeping its id and its signed bytes."""
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    webhooks.redeliver(db, delivery)
    audit.record(
        db,
        "webhook.redelivered",
        actor=user,
        entity="webhook_delivery",
        entity_id=delivery.id,
        ip=client_ip(request),
        details={"event": delivery.event, "endpoint_id": delivery.endpoint_id},
    )
    db.commit()
    return {
        "delivery": webhooks.serialize_delivery(delivery),
        "note": "Requeued with the same id and the same signature, so a receiver that dedupes will ignore it.",
    }


@router.post("/dispatch")
def dispatch(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    endpoint_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Flush due deliveries now. This is the only thing that sends anything."""
    result = webhooks.dispatch(db, limit=limit, endpoint_id=endpoint_id)
    if result["attempted"]:
        audit.record(
            db,
            "webhook.dispatched",
            actor=user,
            entity="webhook_delivery",
            ip=client_ip(request),
            details={
                "attempted": result["attempted"],
                "statuses": sorted({row["status"] for row in result["outcomes"]}),
            },
        )
    db.commit()
    return result


@router.post("/{endpoint_id}/test")
def test_endpoint(
    endpoint_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Queue a `webhook.test` delivery so a receiver can be proven end to end.

    The payload carries a fixed marker rather than live data, so a receiver's
    integration test does not have to reason about which event it is seeing.
    """
    endpoint = _endpoint_or_404(db, endpoint_id)
    # `emit` fans out to every subscribed endpoint; a test is addressed to exactly
    # one, so it is addressed rather than created and then withdrawn.
    created = webhooks.emit(
        db,
        webhooks.EVENT_WEBHOOK_TEST,
        {
            "message": "This is a test delivery from Axion.",
            "requested_by": user.email,
            "endpoint_id": endpoint.id,
            "signature_scheme": "sha256=<hex> over '<timestamp>.<body>'",
        },
        only_endpoint_id=endpoint.id,
    )
    audit.record(
        db,
        "webhook.tested",
        actor=user,
        entity="webhook_endpoint",
        entity_id=endpoint.id,
        ip=client_ip(request),
        details={"queued": len(created)},
    )
    db.commit()
    return {
        "queued": [webhooks.serialize_delivery(row) for row in created],
        "next": "POST /api/admin/webhooks/dispatch to send it now, or wait for a flush.",
    }
