"""The organiser's clock: a singleton table the console can write.

Until now the event name and window lived only in configuration, which meant a
deadline could not be moved without editing the environment and restarting the
deployment. That is the wrong shape for a hackathon: the deadline is the one
number an organiser *does* need to change during an event, and everybody affected
by the change needs to be able to see that it happened, when, and by whose hand.

The table is deliberately a **singleton**: `id = 1` is a database check
constraint, not a convention. Two rows would be two deadlines, and the questions
that follow — which one does the API enforce, which one does the certificate
attest — are exactly the questions this project exists to answer. Zero rows is
the normal state: the configured window (environment variables, or the imported
dataset under `EVENT_SOURCE=fixtures`) stays authoritative until an organiser
takes the clock over, so a fresh `docker compose up` behaves precisely as before
and a container never needs a console visit to be correct.

`revision` is optimistic concurrency: the console sends back the revision it
read, and 0010's row is the record of a deliberate act rather than whatever the
last writer happened to leave behind. `note` and the actor columns exist for the
same reason — a deadline that moved without a stated reason is an incident.

Revision ID: 0010_event_settings
Revises: 0009_judge_invites
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_event_settings"
down_revision: Union[str, None] = "0009_judge_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Every column is NOT NULL while a row exists: a row is the organiser's
        # complete statement of the window, materialised from whatever was
        # effective when it was first written. A half-filled row would make
        # "who owns this field" a question every reader has to answer separately.
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voting_opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voting_closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_email", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # One clock. Enforced by the database, because a second row would be a
        # second answer to "when does this event close".
        sa.CheckConstraint("id = 1", name="ck_event_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("event_settings")
