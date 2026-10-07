"""A case has `case.started` once and `case.completed` once: the database says so (story 1.13).

Both events are about the case as a whole, and the trail is append-only: a
second one could never be taken back. The code writes each where it cannot
repeat, and the event's own unique constraint (case, page, action, ref)
covers a repeat with the same reference. These two indexes hold the rule
whatever the reference: one such row per case, for each of the two actions.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
TABLE = "audit_event"
ONE_STARTED = "uq_workflow_audit_event_case_started"
ONE_COMPLETED = "uq_workflow_audit_event_case_completed"


def upgrade() -> None:
    # Expand only (coding-style rule 29): no build before this one writes
    # either action, so no stored row can break the rule.
    op.create_index(
        ONE_STARTED,
        TABLE,
        ["case_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("action = 'case.started'"),
    )
    op.create_index(
        ONE_COMPLETED,
        TABLE,
        ["case_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("action = 'case.completed'"),
    )


def downgrade() -> None:
    # No event goes: only the two rules.
    op.drop_index(ONE_COMPLETED, table_name=TABLE, schema=SCHEMA)
    op.drop_index(ONE_STARTED, table_name=TABLE, schema=SCHEMA)
