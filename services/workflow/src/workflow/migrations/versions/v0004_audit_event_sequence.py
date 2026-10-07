"""The audit trail gets a sequence: the order its events were written in (story 1.12).

A case's trail is read in the order `workflow` wrote its events. Until now
that order was the record time and then the event's id, which is not stable:
two events can share a record time, the record time is read before the
case's row is locked, and the bits of a UUIDv7 within one millisecond are
random. `audit_event_seq` is numbered by the database as each row is
inserted, and a case's events are inserted one after the other under the
lock on its row, so the number is the order of writing.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
TABLE = "audit_event"
COLUMN = "audit_event_seq"
OLD_INDEX = "ix_workflow_audit_event_case_id_occurred_at"
NEW_INDEX = "ix_workflow_audit_event_case_id_audit_event_seq"
# The row trigger of 0002 that refuses UPDATE and DELETE.
ROW_GUARD = "audit_event_no_update_or_delete"


def upgrade() -> None:
    # Expand only (coding-style rule 29): a new column the database fills
    # itself, so a build that does not know it still inserts as before. The
    # table is locked from here to the end of the migration's transaction.
    op.add_column(TABLE, sa.Column(COLUMN, sa.BigInteger, nullable=True), schema=SCHEMA)
    # The events already there are numbered in the order they were read in
    # until now. That is the one UPDATE this table ever takes: the guard is
    # set aside for it and put back in the same transaction, unchanged, and
    # no column an event was written with is touched.
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.{TABLE} DISABLE TRIGGER {ROW_GUARD}"))
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.{TABLE} AS event SET {COLUMN} = ordered.position "  # noqa: S608 - constants of this module, no input
            "FROM (SELECT audit_event_id, row_number() OVER "
            "(ORDER BY recorded_at, audit_event_id) AS position "
            f"FROM {SCHEMA}.{TABLE}) AS ordered "
            "WHERE event.audit_event_id = ordered.audit_event_id"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE TRIGGER {ROW_GUARD}"))
    op.alter_column(TABLE, COLUMN, nullable=False, schema=SCHEMA)
    # From now on the database numbers every new event, and takes no number
    # from the writer. The service role's INSERT on the table covers it: an
    # identity column's sequence needs no grant of its own.
    op.execute(
        sa.text(
            f"ALTER TABLE {SCHEMA}.{TABLE} ALTER COLUMN {COLUMN} "
            "ADD GENERATED ALWAYS AS IDENTITY"
        )
    )
    numbered = op.get_bind().execute(
        sa.text(f"SELECT coalesce(max({COLUMN}), 0) FROM {SCHEMA}.{TABLE}")  # noqa: S608 - constants of this module, no input
    )
    # An integer read from the database: RESTART takes no bound parameter.
    next_number = int(numbered.scalar_one()) + 1
    op.execute(
        sa.text(
            f"ALTER TABLE {SCHEMA}.{TABLE} ALTER COLUMN {COLUMN} "
            f"RESTART WITH {next_number}"
        )
    )
    # The trail is read by case in this order; nothing reads it by
    # `occurred_at` any more.
    op.create_index(NEW_INDEX, TABLE, ["case_id", COLUMN], schema=SCHEMA)
    op.drop_index(OLD_INDEX, table_name=TABLE, schema=SCHEMA)


def downgrade() -> None:
    # No event and no guard goes: only the numbering and its index.
    op.create_index(OLD_INDEX, TABLE, ["case_id", "occurred_at"], schema=SCHEMA)
    op.drop_index(NEW_INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
