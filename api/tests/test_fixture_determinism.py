"""Two clean imports of the same fixture must produce equivalent databases.

"Re-importing into the same database does not duplicate rows" is covered in
`test_fixture_import.py`. This is the stronger claim the acceptance environment
needs: starting from two *empty* databases, the same fixture must yield the same
ids, counts and relationships, with no dependence on the current time or on
random generation.

The first database is seeded through the `DOGFOOD_FIXTURE_MODE=true` alias and
the second through the explicit `SEED_MODE=fixtures`, so the two ways of asking
for the dataset are also proven to agree.

Excluded from the comparison, by column name: `created_at` / `updated_at` /
`integrity_checked_at` (bookkeeping about *when* a row was written) and
`password_hash` (deliberately salted). Everything else — including row ids,
because both databases are empty and insertion order is deterministic — must
match exactly.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import fixtures as fixtures_module

REPO_ROOT = Path(__file__).resolve().parents[2]
WALL_CLOCK_COLUMNS = {"created_at", "updated_at", "integrity_checked_at", "password_hash"}
SEED_TIMEOUT_SECONDS = 300


def _base_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Neutralise anything this machine might otherwise contribute: a developer's
    # .env is honoured by the app but must not decide what this test seeds.
    for name in ("SEED_MODE", "DOGFOOD_FIXTURE_MODE", "EVENT_SOURCE"):
        env[name] = ""
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    env["EVENT_START"] = ""
    env["EVENT_END"] = ""
    env["LOCAL_DEV_LOGIN"] = "false"
    env["MOCK_GITHUB"] = "true"
    env["AXION_ANNOUNCE_ACCESS"] = "false"
    return env


def _seed_fixture_database(db_path: Path, *, via_alias: bool) -> str:
    """Run the real `python -m app.seed` against a fresh SQLite file."""
    env = _base_env(db_path)
    if via_alias:
        env["DOGFOOD_FIXTURE_MODE"] = "true"
    else:
        env["SEED_MODE"] = "fixtures"
    completed = subprocess.run(
        [sys.executable, "-m", "app.seed"],
        cwd=REPO_ROOT / "api",
        env=env,
        capture_output=True,
        text=True,
        timeout=SEED_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _dump(db_path: Path) -> dict[str, list[tuple]]:
    """Every table, every row, minus the columns that are *about* the write.

    Rows are sorted with `repr` so the comparison is order-independent and
    deterministic without assuming anything about the schema.
    """
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        dump: dict[str, list[tuple]] = {}
        for table in sorted(tables):
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            keep = [column for column in columns if column not in WALL_CLOCK_COLUMNS]
            quoted = ", ".join(f'"{column}"' for column in keep)
            rows = [
                tuple(row[column] for column in keep)
                for row in connection.execute(f'SELECT {quoted} FROM "{table}"')
            ]
            dump[table] = sorted(rows, key=repr)
        return dump
    finally:
        connection.close()


def test_two_clean_imports_produce_equivalent_databases(tmp_path):
    first_path = tmp_path / "fixture-alias.db"
    second_path = tmp_path / "fixture-explicit.db"

    first_stdout = _seed_fixture_database(first_path, via_alias=True)
    second_stdout = _seed_fixture_database(second_path, via_alias=False)
    assert "[axion] seeded fixtures" in first_stdout
    assert "[axion] seeded fixtures" in second_stdout

    first = _dump(first_path)
    second = _dump(second_path)
    assert first == second

    # The counts the acceptance report quotes, derived from the committed file
    # rather than hard-coded twice, so a fixture that silently shrinks fails here.
    payload = fixtures_module.load_fixture()
    assert len(first["submissions"]) == len(payload["projects"]) == 40
    assert len(first["assignments"]) == sum(
        len(project.get("assigned_judges") or []) for project in payload["projects"]
    )
    assert len(first["scores"]) == len(payload["reviews"])
    assert len(first["users"]) == len(payload["judges"]) + len(payload["participants"]) + 1


def test_a_second_in_process_load_is_equivalent(tmp_path, monkeypatch):
    """The same comparison without spawning processes, for a fast regression.

    Hashing is salted by design, so it is excluded from the projection; it is
    stubbed here only to keep the test fast, not to make it pass.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base

    monkeypatch.setattr(
        fixtures_module, "hash_password", lambda password: f"stub:{password}"
    )
    payload = fixtures_module.load_fixture()

    dumps = []
    for name in ("a", "b"):
        path = tmp_path / f"in-process-{name}.db"
        engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
        Base.metadata.create_all(bind=engine)
        with Session(engine) as session:
            summary = fixtures_module.apply_fixture(session, payload, mode="apply")
            assert summary["applied"]["projects_created"] == len(payload["projects"])
        engine.dispose()
        dumps.append(_dump(path))

    assert dumps[0] == dumps[1]
    assert len(dumps[0]["submissions"]) == len(payload["projects"])
