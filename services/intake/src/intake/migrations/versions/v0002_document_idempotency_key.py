"""The idempotency key of an upload (story 1.6).

Expand only: a new nullable column and a unique rule on it. Documents
uploaded before, or without a key, have none.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "intake"
UNIQUE = "uq_intake_document_idempotency_key"


def upgrade() -> None:
    op.add_column(
        "document", sa.Column("idempotency_key", sa.Text, nullable=True), schema=SCHEMA
    )
    # Nulls are distinct, so any number of documents may have no key.
    op.create_unique_constraint(UNIQUE, "document", ["idempotency_key"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_constraint(UNIQUE, "document", schema=SCHEMA, type_="unique")
    op.drop_column("document", "idempotency_key", schema=SCHEMA)
