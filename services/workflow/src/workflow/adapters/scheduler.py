"""Durable Task Scheduler adapter: the client that starts cases and the worker that runs them (AD-5).

The worker runs activities on its own threads, while the database adapter
lives on the service's event loop. `Activities` carries each activity's work
over to that loop and waits for it.
"""

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable, Coroutine
from typing import Any, NamedTuple

import grpc
from durabletask import task
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.azuremanaged.worker import DurableTaskSchedulerWorker
from durabletask.client import OrchestrationState, OrchestrationStatus
from durabletask.internal import orchestrator_service_pb2 as pb
from durabletask.worker import ConcurrencyOptions

from contracts.enums import CaseStatus, ClassifierContender, StageStatus
from contracts.errors import DomainError, ErrorCode
from contracts.models._stage import StageResult
from workflow.adapters.credential import azure_credential
from workflow.adapters.dapr import trace_headers
from workflow.adapters.orchestration import (
    CASE_LIFECYCLE,
    CASE_STATUS,
    CLASSIFY_PAGE,
    CONFIRM_CASE_STARTED,
    MARK_CASE_FAILED,
    OK,
    OUTCOME,
    PAGE_IDS,
    REDACT_DOCUMENT,
    REFUSED,
    activity_retry_policy,
    build_case_lifecycle,
    stage_retry_policy,
)
from workflow.adapters.telemetry import current_trace_id
from workflow.domain.cases import confirm_started, fail_case, record_stage_result
from workflow.domain.entities import CaseRecord
from workflow.domain.lifecycle import case_started
from workflow.domain.ports import CaseStore, EngineState, StageServices
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

logger = logging.getLogger(__name__)

SDK_EVENT = "scheduler_sdk_event"


class SdkLogFilter(logging.Filter):
    """Reduces a record of the scheduler library to a fixed code and an error type.

    The library writes whole error texts, arguments and tracebacks into its
    messages, and those can hold addresses and payloads. What is kept: where
    in the library the record was made, and the type of the exception it
    carries (security rule 31).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[0] if record.exc_info else None
        record.msg = (
            f"{SDK_EVENT} at={record.module}:{record.lineno} "
            f"type={error.__qualname__ if error is not None else 'none'}"
        )
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


# The library logs every work item at INFO. Its records go to a logger of
# ours, held to warnings and stripped of their text.
_sdk_logger = logging.getLogger(f"{__name__}.sdk")
_sdk_logger.setLevel(logging.WARNING)
_sdk_logger.addFilter(SdkLogFilter())

# Orchestrations in these states will never run again.
_DEAD = frozenset({OrchestrationStatus.FAILED, OrchestrationStatus.TERMINATED})
# Errors that the same call would meet again, however often it is repeated.
_PERMANENT = frozenset({ErrorCode.NOT_FOUND, ErrorCode.VALIDATION_FAILED})
# How a recorded stage result left the trail: written now, or there already.
_IN_THE_TRAIL = frozenset({RecordOutcome.RECORDED, RecordOutcome.DUPLICATE})
# What the wait for a stage call adds to the call's own deadline, so the
# call's own error is what the activity reports.
_STAGE_CALL_SLACK_SECONDS = 5.0


def _engine_state(existing: OrchestrationState | None) -> EngineState:
    if existing is None or existing.runtime_status not in (
        _DEAD | {OrchestrationStatus.COMPLETED}
    ):
        return EngineState.ACTIVE
    if existing.runtime_status is OrchestrationStatus.COMPLETED:
        return EngineState.COMPLETED
    return EngineState.DEAD


class _CallDetails(NamedTuple):
    method: Any
    timeout: Any
    metadata: Any
    credentials: Any
    wait_for_ready: Any
    compression: Any


class _ClientCallDetails(_CallDetails, grpc.ClientCallDetails):  # type: ignore[misc]  # grpc ships no type hints, so its base class is Any to mypy
    """The call details an interceptor hands on, with a deadline filled in."""


class DeadlineInterceptor(grpc.UnaryUnaryClientInterceptor):  # type: ignore[misc]  # grpc ships no type hints, so its base class is Any to mypy
    """Gives every call to the scheduler a deadline, so none waits for ever."""

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def intercept_unary_unary(
        self,
        continuation: Callable[[Any, Any], Any],
        client_call_details: Any,
        request: Any,
    ) -> Any:
        if client_call_details.timeout is None:
            client_call_details = _ClientCallDetails(
                client_call_details.method,
                self._timeout_seconds,
                client_call_details.metadata,
                client_call_details.credentials,
                client_call_details.wait_for_ready,
                getattr(client_call_details, "compression", None),
            )
        return continuation(client_call_details, request)


def scheduler_options(settings: Settings) -> dict[str, Any]:
    """How the client and the worker reach the scheduler: the same way, both."""
    return {
        "host_address": settings.scheduler_endpoint,
        "taskhub": settings.scheduler_task_hub,
        # The emulator takes no credential; in Azure the service identity
        # holds Durable Task Data Contributor on the task hub.
        "token_credential": azure_credential(settings)
        if settings.scheduler_entra_auth
        else None,
        "secure_channel": settings.scheduler_secure,
        "logger": _sdk_logger,
    }


def build_client(settings: Settings) -> DurableTaskSchedulerClient:
    """The scheduler client. Building it makes no network call."""
    return DurableTaskSchedulerClient(
        **scheduler_options(settings),
        interceptors=[DeadlineInterceptor(settings.scheduler_timeout_seconds)],
    )


class SchedulerEngine:
    """Starts the one orchestration of a case, whose instance id is the `case_id`."""

    def __init__(self, client: DurableTaskSchedulerClient) -> None:
        self._client = client

    async def ensure_started(self, case: CaseRecord) -> EngineState:
        """Create the case's orchestration unless it has one; say what state it is in."""
        # The library's client blocks, so it runs on a worker thread; each of
        # its calls has a deadline (`DeadlineInterceptor`).
        return await asyncio.to_thread(self._ensure_started, case)

    def _state(self, case_id: str) -> OrchestrationState | None:
        return self._client.get_orchestration_state(case_id, fetch_payloads=False)

    def _ensure_started(self, case: CaseRecord) -> EngineState:
        existing = self._state(case.case_id)
        if existing is not None:
            # Whatever its state, running or finished: one case, one orchestration.
            return _engine_state(existing)
        try:
            self._client.schedule_new_orchestration(
                CASE_LIFECYCLE,
                input=case_started(case).model_dump(mode="json"),
                instance_id=case.case_id,
                # No status may be replaced: without this the scheduler would
                # put a new run in the place of a finished one. It also
                # settles two starts that arrive together: the second is refused.
                reuse_id_policy=pb.OrchestrationIdReusePolicy(),
            )
        except grpc.RpcError as error:
            if error.code() is grpc.StatusCode.ALREADY_EXISTS:
                return _engine_state(self._state(case.case_id))
            raise
        return EngineState.CREATED

    async def aclose(self) -> None:
        await asyncio.to_thread(self._client.close)


class ActivityFailed(Exception):
    """What a failed activity reports to the engine: a name and a code, no detail.

    The engine keeps a failure's message in the orchestration's history, so
    the original error, whose message can hold SQL or an address, stays here.
    The engine tries a failed activity again.
    """

    def __init__(self, activity: str, reason: str) -> None:
        super().__init__(f"activity {activity} failed: {reason}")
        self.activity = activity
        self.reason = reason


class ActivityRefused(Exception):
    """An error no retry can mend: an unknown case or page, a result that is not valid.

    An activity catches it and answers with it. It must never reach the
    engine as a failure, because the engine would run the activity again.
    """

    def __init__(self, activity: str, code: ErrorCode) -> None:
        super().__init__(f"activity {activity} refused: {code.value}")
        self.activity = activity
        self.code = code


class Activities:
    """The activities of the case orchestration.

    Each one may run more than once (AD-6): the engine tries a failed
    activity again, and repeats one whose answer it lost.
    """

    def __init__(
        self,
        store: CaseStore,
        loop: asyncio.AbstractEventLoop,
        timeout_seconds: float,
        stages: StageServices,
        stage_timeout_seconds: float = 200.0,
    ) -> None:
        self._store = store
        self._loop = loop
        self._timeout_seconds = timeout_seconds
        self._stages = stages
        # AD-6: longer than a stage's own deadline of 180 s.
        self._stage_timeout_seconds = stage_timeout_seconds

    def confirm_case_started(
        self, context: task.ActivityContext, case_id: str
    ) -> dict[str, str]:
        """The lifecycle's first step: the case the engine runs is the one that was stored."""
        try:
            status = self._run(
                CONFIRM_CASE_STARTED,
                case_id,
                confirm_started(case_id, store=self._store),
            )
        except ActivityRefused as refused:
            return {OUTCOME: REFUSED, "reason": refused.code.value}
        return {OUTCOME: OK, "case_status": status.value}

    def redact_document(
        self, context: task.ActivityContext, command: dict[str, str | None]
    ) -> dict[str, Any]:
        """AD-21: have `intake` redact the document, and record the result it stored.

        One call to the stage, then the one recording path. `in_progress`, or
        no answer, fails the activity and the engine sends the command again
        (AD-6): `intake` answers a repeat with its stored result. A case
        `intake` does not hold is answered as refused, not retried. A done
        redaction's answer hands the ids of its pages on to the orchestration,
        in document order: ids only (AD-6).
        """
        case_id = str(command["case_id"])
        try:
            result = self._run(
                REDACT_DOCUMENT,
                case_id,
                self._stages.redact_document(
                    case_id,
                    eval_run_id=command.get("eval_run_id"),
                    # Read here, on the activity's thread, where its trace is.
                    trace_context=trace_headers(),
                ),
                self._stage_timeout_seconds + _STAGE_CALL_SLACK_SECONDS,
            )
            outcome = self.record(REDACT_DOCUMENT, result)
        except ActivityRefused as refused:
            return {OUTCOME: REFUSED, "reason": refused.code.value}
        answer = self._stage_answer(REDACT_DOCUMENT, result, outcome)
        if answer.get(CASE_STATUS) == CaseStatus.RUNNING.value:
            return {**answer, PAGE_IDS: list(result.page_ids)}
        return answer

    def classify_page(
        self, context: task.ActivityContext, command: dict[str, str | None]
    ) -> dict[str, str]:
        """AD-13: have `classification` classify one page, and record the result it stored.

        As for redaction: one call to the stage, then the one recording path;
        `in_progress`, or no answer, fails the activity and the engine sends
        the command again. A page that is not the case's, or a contender the
        stage cannot run, is answered as refused, not retried. A done result
        moves its page to `classified`; a failed one fails the page and the
        case (AD-8). Nothing here routes the page (AD-7).
        """
        case_id = str(command["case_id"])
        page_id = str(command["page_id"])
        try:
            # The contender the case was started with, as the start stored it.
            contender = ClassifierContender(str(command.get("contender")))
        except ValueError:
            logger.error(
                "activity failed: activity=%s case_id=%s reason=unknown_contender "
                "retry=False",
                CLASSIFY_PAGE,
                case_id,
            )
            return {OUTCOME: REFUSED, "reason": ErrorCode.VALIDATION_FAILED.value}
        try:
            result = self._run(
                CLASSIFY_PAGE,
                case_id,
                self._stages.classify_page(
                    case_id,
                    page_id,
                    contender,
                    eval_run_id=command.get("eval_run_id"),
                    # Read here, on the activity's thread, where its trace is.
                    trace_context=trace_headers(),
                ),
                self._stage_timeout_seconds + _STAGE_CALL_SLACK_SECONDS,
            )
            outcome = self.record(CLASSIFY_PAGE, result)
        except ActivityRefused as refused:
            return {OUTCOME: REFUSED, "reason": refused.code.value}
        return self._stage_answer(CLASSIFY_PAGE, result, outcome)

    @staticmethod
    def _stage_answer(
        activity: str, result: StageResult, outcome: RecordOutcome
    ) -> dict[str, str]:
        """What a stage activity answers the orchestration with, once its result is recorded."""
        if outcome is RecordOutcome.CASE_FAILED or (
            result.status is StageStatus.FAILED and outcome in _IN_THE_TRAIL
        ):
            # The failed result failed the case, with its `stage.failed`
            # event; or the case had failed before this result came.
            return {OUTCOME: OK, CASE_STATUS: CaseStatus.FAILED.value}
        if outcome in _IN_THE_TRAIL:
            return {OUTCOME: OK, CASE_STATUS: CaseStatus.RUNNING.value}
        # The result contradicts what is stored: the case cannot go on.
        logger.error(
            "stage result not recorded: activity=%s case_id=%s outcome=%s",
            activity,
            result.case_id,
            outcome.value,
        )
        return {OUTCOME: REFUSED, "reason": outcome.value}

    def mark_case_failed(
        self, context: task.ActivityContext, failed: dict[str, str | None]
    ) -> dict[str, str]:
        """Mark a case failed when its orchestration cannot go on: status and event together."""
        case_id = str(failed["case_id"])
        outcome = self._run(
            MARK_CASE_FAILED,
            case_id,
            fail_case(
                case_id,
                store=self._store,
                eval_run_id=failed.get("eval_run_id"),
                trace_id=current_trace_id(None),
            ),
        )
        return {OUTCOME: OK, "recorded": outcome.value}

    def record(self, activity: str, result: StageResult) -> RecordOutcome:
        """Record a stage result: the one path every stage activity ends with (AD-8).

        A stage activity makes its one call to the stage service and hands
        the result here. If it cannot be recorded the activity fails, the
        engine runs it again, the stage answers with its stored result, and
        recording it a second time writes nothing new. A result that can
        never be recorded raises `ActivityRefused`, which the activity
        answers with instead of failing.
        """
        return self._run(
            activity, result.case_id, record_stage_result(result, store=self._store)
        )

    def _run[T](
        self,
        activity: str,
        case_id: str,
        work: Coroutine[Any, Any, T],
        timeout_seconds: float | None = None,
    ) -> T:
        refused: ErrorCode | None = None
        if timeout_seconds is None:
            timeout_seconds = self._timeout_seconds
        try:
            future = asyncio.run_coroutine_threadsafe(work, self._loop)
        except RuntimeError:
            # The service's loop is closed: the service is shutting down.
            work.close()
            reason = "LoopClosed"
        else:
            try:
                return future.result(timeout_seconds)
            except concurrent.futures.CancelledError:
                # The loop was stopped, or the work cancelled, under the
                # activity. Not an `Exception`, so it is named here.
                reason = "Cancelled"
            except Exception as error:  # noqa: BLE001 - whatever failed, the engine is told so without the detail
                future.cancel()
                # security rule 31: an id and a code or type, never the message.
                if isinstance(error, DomainError):
                    reason = error.code.value
                    refused = error.code if error.code in _PERMANENT else None
                else:
                    reason = type(error).__qualname__
        logger.error(
            "activity failed: activity=%s case_id=%s reason=%s retry=%s",
            activity,
            case_id,
            reason,
            refused is None,
        )
        # Raised outside the handlers, so the original error is not attached
        # to this one: the engine stores a failure together with its causes.
        if refused is not None:
            raise ActivityRefused(activity, refused)
        raise ActivityFailed(activity, reason)


def build_worker(
    settings: Settings, activities: Activities
) -> DurableTaskSchedulerWorker:
    """The worker that runs the case orchestration and its activities. Not started here."""
    worker = DurableTaskSchedulerWorker(
        **scheduler_options(settings),
        # Each activity uses a database connection: never more at once than
        # the pool holds (the settings check that).
        concurrency_options=ConcurrencyOptions(
            maximum_concurrent_activity_work_items=settings.worker_max_concurrent_activities
        ),
    )
    worker.add_orchestrator(
        build_case_lifecycle(
            activity_retry_policy(settings), stage_retry_policy(settings)
        )
    )
    worker.add_activity(activities.confirm_case_started)
    worker.add_activity(activities.redact_document)
    worker.add_activity(activities.classify_page)
    worker.add_activity(activities.mark_case_failed)
    return worker
