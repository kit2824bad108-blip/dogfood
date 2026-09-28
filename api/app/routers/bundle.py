"""Whole-event export and import (T4): the operability door.

An event that can only leave this deployment by copying a Postgres volume is an
event its organiser does not really own. So the bundle is a single JSON document
holding everything the event *is*, and the importer is a round trip rather than a
best effort: export from one instance, import into a fresh one, and the counts, the
ranking and the signed records come out the same.

The import is **dry by default**. `POST /api/admin/bundle/import` with no `mode`
reports what would change and writes nothing, because the one thing an import must
never do is half-apply an event and leave a deployment that is neither the old one
nor the new one. See `app/bundle.py` for what does and does not travel.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from .. import audit, bundle as bundle_module
from ..db import get_db
from ..deps import client_ip, require_role
from ..models import User
from ..schemas import BundleImportRequest

router = APIRouter(prefix="/api/admin/bundle", tags=["bundle"])


@router.get("/export")
def export(
    request: Request,
    download: bool = Query(default=False, description="Send it as a file attachment"),
    include_community: bool = Query(default=True, description="Votes and comments"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> Response:
    """The whole event as one document.

    Byte-identical for an unchanged event, which is what makes the checksum in the
    bundle worth quoting in an incident note.
    """
    document = bundle_module.export_bundle(db, include_community=include_community)
    audit.record(
        db,
        "bundle.exported",
        actor=user,
        entity="bundle",
        ip=client_ip(request),
        details={"checksum": document["checksum"], "counts": document["counts"]},
    )
    db.commit()

    import json

    payload = json.dumps(document, indent=2, default=str)
    headers = {"X-Axion-Bundle-Checksum": document["checksum"]}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="axion-event-bundle.json"'
    return Response(content=payload, media_type="application/json", headers=headers)


@router.post("/validate")
def validate(
    payload: BundleImportRequest,
    user: User = Depends(require_role("admin")),
) -> dict:
    """Check a bundle without opening a database cursor in anger.

    Useful before a restore: a bundle that names a team it does not contain is
    refused here, by name, rather than halfway through writing.
    """
    errors = bundle_module.validate_bundle(payload.bundle or {})
    return {
        "valid": not errors,
        "errors": errors,
        "counts": bundle_module.summarise_counts(payload.bundle or {}),
        "note": (
            "Nothing was written. POST the same document to /import with mode=apply "
            "to apply it."
        ),
    }


@router.post("/import")
def import_bundle(
    payload: BundleImportRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Import a bundle. `mode=dry_run` (the default) writes nothing."""
    errors = bundle_module.validate_bundle(payload.bundle or {})
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "The bundle is not importable.", "errors": errors},
        )

    result = bundle_module.import_bundle(db, payload.bundle, mode=payload.mode)
    if payload.mode == "apply":
        audit.record(
            db,
            "bundle.imported",
            actor=user,
            entity="bundle",
            ip=client_ip(request),
            details={"counts": result["counts"], "checksum": result.get("checksum")},
        )
        db.commit()
    return result
