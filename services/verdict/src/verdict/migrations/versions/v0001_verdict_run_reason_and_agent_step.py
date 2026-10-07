"""The verdict run, its reasons and the agent's step log (stories 2.5 and 2.6).

Expand only: three new tables. The step log is append-only: the service's
role gets SELECT and INSERT on it and nothing else, and a trigger refuses
UPDATE, DELETE and TRUNCATE for every role, the table's owner among them.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from verdict.adapters.migrations import (
    grant_on_table,
    grant_schema_usage,
    refuse_if_step_log_has_rows,
    revoke_schema_usage,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "verdict"


def _uuid() -> sa.Uuid[str]:
    return sa.Uuid(as_uuid=False)


def upgrade() -> None:
    # The schema itself is created by the Alembic environment, before the
    # version table (migrations/env.py).
    grant_schema_usage()
    op.create_table(
        "verdict_run",
        sa.Column("verdict_run_id", _uuid(), primary_key=True),
        # The id of `intake`'s case: no foreign key crosses a schema (AD-4).
        sa.Column("case_id", _uuid(), nullable=False),
        sa.Column("retriever_config", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        # What a done run suggests; every one of these is set by domain code.
        sa.Column("verdict", sa.Text, nullable=True),
        sa.Column("loading_pct", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column(
            "system_reasons",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("error_code", sa.Text, nullable=True),
        # AD-6: one run per case and retriever configuration.
        sa.UniqueConstraint(
            "case_id",
            "retriever_config",
            name="uq_verdict_verdict_run_case_retriever_config",
        ),
        sa.CheckConstraint(
            "(verdict = 'loaded') = (loading_pct IS NOT NULL)",
            name="ck_verdict_verdict_run_loading_follows_verdict",
        ),
        schema=SCHEMA,
    )
    # The key row is inserted, settled once, and removed if its run is given
    # up while it is still running.
    grant_on_table("verdict_run", ["SELECT", "INSERT", "UPDATE", "DELETE"])
    op.create_table(
        "reason",
        sa.Column(
            "verdict_run_id",
            _uuid(),
            sa.ForeignKey(f"{SCHEMA}.verdict_run.verdict_run_id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("rule_id", sa.Text, nullable=False),
        # Ids of `extraction`'s facts.
        sa.Column("fact_ids", postgresql.ARRAY(_uuid()), nullable=False),
        sa.Column("effect", sa.Text, nullable=False),
        sa.Column("debit_pct", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("verdict_run_id", "position", name="pk_verdict_reason"),
        sa.CheckConstraint(
            "(effect = 'debit') = (debit_pct IS NOT NULL)",
            name="ck_verdict_reason_debit_follows_effect",
        ),
        schema=SCHEMA,
    )
    # Written with their run's result and never changed.
    grant_on_table("reason", ["SELECT", "INSERT"])
    op.create_table(
        "agent_step",
        # No foreign key to the run: the steps of a run that was given up
        # stay in the log.
        sa.Column("verdict_run_id", _uuid(), nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("case_id", _uuid(), nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("arguments", postgresql.JSONB, nullable=False),
        sa.Column("fact_id", _uuid(), nullable=True),
        sa.Column("rule_ids", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        # The order the steps were logged in, numbered by the database.
        sa.Column(
            "agent_step_seq", sa.BigInteger, sa.Identity(always=True), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "verdict_run_id", "step_no", name="pk_verdict_agent_step"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_verdict_agent_step_case_id_agent_step_seq",
        "agent_step",
        ["case_id", "agent_step_seq"],
        schema=SCHEMA,
    )
    # AD-15: added to and read. No UPDATE and no DELETE.
    grant_on_table("agent_step", ["SELECT", "INSERT"])
    # The same rule for the roles a grant cannot hold back.
    op.execute(
        sa.text(
            "CREATE FUNCTION verdict.refuse_agent_step_change() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'verdict.agent_step is append-only' "
            "USING ERRCODE = 'restrict_violation'; "
            "END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER agent_step_no_update_or_delete "
            "BEFORE UPDATE OR DELETE ON verdict.agent_step "
            "FOR EACH ROW EXECUTE FUNCTION verdict.refuse_agent_step_change()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER agent_step_no_truncate "
            "BEFORE TRUNCATE ON verdict.agent_step "
            "FOR EACH STATEMENT EXECUTE FUNCTION verdict.refuse_agent_step_change()"
        )
    )
    # Readiness reads the revision; only migrations change it.
    grant_on_table("alembic_version", ["SELECT"])


def downgrade() -> None:
    # Dropping a log that holds steps is not a downgrade step.
    refuse_if_step_log_has_rows()
    op.drop_table("agent_step", schema=SCHEMA)
    op.execute(sa.text("DROP FUNCTION verdict.refuse_agent_step_change()"))
    op.drop_table("reason", schema=SCHEMA)
    op.drop_table("verdict_run", schema=SCHEMA)
    revoke_schema_usage()
