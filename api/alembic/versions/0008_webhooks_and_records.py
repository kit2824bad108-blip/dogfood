"""T4: outbound webhooks, their delivery outbox, and signed participation records.

Three tables, and each one is shaped by a rule that has to survive contact with a
real deployment:

  * `webhook_endpoints.secret` — deliveries are **signed**, so a receiver can tell
    a call from this deployment apart from anyone else who learned the URL. A
    webhook without a signature is an open redirect for whoever else knows it.
  * `webhook_deliveries` is an **outbox**, not a log. The envelope is written once
    at emit time and never rewritten, so a retry carries the same delivery id and
    the same bytes: a receiver can dedupe on the id and verify the signature
    without re-deriving the body. `dead` is a state, not a deletion, so what was
    lost is visible and replayable.
  * `participation_records` is **self-contained**: the attested summary, the event
    name, the signer and the algorithm all live inside the signed payload, so
    verification needs the record and the published key and nothing else. The row
    is never edited — revocation is a dated fact beside the signature, because
    "issued then revoked" is a different claim from "never issued".

Revision ID: 0008_webhooks_and_records
Revises: 0007_community_surface
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_webhooks_and_records"
down_revision: Union[str, None] = "0007_community_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── the endpoints an organiser registers ────────────────────────────────
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_webhook_endpoints_active", "webhook_endpoints", ["active"])

    # ── the outbox ─────────────────────────────────────────────────────────
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "endpoint_id",
            sa.Integer(),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signature", sa.String(length=160), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'dead')",
            name="ck_webhook_deliveries_status",
        ),
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_event", "webhook_deliveries", ["event"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
    op.create_index("ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"])

    # ── signed records ─────────────────────────────────────────────────────
    op.create_table(
        "participation_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("subject_kind", sa.String(length=20), nullable=False),
        sa.Column("subject_ref", sa.String(length=120), nullable=True),
        sa.Column("subject_name", sa.String(length=255), nullable=False),
        sa.Column("subject_email", sa.String(length=255), nullable=True),
        sa.Column("event_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=True),
        # The signed payload, stored verbatim: verification is over these bytes
        # rather than over columns reassembled into a payload, so it cannot depend
        # on how a backend round-trips a timestamp.
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("signature", sa.String(length=160), nullable=False),
        sa.Column("algorithm", sa.String(length=40), nullable=False, server_default="hmac-sha256"),
        sa.Column("issued_by", sa.String(length=255), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("code", name="uq_participation_records_code"),
        sa.CheckConstraint(
            "subject_kind IN ('judge', 'participant', 'team')",
            name="ck_participation_records_kind",
        ),
    )
    op.create_index("ix_participation_records_code", "participation_records", ["code"])
    op.create_index("ix_participation_records_subject_kind", "participation_records", ["subject_kind"])
    op.create_index("ix_participation_records_subject_email", "participation_records", ["subject_email"])


def downgrade() -> None:
    op.drop_index("ix_participation_records_subject_email", table_name="participation_records")
    op.drop_index("ix_participation_records_subject_kind", table_name="participation_records")
    op.drop_index("ix_participation_records_code", table_name="participation_records")
    op.drop_table("participation_records")

    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_webhook_endpoints_active", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
