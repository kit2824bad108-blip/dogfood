"""Participation records and certificates (T4).

Two audiences, two doors, and the split is the whole design:

* **The organiser issues and revokes.** `POST /api/admin/records/issue` mints a
  record per judge and per team from the event's own verdicts and ranking, and
  refuses to re-issue one it has already issued — a signed statement about a
  moment would otherwise be silently invalidated everywhere it was already cited.
  Corrections go through revocation, which is a dated fact beside the signature
  rather than an edit to it.

* **Anyone can verify, and nobody has to trust this deployment to do it.** A code
  is looked up at `/api/records/{code}`, which returns the record, the check, and
  the key material needed to repeat the check by hand. The certificate at
  `/api/records/{code}/certificate` is self-contained HTML with no external asset,
  so it prints on a laptop with the network off.

The one thing that changes over time is whether the verification *key* is
published: HMAC is symmetric, so whoever can verify can forge. Publishing it while
results still matter would let anyone mint "this team won". `key_publication()`
carries the reason on the wire, and both endpoints repeat it, so a verifier reading
a closed key learns why rather than assuming the deployment is broken.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, records
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import ParticipationRecord, User
from ..schemas import RecordIssueRequest, RecordRevokeRequest

router = APIRouter(tags=["records"])


# ── organiser ───────────────────────────────────────────────────────────────


@router.get("/api/admin/records")
def list_records(
    subject_kind: str | None = Query(default=None, max_length=20),
    include_revoked: bool = Query(default=True),
    include_payload: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Every record this deployment has issued, and the key's publication state."""
    statement = select(ParticipationRecord).order_by(ParticipationRecord.id.desc()).limit(limit)
    if subject_kind:
        statement = statement.where(ParticipationRecord.subject_kind == subject_kind)
    if not include_revoked:
        statement = statement.where(ParticipationRecord.revoked_at.is_(None))
    rows = db.scalars(statement).all()
    return {
        "records": [records.serialize(row, include_payload=include_payload) for row in rows],
        "counts": {
            "total": len(rows),
            "revoked": sum(1 for row in rows if row.revoked_at is not None),
        },
        "key": records.key_publication(db),
    }


@router.post("/api/admin/records/issue", status_code=status.HTTP_201_CREATED)
def issue_records(
    payload: RecordIssueRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Issue participation records for the event.

    Idempotent by subject: running it twice issues nothing the second time and says
    so, which is what makes it safe to call from a workflow step that might be
    retried.
    """
    result = records.issue_for_event(
        db, actor=user, judges=payload.judges, teams=payload.teams, winners=payload.winners
    )
    audit.record(
        db,
        "record.issued",
        actor=user,
        entity="participation_record",
        ip=client_ip(request),
        details={
            "issued": result["totals"]["issued"],
            "already_issued": result["totals"]["already_issued"],
            "winners": payload.winners,
        },
    )
    db.commit()
    return result


@router.post("/api/admin/records/{code}/revoke")
def revoke_record(
    code: str,
    payload: RecordRevokeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Revoke a record. The signature stays valid; the revocation is the new fact."""
    record = records.find(db, code)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    already = record.revoked_at is not None
    if not already:
        records.revoke(db, record, payload.reason, actor=user)
        audit.record(
            db,
            "record.revoked",
            actor=user,
            entity="participation_record",
            entity_id=record.id,
            ip=client_ip(request),
            details={"code": record.code, "reason": payload.reason},
        )
        db.commit()
    return {
        "record": records.serialize(record, include_payload=False),
        "revoked": True,
        "already_revoked": already,
        "note": (
            "The record is untouched: a verifier now sees both the signature and the "
            "dated revocation, which is more informative than a deletion."
        ),
    }


# ── public verification ─────────────────────────────────────────────────────


@router.get("/api/records/{code}")
def verify_record(code: str, db: Session = Depends(get_db)) -> dict:
    """Verify a record by its code. No authentication: that is the point of it."""
    record = records.find(db, code)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No record with that code was issued by this deployment.",
        )
    return records.verification_report(record, db=db)


@router.get("/api/records/{code}/certificate", response_class=Response)
def certificate(code: str, db: Session = Depends(get_db)) -> Response:
    """A printable certificate. Self-contained, so it works offline."""
    record = records.find(db, code)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return Response(
        content=records.certificate_html(record),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.get("/api/records")
def records_overview(db: Session = Depends(get_db)) -> dict:
    """What this deployment can prove, without naming anyone.

    The public list is counts and the key's state — never the records themselves.
    A participant's email in a public listing would be a privacy leak that no
    amount of signing fixes.
    """
    rows = db.execute(
        select(ParticipationRecord.subject_kind, ParticipationRecord.revoked_at)
    ).all()
    by_kind: dict[str, int] = {}
    revoked = 0
    for kind, revoked_at in rows:
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if revoked_at is not None:
            revoked += 1
    return {
        "issued": {"total": len(rows), "by_kind": by_kind, "revoked": revoked},
        "lookup": "GET /api/records/{code} to verify one, /certificate for the printable copy",
        "key": records.key_publication(db),
        "verified_against": "the deployment's own verdicts and ranking, duplicates excluded",
    }
