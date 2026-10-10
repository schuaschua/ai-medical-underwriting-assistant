"""Story 1.13: who started a case, that it was completed, and the underwriter's case list.

Unit tests, with stand-ins for the store and the engine (coding-style rule
23). The same rules are proven against PostgreSQL in
`test_workflow_case_events_integration.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from workflow_fakes import (
    TRACE_ID,
    FakeEngine,
    MemoryCaseStore,
)

from contracts.audit import AuditRecord
from contracts.enums import (
    ClassifierContender,
    RetrieverConfig,
    StopAfter,
)
from contracts.models.workflow import (
    StartCaseRequest,
)
from workflow.domain.cases import (
    read_audit_trail,
    start_case,
)
from workflow.domain.entities import StartParameters

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
DEFAULTS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)


def start(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    actor: str | None = "customer",
    now: datetime = NOW,
    **options: Any,
) -> None:
    asyncio.run(
        start_case(
            case_id,
            StartCaseRequest(actor=actor, **options),
            store=store,
            engine=engine,
            defaults=DEFAULTS,
            trace_id=TRACE_ID,
            now=lambda: now,
        )
    )


def trail_of(case_id: str, store: MemoryCaseStore) -> list[AuditRecord]:
    return asyncio.run(read_audit_trail(case_id, store=store)).events


# --- The start ---------------------------------------------------------------------


@pytest.mark.parametrize("again", ["underwriter"])
def test_story_1_13_a_repeated_start_adds_no_event_and_the_first_actor_stands(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, again: str
) -> None:
    start(case_id, store, engine, "customer")

    start(case_id, store, engine, again, now=NOW + timedelta(minutes=5))
    start(case_id, store, engine, again, stop_after=StopAfter.GATE)

    (started,) = trail_of(case_id, store)
    assert (started.actor, started.occurred_at) == ("customer", NOW)
    assert len(engine.instances) == 1
