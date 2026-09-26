"""Import provenance, duplicate review and source identifiers.

Revision ID: 0003_import_and_coverage
Revises: 0002_event_and_rubrics
Create Date: 2026-09-26

Two nullable columns and two new tables. Nothing existing is rewritten and both
columns are nullable, so this is safe to apply to a database that is already
running an event: rows created inside the app simply have no external source id.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_import_and_coverage"
down_revision: Union[str, None] = "0002_event_and_rubrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    # The identifier a row carried in the system it was imported from. Imported
    # datasets use their own keys (`proj_017`, `judge_03`), preserved here rather
    # than reinterpreted as integer ids.
    op.add_column("users", sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.create_index("ix_users_source_ref", "users", ["source_ref"])
    op.add_column("submissions", sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.create_index("ix_submissions_source_ref", "submissions", ["source_ref"])

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="dry_run"),
        sa.Column("fixture_version", sa.Integer(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_import_batches_mode", "import_batches", ["mode"])
    op.create_index("ix_import_batches_created_at", "import_batches", ["created_at"])
    op.create_index("ix_import_batches_actor_id", "import_batches", ["actor_id"])

    op.create_table(
        "duplicate_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_of_submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=20), nullable=False, server_default="duplicate"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint(
            "submission_id", "duplicate_of_submission_id", name="uq_duplicate_pair"
        ),
    )
    op.create_index("ix_duplicate_reviews_submission_id", "duplicate_reviews", ["submission_id"])
    op.create_index(
        "ix_duplicate_reviews_duplicate_of_submission_id",
        "duplicate_reviews",
        ["duplicate_of_submission_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_duplicate_reviews_duplicate_of_submission_id", table_name="duplicate_reviews")
    op.drop_index("ix_duplicate_reviews_submission_id", table_name="duplicate_reviews")
    op.drop_table("duplicate_reviews")

    op.drop_index("ix_import_batches_actor_id", table_name="import_batches")
    op.drop_index("ix_import_batches_created_at", table_name="import_batches")
    op.drop_index("ix_import_batches_mode", table_name="import_batches")
    op.drop_table("import_batches")

    op.drop_index("ix_submissions_source_ref", table_name="submissions")
    op.drop_column("submissions", "source_ref")
    op.drop_index("ix_users_source_ref", table_name="users")
    op.drop_column("users", "source_ref")
