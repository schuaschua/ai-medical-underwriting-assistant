"""The verdict stage in the lifecycle: completing a case, and one more run on a finished one (AD-15).

Once every page of a case is final the lifecycle commands one verdict run
per retriever configuration the case was started with, records each, and
only then completes the case. Later anyone may ask for one more run, with
another configuration: that is an orchestration of its own. `workflow`
sequences; `verdict` does the work and never starts itself. What a run
suggests is never a decision, and nothing here stores one (AD-10).
"""

import logging
from collections.abc import Callable
from datetime import datetime

from contracts.enums import CaseStatus, RetrieverConfig, StageStatus
from contracts.errors import DomainError, ErrorCode
from contracts.models.workflow import VerdictRunRequest, VerdictRunRequested
from workflow.domain.case_status import pages_are_final
from workflow.domain.cases import (
    DEFAULT_AVAILABLE_RETRIEVER_CONFIGS,
    ROW_NOT_AVAILABLE_MESSAGE,
    UNKNOWN_CASE_MESSAGE,
    utc_now,
)
from workflow.domain.entities import SettledCase
from workflow.domain.ports import CaseStore, LifecycleEngine

logger = logging.getLogger(__name__)

PAGES_NOT_TERMINAL_MESSAGE = (
    "A verdict run cannot be made yet: a page of the case is not final."
)
NOT_FINISHED_MESSAGE = (
    "A verdict run cannot be asked for yet: the case's own runs are still under way."
)
CASE_FAILED_MESSAGE = "A verdict run cannot be made: the case has failed."
NOT_REQUESTED_MESSAGE = "The verdict run could not be started. Please try again."


def verdict_run_instance_id(case_id: str, retriever_config: str) -> str:
    """AD-15: the instance id of the orchestration of an asked-for verdict run."""
    return f"{case_id}:verdict:{retriever_config}"


async def complete_case(
    case_id: str,
    *,
    store: CaseStore,
    # The trace of the caller, for the `case.completed` event; None when
    # there is none. Not left out: the caller says which.
    trace_id: str | None,
    now: Callable[[], datetime] = utc_now,
) -> SettledCase:
    """Complete a case after its verdict runs: the lifecycle's last step.

    The store completes the case only if every page is final and a
    `verdict.suggested` event is in the trail for each of its retriever
    configurations, and says what the case is afterwards. Safe to repeat.
    `not_found` for a case never started.
    """
    settled = await store.complete_case(case_id, now(), trace_id)
    if settled is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    logger.info(
        "case after its verdict runs: case_id=%s case_status=%s",
        case_id,
        settled.case_status.value,
    )
    return settled


async def request_verdict_run(
    case_id: str,
    request: VerdictRunRequest,
    *,
    store: CaseStore,
    engine: LifecycleEngine,
    # AD-11: the ladder rows a case may run with in this build.
    available: frozenset[RetrieverConfig] = DEFAULT_AVAILABLE_RETRIEVER_CONFIGS,
) -> VerdictRunRequested:
    """Ask for one more verdict run on a case whose pages are all final (the Compare toggle, AD-11).

    Idempotent on the case and the retriever configuration: the run is an
    orchestration of its own, one per pair, so asking again starts nothing
    new and answers with the state of the run there is; only when the
    earlier orchestration ended without a run stored at `verdict` is the run
    scheduled again. A run that failed is answered with its error code.
    `not_found` for a case never started; `retriever_not_available` for a
    row this build cannot run; `pages_not_terminal` while a page is not final, for
    a case with no page yet, while the case's own runs are still under way
    (the case is not `completed` yet), and for a case that has failed, whose
    runs could not be recorded.
    """
    case = await store.case(case_id)
    progress = await store.progress(case_id) if case is not None else None
    if case is None or progress is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    config = request.retriever_config
    if config not in available:
        logger.info(
            "verdict run refused: case_id=%s retriever_config=%s code=%s case_status=%s",
            case_id,
            config.value,
            ErrorCode.RETRIEVER_NOT_AVAILABLE.value,
            case.case_status.value,
        )
        raise DomainError(ErrorCode.RETRIEVER_NOT_AVAILABLE, ROW_NOT_AVAILABLE_MESSAGE)
    refusal: str | None = None
    if not pages_are_final(page.page_status for page in progress.pages):
        refusal = PAGES_NOT_TERMINAL_MESSAGE
    elif case.case_status is CaseStatus.FAILED:
        refusal = CASE_FAILED_MESSAGE
    elif case.case_status is not CaseStatus.COMPLETED:
        # Every page is final and the lifecycle's own runs are under way.
        # One more run is for a finished case: asked for now, its result
        # could be recorded in the place of the lifecycle's own.
        refusal = NOT_FINISHED_MESSAGE
    if refusal is not None:
        logger.info(
            "verdict run refused: case_id=%s retriever_config=%s code=%s case_status=%s",
            case_id,
            config.value,
            ErrorCode.PAGES_NOT_TERMINAL.value,
            case.case_status.value,
        )
        raise DomainError(ErrorCode.PAGES_NOT_TERMINAL, refusal)
    try:
        state = await engine.ensure_verdict_run(case, config)
    except Exception as error:
        # security rule 31: the error's type; its message can hold an address.
        logger.error(
            "verdict run request failed: case_id=%s retriever_config=%s type=%s",
            case_id,
            config.value,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_REQUESTED_MESSAGE
        ) from error
    logger.info(
        "verdict run requested: case_id=%s retriever_config=%s status=%s verdict_run_id=%s",
        case_id,
        config.value,
        state.status.value,
        state.verdict_run_id,
    )
    return VerdictRunRequested(
        case_id=case_id,
        retriever_config=config,
        status=state.status,
        verdict_run_id=state.verdict_run_id,
        # A failed run always says why; `stage_failed` when the engine holds
        # no code for it (its orchestration died).
        error_code=(state.error_code or ErrorCode.STAGE_FAILED)
        if state.status is StageStatus.FAILED
        else None,
    )
