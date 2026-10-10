"""Story 1.6: the lifecycle's rules, with in-memory stand-ins for the store and the engine."""

import asyncio
from datetime import datetime

from workflow_fakes import (
    BY_CUSTOMER,
    FakeEngine,
    MemoryCaseStore,
    after_start,
    classification_done,
    facts_done,
    redaction_done,
    redaction_failed,
    verdict_done,
)

from contracts.audit import ACTIONS_BY_A_HUMAN
from contracts.enums import (
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
)
from contracts.ids import new_id
from contracts.models._stage import StageResult
from contracts.models.workflow import CaseStarted, StartCaseRequest
from workflow.domain.cases import (
    record_stage_result,
    start_case,
)
from workflow.domain.entities import StartParameters
from workflow.domain.recording import (
    RecordOutcome,
    plan_recording,
)

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
    request: StartCaseRequest | None = None,
) -> CaseStarted:
    # Story 1.13: a start names the demo role that asks for it.
    by_customer = (request or BY_CUSTOMER).model_copy(update={"actor": "customer"})
    return asyncio.run(
        start_case(
            case_id,
            by_customer,
            store=store,
            engine=engine,
            defaults=DEFAULTS,
            trace_id=None,
        )
    )


# --- The recording rule --------------------------------------------------------


def test_story_1_6_no_stage_result_can_carry_a_human_action(case_id: str) -> None:
    # AD-10: keep, discard, accept and deny never come from a stage.
    for result in (
        redaction_done(case_id, [new_id()]),
        classification_done(case_id, new_id()),
        facts_done(case_id, new_id()),
        verdict_done(case_id),
        redaction_failed(case_id),
    ):
        assert plan_recording(result).audit.action not in ACTIONS_BY_A_HUMAN


# --- Recording -----------------------------------------------------------------


def outcome_of(
    result: StageResult, store: MemoryCaseStore, now: datetime
) -> RecordOutcome:
    return asyncio.run(record_stage_result(result, store=store, now=lambda: now))


def record(result: StageResult, store: MemoryCaseStore, now: datetime) -> bool:
    """Record a result; say whether anything was written."""
    return outcome_of(result, store, now) is RecordOutcome.RECORDED


# --- Which status may follow which -----------------------------------------------


def tracked_page(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, now: datetime
) -> str:
    start(case_id, store, engine)
    page_id = new_id()
    record(redaction_done(case_id, [page_id]), store, now)
    return page_id


def test_story_1_6_a_result_that_arrives_out_of_order_changes_nothing(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    page_id = tracked_page(case_id, store, engine, fixed_now)
    # The page was classified and has moved on; a second classification,
    # under another ref, arrives late.
    record(classification_done(case_id, page_id), store, fixed_now)
    store.pages[page_id].page_status = PageStatus.EXTRACTING
    events = len(after_start(store))

    outcome = outcome_of(classification_done(case_id, page_id), store, fixed_now)

    assert outcome is RecordOutcome.OUT_OF_ORDER
    # Not moved back, and no audit row for a change that did not happen.
    assert store.pages[page_id].page_status is PageStatus.EXTRACTING
    assert len(after_start(store)) == events
    # Extraction before the page was sent to extraction is out of order too.
    store.pages[page_id].page_status = PageStatus.CLASSIFIED
    assert (
        outcome_of(facts_done(case_id, page_id), store, fixed_now)
        is RecordOutcome.OUT_OF_ORDER
    )
