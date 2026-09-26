"""Readiness and liveness probes for orchestrators and the Compose healthcheck.

`/api/health` predates these and stays as it is — the acceptance manifest and the
documentation point at it. These answer different questions:

  * live   — the process is up and serving. Touches nothing else.
  * ready  — this instance can do useful work: the database answers, the schema
             is migrated to head, and a dataset has actually been initialised.

A failing readiness check returns 503 and names every check that failed, so a
stuck boot is diagnosable from outside the container. The endpoint is
deliberately unauthenticated: it reports health, not data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Submission, User

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"


def _script_head() -> Optional[str]:
    """The revision alembic would migrate to, read from the version files.

    File-based rather than a second Alembic environment: this must work in the
    runtime image, and it must not create or write anything.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic ships with the API image
        return None
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    try:
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:  # noqa: BLE001 - an unreadable directory is a readiness failure
        return None
    return str(heads[0]) if len(heads) == 1 else None


def _database_state(db: Session) -> str:
    db.execute(text("SELECT 1"))
    return "ok"


def _migration_state(db: Session) -> str:
    head = _script_head()
    if head is None:
        raise RuntimeError("the alembic version directory could not be read")
    applied = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if applied != head:
        raise RuntimeError(f"schema is at {applied}, head is {head}")
    return "ok"


def _dataset_state(db: Session) -> str:
    users = db.scalar(select(func.count(User.id))) or 0
    submissions = db.scalar(select(func.count(Submission.id))) or 0
    if not users or not submissions:
        raise RuntimeError(
            f"no dataset initialised ({users} users, {submissions} submissions)"
        )
    return f"{users} users, {submissions} submissions"


CHECKS: tuple[tuple[str, Callable[[Session], str]], ...] = (
    ("database", _database_state),
    ("migrations", _migration_state),
    ("dataset", _dataset_state),
)


def readiness_report() -> tuple[dict[str, Any], int]:
    """Evaluate every check; 200 only when all of them pass."""
    checks: dict[str, str] = {}
    failed: list[str] = []

    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001 - an unreachable database is the report
        for label, _check in CHECKS:
            checks[label] = "not evaluated"
        checks["database"] = f"unreachable ({type(exc).__name__})"
        failed = [label for label, _check in CHECKS]
        return {"ready": False, "checks": checks, "failed": failed}, 503

    try:
        for label, check in CHECKS:
            try:
                checks[label] = check(db)
            except Exception as exc:  # noqa: BLE001 - each failure is reported
                # A failed statement poisons the transaction on Postgres; roll
                # back so the next check reports its own state rather than this
                # one's failure.
                db.rollback()
                detail = str(exc).strip().splitlines()
                checks[label] = "failed: " + (detail[0] if detail else type(exc).__name__)
                failed.append(label)
    finally:
        db.close()

    ready = not failed
    return {"ready": ready, "checks": checks, "failed": failed}, (200 if ready else 503)
