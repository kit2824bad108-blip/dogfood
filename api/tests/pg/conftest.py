"""PostgreSQL integration harness.

These tests run against a real PostgreSQL server and a schema built by the real
Alembic migrations — not by ``Base.metadata.create_all``. That distinction is the
whole point of the directory: constraint names, server defaults, the
``alembic_version`` row and the append-only audit trigger only exist if a
migration put them there, so a green run here is evidence about the schema a
deployment actually runs rather than about the models.

    AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test \
        pytest tests/pg

With ``AXION_TEST_DATABASE_URL`` unset every test in this directory skips with
that instruction. CI sets ``AXION_REQUIRE_POSTGRES=1``, which turns a missing or
unreachable server into a failure instead of a skip, so the suite cannot quietly
not run where it is supposed to run.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import settings
from app.db import Base, engine

API_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = API_DIR / "alembic.ini"
ALEMBIC_DIR = API_DIR / "alembic"
AUDIT_TRIGGER = "axion_audit_logs_immutable"
_TRUTHY = {"1", "true", "yes", "on"}

HARNESS_HINT = (
    "Start a PostgreSQL 16 server and point the suite at it, for example:\n"
    "    docker compose -f docker-compose.test.yml up -d\n"
    "    export AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test\n"
    "    pytest tests/pg"
)


def _url() -> str:
    return os.environ.get("AXION_TEST_DATABASE_URL", "").strip()


def _required() -> bool:
    return os.environ.get("AXION_REQUIRE_POSTGRES", "").strip().lower() in _TRUTHY


def _skip_or_fail(reason: str) -> None:
    if _required():
        pytest.fail(f"{reason}\n(AXION_REQUIRE_POSTGRES=1 makes this fatal)\n{HARNESS_HINT}")
    pytest.skip(f"{reason}\n{HARNESS_HINT}")


class Alembic:
    """The migration commands, run in-process against the test database."""

    @staticmethod
    def _config():
        from alembic.config import Config

        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        # `alembic/env.py` injects settings.database_url itself; setting it here
        # too keeps the config honest for anyone reading it in a debugger.
        config.set_main_option("sqlalchemy.url", _url())
        return config

    def upgrade(self, revision: str = "head") -> None:
        from alembic import command

        command.upgrade(self._config(), revision)

    def downgrade(self, revision: str) -> None:
        from alembic import command

        command.downgrade(self._config(), revision)

    def head(self) -> str:
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(self._config()).get_heads()
        assert len(heads) == 1, f"expected a single migration head, found {heads}"
        return str(heads[0])

    def revisions(self) -> list[str]:
        from alembic.script import ScriptDirectory

        return [script.revision for script in ScriptDirectory.from_config(self._config()).walk_revisions()]


@pytest.fixture(scope="session")
def alembic() -> Alembic:
    return Alembic()


@pytest.fixture(scope="session")
def pg_server() -> dict:
    """Prove the thing under test is a real PostgreSQL server before any test runs."""
    url = _url()
    if not url.startswith("postgresql"):
        _skip_or_fail(
            "AXION_TEST_DATABASE_URL is not set to a postgresql:// URL "
            f"(got {url or 'nothing'}), so these tests would silently run on SQLite"
        )
    if settings.database_url != url:
        pytest.fail(
            "test bootstrap drift: app.config read DATABASE_URL="
            f"{settings.database_url!r} but the PostgreSQL suite expects {url!r}"
        )
    try:
        with engine.connect() as connection:
            server_version_num = int(
                connection.execute(text("SELECT current_setting('server_version_num')")).scalar()
            )
            version_text = connection.execute(text("SELECT version()")).scalar()
            database = connection.execute(text("SELECT current_database()")).scalar()
            user = connection.execute(text("SELECT current_user")).scalar()
    except OperationalError as exc:
        _skip_or_fail(f"PostgreSQL at {url} is unreachable: {exc.orig}")

    return {
        "url": url,
        "server_version_num": server_version_num,
        "version_text": version_text,
        "database": database,
        "user": user,
    }


def reset_public_schema() -> None:
    """Back to a database with no tables at all — the top of the migration chain."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def _public_tables() -> set[str]:
    with engine.connect() as connection:
        return {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }


def _trigger_present() -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text("SELECT 1 FROM pg_trigger WHERE tgname = :name"),
                {"name": AUDIT_TRIGGER},
            ).scalar()
        )


def _schema_is_current(alembic: Alembic) -> bool:
    try:
        with engine.connect() as connection:
            if connection.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar() is None:
                return False
            applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except OperationalError:
        return False
    missing = set(Base.metadata.tables) - _public_tables()
    return applied == alembic.head() and not missing and _trigger_present()


def _truncate_data_tables() -> None:
    """Clear data between tests without rebuilding the schema.

    TRUNCATE (rather than DROP/CREATE) keeps the migrated schema — and therefore
    the trigger, the constraints and their names — exactly as the migration left
    it. Row-level triggers do not fire for TRUNCATE, which is why the append-only
    audit table can be cleared here at all; see test_audit.py for what that means
    for the guarantee.
    """
    with engine.begin() as connection:
        targets = sorted(name for name in _public_tables() if name != "alembic_version")
        if not targets:
            return
        quoted = ", ".join(f'"{name}"' for name in targets)
        connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def pg_migrated(pg_server, alembic) -> dict:
    """empty database → migrations → current schema."""
    reset_public_schema()
    alembic.upgrade("head")
    return {"head": alembic.head(), **pg_server}


@pytest.fixture(autouse=True)
def _fresh_schema(pg_migrated, alembic):
    """Shadow the SQLite per-test rebuild with the migrated schema.

    The schema is built once per session by `pg_migrated`; between tests only the
    data is cleared. `_schema_is_current` restores the schema if a test tore it
    down on purpose (the migration tests downgrade and upgrade for real).
    """
    if not _schema_is_current(alembic):
        reset_public_schema()
        alembic.upgrade("head")
    _truncate_data_tables()
    yield


@pytest.fixture()
def reset_schema():
    return reset_public_schema


@pytest.fixture()
def pg_conn():
    """A connection whose statements commit when the block ends."""
    with engine.begin() as connection:
        yield connection


@pytest.fixture()
def raw():
    """Run one statement on its own connection and transaction.

    A statement that violates a constraint aborts its transaction, so a violating
    statement never shares a connection with the next one.
    """

    def _run(statement: str, params: dict | None = None):
        with engine.begin() as connection:
            return connection.execute(text(statement), params or {})

    return _run


@pytest.fixture()
def mark_audit_entries():
    """The highest audit id right now.

    The trail is append-only, so a test that writes a few entries cannot remove
    them again; a marker lets it talk about exactly the rows it caused.
    """

    def _mark() -> int:
        with engine.begin() as connection:
            return int(
                connection.execute(
                    text("SELECT coalesce(max(id), 0) FROM audit_logs")
                ).scalar()
                or 0
            )

    return _mark


@pytest.fixture()
def query():
    """Read one statement inside its own transaction, fetching every row."""

    def _query(statement: str, params: dict | None = None) -> list:
        with engine.begin() as connection:
            return connection.execute(text(statement), params or {}).all()

    return _query


@pytest.fixture()
def constraint_name():
    """The name of the constraint a PostgreSQL error came from."""

    def _name(exc: IntegrityError) -> str:
        diag = getattr(getattr(exc, "orig", None), "diag", None)
        return getattr(diag, "constraint_name", None) or ""

    return _name


@pytest.fixture()
def sqlstate():
    def _state(exc: Exception) -> str:
        diag = getattr(getattr(exc, "orig", None), "diag", None)
        return getattr(diag, "sqlstate", None) or getattr(exc.orig, "sqlstate", "") or ""

    return _state


@pytest.fixture()
def expect_violation(raw, constraint_name):
    """Assert that a statement is rejected *by the database*.

    Returns the caught IntegrityError so a test can also assert which constraint
    refused the write — the difference between "something went wrong" and "the
    invariant this test is about is the thing that held".
    """

    @contextmanager
    def _expect(statement: str, params: dict | None = None, constraint: str | None = None):
        with pytest.raises(IntegrityError) as info:
            raw(statement, params)
        if constraint:
            assert constraint_name(info.value) == constraint, (
                f"expected {constraint} to refuse the write, "
                f"{constraint_name(info.value) or 'no named constraint'} did"
            )
        yield info.value

    return _expect
