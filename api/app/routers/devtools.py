"""Development-only endpoints.

Currently one: the stable header credentials the acceptance checker uses so it
never has to perform a login round-trip. Disabled unless the deployment is
explicitly a demo, which is the same gate as the passwordless dev login.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import devtokens
from ..config import settings

router = APIRouter(prefix="/api/dev", tags=["dev"])

DISABLED_DETAIL = (
    "Dev login is disabled. Set MOCK_GITHUB, SEED_DEMO or LOCAL_DEV_LOGIN to "
    "enable the offline development login."
)


@router.get("/checker-headers")
def checker_headers() -> dict:
    """Header credentials for the acceptance checker."""
    if not settings.local_dev_login:
        raise HTTPException(status_code=403, detail=DISABLED_DETAIL)
    return {
        "auth_mode": "bearer",
        "note": (
            "Development credentials, valid until 2100 so a report stays "
            "reproducible. Disabled unless this deployment is a demo."
        ),
        "roles": devtokens.checker_headers(),
    }
