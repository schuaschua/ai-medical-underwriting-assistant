"""The decisions people made about pages (story 1.10).

One row per decision (AD-10). It is written in the transaction that changes
the page's status and adds the audit event, and never changed afterwards.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from workflow.adapters.migrations import grant_on_table

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
HAS_ROWS_MESSAGE = (
    f"{SCHEMA}.human_decision holds decisions: this downgrade would remove them, "
    "and is refused."
)


def upgrade() -> None:
    # Expand only (coding-style rule 29): a new table; nothing an older
    # build reads is changed.
    op.create_table(
        "human_decision",
        sa.Column("decision_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.case_status.case_id"),
            nullable=False,
        ),
        sa.Column(
            "page_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.page_status.page_id"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        # A page takes each decision once: the same decision sent again
        # finds its row.
        sa.UniqueConstraint(
            "page_id", "decision", name="uq_workflow_human_decision_page_id_decision"
        ),
        # AD-10: the database too takes a decision from a demo role only,
        # and only one of the four decisions.
        sa.CheckConstraint(
            "actor IN ('customer', 'underwriter')",
            name="ck_workflow_human_decision_actor",
        ),
        sa.CheckConstraint(
            "decision IN ('keep', 'discard', 'accept', 'deny')",
            name="ck_workflow_human_decision_decision",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_human_decision_case_id",
        "human_decision",
        ["case_id"],
        schema=SCHEMA,
    )
    # Added to and read, like the audit trail: no UPDATE and no DELETE.
    grant_on_table("human_decision", ["SELECT", "INSERT"])


def downgrade() -> None:
    held = op.get_bind().execute(
        sa.select(sa.exists().select_from(sa.table("human_decision", schema=SCHEMA)))
    )
    if held.scalar_one():
        raise RuntimeError(HAS_ROWS_MESSAGE)
    op.drop_index(
        "ix_workflow_human_decision_case_id",
        table_name="human_decision",
        schema=SCHEMA,
    )
    # Rights on a dropped table go with it.
    op.drop_table("human_decision", schema=SCHEMA)
