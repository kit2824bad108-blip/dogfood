"""Stable, header-based credentials for the offline acceptance check.

`.dogfood.toml` points at `GET /api/dev/checker-headers` instead of embedding
literal tokens, so the manifest stays valid on any machine and no secret is
committed. Everything here is gated on `local_dev_login`, which is true only when
the deployment is explicitly a demo (MOCK_GITHUB / SEED_DEMO / LOCAL_DEV_LOGIN).

The tokens are deliberately long-lived: an acceptance report has to be
reproducible, and a token that rotated between the run and the commit would make
the artefact it cites unverifiable. See THREAT-MODEL.md for why that is
acceptable here and not in a live event.
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import TeamMember, User
from .security import sign_payload

# 2100-01-01T00:00:00Z. Fixed, not "now plus a year", so repeated calls return
# byte-identical headers within a run.
DEV_TOKEN_EXPIRY = 4_102_444_800

ROLE_ORDER = ("organizer", "judge_a", "judge_b", "participant")


def stable_token(user: User) -> str:
    """A signed session token with a fixed expiry, identical on every call."""
    return sign_payload(
        {"uid": user.id, "exp": DEV_TOKEN_EXPIRY, "purpose": "checker"},
        settings.secret_key,
    )


def _record(user: User) -> dict[str, str]:
    token = stable_token(user)
    return {
        "email": user.email,
        "name": user.name or "",
        "role": user.role,
        "Authorization": f"Bearer {token}",
        "X-Axion-Session": token,
    }


def select_actors(db: Session) -> dict[str, User]:
    """The four accounts a checker needs: an admin, two judges, a participant."""
    users = list(db.scalars(select(User).order_by(User.id)).all())
    admins = [user for user in users if user.role == "admin"]
    judges = [user for user in users if user.role == "judge"]
    on_team = set(db.scalars(select(TeamMember.user_id)).all())
    participants = [
        user for user in users if user.role == "participant" and user.id in on_team
    ] or [user for user in users if user.role == "participant"]

    chosen: dict[str, User] = {}
    if admins:
        chosen["organizer"] = admins[0]
    if judges:
        chosen["judge_a"] = judges[0]
    if len(judges) > 1:
        chosen["judge_b"] = judges[1]
    if participants:
        chosen["participant"] = participants[0]
    return chosen


def checker_headers(db: Session | None = None) -> dict[str, dict[str, str]]:
    """{role: headers} for every role that actually exists in this deployment."""
    owns_session = db is None
    session = db or SessionLocal()
    try:
        records = {
            role: _record(user) for role, user in select_actors(session).items()
        }
    finally:
        if owns_session:
            session.close()
    return records


def should_announce() -> bool:
    if not settings.local_dev_login:
        return False
    if os.environ.get("AXION_ANNOUNCE_ACCESS", "").strip().lower() in {"0", "false", "no"}:
        return False
    # The API is created by the test suite too, where the boot banner is pure
    # noise and there are no accounts to print anyway.
    return "PYTEST_CURRENT_TEST" not in os.environ


def announce() -> None:
    """Print the checker headers once at boot. Never fatal, never in production."""
    if not should_announce():
        return
    try:
        headers = checker_headers()
    except Exception:  # noqa: BLE001 - an empty or unmigrated database is not an error
        return
    if not headers:
        return
    # ASCII only: this banner is often redirected into a log file whose encoding
    # is not UTF-8, and a mojibake dash in the one line a judge reads is sloppy.
    print("\n[axion] offline access enabled - the acceptance checker can use:", flush=True)
    for role in ROLE_ORDER:
        record = headers.get(role)
        if record:
            print(
                f"  {role:<12} {record['email']:<24} Authorization: {record['Authorization']}",
                flush=True,
            )
    print("  (.dogfood.toml reads these from GET /api/dev/checker-headers)\n", flush=True)
