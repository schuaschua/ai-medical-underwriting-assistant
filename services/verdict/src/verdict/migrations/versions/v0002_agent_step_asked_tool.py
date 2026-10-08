"""A call to a tool that does not exist is a step of the log (owner, 2026-10-08).

Expand only (coding-style rule 29): `tool` may be null, and the new column
`asked_tool` holds the name the model asked for when it is. Nothing is
renamed or dropped, no logged row is changed, and the log stays append-only:
the grants and the triggers of migration 0001 are left as they are. The
build before this one writes a tool on every step and no `asked_tool`, which
both checks take.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from contracts.models.verdict import MAX_ASKED_TOOL_CHARS
from verdict.adapters.migrations import refuse_if_a_step_asked_for_no_tool

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "verdict"
TABLE = "agent_step"
ONE_OR_THE_OTHER = "ck_verdict_agent_step_asked_tool_stands_in_for_no_tool"
BOUNDED = "ck_verdict_agent_step_asked_tool_is_bounded"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("asked_tool", sa.Text, nullable=True), schema=SCHEMA)
    op.alter_column(TABLE, "tool", nullable=True, schema=SCHEMA)
    op.create_check_constraint(
        ONE_OR_THE_OTHER,
        TABLE,
        "(tool IS NULL) = (asked_tool IS NOT NULL)",
        schema=SCHEMA,
    )
    # The model wrote the name: the database holds it to its bound as well.
    op.create_check_constraint(
        BOUNDED,
        TABLE,
        f"char_length(asked_tool) <= {MAX_ASKED_TOOL_CHARS}",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # A step without a tool cannot be held by the schema before this one,
    # and no logged step is removed to make room for it.
    refuse_if_a_step_asked_for_no_tool()
    op.drop_constraint(BOUNDED, TABLE, schema=SCHEMA)
    op.drop_constraint(ONE_OR_THE_OTHER, TABLE, schema=SCHEMA)
    op.alter_column(TABLE, "tool", nullable=False, schema=SCHEMA)
    op.drop_column(TABLE, "asked_tool", schema=SCHEMA)
