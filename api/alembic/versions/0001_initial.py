"""Initial Axion schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="participant"),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("github_id", sa.String(length=64), nullable=True),
        sa.Column("github_login", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("github_id", name="uq_users_github_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("invite_code", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("name", name="uq_teams_name"),
        sa.UniqueConstraint("invite_code", name="uq_teams_invite_code"),
    )
    op.create_index("ix_teams_name", "teams", ["name"])
    op.create_index("ix_teams_invite_code", "teams", ["invite_code"])

    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("user_id", name="uq_team_members_user"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("docs_url", sa.String(length=500), nullable=True),
        sa.Column("demo_url", sa.String(length=500), nullable=True),
        sa.Column("video_url", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("integrity_pct_in_window", sa.Float(), nullable=True),
        sa.Column("integrity_flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("integrity_source", sa.String(length=40), nullable=True),
        sa.Column("integrity_details", sa.JSON(), nullable=True),
        sa.Column("integrity_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("team_id", name="uq_submissions_team"),
    )
    op.create_index("ix_submissions_team_id", "submissions", ["team_id"])

    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("judge_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("judge_id", "submission_id", name="uq_assignment_pair"),
    )
    op.create_index("ix_assignments_judge_id", "assignments", ["judge_id"])
    op.create_index("ix_assignments_submission_id", "assignments", ["submission_id"])

    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("judge_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("technical_score", sa.Integer(), nullable=True),
        sa.Column("technical_comment", sa.Text(), nullable=True),
        sa.Column("presentation_score", sa.Integer(), nullable=True),
        sa.Column("presentation_comment", sa.Text(), nullable=True),
        sa.Column("technical_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("presentation_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("judge_id", "submission_id", name="uq_score_pair"),
    )
    op.create_index("ix_scores_submission_id", "scores", ["submission_id"])
    op.create_index("ix_scores_judge_id", "scores", ["judge_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # The audit trail is append-only. Enforce it in the database, not just in the
    # application layer, so a compromised API key still cannot rewrite history.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION axion_audit_logs_immutable()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (attempted %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER axion_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION axion_audit_logs_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS axion_audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS axion_audit_logs_immutable()")
    op.drop_table("audit_logs")
    op.drop_table("scores")
    op.drop_table("assignments")
    op.drop_table("submissions")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("users")
