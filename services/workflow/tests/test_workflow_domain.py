"""Story 1.6: the lifecycle's rules, with in-memory stand-ins for the store and the engine."""

import asyncio
import logging
from datetime import datetime

import pytest
from workflow_fakes import (
    FakeEngine,
    MemoryCaseStore,
    classification_done,
    classification_failed,
    facts_done,
    redaction_done,
    redaction_failed,
    verdict_done,
)

from contracts.audit import HUMAN_ACTIONS, AuditAction
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
    StageStatus,
    StopAfter,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models._stage import StageResult
from contracts.models.workflow import CaseStarted, StartCaseRequest
from workflow.domain.cases import (
    confirm_started,
    fail_case,
    read_audit_trail,
    read_progress,
    record_stage_result,
    start_case,
)
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import new_case, resolve_start_parameters
from workflow.domain.ports import EngineState
from workflow.domain.recording import (
    LIFECYCLE_ACTOR,
    NewPage,
    PageChange,
    RecordOutcome,
    lifecycle_failure,
    plan_recording,
)
from workflow.domain.transitions import (
    CASE_STATUSES_TAKING_RESULTS,
    CASE_TRANSITIONS,
    PAGE_TRANSITIONS,
    REDACTION_TRANSITIONS,
    case_statuses_before,
    page_statuses_before,
    redaction_statuses_before,
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
    return asyncio.run(
        start_case(case_id, request, store=store, engine=engine, defaults=DEFAULTS)
    )


# --- Start parameters ----------------------------------------------------------


def test_story_1_6_a_start_without_options_takes_every_default() -> None:
    assert resolve_start_parameters(None, DEFAULTS) == DEFAULTS
    assert resolve_start_parameters(StartCaseRequest(), DEFAULTS) == DEFAULTS


def test_story_1_6_options_given_at_start_replace_the_defaults() -> None:
    eval_run_id = new_id()
    request = StartCaseRequest(
        classifier_contender=ClassifierContender.DOC_INTELLIGENCE,
        retriever_configs=[RetrieverConfig.R4, RetrieverConfig.R5],
        stop_after=StopAfter.GATE,
        eval_run_id=eval_run_id,
    )

    assert resolve_start_parameters(request, DEFAULTS) == StartParameters(
        classifier_contender=ClassifierContender.DOC_INTELLIGENCE,
        retriever_configs=(RetrieverConfig.R4, RetrieverConfig.R5),
        stop_after=StopAfter.GATE,
        eval_run_id=eval_run_id,
    )


def test_story_1_6_each_option_left_out_is_filled_on_its_own() -> None:
    request = StartCaseRequest(stop_after=StopAfter.GATE)

    parameters = resolve_start_parameters(request, DEFAULTS)

    assert parameters.stop_after is StopAfter.GATE
    assert parameters.classifier_contender is ClassifierContender.LLM
    assert parameters.retriever_configs == (RetrieverConfig.R3,)
    assert parameters.eval_run_id is None


def test_story_1_6_a_started_case_is_running_and_waits_for_redaction(
    case_id: str, fixed_now: datetime
) -> None:
    case = new_case(case_id, DEFAULTS, fixed_now)

    assert case.case_status is CaseStatus.RUNNING
    assert case.redaction_status is StageStatus.RUNNING
    assert case.created_at == fixed_now


# --- Start ---------------------------------------------------------------------


def test_story_1_6_starting_a_case_stores_it_and_gives_it_one_orchestration(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    started = start(case_id, store, engine)

    assert started.case_id == case_id
    assert started.case_status is CaseStatus.RUNNING
    assert list(store.cases) == [case_id]
    # AD-5: the orchestration's instance id is the case id.
    assert list(engine.instances) == [case_id]


def test_story_1_6_starting_twice_changes_nothing_and_answers_the_same(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    first = start(case_id, store, engine)
    stored = dict(store.cases)

    second = start(case_id, store, engine)

    assert second == first
    assert store.cases == stored
    assert list(engine.instances) == [case_id]


def test_story_1_6_a_repeat_with_other_options_still_answers_as_first_started(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    first = start(case_id, store, engine, StartCaseRequest(stop_after=StopAfter.GATE))

    second = start(
        case_id,
        store,
        engine,
        StartCaseRequest(classifier_contender=ClassifierContender.DOC_INTELLIGENCE),
    )

    assert second == first
    assert second.stop_after is StopAfter.GATE
    assert second.classifier_contender is ClassifierContender.LLM


def test_story_1_6_a_start_that_cannot_reach_the_engine_is_502_and_a_repeat_finishes_it(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine.fail = True
    with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as raised:
        start(case_id, store, engine)

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert engine.instances == {}
    # Ids, a stage and a type; never the error's own text (security rule 31).
    assert f"case_id={case_id} stage=schedule type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text

    engine.fail = False
    started = start(case_id, store, engine)

    assert started.case_status is CaseStatus.RUNNING
    assert list(engine.instances) == [case_id]
    assert len(store.cases) == 1


def test_story_1_6_the_first_step_confirms_the_case_was_stored(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    with pytest.raises(DomainError) as raised:
        asyncio.run(confirm_started(case_id, store=store))
    assert raised.value.code is ErrorCode.NOT_FOUND

    start(case_id, store, engine)

    assert asyncio.run(confirm_started(case_id, store=store)) is CaseStatus.RUNNING


# --- The recording rule --------------------------------------------------------


def test_story_1_6_a_done_redaction_starts_the_tracking_of_its_pages(
    case_id: str,
) -> None:
    pages = [new_id(), new_id(), new_id()]
    result = redaction_done(case_id, pages)

    recording = plan_recording(result)

    assert recording.audit is result.audit
    assert recording.redaction_status is StageStatus.DONE
    assert recording.new_pages == (
        NewPage(pages[0], 1),
        NewPage(pages[1], 2),
        NewPage(pages[2], 3),
    )
    # The case goes on running; nothing here routes a page.
    assert recording.case_status is None
    assert recording.page_change is None
    assert recording.error_code is None


def test_story_1_6_a_failed_redaction_fails_the_case_and_creates_no_pages(
    case_id: str,
) -> None:
    recording = plan_recording(redaction_failed(case_id, "stage_timeout"))

    assert recording.case_status is CaseStatus.FAILED
    assert recording.redaction_status is StageStatus.FAILED
    assert recording.error_code is ErrorCode.STAGE_TIMEOUT
    assert recording.audit.action is AuditAction.STAGE_FAILED
    assert recording.new_pages == ()


def test_story_1_6_a_done_page_stage_moves_its_page_on(case_id: str) -> None:
    page_id = new_id()

    classified = plan_recording(classification_done(case_id, page_id))
    extracted = plan_recording(facts_done(case_id, page_id))

    assert classified.page_change == PageChange(page_id, PageStatus.CLASSIFIED)
    assert extracted.page_change == PageChange(page_id, PageStatus.EXTRACTED)
    for recording in (classified, extracted):
        assert recording.case_status is None
        assert recording.redaction_status is None


def test_story_1_6_a_failed_page_stage_fails_its_page_and_the_case(
    case_id: str,
) -> None:
    page_id = new_id()

    recording = plan_recording(classification_failed(case_id, page_id))

    assert recording.case_status is CaseStatus.FAILED
    assert recording.page_change == PageChange(page_id, PageStatus.FAILED)
    assert recording.error_code is ErrorCode.MODEL_UNAVAILABLE
    # Only a redaction result says anything about the redaction stage.
    assert recording.redaction_status is None


def test_story_1_6_a_suggested_verdict_is_recorded_without_a_status_change(
    case_id: str,
) -> None:
    recording = plan_recording(verdict_done(case_id))

    assert recording.audit.action is AuditAction.VERDICT_SUGGESTED
    assert (recording.case_status, recording.page_change) == (None, None)


def test_story_1_6_no_stage_result_can_carry_a_human_action(case_id: str) -> None:
    # AD-10: keep, discard, accept and deny never come from a stage.
    for result in (
        redaction_done(case_id, [new_id()]),
        classification_done(case_id, new_id()),
        facts_done(case_id, new_id()),
        verdict_done(case_id),
        redaction_failed(case_id),
    ):
        assert plan_recording(result).audit.action not in HUMAN_ACTIONS


# --- Recording -----------------------------------------------------------------


def outcome_of(
    result: StageResult, store: MemoryCaseStore, now: datetime
) -> RecordOutcome:
    return asyncio.run(record_stage_result(result, store=store, now=lambda: now))


def record(result: StageResult, store: MemoryCaseStore, now: datetime) -> bool:
    """Record a result; say whether anything was written."""
    return outcome_of(result, store, now) is RecordOutcome.RECORDED


def test_story_1_6_a_recorded_result_changes_status_and_adds_one_event(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    pages = [new_id(), new_id()]
    result = redaction_done(case_id, pages)

    assert record(result, store, fixed_now) is True

    progress = asyncio.run(read_progress(case_id, store=store))
    assert progress.redaction_status is StageStatus.DONE
    assert [
        (page.page_id, page.page_number, page.page_status) for page in progress.pages
    ] == [
        (pages[0], 1, PageStatus.UPLOADED),
        (pages[1], 2, PageStatus.UPLOADED),
    ]
    trail = asyncio.run(read_audit_trail(case_id, store=store))
    assert trail.events == [result.audit]


def test_story_1_6_recording_the_same_result_twice_writes_nothing_new(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    start(case_id, store, engine)
    result = redaction_done(case_id, [new_id()])
    record(result, store, fixed_now)
    before = asyncio.run(read_progress(case_id, store=store))

    with caplog.at_level(logging.INFO):
        assert record(result, store, fixed_now) is False

    assert asyncio.run(read_progress(case_id, store=store)) == before
    assert len(store.events) == 1
    assert f"stage result duplicate: case_id={case_id}" in caplog.text


def test_story_1_6_a_failed_result_fails_the_case_with_one_stage_failed_event(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    result = redaction_failed(case_id, "redaction_failed")

    assert record(result, store, fixed_now) is True
    assert record(result, store, fixed_now) is False

    progress = asyncio.run(read_progress(case_id, store=store))
    assert progress.case_status is CaseStatus.FAILED
    assert progress.redaction_status is StageStatus.FAILED
    assert progress.pages == []
    ((_, recording),) = store.events
    assert recording.audit.action is AuditAction.STAGE_FAILED
    assert recording.error_code is ErrorCode.REDACTION_FAILED


def test_story_1_6_a_recording_that_fails_is_raised_and_leaves_nothing(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    before = asyncio.run(read_progress(case_id, store=store))
    store.fail_audit_insert = True

    # Raised as it is, so the activity fails and the orchestration retries.
    with pytest.raises(Exception, match="secret-store-detail"):
        record(redaction_failed(case_id), store, fixed_now)

    assert asyncio.run(read_progress(case_id, store=store)) == before
    assert store.events == []


def test_story_1_6_a_result_for_an_unknown_case_or_page_is_not_found(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    with pytest.raises(DomainError) as unknown_case:
        record(redaction_done(case_id, [new_id()]), store, fixed_now)
    assert unknown_case.value.code is ErrorCode.NOT_FOUND

    start(case_id, store, engine)
    with pytest.raises(DomainError) as unknown_page:
        record(classification_done(case_id, new_id()), store, fixed_now)
    assert unknown_page.value.code is ErrorCode.NOT_FOUND
    assert store.events == []


def test_story_1_6_events_are_read_in_time_order_whatever_order_they_arrived_in(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    first, second = new_id(), new_id()
    redacted = redaction_done(case_id, [first, second])
    later = datetime(2026, 10, 6, 12, 5, tzinfo=redacted.audit.occurred_at.tzinfo)
    earlier = datetime(2026, 10, 6, 12, 2, tzinfo=redacted.audit.occurred_at.tzinfo)
    # Two pages are classified side by side; the later result is recorded first.
    late = classification_done(case_id, second, occurred_at=later)
    early = classification_done(case_id, first, occurred_at=earlier)
    for result in (redacted, late, early):
        assert record(result, store, fixed_now) is True

    trail = asyncio.run(read_audit_trail(case_id, store=store))

    assert [event.page_id for event in trail.events] == [None, first, second]
    times = [event.occurred_at for event in trail.events]
    assert times == sorted(times)


def test_story_1_6_progress_and_audit_of_an_unknown_case_are_not_found(
    case_id: str, store: MemoryCaseStore
) -> None:
    with pytest.raises(DomainError) as no_progress:
        asyncio.run(read_progress(case_id, store=store))
    with pytest.raises(DomainError) as no_trail:
        asyncio.run(read_audit_trail(case_id, store=store))

    assert no_progress.value.code is ErrorCode.NOT_FOUND
    assert no_trail.value.code is ErrorCode.NOT_FOUND


# --- Which status may follow which -----------------------------------------------

FINAL_PAGES = {
    PageStatus.EXTRACTED,
    PageStatus.DISCARDED,
    PageStatus.DENIED,
    PageStatus.FAILED,
}


def test_story_1_6_one_table_says_which_status_may_follow_which() -> None:
    # Every status has its row; a final status has nothing after it.
    assert set(PAGE_TRANSITIONS) == set(PageStatus)
    assert set(CASE_TRANSITIONS) == set(CaseStatus)
    assert set(REDACTION_TRANSITIONS) == set(StageStatus)
    for status in PageStatus:
        assert (PAGE_TRANSITIONS[status] == frozenset()) == (status in FINAL_PAGES)
        # Nothing leads back to where a page began, or to itself.
        assert PageStatus.UPLOADED not in PAGE_TRANSITIONS[status]
        assert status not in PAGE_TRANSITIONS[status]
    assert CASE_TRANSITIONS[CaseStatus.FAILED] == frozenset()
    assert CASE_TRANSITIONS[CaseStatus.COMPLETED] == frozenset()
    assert REDACTION_TRANSITIONS[StageStatus.DONE] == frozenset()


def test_story_1_6_the_statuses_a_change_may_come_from_are_read_off_the_table() -> None:
    assert page_statuses_before(PageStatus.CLASSIFIED) == {PageStatus.UPLOADED}
    assert page_statuses_before(PageStatus.EXTRACTED) == {PageStatus.EXTRACTING}
    # A page can fail from any status that is not final.
    assert page_statuses_before(PageStatus.FAILED) == set(PageStatus) - FINAL_PAGES
    assert case_statuses_before(CaseStatus.FAILED) == {
        CaseStatus.RUNNING,
        CaseStatus.AWAITING_HUMAN,
    }
    assert redaction_statuses_before(StageStatus.DONE) == {StageStatus.RUNNING}
    assert CASE_STATUSES_TAKING_RESULTS == set(CaseStatus) - {CaseStatus.FAILED}


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
    events = len(store.events)

    outcome = outcome_of(classification_done(case_id, page_id), store, fixed_now)

    assert outcome is RecordOutcome.OUT_OF_ORDER
    # Not moved back, and no audit row for a change that did not happen.
    assert store.pages[page_id].page_status is PageStatus.EXTRACTING
    assert len(store.events) == events
    # Extraction before the page was sent to extraction is out of order too.
    store.pages[page_id].page_status = PageStatus.CLASSIFIED
    assert (
        outcome_of(facts_done(case_id, page_id), store, fixed_now)
        is RecordOutcome.OUT_OF_ORDER
    )


@pytest.mark.parametrize("final", sorted(FINAL_PAGES))
def test_story_1_6_a_result_for_a_page_in_a_final_status_changes_nothing(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    fixed_now: datetime,
    final: PageStatus,
) -> None:
    page_id = tracked_page(case_id, store, engine, fixed_now)
    store.pages[page_id].page_status = final
    events = len(store.events)

    for result in (
        classification_done(case_id, page_id),
        facts_done(case_id, page_id),
        # Not even a failure moves a page that is final, or fails its case.
        classification_failed(case_id, page_id),
    ):
        assert outcome_of(result, store, fixed_now) is RecordOutcome.OUT_OF_ORDER

    assert store.pages[page_id].page_status is final
    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    assert len(store.events) == events


def test_story_1_6_a_failed_case_takes_no_further_result(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    first, second = new_id(), new_id()
    record(redaction_done(case_id, [first, second]), store, fixed_now)
    record(classification_failed(case_id, first), store, fixed_now)
    events = len(store.events)

    for result in (
        classification_done(case_id, second),
        classification_failed(case_id, second),
        verdict_done(case_id),
    ):
        assert outcome_of(result, store, fixed_now) is RecordOutcome.CASE_FAILED

    assert store.cases[case_id].case_status is CaseStatus.FAILED
    assert store.pages[second].page_status is PageStatus.UPLOADED
    assert len(store.events) == events


def test_story_1_6_a_second_redaction_result_does_not_track_the_pages_again(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    page_id = tracked_page(case_id, store, engine, fixed_now)

    # The same pages, or others, under a new ref.
    again = outcome_of(redaction_done(case_id, [page_id]), store, fixed_now)
    others = outcome_of(redaction_done(case_id, [new_id()]), store, fixed_now)

    assert (again, others) == (
        RecordOutcome.PAGES_ALREADY_TRACKED,
        RecordOutcome.PAGES_ALREADY_TRACKED,
    )
    assert list(store.pages) == [page_id]
    assert len(store.events) == 1


def test_story_1_6_a_result_whose_audit_record_names_another_page_is_refused(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)
    first, second = new_id(), new_id()
    record(redaction_done(case_id, [first, second]), store, fixed_now)
    about_first = classification_done(case_id, first)
    wrong = about_first.model_copy(
        update={"audit": about_first.audit.model_copy(update={"page_id": second})}
    )
    # A case-level stage's record names no page at all.
    paged_redaction = redaction_failed(case_id).model_copy(
        update={
            "audit": redaction_failed(case_id).audit.model_copy(
                update={"page_id": first}
            )
        }
    )

    for result in (wrong, paged_redaction):
        with pytest.raises(DomainError) as raised:
            outcome_of(result, store, fixed_now)
        assert raised.value.code is ErrorCode.VALIDATION_FAILED

    assert len(store.events) == 1
    assert store.pages[second].page_status is PageStatus.UPLOADED


# --- A case whose orchestration cannot go on ---------------------------------------


def test_story_1_6_a_lifecycle_failure_is_one_case_level_stage_failed_event(
    case_id: str, fixed_now: datetime
) -> None:
    eval_run_id = new_id()

    recording = lifecycle_failure(
        case_id, occurred_at=fixed_now, eval_run_id=eval_run_id
    )

    audit = recording.audit
    assert recording.case_status is CaseStatus.FAILED
    assert recording.error_code is ErrorCode.STAGE_FAILED
    assert (audit.action, audit.page_id) == (AuditAction.STAGE_FAILED, None)
    assert (audit.actor_kind.value, audit.actor) == ("ai", LIFECYCLE_ACTOR)
    # The reference is the case, so the trail can hold this event only once.
    assert audit.ref == case_id
    assert (audit.eval_run_id, audit.trace_id) == (eval_run_id, "0" * 32)


def test_story_1_6_failing_a_case_changes_its_status_with_one_event_however_often(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, fixed_now: datetime
) -> None:
    start(case_id, store, engine)

    first = asyncio.run(fail_case(case_id, store=store, now=lambda: fixed_now))
    again = asyncio.run(fail_case(case_id, store=store, now=lambda: fixed_now))
    unknown = asyncio.run(fail_case(new_id(), store=store, now=lambda: fixed_now))

    assert (first, again) == (RecordOutcome.RECORDED, RecordOutcome.DUPLICATE)
    # An unknown case is answered, not raised: no retry could find it.
    assert unknown is RecordOutcome.UNKNOWN_CASE
    assert store.cases[case_id].case_status is CaseStatus.FAILED
    ((_, recording),) = store.events
    assert recording.audit.action is AuditAction.STAGE_FAILED


@pytest.mark.parametrize(
    ("existing", "status", "events"),
    [
        (EngineState.ACTIVE, CaseStatus.RUNNING, 0),
        (EngineState.COMPLETED, CaseStatus.RUNNING, 0),
        # Failed or terminated: nothing will ever run the case again.
        (EngineState.DEAD, CaseStatus.FAILED, 1),
    ],
)
def test_story_1_6_a_repeat_start_reports_the_case_as_it_is_now(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
    existing: EngineState,
    status: CaseStatus,
    events: int,
) -> None:
    start(case_id, store, engine)
    engine.existing = existing

    with caplog.at_level(logging.INFO):
        repeat = start(case_id, store, engine)
        once_more = start(case_id, store, engine)

    assert repeat.case_status is status
    assert once_more == repeat
    assert store.cases[case_id].case_status is status
    assert len(store.events) == events
    # The state is logged by id.
    assert (
        f"case started: case_id={case_id} orchestration={existing.value} "
        f"case_status={status.value}"
    ) in caplog.text
