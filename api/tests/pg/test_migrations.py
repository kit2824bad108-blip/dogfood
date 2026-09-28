"""Migrations against a real PostgreSQL server: empty → migrated → current schema.

The chain the brief names, tested in order and from zero. "From zero" means the
`public` schema is dropped before the first `upgrade`, so nothing here can pass by
accident because a table already existed.

The last test is the interesting one: it compares the schema a migration produces
with the schema the models declare. `Base.metadata.create_all` (what the fast
SQLite suite builds) and `alembic upgrade head` (what a deployment runs) are two
independent descriptions of the same database, and nothing else in the project
notices when they drift apart.
"""
from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db import Base

pytestmark = pytest.mark.postgres

APP_TABLES = {
    "users",
    "teams",
    "team_members",
    "submissions",
    "assignments",
    "scores",
    "audit_logs",
    "tracks",
    "prizes",
    "rubrics",
    "score_criteria",
    "import_batches",
    "duplicate_reviews",
    # T3: the community surface
    "voters",
    "votes",
    "comments",
    "throttle_events",
    # T4: the outbound outbox and signed records
    "webhook_endpoints",
    "webhook_deliveries",
    "participation_records",
}
EXPECTED_CHAIN = [
    "0008_webhooks_and_records",
    "0007_community_surface",
    "0006_imported_reality_is_partial",
    "0005_audit_actor_is_historical",
    "0004_integrity_constraints",
    "0003_import_and_coverage",
    "0002_event_and_rubrics",
    "0001_initial",
]


def _tables(query) -> set[str]:
    return {
        row[0]
        for row in query("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    }


def _version(query) -> str | None:
    return query("SELECT version_num FROM alembic_version")[0][0]


def _trigger_definition(query) -> str:
    rows = query(
        """
        SELECT pg_get_triggerdef(t.oid)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'axion_audit_logs_immutable' AND n.nspname = 'public'
        """
    )
    return rows[0][0] if rows else ""


def _function_definition(query) -> tuple[str, str]:
    rows = query(
        """
        SELECT l.lanname, pg_get_functiondef(p.oid)
        FROM pg_proc p
        JOIN pg_language l ON l.oid = p.prolang
        WHERE p.proname = 'axion_audit_logs_immutable'
        """
    )
    return rows[0] if rows else ("", "")


# ── from zero ────────────────────────────────────────────────────────────────


def test_an_empty_database_migrates_to_head(reset_schema, alembic, query):
    reset_schema()
    assert _tables(query) == set(), "the reset must leave no tables behind"

    alembic.upgrade("head")

    assert _version(query) == alembic.head()
    assert _tables(query) == APP_TABLES | {"alembic_version"}


def test_the_migration_creates_the_append_only_audit_trigger(reset_schema, alembic, query):
    reset_schema()
    alembic.upgrade("head")

    definition = _trigger_definition(query)
    assert definition, "audit_logs has no trigger after migrating"
    # PostgreSQL rewrites the definition (`BEFORE DELETE OR UPDATE`), so the
    # assertion is on the tokens that carry the meaning rather than on the order
    # they happen to be printed in.
    tokens = definition.split()
    assert "BEFORE" in tokens, definition
    assert {"UPDATE", "DELETE"} <= set(tokens), definition
    assert "FOR EACH ROW" in definition

    language, function = _function_definition(query)
    assert language == "plpgsql"
    assert "audit_logs is append-only" in function


def test_the_revision_chain_is_linear(reset_schema, alembic):
    """Pinned so a new migration has to be a deliberate addition to this list."""
    assert alembic.revisions() == EXPECTED_CHAIN


def test_the_audit_trail_has_no_foreign_key_that_could_rewrite_it(pg_migrated, query):
    """0005: deleting a user must not be a write to the trail.

    `ON DELETE SET NULL` reaches PostgreSQL as an ordinary `UPDATE` of the audit
    row, which the append-only trigger refuses — so the delete failed outright
    and `ON DELETE` could never run. The column is historical instead: it keeps
    the id, and `actor_email` carries the attribution.
    """
    rows = query(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'audit_logs' AND con.contype = 'f'
        """
    )
    assert rows == [], "a foreign key on the trail is a rewrite path"
    assert not Base.metadata.tables["audit_logs"].columns["actor_id"].foreign_keys, (
        "the models and the migration must agree about this; see 0005"
    )


def test_the_uniqueness_imported_data_needs_is_partial(pg_migrated, query):
    """0006 narrowed two unique constraints instead of dropping them.

    The organisers' fixture data violates both as they were written: one team (of
    40) submits twice, and three teams share the name "StillTrail". What matters
    about the replacement is that it is *partial* — it still holds for every row
    this application creates, so no guarantee was traded away for the import.
    """
    definitions = dict(
        query(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename IN ('submissions', 'teams') "
            "ORDER BY indexname"
        )
    )

    assert "duplicate_of_submission_id IS NULL" in definitions["uq_submissions_team_canonical"]
    assert "source_ref IS NULL" in definitions["uq_teams_name_app"]

    # The broad constraints are gone rather than supplemented: keeping them would
    # have made the import impossible again for a database that has run 0006.
    assert "uq_submissions_team" not in definitions
    assert "uq_teams_name" not in definitions


# ── earlier schema → head ────────────────────────────────────────────────────


def test_upgrading_from_the_first_release_keeps_rows_and_backfills_timestamps(
    reset_schema, alembic, raw, query
):
    """0002 adds `submitted_at` to a table that may already hold projects.

    The migration promises existing rows become "submitted as of their own
    timestamp". That promise is only observable by upgrading a populated 0001
    database, which is what this does.
    """
    reset_schema()
    alembic.upgrade("0001_initial")
    raw(
        "INSERT INTO users (email, name, role) VALUES ('early@test.dev', 'Early', 'participant')"
    )
    raw(
        "INSERT INTO teams (name, invite_code, created_by) VALUES ('Early Team', 'EARLY001', 1)"
    )
    raw(
        "INSERT INTO submissions (team_id, title, repo_url) "
        "VALUES (1, 'Early Project', 'https://github.com/early/project')"
    )
    raw(
        "INSERT INTO scores (submission_id, judge_id, technical_score) VALUES (1, 1, 7)"
    )

    alembic.upgrade("head")

    row = query("SELECT status, submitted_at, created_at FROM submissions")[0]
    assert row[0] == "submitted", "existing rows become submitted"
    assert row[1] == row[2], "submitted_at is backfilled from created_at"
    assert query("SELECT count(*) FROM scores")[0][0] == 1, "the existing verdict survived"
    assert _version(query) == alembic.head()


def test_upgrading_an_already_migrated_database_is_a_no_op(pg_migrated, alembic, query):
    alembic.upgrade("head")
    first = _version(query)
    alembic.upgrade("head")
    assert _version(query) == first == pg_migrated["head"]


def test_downgrading_to_base_removes_the_schema_and_the_trigger(reset_schema, alembic, query):
    reset_schema()
    alembic.upgrade("head")

    alembic.downgrade("base")

    # alembic keeps its own bookkeeping table; everything else is gone.
    assert _tables(query) == {"alembic_version"}
    assert _trigger_definition(query) == ""
    assert _function_definition(query)[0] == "", "the trigger function is dropped too"


# ── the migrated schema is the schema the models describe ────────────────────


def _live_columns(query) -> dict[str, dict[str, dict]]:
    rows = query(
        """
        SELECT table_name, column_name, is_nullable, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    )
    live: dict[str, dict[str, dict]] = {}
    for table, column, nullable, length in rows:
        live.setdefault(table, {})[column] = {
            "nullable": nullable == "YES",
            "length": length,
        }
    return live


def _model_columns() -> dict[str, dict[str, dict]]:
    return {
        name: {
            column.name: {
                "nullable": bool(column.nullable),
                "length": getattr(column.type, "length", None),
            }
            for column in table.columns
        }
        for name, table in Base.metadata.tables.items()
    }


def _unique_column_sets(query) -> dict[str, set[frozenset]]:
    """Every unique guarantee in the database, as sets of columns.

    A unique *index* over one column and a unique *constraint* over one column are
    the same promise to the application, so both catalogs are folded into one set
    of column sets rather than compared by object kind.
    """
    constraints = query(
        """
        SELECT c.relname, con.conname, string_agg(a.attname, ',' ORDER BY k.ord)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND con.contype = 'u'
        GROUP BY c.relname, con.conname
        """
    )
    indexes = query(
        """
        SELECT c.relname, i.indexrelid::regclass::text, string_agg(a.attname, ',' ORDER BY k.ord)
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND i.indisunique AND NOT i.indisprimary
        GROUP BY c.relname, i.indexrelid
        """
    )
    unique: dict[str, set[frozenset]] = {}
    for table, _name, columns in list(constraints) + list(indexes):
        unique.setdefault(table, set()).add(frozenset(columns.split(",")))
    return unique


def _model_unique_sets() -> dict[str, set[frozenset]]:
    unique: dict[str, set[frozenset]] = {}
    for name, table in Base.metadata.tables.items():
        sets: set[frozenset] = set()
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                sets.add(frozenset(column.name for column in constraint.columns))
        for column in table.columns:
            if column.unique and not column.primary_key:
                sets.add(frozenset([column.name]))
        unique[name] = sets
    return unique


def _live_check_constraints(query) -> dict[str, set[str]]:
    rows = query(
        """
        SELECT c.relname, con.conname
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND con.contype = 'c'
        """
    )
    checks: dict[str, set[str]] = {}
    for table, name in rows:
        checks.setdefault(table, set()).add(name)
    return checks


def _model_check_constraints() -> dict[str, set[str]]:
    return {
        name: {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        for name, table in Base.metadata.tables.items()
    }


def _live_foreign_keys(query) -> set[tuple[str, str, str, str]]:
    rows = query(
        """
        SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name, rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.constraint_schema = tc.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.constraint_schema
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_name = tc.constraint_name
         AND rc.constraint_schema = tc.constraint_schema
        WHERE tc.table_schema = 'public' AND tc.constraint_type = 'FOREIGN KEY'
        """
    )
    return {(row[0], row[1], row[2], row[4].upper()) for row in rows}


def _model_foreign_keys() -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    for name, table in Base.metadata.tables.items():
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                keys.add(
                    (
                        name,
                        column.name,
                        # The referenced *table*: `information_schema.constraint_column_usage`
                        # reports the table, not `table.column`, for a composite key.
                        foreign_key.column.table.name,
                        (foreign_key.ondelete or "NO ACTION").upper(),
                    )
                )
    return keys


def test_the_migrated_schema_matches_the_models(pg_migrated, query):
    """`create_all` and `upgrade head` must describe the same database."""
    live_columns = _live_columns(query)
    model_columns = _model_columns()

    for table, columns in model_columns.items():
        assert table in live_columns, f"{table} is missing from the migrated schema"
        assert columns == live_columns[table], (
            f"{table} differs between the models and the migration:\n"
            f"  models:  {columns}\n"
            f"  migrated:{live_columns[table]}"
        )

    live_unique = _unique_column_sets(query)
    for table, sets in _model_unique_sets().items():
        assert sets <= live_unique.get(table, set()), (
            f"{table} is missing a unique guarantee in the migrated schema: "
            f"{sets - live_unique.get(table, set())}"
        )

    live_checks = _live_check_constraints(query)
    for table, names in _model_check_constraints().items():
        if names:
            assert names <= live_checks.get(table, set()), (
                f"{table} is missing check constraints: {names - live_checks.get(table, set())}"
            )

    live_keys = _live_foreign_keys(query)
    model_keys = _model_foreign_keys()
    assert model_keys == live_keys, (
        "foreign keys differ between the models and the migration:\n"
        f"  only in models:   {sorted(model_keys - live_keys)}\n"
        f"  only in migrated: {sorted(live_keys - model_keys)}"
    )

    # The reverse direction matters too: a table the migration creates but no
    # model knows about is drift just as much as a missing column.
    assert set(live_columns) - set(model_columns) == {"alembic_version"}
