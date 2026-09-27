"""Database-level domain checks for values the API already validates.

A3 asks that the database — not only the API and the browser — protect the
invariants that matter. Uniqueness, foreign keys and NOT NULL were already
enforced by PostgreSQL; the value *domains* were enforced only by pydantic:

    technical_score  1..10      schemas.ScoreUpsertRequest
    criterion value  1..10      schemas.CriterionScore
    role             enumerated config/models.ROLES
    status           enumerated models.SUBMISSION_STATUSES
    integrity %      0..100     reported by app.github

Those are the numbers published on a leaderboard, so a row that reaches the
database through any other path (a script, a psql session, a future importer)
should still be refused. Every constraint below is also declared on the models,
so `Base.metadata.create_all` and the migration agree — `test_migrations.py`
asserts exactly that.

Revision ID: 0004_integrity_constraints
Revises: 0003_import_and_coverage
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_integrity_constraints"
down_revision: Union[str, None] = "0003_import_and_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECKS: tuple[tuple[str, str, str], ...] = (
    ("ck_users_role", "users", "role IN ('admin', 'judge', 'participant')"),
    ("ck_submissions_status", "submissions", "status IN ('draft', 'submitted')"),
    (
        "ck_submissions_integrity_pct",
        "submissions",
        "integrity_pct_in_window IS NULL "
        "OR (integrity_pct_in_window >= 0 AND integrity_pct_in_window <= 100)",
    ),
    (
        "ck_scores_technical_range",
        "scores",
        "technical_score IS NULL OR (technical_score >= 1 AND technical_score <= 10)",
    ),
    (
        "ck_scores_presentation_range",
        "scores",
        "presentation_score IS NULL OR (presentation_score >= 1 AND presentation_score <= 10)",
    ),
    (
        "ck_score_criteria_value_range",
        "score_criteria",
        "value >= 1 AND value <= 10",
    ),
)


def upgrade() -> None:
    for name, table, condition in CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for name, table, _condition in CHECKS:
        op.drop_constraint(name, table, type_="check")
