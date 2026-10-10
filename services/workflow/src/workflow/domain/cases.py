"""The operations of the lifecycle: start a case, record a result, read progress and audit."""

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from contracts.decisions import human_role
from contracts.enums import CaseStatus, DemoRole, RetrieverConfig
from contracts.errors import DomainError, ErrorCode
from contracts.models._stage import StageResult
from contracts.models.workflow import (
    AuditTrail,
    CaseProgress,
    CaseStarted,
    StartCaseRequest,
)
from workflow.domain.entities import SettledCase, StartParameters
from workflow.domain.gate import Route, route_recording
from workflow.domain.lifecycle import (
    case_started,
    new_case,
    resolve_start_parameters,
)
from workflow.domain.ports import CaseStore, EngineState, LifecycleEngine
from workflow.domain.recording import (
    RecordOutcome,
    case_started_event,
    lifecycle_failure,
    plan_recording,
)

logger = logging.getLogger(__name__)

UNKNOWN_CASE_MESSAGE = "That case could not be found."
UNKNOWN_PAGE_MESSAGE = "That page could not be found."
NOT_STARTED_MESSAGE = "The case could not be started. Please try again."
ACTOR_NOT_HUMAN_MESSAGE = "Only a person may start a case."

# How many events one read of a case's trail lists at most (WORKFLOW_AUDIT_TRAIL_LIMIT).
DEFAULT_AUDIT_TRAIL_LIMIT = 500


# AD-11: the ladder rows a case may run with unless the settings say otherwise
# (WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS).
DEFAULT_AVAILABLE_RETRIEVER_CONFIGS = frozenset(
    {RetrieverConfig.R1, RetrieverConfig.R2, RetrieverConfig.R3}
)
ROW_NOT_AVAILABLE_MESSAGE = "That retrieval row cannot be used yet."


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


def starting_role(request: StartCaseRequest | None) -> DemoRole:
    """The demo role that asks for a start; `actor_not_human` if the request names none (AD-9).

    A case is started by a person. A start with no actor, or with one that
    is blank or not a demo role, is refused whatever else it asks for.
    """
    actor = request.actor if request is not None else None
    role = human_role(actor) if actor is not None else None
    if role is None:
        raise DomainError(ErrorCode.ACTOR_NOT_HUMAN, ACTOR_NOT_HUMAN_MESSAGE)
    return role


async def start_case(
    case_id: str,
    request: StartCaseRequest | None,
    *,
    store: CaseStore,
    engine: LifecycleEngine,
    defaults: StartParameters,
    # AD-11: the ladder rows a case may run with in this build.
    available: frozenset[RetrieverConfig] = DEFAULT_AVAILABLE_RETRIEVER_CONFIGS,
    # The trace of the request that asks, for the `case.started` event; None
    # when there is none. Not left out: the caller says which.
    trace_id: str | None,
    now: Callable[[], datetime] = utc_now,
) -> CaseStarted:
    """Start a case: store it, and give it its one orchestration (AD-5).

    Idempotent on `case_id`. A repeat stores nothing, starts no second
    orchestration and answers with the case as it was first started, even if
    the repeat asks for other options or comes from the other role. If the
    first call stored the case and then failed to reach the engine, a repeat
    finishes the job.

    The case is stored with its `case.started` event, whose actor is the
    demo role that asked first (AD-8). A start that names no demo role is
    `actor_not_human`, and stores nothing. Nor does a start with a retriever
    row this build cannot run (`retriever_not_available`): such a case would
    fail at its last step, after every stage and every decision.
    """
    started = time.monotonic()
    try:
        role = starting_role(request)
    except DomainError as refusal:
        # security rule 31: the id and the code. The actor is the caller's
        # own text and is never logged.
        logger.warning(
            "case start refused: case_id=%s code=%s", case_id, refusal.code.value
        )
        raise
    parameters = resolve_start_parameters(request, defaults)
    if not set(parameters.retriever_configs) <= available:
        logger.warning(
            "case start refused: case_id=%s code=%s",
            case_id,
            ErrorCode.RETRIEVER_NOT_AVAILABLE.value,
        )
        raise DomainError(ErrorCode.RETRIEVER_NOT_AVAILABLE, ROW_NOT_AVAILABLE_MESSAGE)
    wanted = new_case(case_id, parameters, now())
    case = await store.start(
        wanted,
        case_started_event(
            case_id,
            role,
            occurred_at=wanted.created_at,
            eval_run_id=wanted.parameters.eval_run_id,
            trace_id=trace_id,
        ),
    )
    try:
        state = await engine.ensure_started(case)
    except Exception as error:
        # security rule 31: the error's type; its message can hold an address.
        logger.error(
            "case start failed: case_id=%s stage=schedule type=%s",
            case_id,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_STARTED_MESSAGE
        ) from error
    if state is EngineState.DEAD and case.case_status is not CaseStatus.FAILED:
        # The orchestration failed or was terminated and nothing will run the
        # case again. It is reported as it is now, and the trail says so.
        await fail_case(
            case_id, store=store, eval_run_id=case.parameters.eval_run_id, now=now
        )
        case = replace(case, case_status=CaseStatus.FAILED)
    logger.info(
        "case started: case_id=%s orchestration=%s case_status=%s duration_ms=%d",
        case_id,
        state.value,
        case.case_status.value,
        int((time.monotonic() - started) * 1000),
    )
    return case_started(case)


async def fail_case(
    case_id: str,
    *,
    store: CaseStore,
    eval_run_id: str | None = None,
    trace_id: str | None = None,
    now: Callable[[], datetime] = utc_now,
) -> RecordOutcome:
    """Mark a case failed because its orchestration cannot go on (AD-8).

    Goes through the recording path: the case status and one case-level
    `stage.failed` event, together. Safe to repeat.
    """
    recording = lifecycle_failure(
        case_id, occurred_at=now(), eval_run_id=eval_run_id, trace_id=trace_id
    )
    outcome = await store.record(recording, now())
    logger.warning("case failed: case_id=%s outcome=%s", case_id, outcome.value)
    return outcome


async def confirm_started(case_id: str, *, store: CaseStore) -> CaseStatus:
    """The orchestration's first step: confirm the case it runs was stored as started.

    The start stores the case as `running` before it asks for the
    orchestration, so this finds it; `not_found` means the engine holds an
    instance for a case this database does not know.
    """
    status = await store.status(case_id)
    if status is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return status


async def record_stage_result(
    result: StageResult,
    *,
    store: CaseStore,
    asked_afterwards: bool = False,
    now: Callable[[], datetime] = utc_now,
) -> RecordOutcome:
    """Record a stage result: its status changes and its audit event, together (AD-8).

    Returns how it was left. A result that was already recorded (an activity
    that ran again), or that came late or out of order, writes nothing and
    says so. An unknown case or page is `not_found`, which no retry can mend;
    any other failure is raised as it is, so the activity is tried again.
    `asked_afterwards` is for a verdict run asked for on a finished case:
    its result is recorded and changes no status.
    """
    recording = plan_recording(result, asked_afterwards=asked_afterwards)
    audit = recording.audit
    outcome = await store.record(recording, now())
    logger.info(
        "stage result %s: case_id=%s page_id=%s action=%s ref=%s error_code=%s",
        outcome.value,
        audit.case_id,
        audit.page_id,
        audit.action.value,
        audit.ref,
        recording.error_code.value if recording.error_code is not None else None,
    )
    if outcome is RecordOutcome.UNKNOWN_CASE:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    if outcome is RecordOutcome.UNKNOWN_PAGE:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    return outcome


async def record_route(
    case_id: str,
    page_id: str,
    classification_id: str,
    route: Route,
    threshold: float,
    *,
    store: CaseStore,
    eval_run_id: str | None = None,
    trace_id: str | None = None,
    now: Callable[[], datetime] = utc_now,
) -> tuple[RecordOutcome, Route | None]:
    """Record the gate's route of one page: its status and `page.routed`, together (AD-7, AD-8).

    Goes through the recording path, so it is safe to repeat: a route that
    is in the trail already writes nothing. Answers with how it was left and
    the route the trail holds for the page: the one given if it was written
    now, the one written before if it was there already (which is what the
    page was given, whatever is asked for now), and none if nothing is
    stored. An unknown case or page is `not_found`.
    """
    recording = route_recording(
        case_id,
        page_id,
        classification_id,
        route,
        threshold,
        occurred_at=now(),
        eval_run_id=eval_run_id,
        trace_id=trace_id,
    )
    outcome = await store.record(recording, now())
    logger.info(
        "page routed %s: case_id=%s page_id=%s route=%s ref=%s",
        outcome.value,
        case_id,
        page_id,
        route.value,
        classification_id,
    )
    if outcome is RecordOutcome.UNKNOWN_CASE:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    if outcome is RecordOutcome.UNKNOWN_PAGE:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    if outcome is RecordOutcome.RECORDED:
        return outcome, route
    if outcome is RecordOutcome.DUPLICATE:
        stored = await store.route_of(case_id, page_id, classification_id)
        return outcome, Route(stored.route.value) if stored is not None else None
    return outcome, None


async def settle_case_after_gate(
    case_id: str,
    *,
    store: CaseStore,
    # The trace of the caller, for a `case.completed` event; None when
    # there is none. Not left out: the caller says which.
    trace_id: str | None,
    now: Callable[[], datetime] = utc_now,
) -> SettledCase:
    """Give a case the status its pages give it after the gate; answer with the case as it is now.

    The store works the status out from the pages as they are stored, by
    the one rule (`domain/case_status.py`): waiting for a human, running,
    or, with every page final or the case told to stop there, completed. So
    a late repeat cannot undo what a decision has changed before it, and a
    case that has failed stays failed. A case this completes gets its
    `case.completed` event with the status, once. The answer also says what each page
    is at that moment, so the lifecycle waits for the pages that still wait
    and for no page that was decided already. `not_found` for a case never
    started.
    """
    settled = await store.settle_case(case_id, now(), trace_id)
    if settled is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    logger.info(
        "case after the gate: case_id=%s case_status=%s",
        case_id,
        settled.case_status.value,
    )
    return settled


async def read_progress(case_id: str, *, store: CaseStore) -> CaseProgress:
    """The case's status and page list; `not_found` for a case never started."""
    progress = await store.progress(case_id)
    if progress is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return progress


async def read_audit_trail(
    case_id: str, *, store: CaseStore, limit: int = DEFAULT_AUDIT_TRAIL_LIMIT
) -> AuditTrail:
    """The case's first audit events in the order they were recorded, at most `limit`.

    `not_found` for a case never started. A limit under 1 is the caller's
    mistake and is refused before anything is read.
    """
    if limit < 1:
        raise ValueError("the audit trail's limit must be at least 1")
    trail = await store.audit_trail(case_id, limit)
    if trail is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return trail
