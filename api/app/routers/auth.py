"""Authentication.

Participants sign in with GitHub; judges and admins use email + password so that
seeded accounts can log in during a demo. Both paths converge on one signed
HttpOnly session cookie, so every other router sees a single identity model.
"""
from __future__ import annotations

import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..deps import client_ip, optional_user
from ..models import User
from ..schemas import DevLoginRequest, LoginRequest, RegisterRequest
from ..security import hash_password, sign_session, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
OAUTH_STATE_COOKIE = "axion_oauth_state"
MIN_PASSWORD_LENGTH = 8

# Offline mode accounts. `admin@axion.local` / `password` is the documented
# pair for a Wi-Fi-free judging session; the same password works for every role
# because these accounts only exist while MOCK_GITHUB or SEED_DEMO is on.
DEV_ACCOUNTS: dict[str, tuple[str, str]] = {
    "admin": ("admin@axion.local", "Axion Admin"),
    "judge": ("judge@axion.local", "Axion Judge"),
    "participant": ("hacker@axion.local", "Axion Hacker"),
}
DEV_PASSWORD = "password"


def oauth_redirect_uri() -> str:
    """Must be registered on the GitHub OAuth app, byte for byte."""
    return f"{settings.web_url}/api/auth/github/callback"


def public_user(user: User | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "github_login": user.github_login,
    }


def _set_session(response: Response, user_id: int) -> None:
    token = sign_session(user_id, settings.secret_key, settings.session_max_age_seconds)
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.get("/me")
def me(user: User | None = Depends(optional_user)) -> dict:
    return {"authenticated": user is not None, "user": public_user(user)}


@router.get("/status")
def auth_status(db: Session = Depends(get_db)) -> dict:
    return {
        "github_oauth_enabled": settings.github_oauth_enabled,
        "event_name": settings.event_name,
        "event_start": settings.event_start.isoformat(),
        "event_end": settings.event_end.isoformat(),
        "mock_github": settings.mock_github,
        "local_dev_login": settings.local_dev_login,
        # Emails only — the shared demo password is documented in the README and
        # in .env.example, never served over an endpoint. Each entry is the
        # account the matching one-click button will actually sign in as.
        "demo_accounts": _resolved_dev_accounts(db) if settings.local_dev_login else [],
    }


def _resolved_dev_accounts(db: Session) -> list[dict]:
    """What the one-click buttons would do, resolved against real rows."""
    resolved = []
    for role in DEV_ACCOUNTS:
        preferred = DEV_ACCOUNTS[role][0]
        user = db.scalar(select(User).where(User.email == preferred))
        if user is None:
            user = db.scalar(select(User).where(User.role == role).order_by(User.id))
        resolved.append(
            {
                "role": role,
                "email": user.email if user else preferred,
                "name": (user.name or user.email) if user else DEV_ACCOUNTS[role][1],
                "seeded": user is not None,
            }
        )
    return resolved


def _dev_user(db: Session, role: str) -> User:
    """Find (or provision) the offline account for a role."""
    preferred = DEV_ACCOUNTS[role][0]
    user = db.scalar(select(User).where(User.email == preferred))
    if user is not None:
        return user
    by_role = db.scalar(select(User).where(User.role == role).order_by(User.id))
    if by_role is not None:
        return by_role

    email, name = DEV_ACCOUNTS[role]
    user = User(email=email, name=name, role=role, password_hash=hash_password(DEV_PASSWORD))
    db.add(user)
    db.flush()
    return user


@router.post("/dev-login")
def dev_login(
    payload: DevLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Offline sign-in with zero external calls.

    Enabled only when the deployment is already in demo mode (MOCK_GITHUB or
    SEED_DEMO). Disabled deployments get a 403, so this can never become a
    silent backdoor in a real event.
    """
    if not settings.local_dev_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local dev login is disabled (set MOCK_GITHUB=true or SEED_DEMO=true)",
        )

    if payload.email:
        user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
        if user is None:
            raise HTTPException(status_code=404, detail="No seeded account with that email")
    else:
        user = _dev_user(db, payload.role)

    _set_session(response, user.id)
    audit.record(
        db,
        "auth.dev_login",
        actor=user,
        entity="user",
        entity_id=user.id,
        ip=client_ip(request),
        details={"role": user.role, "offline": True},
    )
    db.commit()
    return {"authenticated": True, "user": public_user(user), "offline": True}


@router.post("/register")
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    user = User(
        email=payload.email,
        name=payload.name or payload.email.split("@")[0],
        role="participant",
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    audit.record(db, "user.registered", actor=user, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()

    _set_session(response, user.id)
    return {"authenticated": True, "user": public_user(user)}


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        audit.record(
            db,
            "auth.login_failed",
            entity="user",
            entity_id=payload.email,
            ip=client_ip(request),
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    _set_session(response, user.id)
    audit.record(db, "auth.login", actor=user, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return {"authenticated": True, "user": public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    user = optional_user(request, db)
    if user is not None:
        audit.record(db, "auth.logout", actor=user, entity="user", entity_id=user.id, ip=client_ip(request))
        db.commit()
    response.delete_cookie(settings.cookie_name, path="/")
    return {"authenticated": False}


@router.get("/github/login")
def github_login() -> RedirectResponse:
    if not settings.github_oauth_enabled:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth is not configured (set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)",
        )
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.github_client_id,
        # The callback is deliberately routed through the *web* origin, not the
        # API origin: GitHub redirects the browser back to :3000, where the Next
        # proxy forwards it to this service, so the session cookie is set on the
        # same origin the app is served from.
        "redirect_uri": oauth_redirect_uri(),
        "scope": "read:user user:email",
        "state": state,
    }
    url = f"{GITHUB_AUTHORIZE_URL}?{httpx.QueryParams(params)}"
    response = RedirectResponse(url)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.get("/github/callback")
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not settings.github_oauth_enabled:
        raise HTTPException(status_code=400, detail="GitHub OAuth is not configured")
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not code or not state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — start the login again")

    with httpx.Client(timeout=15.0) as client:
        token_response = client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": oauth_redirect_uri(),
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="GitHub did not return an access token")

        github_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "axion",
        }
        profile_response = client.get(GITHUB_USER_URL, headers=github_headers)
        profile_response.raise_for_status()
        profile = profile_response.json()

        email = profile.get("email")
        if not email:
            emails_response = client.get(f"{GITHUB_USER_URL}/emails", headers=github_headers)
            if emails_response.status_code == 200:
                verified = [e for e in emails_response.json() if e.get("primary") and e.get("verified")]
                email = (verified or emails_response.json() or [{}])[0].get("email")

    login_name = profile.get("login") or "github-user"
    email = email or f"{login_name}@users.noreply.github.com"
    github_id = str(profile.get("id"))

    user = db.scalar(select(User).where(User.github_id == github_id))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=profile.get("name") or login_name, role="participant")
        db.add(user)
    user.github_id = github_id
    user.github_login = login_name
    user.name = user.name or profile.get("name") or login_name
    db.flush()
    audit.record(db, "auth.github_login", actor=user, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()

    redirect = RedirectResponse(f"{settings.web_url}/")
    _set_session(redirect, user.id)
    redirect.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return redirect
