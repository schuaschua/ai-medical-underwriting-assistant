"""Case and document tables (story 1.5).

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

SCHEMA = "intake"


def upgrade() -> None:
    op.create_table(
        "case",
        sa.Column("case_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "document",
        sa.Column("document_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.case.case_id"),
            nullable=False,
        ),
        sa.Column("original_blob_name", sa.Text, nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_intake_document_case_id", "document", ["case_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_intake_document_case_id", table_name="document", schema=SCHEMA)
    op.drop_table("document", schema=SCHEMA)
    op.drop_table("case", schema=SCHEMA)
