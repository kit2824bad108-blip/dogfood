"""Judge invite flow — T2 requirement.

An organiser generates a one-time invite link (POST /api/admin/judges/invite).
The invitee follows the link, validates the token (GET /api/auth/accept-invite),
and claims it with a name and password (POST /api/auth/accept-invite), which
creates or upgrades their account to role=judge and sets a session cookie.

Tests cover:
  - Happy path: fresh account
  - Happy path: existing participant is upgraded
  - Admin account is refused (cannot be downgraded)
  - Token is invalid / not found
  - Token is expired
  - Token cannot be used twice (single-use)
  - Only organiser (admin role) can generate an invite
  - Invite URL embeds the raw token and points to the correct web origin
  - Audit log entries are written for both sides of the flow
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditLog, InviteToken, User
from app.security import hash_password


# ── Helpers ──────────────────────────────────────────────────────────────────


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_admin(db, email="org@test.dev", password="organiser-pw99"):
    user = User(email=email, name="Organiser", role="admin", password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def _make_participant(db, email="hack@test.dev", password="password123"):
    user = User(email=email, name="Hacker", role="participant", password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def _admin_session(client, email, password):
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _inject_invite(db, email: str, raw_token: str, expired: bool = False, used: bool = False):
    """Insert an InviteToken row directly, bypassing the API."""
    now = datetime.now(timezone.utc)
    expires_at = (now - timedelta(hours=1)) if expired else (now + timedelta(hours=72))
    used_at = now if used else None
    invite = InviteToken(
        token_digest=_digest(raw_token),
        email=email,
        invited_by="org@test.dev",
        expires_at=expires_at,
        used_at=used_at,
    )
    db.add(invite)
    db.commit()
    return invite


# ── Generate invite (admin side) ─────────────────────────────────────────────


def test_admin_generates_invite_link(client, db, make_user):
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)

    resp = client.post("/api/admin/judges/invite", json={"email": "newie@test.dev"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "invite_url" in body
    assert "token" in body
    assert body["email"] == "newie@test.dev"
    assert "expires_at" in body
    # The invite URL should contain the raw token
    assert body["token"] in body["invite_url"]
    # Should point to the web origin's accept-invite page
    assert "/judge/accept-invite" in body["invite_url"]


def test_invite_url_embeds_the_right_web_origin(client, db):
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)
    resp = client.post("/api/admin/judges/invite", json={"email": "z@test.dev"})
    assert resp.status_code == 200, resp.text
    # The web URL used in tests is http://localhost:3000 (set in conftest via WEB_URL)
    assert resp.json()["invite_url"].startswith("http://localhost:3000")


def test_invite_only_organiser_can_generate(client, db):
    """Judges and participants cannot generate invite links."""
    judge = User(email="j@test.dev", name="J", role="judge", password_hash=hash_password("pw123456"))
    participant = User(email="p@test.dev", name="P", role="participant", password_hash=hash_password("pw123456"))
    db.add_all([judge, participant])
    db.commit()

    for email in ("j@test.dev", "p@test.dev"):
        client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
        resp = client.post("/api/admin/judges/invite", json={"email": "x@test.dev"})
        assert resp.status_code in (401, 403), f"{email}: expected 401/403, got {resp.status_code}"


def test_invite_requires_authentication(client):
    resp = client.post("/api/admin/judges/invite", json={"email": "nobody@test.dev"})
    assert resp.status_code in (401, 403)


def test_invite_token_stored_as_digest_only(client, db):
    """The raw token must not be stored in the database."""
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)
    resp = client.post("/api/admin/judges/invite", json={"email": "safe@test.dev"})
    assert resp.status_code == 200
    raw = resp.json()["token"]

    invite = db.scalar(select(InviteToken).where(InviteToken.email == "safe@test.dev"))
    assert invite is not None
    assert invite.token_digest == _digest(raw)
    # The raw token value must not appear in the row anywhere
    for attr in ("token_digest", "email", "invited_by"):
        assert raw not in str(getattr(invite, attr, "") or "")


def test_invite_audit_log_written(client, db):
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)
    client.post("/api/admin/judges/invite", json={"email": "audit@test.dev"})
    actions = [e.action for e in db.scalars(select(AuditLog)).all()]
    assert "judge.invite_generated" in actions


def test_invite_custom_expiry(client, db):
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)
    resp = client.post(
        "/api/admin/judges/invite", json={"email": "exp@test.dev", "expires_hours": 1}
    )
    assert resp.status_code == 200
    invite = db.scalar(select(InviteToken).where(InviteToken.email == "exp@test.dev"))
    expires_at = invite.expires_at.replace(tzinfo=timezone.utc) if invite.expires_at.tzinfo is None else invite.expires_at
    delta = expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=50) < delta < timedelta(hours=1, minutes=10)


# ── Validate invite (GET) ─────────────────────────────────────────────────────


def test_check_invite_returns_email_and_expiry(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "check@test.dev", raw)
    resp = client.get(f"/api/auth/accept-invite?token={raw}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["email"] == "check@test.dev"
    assert "expires_at" in body


def test_check_invite_unknown_token(client):
    resp = client.get("/api/auth/accept-invite?token=totallyunknowntoken1234567890abc")
    assert resp.status_code == 404


def test_check_invite_expired(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "exp@test.dev", raw, expired=True)
    resp = client.get(f"/api/auth/accept-invite?token={raw}")
    assert resp.status_code == 410


def test_check_invite_already_used(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "used@test.dev", raw, used=True)
    resp = client.get(f"/api/auth/accept-invite?token={raw}")
    assert resp.status_code == 410


# ── Accept invite (POST) — happy paths ───────────────────────────────────────


def test_accept_invite_creates_judge_account(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "newjudge@test.dev", raw)

    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "New Judge", "password": "secure-pw-99"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["role"] == "judge"
    assert body["user"]["email"] == "newjudge@test.dev"
    assert body["user"]["name"] == "New Judge"

    # Account exists in DB with the right role
    user = db.scalar(select(User).where(User.email == "newjudge@test.dev"))
    assert user is not None
    assert user.role == "judge"


def test_accept_invite_sets_session_cookie(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "cookie@test.dev", raw)
    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Cookie Judge", "password": "secure-pw-99"},
    )
    assert resp.status_code == 200
    # Session cookie must be present
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["role"] == "judge"


def test_accept_invite_upgrades_participant_to_judge(client, db):
    participant, _ = _make_participant(db, "upgrade@test.dev")
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "upgrade@test.dev", raw)

    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Upgraded Judge", "password": "new-secure-99"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["role"] == "judge"

    db.refresh(participant)
    assert participant.role == "judge"
    assert participant.name == "Upgraded Judge"


def test_accept_invite_marks_token_used(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "oneshot@test.dev", raw)

    client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Judge One", "password": "secure-pw-99"},
    )

    invite = db.scalar(
        select(InviteToken).where(InviteToken.token_digest == _digest(raw))
    )
    assert invite.used_at is not None


def test_accept_invite_audit_log_written(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "auditcheck@test.dev", raw)
    client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Audited Judge", "password": "secure-pw-99"},
    )
    actions = [e.action for e in db.scalars(select(AuditLog)).all()]
    assert "judge.invite_accepted" in actions


# ── Accept invite — error paths ───────────────────────────────────────────────


def test_accept_invite_unknown_token(client):
    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": "completelyfakeandinvalidtoken000000", "name": "X", "password": "secure-pw-99"},
    )
    assert resp.status_code == 404


def test_accept_invite_expired_token(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "expiry@test.dev", raw, expired=True)
    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Late Judge", "password": "secure-pw-99"},
    )
    assert resp.status_code == 410


def test_accept_invite_single_use(client, db):
    """A second POST with the same token is rejected after the first."""
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "singleuse@test.dev", raw)

    first = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Judge First", "password": "secure-pw-99"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Judge Second", "password": "another-pw-99"},
    )
    assert second.status_code == 410


def test_accept_invite_admin_account_refused(client, db):
    """An existing admin cannot be downgraded via an invite link."""
    admin, _ = _make_admin(db, "bigboss@test.dev")
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "bigboss@test.dev", raw)

    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "Sneaky", "password": "secure-pw-99"},
    )
    assert resp.status_code == 409
    # Admin role unchanged
    db.refresh(admin)
    assert admin.role == "admin"


def test_accept_invite_requires_minimum_password_length(client, db):
    raw = secrets.token_urlsafe(32)
    _inject_invite(db, "short@test.dev", raw)
    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": raw, "name": "J", "password": "short"},
    )
    assert resp.status_code == 422


# ── End-to-end: full invite flow via API ─────────────────────────────────────


def test_full_invite_flow_admin_generates_judge_accepts(client, db):
    """Exercise the complete flow through the real API without bypassing anything."""
    # Step 1: admin generates invite
    admin, pw = _make_admin(db)
    _admin_session(client, admin.email, pw)
    gen = client.post("/api/admin/judges/invite", json={"email": "fullflow@test.dev"})
    assert gen.status_code == 200, gen.text
    raw_token = gen.json()["token"]

    # Step 2: (separate client context) invitee checks the token
    client.post("/api/auth/logout")  # log out admin
    check = client.get(f"/api/auth/accept-invite?token={raw_token}")
    assert check.status_code == 200
    assert check.json()["email"] == "fullflow@test.dev"

    # Step 3: invitee claims the token
    claim = client.post(
        "/api/auth/accept-invite",
        json={"token": raw_token, "name": "Full Flow Judge", "password": "flowpw-secure-1"},
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["user"]["role"] == "judge"

    # Step 4: new judge can log in normally
    client.post("/api/auth/logout")
    login = client.post(
        "/api/auth/login",
        json={"email": "fullflow@test.dev", "password": "flowpw-secure-1"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "judge"
