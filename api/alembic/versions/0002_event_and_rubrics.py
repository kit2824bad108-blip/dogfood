"""Tracks, prizes, weighted rubrics, per-criterion scores and submission drafts.

Revision ID: 0002_event_and_rubrics
Revises: 0001_initial
Create Date: 2026-09-26

Every new column is nullable or carries a server default, so this upgrade is
safe to apply to a database that is already running an event: existing rows
become `submitted` and keep a null track, and the original 0001 tables are
untouched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_event_and_rubrics"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prize_pool", sa.String(length=120), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("name", name="uq_tracks_name"),
        sa.UniqueConstraint("slug", name="uq_tracks_slug"),
    )
    op.create_index("ix_tracks_name", "tracks", ["name"])
    op.create_index("ix_tracks_slug", "tracks", ["slug"])

    op.create_table(
        "prizes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_prizes_track_id", "prizes", ["track_id"])

    op.create_table(
        "rubrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, server_default="Default technical rubric"),
        sa.Column("criteria", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_rubrics_is_active", "rubrics", ["is_active"])

    op.create_table(
        "score_criteria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("score_id", sa.Integer(), sa.ForeignKey("scores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("score_id", "key", name="uq_score_criterion_key"),
    )
    op.create_index("ix_score_criteria_score_id", "score_criteria", ["score_id"])

    op.add_column("submissions", sa.Column("track_id", sa.Integer(), nullable=True))
    op.add_column(
        "submissions",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
    )
    op.add_column("submissions", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_submissions_track_id", "submissions", "tracks", ["track_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_submissions_track_id", "submissions", ["track_id"])
    op.create_index("ix_submissions_status", "submissions", ["status"])

    # Existing rows predate drafts, so they are submitted as of their own timestamp.
    op.execute("UPDATE submissions SET submitted_at = created_at WHERE submitted_at IS NULL")

    op.add_column("scores", sa.Column("rubric_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_scores_rubric_id", "scores", "rubrics", ["rubric_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_scores_rubric_id", "scores", type_="foreignkey")
    op.drop_column("scores", "rubric_id")

    op.drop_index("ix_submissions_status", table_name="submissions")
    op.drop_index("ix_submissions_track_id", table_name="submissions")
    op.drop_constraint("fk_submissions_track_id", "submissions", type_="foreignkey")
    op.drop_column("submissions", "submitted_at")
    op.drop_column("submissions", "status")
    op.drop_column("submissions", "track_id")

    op.drop_table("score_criteria")
    op.drop_table("rubrics")
    op.drop_table("prizes")
    op.drop_table("tracks")
