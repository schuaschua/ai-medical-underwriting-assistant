"""The audit trail refuses every change but an insert, for every role (story 1.6).

The service role has no UPDATE or DELETE on `workflow.audit_event` (0001).
This adds the same rule for the roles a grant cannot hold back, the table's
owner among them: a trigger that refuses UPDATE, DELETE and TRUNCATE.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from workflow.adapters.migrations import refuse_if_audit_trail_has_rows

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE FUNCTION workflow.refuse_audit_event_change() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'workflow.audit_event is append-only' "
            "USING ERRCODE = 'restrict_violation'; "
            "END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER audit_event_no_update_or_delete "
            "BEFORE UPDATE OR DELETE ON workflow.audit_event "
            "FOR EACH ROW EXECUTE FUNCTION workflow.refuse_audit_event_change()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER audit_event_no_truncate "
            "BEFORE TRUNCATE ON workflow.audit_event "
            "FOR EACH STATEMENT EXECUTE FUNCTION workflow.refuse_audit_event_change()"
        )
    )


def downgrade() -> None:
    # Taking the guard off a trail that holds events is not a downgrade step.
    refuse_if_audit_trail_has_rows()
    op.execute(sa.text("DROP TRIGGER audit_event_no_truncate ON workflow.audit_event"))
    op.execute(
        sa.text("DROP TRIGGER audit_event_no_update_or_delete ON workflow.audit_event")
    )
    op.execute(sa.text("DROP FUNCTION workflow.refuse_audit_event_change()"))
