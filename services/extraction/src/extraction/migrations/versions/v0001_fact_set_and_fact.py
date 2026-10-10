"""The fact set and fact tables: key row, stored result and the facts (story 2.4).

Expand only: two new tables.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "extraction"


def _uuid() -> sa.Uuid[str]:
    return sa.Uuid(as_uuid=False)


def upgrade() -> None:
    # The schema itself is created by the Alembic environment, before the
    # version table (migrations/env.py).
    op.create_table(
        "fact_set",
        sa.Column("fact_set_id", _uuid(), primary_key=True),
        # Ids of `intake`'s records: no foreign key crosses a schema (AD-4).
        sa.Column("case_id", _uuid(), nullable=False),
        sa.Column("page_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        # AD-6: one fact set per case and page.
        sa.UniqueConstraint(
            "case_id", "page_id", name="uq_extraction_fact_set_case_page"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "fact",
        sa.Column("fact_id", _uuid(), primary_key=True),
        sa.Column(
            "fact_set_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.fact_set.fact_set_id"),
            nullable=False,
        ),
        sa.Column("case_id", _uuid(), nullable=False),
        sa.Column("page_id", _uuid(), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        # The fact's place among its page's facts, as they were stored.
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("quote_verified", sa.Boolean, nullable=False),
        # Offsets into the page text `intake` stores, in Unicode code points
        # (the contracts' QUOTE_OFFSET_UNIT).
        sa.Column("quote_start", sa.Integer, nullable=True),
        sa.Column("quote_end", sa.Integer, nullable=True),
        sa.UniqueConstraint(
            "fact_set_id", "position", name="uq_extraction_fact_fact_set_position"
        ),
        # AD-14: offsets are set when, and only when, the quote was found.
        sa.CheckConstraint(
            "(quote_verified AND quote_start IS NOT NULL AND quote_end IS NOT NULL "
            "AND quote_start < quote_end) OR (NOT quote_verified AND quote_start IS NULL "
            "AND quote_end IS NULL)",
            name="ck_extraction_fact_offsets_follow_verification",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_extraction_fact_case_id_page_number",
        "fact",
        ["case_id", "page_number"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_fact_case_id_page_number", table_name="fact", schema=SCHEMA
    )
    op.drop_table("fact", schema=SCHEMA)
    op.drop_table("fact_set", schema=SCHEMA)
