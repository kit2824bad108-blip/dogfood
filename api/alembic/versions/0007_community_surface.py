"""The community surface: voters, votes, comments, rate-limit events.

T3 asks for community voting (email gated), project comments, results hidden
during the voting window, randomised ballot ordering, and anti-abuse in the form
of rate limits, duplicate detection and an audit trail. Four tables carry it, and
each one encodes its rule rather than trusting the API layer to remember it:

  * `voters.email` is **unique** (one address, one ballot) and the token is stored
    as a SHA-256 digest, so the database cannot be read to obtain a live ballot;
  * `votes` has **`uq_vote_voter_submission`** — one vote per person per project,
    enforced under concurrency — plus range and status checks, and a `struck`
    state so an organiser can invalidate a vote without deleting the row it is;
  * `comments.status` is a checked `visible`/`hidden` rather than a delete,
    because a moderation decision is part of the record;
  * `throttle_events` keeps rate limiting honest across workers: an in-process
    counter would silently stop applying the moment a deployment runs two of
    them.

Nothing here is a foreign key onto `audit_logs`; attribution is denormalised into
`voters`/`comments` for the same reason it is in the audit trail — history must
survive the deletion of the account that produced it.

Revision ID: 0007_community_surface
Revises: 0006_imported_reality_is_partial
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_community_surface"
down_revision: Union[str, None] = "0006_imported_reality_is_partial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── voters: an email address and a link ─────────────────────────────────
    op.create_table(
        "voters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_ip", sa.String(length=64), nullable=True),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        # `nullable=False` on a timestamp that has a server default is not redundant:
        # the default only applies when the column is omitted, and a deployment that
        # writes NULL explicitly would otherwise have a voter with no creation time.
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("email", name="uq_voters_email"),
    )
    op.create_index("ix_voters_email", "voters", ["email"])
    op.create_index("ix_voters_token_hash", "voters", ["token_hash"], unique=True)
    op.create_index("ix_voters_blocked", "voters", ["blocked"])

    # ── votes: one per person per project, never deleted ────────────────────
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "voter_id",
            sa.Integer(),
            sa.ForeignKey("voters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="cast"),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column(
            "cast_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("struck_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("struck_by", sa.String(length=255), nullable=True),
        sa.Column("struck_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("voter_id", "submission_id", name="uq_vote_voter_submission"),
        sa.CheckConstraint("score >= 1 AND score <= 5", name="ck_votes_score_range"),
        sa.CheckConstraint("status IN ('cast', 'struck')", name="ck_votes_status"),
    )
    op.create_index("ix_votes_voter_id", "votes", ["voter_id"])
    op.create_index("ix_votes_submission_id", "votes", ["submission_id"])
    op.create_index("ix_votes_status", "votes", ["status"])
    op.create_index("ix_votes_cast_at", "votes", ["cast_at"])

    # ── comments: hidden, not deleted ───────────────────────────────────────
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "voter_id",
            sa.Integer(),
            sa.ForeignKey("voters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("author_email", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="visible"),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("moderated_by", sa.String(length=255), nullable=True),
        sa.Column("moderated_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('visible', 'hidden')", name="ck_comments_status"),
    )
    op.create_index("ix_comments_submission_id", "comments", ["submission_id"])
    op.create_index("ix_comments_author_id", "comments", ["author_id"])
    op.create_index("ix_comments_voter_id", "comments", ["voter_id"])
    op.create_index("ix_comments_status", "comments", ["status"])
    op.create_index("ix_comments_created_at", "comments", ["created_at"])

    # ── throttle_events: the rate limiter's own storage ─────────────────────
    op.create_table(
        "throttle_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bucket", sa.String(length=60), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("at_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_throttle_events_bucket", "throttle_events", ["bucket"])
    op.create_index("ix_throttle_events_key", "throttle_events", ["key"])
    op.create_index("ix_throttle_events_at_epoch", "throttle_events", ["at_epoch"])
    op.create_index(
        "ix_throttle_bucket_key_epoch", "throttle_events", ["bucket", "key", "at_epoch"]
    )


def downgrade() -> None:
    op.drop_index("ix_throttle_bucket_key_epoch", table_name="throttle_events")
    op.drop_index("ix_throttle_events_at_epoch", table_name="throttle_events")
    op.drop_index("ix_throttle_events_key", table_name="throttle_events")
    op.drop_index("ix_throttle_events_bucket", table_name="throttle_events")
    op.drop_table("throttle_events")

    op.drop_index("ix_comments_created_at", table_name="comments")
    op.drop_index("ix_comments_status", table_name="comments")
    op.drop_index("ix_comments_voter_id", table_name="comments")
    op.drop_index("ix_comments_author_id", table_name="comments")
    op.drop_index("ix_comments_submission_id", table_name="comments")
    op.drop_table("comments")

    op.drop_index("ix_votes_cast_at", table_name="votes")
    op.drop_index("ix_votes_status", table_name="votes")
    op.drop_index("ix_votes_submission_id", table_name="votes")
    op.drop_index("ix_votes_voter_id", table_name="votes")
    op.drop_table("votes")

    op.drop_index("ix_voters_blocked", table_name="voters")
    op.drop_index("ix_voters_token_hash", table_name="voters")
    op.drop_index("ix_voters_email", table_name="voters")
    op.drop_table("voters")
