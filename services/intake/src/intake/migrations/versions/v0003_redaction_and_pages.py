"""Redaction key row, pages, page text and word boxes (story 1.7).

Expand only: four new tables.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "intake"


def _uuid() -> sa.Uuid[str]:
    return sa.Uuid(as_uuid=False)


def upgrade() -> None:
    op.create_table(
        "redaction",
        sa.Column(
            "case_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.case.case_id"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.document.document_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("redacted_blob_name", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "page",
        sa.Column("page_id", _uuid(), primary_key=True),
        sa.Column(
            "case_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.case.case_id"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.document.document_id"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("page_width", sa.Float, nullable=False),
        sa.Column("page_height", sa.Float, nullable=False),
        sa.Column("thumbnail_blob_name", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "document_id", "page_number", name="uq_intake_page_document_page_number"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_intake_page_case_id", "page", ["case_id"], schema=SCHEMA)
    op.create_table(
        "page_text",
        sa.Column(
            "page_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.page.page_id"),
            primary_key=True,
        ),
        sa.Column("text", sa.Text, nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "word_box",
        sa.Column(
            "page_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.page.page_id"),
            primary_key=True,
        ),
        sa.Column("word_number", sa.Integer, primary_key=True),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("x0", sa.Float, nullable=False),
        sa.Column("y0", sa.Float, nullable=False),
        sa.Column("x1", sa.Float, nullable=False),
        sa.Column("y1", sa.Float, nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("word_box", schema=SCHEMA)
    op.drop_table("page_text", schema=SCHEMA)
    op.drop_index("ix_intake_page_case_id", table_name="page", schema=SCHEMA)
    op.drop_table("page", schema=SCHEMA)
    op.drop_table("redaction", schema=SCHEMA)
