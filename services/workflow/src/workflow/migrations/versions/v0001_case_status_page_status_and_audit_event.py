"""Case status, page status and the audit trail (story 1.6).

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from workflow.adapters.migrations import (
    grant_on_table,
    grant_schema_usage,
    refuse_if_audit_trail_has_rows,
    revoke_schema_usage,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"


def upgrade() -> None:
    op.create_table(
        "case_status",
        sa.Column("case_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("case_status", sa.Text, nullable=False),
        sa.Column("redaction_status", sa.Text, nullable=False),
        sa.Column("classifier_contender", sa.Text, nullable=False),
        sa.Column("retriever_configs", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("stop_after", sa.Text, nullable=True),
        sa.Column("eval_run_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "page_status",
        sa.Column("page_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.case_status.case_id"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("page_status", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "case_id", "page_number", name="uq_workflow_page_status_case_id_page_number"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "audit_event",
        sa.Column("audit_event_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("actor_kind", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "case_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(f"{SCHEMA}.case_status.case_id"),
            nullable=False,
        ),
        sa.Column("page_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("ref", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("detail", postgresql.JSONB, nullable=True),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("eval_run_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        # AD-8: a null page_id counts as one value, so a case-level event
        # cannot be written twice either.
        sa.UniqueConstraint(
            "case_id",
            "page_id",
            "action",
            "ref",
            name="uq_workflow_audit_event_subject",
            postgresql_nulls_not_distinct=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_audit_event_case_id_occurred_at",
        "audit_event",
        ["case_id", "occurred_at"],
        schema=SCHEMA,
    )

    # The service role's rights, table by table. No row of any table is ever
    # deleted by the service, so DELETE is granted nowhere.
    grant_schema_usage()
    grant_on_table("case_status", ["SELECT", "INSERT", "UPDATE"])
    grant_on_table("page_status", ["SELECT", "INSERT", "UPDATE"])
    # AD-8, security rule 32: append-only. No UPDATE and no DELETE.
    grant_on_table("audit_event", ["SELECT", "INSERT"])
    # Readiness reads the revision; only migrations change it.
    grant_on_table("alembic_version", ["SELECT"])


def downgrade() -> None:
    # AD-8: the trail is never dropped with events in it.
    refuse_if_audit_trail_has_rows()
    op.drop_index(
        "ix_workflow_audit_event_case_id_occurred_at",
        table_name="audit_event",
        schema=SCHEMA,
    )
    op.drop_table("audit_event", schema=SCHEMA)
    op.drop_table("page_status", schema=SCHEMA)
    op.drop_table("case_status", schema=SCHEMA)
    # Rights on a dropped table go with it; the schema's are taken back here.
    revoke_schema_usage()
