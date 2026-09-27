"""The audit trail keeps its actor, and a user's deletion cannot touch it.

`audit_logs.actor_id` was created in `0001_initial` as
`FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL`. PostgreSQL
applies that action as an ordinary `UPDATE`, and `audit_logs` carries the
append-only trigger from `0001` — so deleting a user whose actions are on record
raised `audit_logs is append-only (attempted UPDATE)` and the delete could not
complete at all. The trail could neither be anonymised nor left alone.

Neither half of that pair is the thing to weaken. The trigger is the guarantee
that scores and deadlines cannot be rewritten. `ON DELETE SET NULL` was a way of
editing history on a user's behalf, so this migration drops the foreign key and
keeps `actor_id` as the historical identifier it always was: `actor_email` is the
denormalised, durable attribution (that is why it is stored), and a row that says
`actor_id = 7` still says it after user 7 is gone.

Found by the PostgreSQL integration suite (`api/tests/pg/test_constraints.py`),
which is the only place this pair could be observed — SQLite has neither the
trigger nor the referential action.

Revision ID: 0005_audit_actor_is_historical
Revises: 0004_integrity_constraints
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_audit_actor_is_historical"
down_revision: Union[str, None] = "0004_integrity_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# PostgreSQL's name for the implicit constraint 0001's `sa.ForeignKey` generated.
CONSTRAINT = "audit_logs_actor_id_fkey"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "audit_logs", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        CONSTRAINT,
        "audit_logs",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )
