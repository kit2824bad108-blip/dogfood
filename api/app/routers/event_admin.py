"""The organiser's clock: read it, move it, hand it back.

Four endpoints under `/api/admin/event`, all organiser-only, all audited:

    GET     the effective window, where it came from, and what it implies
    PATCH   move it (or take it over), with a revision check and an impact preview
    DELETE  hand it back to the deployment's configuration
    GET     the change log, from the append-only audit trail

Three decisions shape this module, and each one is a rule rather than a
convenience:

**A partial change is partial.** PATCH with only `ends_at` moves the deadline and
leaves the ballot window exactly where it was. Materialising a fresh row for every
change is what makes that true: the row is the organiser's complete statement of
the window, so no reader has to ask which fields were overridden.

**A change is previewed before it is applied.** The response carries `impact` —
whether submissions are open before and after, whether community results and the
signed-record key become public, how many projects already carry a `submitted_at`
after the new deadline — as sentences, not flags. All of it is *descriptive*:
re-opening a deadline is a thing an organiser is entitled to do, and a console
that forbade it would be the wrong console. The point is that they do it knowing.

**A change is recorded, and can be raced.** `revision` is optimistic concurrency:
a form that sends the revision it read gets a 409 rather than silently
overwriting a colleague's edit. Every write also lands in `audit_logs` with the
actor, the IP and the before/after window, and is announced on the webhook
catalogue, so the people affected can be told by their own systems.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, eventconfig, webhooks
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import AuditLog, User
from ..schemas import EventSettingsUpdateRequest
from ..timeutil import iso

router = APIRouter(prefix="/api/admin/event", tags=["admin"])

ADMIN_ROLES = ("admin",)

# The actions this console writes, named once. The change log filters on them, so
# a typo in one place would silently empty the history in another.
ACTION_UPDATED = "event.settings_updated"
ACTION_RESET = "event.settings_reset"


def _snapshot(db: Session) -> dict:
    """Everything the console needs to render the clock as it stands."""
    clock = eventconfig.active(db)
    default = eventconfig.deployment_default()
    return {
        "event": clock.as_dict(),
        # What a fresh deployment of this configuration would have. Shown beside
        # the effective values so "reset" is a promise the organiser can read
        # before pressing it.
        "deployment_default": default.as_dict(),
        "overridden": clock.organiser_set,
        "can_reset": clock.organiser_set,
        "status": _status(db, clock),
        "impact": eventconfig.impact(db, clock),
        "validation": {
            "name_max": eventconfig.MAX_NAME,
            "note_max": eventconfig.MAX_NOTE,
            "timezone": "UTC",
            "rules": [
                "Submissions must close after they open.",
                "Community voting must close after it opens.",
                "The two windows are independent: the crowd may keep voting after the deadline.",
            ],
        },
    }


def _status(db: Session, clock: eventconfig.EventConfig) -> dict:
    """The derived facts an organiser is really asking about."""
    window = eventconfig.window(db)
    ballot = eventconfig.voting_window(db)
    return {
        "now": window["now"],
        "submissions_open": not window["closed"] and not window["not_yet_open"],
        "submissions_closed": window["closed"],
        "submissions_upcoming": window["not_yet_open"],
        "voting_open": ballot["open"],
        "voting_phase": ballot["phase"],
        "results_visible": ballot["results_visible"],
        # Published means *visible to anyone who holds a record*, which is a
        # consequence of closing the event rather than a separate switch.
        "record_key_published": eventconfig.records_verifiable_publicly(db),
    }


@router.get("")
def read_event(
    db: Session = Depends(get_db), user: User = Depends(require_role(*ADMIN_ROLES))
) -> dict:
    """The effective event clock, its provenance, and what it implies."""
    return _snapshot(db)


@router.patch("")
def update_event(
    payload: EventSettingsUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Move the clock, or take it over.

    Any field left out keeps its current effective value. A 409 means somebody
    else wrote first; a 422 means the window cannot mean anything.
    """
    try:
        before, preview, after = eventconfig.apply(
            db,
            name=payload.name,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            voting_opens_at=payload.voting_opens_at,
            voting_closes_at=payload.voting_closes_at,
            note=payload.note,
            expected_revision=payload.expected_revision,
            actor=user,
        )
    except eventconfig.InvalidWindow as refused:
        if refused.field == "revision":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": refused.detail,
                    "current": eventconfig.active(db).as_dict(),
                },
            ) from refused
        raise HTTPException(
            status_code=422,
            detail={"error": refused.detail, "field": refused.field},
        ) from refused

    audit.record(
        db,
        ACTION_UPDATED,
        actor=user,
        entity="event",
        entity_id=eventconfig.SETTINGS_ID,
        ip=client_ip(request),
        details={
            "before": _window_facts(before),
            "after": _window_facts(after),
            "changed": sorted(_differences(before, after)),
            "note": after.note,
            "revision": after.revision,
            "impact": preview,
        },
    )
    # Announced in the same transaction as the change: either a subscriber learns
    # about the new deadline or the change did not happen.
    webhooks.emit(
        db,
        webhooks.EVENT_SETTINGS_UPDATED,
        {
            "action": "updated",
            "revision": after.revision,
            "event": after.as_dict(),
            "previous": before.as_dict(),
            "changed": sorted(_differences(before, after)),
            "impact": preview,
        },
    )
    db.commit()

    return {
        "event": eventconfig.active(db).as_dict(),
        "before": before.as_dict(),
        "deployment_default": eventconfig.deployment_default().as_dict(),
        "changed": sorted(_differences(before, after)),
        "impact": preview,
        "warnings": preview["warnings"],
        "status": _status(db, after),
        "audited": True,
        "notice": (
            "The event window is now the organiser's. Every request resolves it through "
            "the database, and the change is in the audit trail and on the webhook queue."
        ),
    }


@router.delete("")
def reset_event(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Hand the clock back to `EVENT_*` / the dataset.

    Deleting the row is the reset: the deployment's configuration was never
    overwritten, so "put it back" cannot fail to find the original.
    """
    before = eventconfig.active(db)
    if not before.organiser_set:
        return {
            "event": before.as_dict(),
            "changed": [],
            "notice": "The event window already comes from the deployment's configuration.",
        }

    after = eventconfig.clear(db, actor=user)
    audit.record(
        db,
        ACTION_RESET,
        actor=user,
        entity="event",
        entity_id=eventconfig.SETTINGS_ID,
        ip=client_ip(request),
        details={
            "before": _window_facts(before),
            "after": _window_facts(after),
            # Named for the same reason the update path names them: a reset that
            # moved the deadline back is a change to the deadline, and the trail
            # should say which fields it moved rather than "nothing" (the reset
            # row has no `note` to carry that story instead).
            "changed": sorted(_differences(before, after)),
            "revision": after.revision,
        },
    )
    webhooks.emit(
        db,
        webhooks.EVENT_SETTINGS_UPDATED,
        {
            "action": "reset",
            "revision": after.revision,
            "event": after.as_dict(),
            "previous": before.as_dict(),
        },
    )
    db.commit()
    return {
        "event": after.as_dict(),
        "before": before.as_dict(),
        "changed": sorted(_differences(before, after)),
        "status": _status(db, after),
        "notice": "The event window is back under the deployment's configuration.",
    }


@router.get("/history")
def event_history(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ADMIN_ROLES)),
) -> dict:
    """Every change to the clock, newest first, read from the append-only trail.

    Deliberately read from `audit_logs` rather than maintained as a second list:
    there is one record of what happened to this deployment, and the console does
    not get its own copy that could disagree with it.
    """
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_((ACTION_UPDATED, ACTION_RESET)))
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    return {
        "entries": [
            {
                "id": row.id,
                "action": row.action,
                "actor_email": row.actor_email,
                "at": iso(row.created_at),
                "ip": row.ip,
                "note": (row.details or {}).get("note"),
                "revision": (row.details or {}).get("revision"),
                "changed": (row.details or {}).get("changed") or [],
                "before": (row.details or {}).get("before"),
                "after": (row.details or {}).get("after"),
            }
            for row in rows
        ],
        "count": len(rows),
        "note": (
            "Read from the append-only audit trail: the console keeps no second copy "
            "that could disagree with it."
        ),
    }


def _window_facts(clock: eventconfig.EventConfig) -> dict:
    return {
        "name": clock.name,
        "starts_at": clock.starts_at.isoformat(),
        "ends_at": clock.ends_at.isoformat(),
        "voting_opens_at": clock.voting_opens_at.isoformat(),
        "voting_closes_at": clock.voting_closes_at.isoformat(),
        "source": clock.source,
    }


def _differences(before: eventconfig.EventConfig, after: eventconfig.EventConfig) -> set[str]:
    """Which fields actually moved. The trail should not claim a change nobody made."""
    fields = ("name", "starts_at", "ends_at", "voting_opens_at", "voting_closes_at", "note")
    return {
        field
        for field in fields
        if getattr(before, field) != getattr(after, field)
    }
