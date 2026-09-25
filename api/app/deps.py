"""FastAPI dependencies: session resolution, role guards, client IP."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User
from .security import verify_session


def client_ip(request: Request) -> Optional[str]:
    """Real client IP, honouring the proxy header set by the Compose front door."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def optional_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(settings.cookie_name)
    payload = verify_session(token, settings.secret_key)
    if not payload:
        return None
    return db.get(User, int(payload["uid"]))


def current_user(
    user: Optional[User] = Depends(optional_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def require_role(*roles: str):
    """Guard factory. Usage: Depends(require_role("admin", "judge"))."""

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {' or '.join(roles)}",
            )
        return user

    return dependency
