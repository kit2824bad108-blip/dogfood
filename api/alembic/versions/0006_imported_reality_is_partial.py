"""Imported reality: uniqueness that applies to the app, not to the dataset.

The organiser fixture dataset contains awkward cases on purpose, and two of them
were unrepresentable in this schema. Importing it honestly was impossible:

  * **a duplicate submission.** `prj_41` is a second submission from `tm_07` with
    the same repository URL as `prj_07`, filed three minutes before the deadline.
    `uq_submissions_team` refused the row, so the import silently collapsed 41
    projects into 40 and the case the brief advertises ("one duplicate
    submission... how your portal copes is interesting") never reached the
    database at all.
  * **duplicate team names.** "StillTrail" appears three times (`tm_03`, `tm_30`,
    `tm_40`), "OpenSignal" twice and "AmberSwitch" twice. `uq_teams_name` refused
    them, so those teams merged into one row and their projects were attributed to
    the wrong team.

Neither constraint was wrong; each was *too broad*. What actually matters is:

  * one **live** submission per team — a team may hold a second row only when it
    is marked as a duplicate of another, which is how an organiser compares the
    pair before deciding;
  * team names unique **as created in this application** — where a 409 is the
    answer an organiser expects — while a dataset that already contains repeated
    names is imported as written rather than renamed to fit.

So both unique constraints become partial unique indexes:

  * `uq_submissions_team_canonical` on `submissions(team_id)`
    WHERE `duplicate_of_submission_id IS NULL`
  * `uq_teams_name_app` on `teams(name)` WHERE `source_ref IS NULL`

Both are still enforced by the database, including under concurrency: two writers
racing to create the same team's submission, or two simultaneous requests for the
same team name, still produce exactly one winner. `api/tests/pg` asserts that
against a real PostgreSQL server.

`submissions.duplicate_of_submission_id` records the detected duplicate relation
so the organiser's decision has a durable subject; nothing is ever deleted.
`teams.source_ref` keeps an imported team's own identifier, so a name is never
used as an identity key again.

Revision ID: 0006_imported_reality_is_partial
Revises: 0005_audit_actor_is_historical
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_imported_reality_is_partial"
down_revision: Union[str, None] = "0005_audit_actor_is_historical"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CANONICAL_ONLY = "duplicate_of_submission_id IS NULL"
APP_CREATED_ONLY = "source_ref IS NULL"


def _drop_unique(table: str, constraint: str) -> None:
    """Drop a table-level UNIQUE constraint on either dialect.

    SQLite cannot drop a constraint in place, so it rebuilds the table; the
    postgres path is a plain `DROP CONSTRAINT`. Written explicitly rather than as
    one `batch_alter_table` because batch mode reflects the table, and reflection
    is where SQLite's CHECK constraints go missing.
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(constraint, type_="unique")
    else:
        op.drop_constraint(constraint, table, type_="unique")


def upgrade() -> None:
    # ── teams: an imported team keeps its own identifier ─────────────────────
    op.add_column("teams", sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.create_index("ix_teams_source_ref", "teams", ["source_ref"])

    # ── submissions: which submission a duplicate points at ──────────────────
    op.add_column(
        "submissions",
        sa.Column("duplicate_of_submission_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_submissions_duplicate_of",
        "submissions",
        "submissions",
        ["duplicate_of_submission_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_submissions_duplicate_of_submission_id",
        "submissions",
        ["duplicate_of_submission_id"],
    )

    # ── the two constraints, narrowed rather than removed ────────────────────
    _drop_unique("submissions", "uq_submissions_team")
    _drop_unique("teams", "uq_teams_name")

    op.create_index(
        "uq_submissions_team_canonical",
        "submissions",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text(CANONICAL_ONLY),
        sqlite_where=sa.text(CANONICAL_ONLY),
    )
    op.create_index(
        "uq_teams_name_app",
        "teams",
        ["name"],
        unique=True,
        postgresql_where=sa.text(APP_CREATED_ONLY),
        sqlite_where=sa.text(APP_CREATED_ONLY),
    )


def downgrade() -> None:
    op.drop_index("uq_teams_name_app", table_name="teams")
    op.drop_index("uq_submissions_team_canonical", table_name="submissions")

    # Restoring the broad constraints fails on a database that already holds the
    # fixture duplicates — which is the honest outcome: the data has to be
    # resolved by a human before the stricter schema can hold it again.
    op.create_unique_constraint("uq_teams_name", "teams", ["name"])
    op.create_unique_constraint("uq_submissions_team", "submissions", ["team_id"])

    op.drop_index("ix_submissions_duplicate_of_submission_id", table_name="submissions")
    op.drop_constraint("fk_submissions_duplicate_of", "submissions", type_="foreignkey")
    op.drop_column("submissions", "duplicate_of_submission_id")

    op.drop_index("ix_teams_source_ref", table_name="teams")
    op.drop_column("teams", "source_ref")
