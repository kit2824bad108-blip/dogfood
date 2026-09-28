"""Literal, printed credentials for the acceptance checker.

The official checker never logs in. It attaches the exact header string written in
`.dogfood.toml` and nothing else — there is no code in `run.py` that fetches a
token. An earlier revision of this project asked the manifest to name an endpoint
that served signed tokens, which was tidy and could not be checked: the official
runner has no way to call it.

So the four roles get literal session tokens, printed at boot in the shape the
brief's own story shows:

    seeded. test logins:
      organizer    Cookie: session=axion-organizer-1
      judge_a      Cookie: session=axion-judge-a-1
      judge_b      Cookie: session=axion-judge-b-1
      participant  Cookie: session=axion-participant-1

They are fixed strings rather than random ones for the reason the brief gives:
the header has to be writable into a committed manifest, readable by a human, and
identical on every machine — a value that rotated between a run and the commit
would make the report it cites unverifiable.

What keeps that honest is the gate. Literal tokens are accepted only when
`settings.local_dev_login` is true, which means the deployment has explicitly
declared itself a demo or fixture instance (MOCK_GITHUB, SEED_DEMO,
LOCAL_DEV_LOGIN or DOGFOOD_FIXTURE_MODE). A live event returns 401 to all four,
and `api/tests/test_access.py` asserts exactly that. The signed tokens issued by
`/api/dev/checker-headers` keep working and remain the form Axion's own deeper
self-check uses. See THREAT-MODEL.md.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import User

# The cookie name the brief's example uses: `Cookie: session=org_7f2a`. Axion's
# own session cookie is `axion_session`; both are read, because the checker sends
# whatever the manifest says and the manifest follows the brief.
LITERAL_COOKIE = "session"

ROLE_ORDER = ("organizer", "judge_a", "judge_b", "participant")

# {role: token}. Self-describing on purpose: a header pasted into a bug report
# should say which role it proves.
LITERAL_TOKENS: dict[str, str] = {
    "organizer": "axion-organizer-1",
    "judge_a": "axion-judge-a-1",
    "judge_b": "axion-judge-b-1",
    "participant": "axion-participant-1",
}

_BY_TOKEN: dict[str, str] = {token: role for role, token in LITERAL_TOKENS.items()}


def enabled() -> bool:
    """Literal tokens are a demo affordance, never a production one."""
    return settings.local_dev_login


def header_for(role: str) -> Optional[str]:
    token = LITERAL_TOKENS.get(role)
    return f"Cookie: {LITERAL_COOKIE}={token}" if token else None


def headers() -> dict[str, str]:
    """{role: the exact header to paste into .dogfood.toml}."""
    return {role: header_for(role) for role in ROLE_ORDER if role in LITERAL_TOKENS}


def actor_for_role(session: Session, role: str) -> Optional[User]:
    """The account a literal token signs in as, for this deployment.

    Reuses `devtokens.select_actors`, so the literal headers and the signed ones
    address the same four people. In the organisers' fixture dataset that is the
    organiser, `jdg_01`, `jdg_02` and a team member — which is what lets
    `.dogfood.toml`'s `peer_scores` url name judge A's own record.
    """
    from .devtokens import select_actors

    return select_actors(session).get(role)


def resolve(token: Optional[str], session: Session) -> Optional[User]:
    """The user a literal token signs in as, or None if it is not one."""
    if not token or not enabled():
        return None
    role = _BY_TOKEN.get(token)
    if role is None:
        return None
    return actor_for_role(session, role)


def should_announce() -> bool:
    import os

    if not enabled():
        return False
    if os.environ.get("AXION_ANNOUNCE_ACCESS", "").strip().lower() in {"0", "false", "no"}:
        return False
    # The test suite creates the app repeatedly; the banner is noise there.
    return "PYTEST_CURRENT_TEST" not in os.environ


def announce(session: Optional[Session] = None) -> None:
    """Print the four headers once at boot. Never fatal, never in production."""
    if not should_announce():
        return
    owns_session = session is None
    db = session or SessionLocal()
    try:
        # Emails are read here rather than held: the session may be closed
        # before the banner is printed, and a detached row is not worth relying on.
        rows = [
            (role, header_for(role), (actor.email if actor else None))
            for role in ROLE_ORDER
            if role in LITERAL_TOKENS
            for actor in [actor_for_role(db, role)]
        ]
    except Exception:  # noqa: BLE001 - an empty or unmigrated database is not an error
        return
    finally:
        if owns_session:
            db.close()

    if not any(email for _role, _header, email in rows):
        return

    # ASCII only: this banner is often redirected into a log file whose encoding
    # is not UTF-8, and a mojibake dash in the one block a judge reads is sloppy.
    print("\nseeded. test logins:", flush=True)
    for role, header, email in rows:
        if email is None:
            continue
        print(f"  {role:<12} {header}   ({email})", flush=True)
    print(
        "  .dogfood.toml uses these; GET /api/dev/checker-headers also serves "
        "signed bearer tokens\n",
        flush=True,
    )


__all__ = [
    "LITERAL_COOKIE",
    "LITERAL_TOKENS",
    "ROLE_ORDER",
    "announce",
    "enabled",
    "header_for",
    "headers",
    "resolve",
]
