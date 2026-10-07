"""The operations of the lifecycle: start a case, record a result, read progress and audit."""

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from contracts.enums import CaseStatus
from contracts.errors import DomainError, ErrorCode
from contracts.models._stage import StageResult
from contracts.models.workflow import (
    AuditTrail,
    CaseProgress,
    CaseStarted,
    StartCaseRequest,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import STATUSES_THE_GATE_SETS, Route, route_recording
from workflow.domain.lifecycle import (
    case_started,
    new_case,
    resolve_start_parameters,
)
from workflow.domain.ports import CaseStore, EngineState, LifecycleEngine
from workflow.domain.recording import (
    RecordOutcome,
    lifecycle_failure,
    plan_recording,
)

logger = logging.getLogger(__name__)

UNKNOWN_CASE_MESSAGE = "That case could not be found."
UNKNOWN_PAGE_MESSAGE = "That page could not be found."
NOT_A_GATE_STATUS_MESSAGE = "The gate does not give a case that status."
NOT_STARTED_MESSAGE = "The case could not be started. Please try again."


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


async def start_case(
    case_id: str,
    request: StartCaseRequest | None,
    *,
    store: CaseStore,
    engine: LifecycleEngine,
    defaults: StartParameters,
    now: Callable[[], datetime] = utc_now,
) -> CaseStarted:
    """Start a case: store it, and give it its one orchestration (AD-5).

    Idempotent on `case_id`. A repeat stores nothing, starts no second
    orchestration and answers with the case as it was first started, even if
    the repeat asks for other options. If the first call stored the case and
    then failed to reach the engine, a repeat finishes the job.
    """
    started = time.monotonic()
    wanted = new_case(case_id, resolve_start_parameters(request, defaults), now())
    case = await store.start(wanted)
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
    now: Callable[[], datetime] = utc_now,
) -> RecordOutcome:
    """Record a stage result: its status changes and its audit event, together (AD-8).

    Returns how it was left. A result that was already recorded (an activity
    that ran again), or that came late or out of order, writes nothing and
    says so. An unknown case or page is `not_found`, which no retry can mend;
    any other failure is raised as it is, so the activity is tried again.
    """
    recording = plan_recording(result)
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
    case_status: CaseStatus,
    *,
    store: CaseStore,
    now: Callable[[], datetime] = utc_now,
) -> CaseStatus:
    """Give a case the status the gate left it with; answer with the status it has now.

    The gate leaves a case waiting for a human or, told to stop there,
    completed: any other status is `validation_failed`. A case that has
    failed in the meantime stays failed. Safe to repeat. `not_found` for a
    case never started.
    """
    if case_status not in STATUSES_THE_GATE_SETS:
        raise DomainError(ErrorCode.VALIDATION_FAILED, NOT_A_GATE_STATUS_MESSAGE)
    status = await store.move_case(case_id, case_status, now())
    if status is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    logger.info(
        "case after the gate: case_id=%s wanted=%s case_status=%s",
        case_id,
        case_status.value,
        status.value,
    )
    return status


async def read_progress(case_id: str, *, store: CaseStore) -> CaseProgress:
    """The case's status and page list; `not_found` for a case never started."""
    progress = await store.progress(case_id)
    if progress is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return progress


async def read_audit_trail(case_id: str, *, store: CaseStore) -> AuditTrail:
    """The case's audit events in time order; `not_found` for a case never started."""
    trail = await store.audit_trail(case_id)
    if trail is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return trail
