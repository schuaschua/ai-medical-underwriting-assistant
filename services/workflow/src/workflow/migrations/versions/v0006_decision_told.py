"""The mark that a case's orchestration was told of a decision (story 2.4).

A decision is stored by the request that makes it, and the orchestration is
told of it afterwards (AD-5). One row here per decision told. A decision
without one is told again by the service itself, so a waiting case no longer
depends on the browser repeating the decision. A table of its own, so that
`human_decision` stays as it is: added to and read, never changed.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from workflow.adapters.migrations import grant_on_table

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"


def upgrade() -> None:
    # Expand only (coding-style rule 29): a new table; nothing an older
    # build reads is changed.
    op.create_table(
        "decision_told",
        sa.Column(
            "decision_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.human_decision.decision_id"),
            primary_key=True,
        ),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    # Decisions stored before this migration have no mark. The service
    # tells each of them once more at its first start and marks it: an
    # orchestration that has ended, or is gone, takes no harm from that.
    # Added to and read, like the decisions themselves: no UPDATE and no DELETE.
    grant_on_table("decision_told", ["SELECT", "INSERT"])


def downgrade() -> None:
    # The marks are no record of what people decided: dropping them only
    # means an older build runs without them, as it did before.
    op.drop_table("decision_told", schema=SCHEMA)
