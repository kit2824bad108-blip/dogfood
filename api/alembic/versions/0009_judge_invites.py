"""T2: judge invite tokens.

An organiser calls POST /api/admin/judges/invite with an email address and gets
back a one-time URL. The invitee opens it, picks a name and password, and the
account is created (or upgraded to judge role). Only the SHA-256 digest of the
raw token is stored, so a database snapshot cannot be replayed.

Revision ID: 0009_judge_invites
Revises: 0008_webhooks_and_records
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_judge_invites"
down_revision: Union[str, None] = "0008_webhooks_and_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invite_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Only the digest is stored — raw token never touches the DB.
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("invited_by", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_invite_tokens_token_digest",
        "invite_tokens",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        "ix_invite_tokens_email",
        "invite_tokens",
        ["email"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_invite_tokens_email", table_name="invite_tokens")
    op.drop_index("ix_invite_tokens_token_digest", table_name="invite_tokens")
    op.drop_table("invite_tokens")
