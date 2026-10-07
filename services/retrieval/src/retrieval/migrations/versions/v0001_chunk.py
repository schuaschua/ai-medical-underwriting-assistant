"""The chunk table: one row per chunk of the manual, with its vector (story 2.2).

Expand only: the `vector` extension, the chunk table with its two indexes,
and the table that notes what each chunk set was last built from.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "retrieval"
# AD-12: `text-embedding-3-large`.
EMBEDDING_DIMENSIONS = 3072
# Hybrid search (story 2.3) reads this column: the chunk text in the
# `english` configuration, with every rule id written as one word first
# (`UW-DM-001` as `UWDM001`), because the parser would split it at its dashes.
FULL_TEXT = (
    "to_tsvector('english'::regconfig, "
    r"regexp_replace(text, '\mUW-([A-Z]{2,4})-([0-9]{3})\M', 'UW\1\2', 'g'))"
)


def upgrade() -> None:
    # The schema itself is created by the Alembic environment, before the
    # version table (migrations/env.py). The extension is the database's, not
    # the schema's: in Azure it is on the server's allow-list
    # (`azure.extensions`), and whoever runs this must be allowed to create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chunk",
        sa.Column("chunk_id", sa.String(64), primary_key=True),
        sa.Column("chunk_set", sa.String(16), nullable=False),
        sa.Column("rule_ids", postgresql.ARRAY(sa.String(16)), nullable=False),
        sa.Column(
            "reference_rule_ids", postgresql.ARRAY(sa.String(16)), nullable=False
        ),
        sa.Column("section_id", sa.String(16), nullable=False),
        sa.Column("section_title", sa.Text, nullable=False),
        sa.Column("impairment", sa.Text, nullable=False),
        sa.Column("manual_page", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context_line", sa.Text, nullable=False),
        # No approximate index: search is exact (AD-12).
        sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chat_deployment", sa.String(128), nullable=False),
        sa.Column("embedding_deployment", sa.String(128), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "text_search", postgresql.TSVECTOR, sa.Computed(FULL_TEXT, persisted=True)
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_chunk_chunk_set", "chunk", ["chunk_set"], schema=SCHEMA)
    op.create_index(
        "ix_chunk_text_search",
        "chunk",
        ["text_search"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    # One row per chunk set: the manual, prompt and deployments of the last
    # run that succeeded.
    op.create_table(
        "ingest_run",
        sa.Column("chunk_set", sa.String(16), primary_key=True),
        sa.Column("manual_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_digest", sa.String(64), nullable=False),
        sa.Column("chat_deployment", sa.String(128), nullable=False),
        sa.Column("embedding_deployment", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer, nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # The extension stays: it belongs to the database, and dropping it would
    # take every vector column there is with it.
    op.drop_table("ingest_run", schema=SCHEMA, if_exists=True)
    op.drop_table("chunk", schema=SCHEMA)
