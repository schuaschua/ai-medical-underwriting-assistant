"""The classification table: key row and stored result (story 1.8).

Expand only: one new table.

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

SCHEMA = "classification"


def _uuid() -> sa.Uuid[str]:
    return sa.Uuid(as_uuid=False)


def upgrade() -> None:
    # The schema itself is created by the Alembic environment, before the
    # version table (migrations/env.py).
    op.create_table(
        "classification",
        sa.Column("classification_id", _uuid(), primary_key=True),
        # Ids of `intake`'s records: no foreign key crosses a schema (AD-4).
        sa.Column("case_id", _uuid(), nullable=False),
        sa.Column("page_id", _uuid(), nullable=False),
        sa.Column("contender", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("page_type", sa.String(64), nullable=True),
        sa.Column("is_medical", sa.Boolean, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "case_id",
            "page_id",
            "contender",
            name="uq_classification_case_page_contender",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("classification", schema=SCHEMA)
