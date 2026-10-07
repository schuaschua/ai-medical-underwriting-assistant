"""Story 1.6: the case orchestration, its activities and the scheduler client, without a scheduler."""

import asyncio
import inspect
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast

import grpc
import pytest
from azure.identity import ManagedIdentityCredential
from durabletask import task
from durabletask.client import OrchestrationStatus
from pydantic import ValidationError
from workflow_fakes import (
    FakeStages,
    MemoryCaseStore,
    redaction_done,
    redaction_failed,
)

from contracts.enums import ClassifierContender, RetrieverConfig
from contracts.ids import new_id
from workflow.adapters import orchestration
from workflow.adapters.orchestration import (
    CASE_LIFECYCLE,
    CONFIRM_CASE_STARTED,
    MARK_CASE_FAILED,
    activity_retry_policy,
    build_case_lifecycle,
)
from workflow.adapters.scheduler import (
    SDK_EVENT,
    Activities,
    ActivityFailed,
    ActivityRefused,
    DeadlineInterceptor,
    SchedulerEngine,
    SdkLogFilter,
    build_client,
    build_worker,
    scheduler_options,
)
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import case_started, new_case
from workflow.domain.ports import EngineState
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

CLIENT_ID = "00000000-0000-0000-0000-000000000001"
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=3
)


# --- The orchestrator ----------------------------------------------------------


@dataclass
class RecordingContext:
    """Stands in for the engine's context: it notes what the orchestrator asks for."""

    asked: list[dict[str, Any]] = field(default_factory=list)

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        # A task of the engine's own kind: the orchestrator may wait for
        # several of them together (story 1.8).
        return task.CompletableTask[Any]()


CONFIRMED = {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}
# Story 1.7: redaction, the first stage, is done and its result recorded.
# Story 1.8: it hands on the id of its one page, which is then classified.
REDACTED = {"outcome": "ok", "case_status": "running", "page_ids": [new_id()]}
# Story 1.9: a done classification hands on what the gate needs, and the gate
# routes the page (here to extraction, so the case goes on running).
CLASSIFIED = [
    {
        "outcome": "ok",
        "case_status": "running",
        "classification_id": new_id(),
        "is_medical": True,
        "confidence": 0.95,
    }
]
ROUTED = [{"outcome": "ok", "case_status": "running", "route": "extracting"}]


def run_lifecycle(
    case_id: str, answers: list[object]
) -> tuple[list[dict[str, Any]], Any]:
    """Run the orchestrator to its end, answering each activity in turn.

    An answer that is an exception is thrown into the orchestrator, as the
    engine does when an activity has failed on every retry.
    """
    context = RecordingContext()
    lifecycle = build_case_lifecycle(RETRY)
    steps: Any = lifecycle(context, {"case_id": case_id})  # type: ignore[arg-type]  # the stand-in has the one method used
    # `Any` again after the check, which narrows it to a generator of nothing.
    assert inspect.isgenerator(steps)
    steps = cast(Any, steps)
    next(steps)
    for answer in answers:
        try:
            if isinstance(answer, Exception):
                steps.throw(answer)
            else:
                steps.send(answer)
        except StopIteration as done:
            return context.asked, done.value
    raise AssertionError("the orchestrator asked for more activities than expected")


def test_story_1_6_the_orchestration_confirms_the_case_before_the_first_stage(
    case_id: str,
) -> None:
    asked, result = run_lifecycle(case_id, [CONFIRMED, REDACTED, CLASSIFIED, ROUTED])

    # The first activity, retried on failure (AD-6); ids only go in and out.
    # What follows it is redaction, the first stage (story 1.7).
    assert asked[0] == {
        "activity": CONFIRM_CASE_STARTED,
        "input": case_id,
        "retry_policy": RETRY,
    }
    assert result == {"case_id": case_id, "case_status": "running"}


def test_story_1_6_the_orchestrator_is_deterministic(case_id: str) -> None:
    # Replayed from history, the same answers must lead to the same requests.
    answers: list[object] = [CONFIRMED, REDACTED, CLASSIFIED, ROUTED]
    assert run_lifecycle(case_id, answers) == run_lifecycle(case_id, answers)
    failed = task.TaskFailedError("failed", RuntimeError("x"))
    assert run_lifecycle(case_id, [failed, {}]) == run_lifecycle(case_id, [failed, {}])

    # It holds sequencing only: no clock, no random value, no I/O (AD-5).
    source = inspect.getsource(orchestration.build_case_lifecycle)
    for forbidden in (
        "datetime",
        "time.",
        "random",
        "uuid",
        "new_id",
        "await ",
        "open(",
    ):
        assert forbidden not in source


def test_story_1_6_the_names_the_engine_keeps_are_the_registered_ones(
    store: MemoryCaseStore,
) -> None:
    activities = Activities(store, asyncio.new_event_loop(), 1.0, FakeStages())

    assert (
        task.get_name(build_case_lifecycle(RETRY)) == CASE_LIFECYCLE == "case_lifecycle"
    )
    assert task.get_name(activities.confirm_case_started) == CONFIRM_CASE_STARTED
    assert task.get_name(activities.mark_case_failed) == MARK_CASE_FAILED


@pytest.mark.parametrize(
    "first_answer",
    [
        # Every retry of the activity failed.
        task.TaskFailedError("activity failed", RuntimeError("secret")),
        # The activity answered that no retry can mend it.
        {"outcome": "refused", "reason": "not_found"},
    ],
    ids=["retries-exhausted", "refused"],
)
def test_story_1_6_a_case_that_cannot_go_on_is_marked_failed_by_the_orchestration(
    case_id: str, first_answer: object
) -> None:
    context = RecordingContext()
    eval_run_id = new_id()
    steps: Any = build_case_lifecycle(RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the one method used
        {"case_id": case_id, "eval_run_id": eval_run_id},
    )
    next(steps)
    if isinstance(first_answer, Exception):
        steps.throw(first_answer)
    else:
        steps.send(first_answer)
    with pytest.raises(StopIteration) as done:
        steps.send({"outcome": "ok", "recorded": "recorded"})

    # One more activity, through the recording path, and the run ends as failed
    # instead of leaving the case `running`.
    assert context.asked[1] == {
        "activity": MARK_CASE_FAILED,
        "input": {"case_id": case_id, "eval_run_id": eval_run_id},
        "retry_policy": RETRY,
    }
    assert len(context.asked) == 2
    assert done.value.value == {"case_id": case_id, "case_status": "failed"}


def test_story_1_6_failed_activities_are_retried_with_a_growing_wait() -> None:
    policy = activity_retry_policy(
        Settings(
            activity_max_attempts=4,
            activity_first_retry_seconds=1.5,
            activity_backoff_coefficient=3.0,
        )
    )

    assert policy.max_number_of_attempts == 4
    assert policy.first_retry_interval == timedelta(seconds=1.5)
    assert policy.backoff_coefficient == 3.0
    defaults = Settings()
    assert defaults.activity_max_attempts > 1
    assert defaults.activity_backoff_coefficient > 1


# --- The activities ------------------------------------------------------------


@contextmanager
def service_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop on a thread of its own, as the service's is to the worker."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def start(store: MemoryCaseStore, case_id: str) -> None:
    case = new_case(case_id, PARAMETERS, datetime.fromisoformat("2026-10-06T12:00:00Z"))
    asyncio.run(store.start(case))


def test_story_1_6_the_first_activity_reports_the_started_case_as_running(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, FakeStages())
        answer = activities.confirm_case_started(
            task.ActivityContext(case_id, 1), case_id
        )

    # Story 1.9: with the gate's threshold in force, for the case's history.
    assert answer == {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}


def test_story_1_6_an_error_no_retry_can_mend_is_answered_not_raised(
    store: MemoryCaseStore, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    with service_loop() as loop, caplog.at_level(logging.ERROR):
        activities = Activities(store, loop, 5.0, FakeStages())
        # An unknown case: raising would make the engine try again, in vain.
        answer = activities.confirm_case_started(
            task.ActivityContext(case_id, 1), case_id
        )
        with pytest.raises(ActivityRefused) as refused:
            activities.record("redact_document", redaction_done(case_id, [new_id()]))

    assert answer == {"outcome": "refused", "reason": "not_found"}
    assert refused.value.code.value == "not_found"
    assert not isinstance(refused.value, ActivityFailed)
    assert (
        f"activity failed: activity=confirm_case_started case_id={case_id} "
        "reason=not_found retry=False"
    ) in caplog.text


def test_story_1_6_the_failure_activity_marks_the_case_failed_with_one_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    eval_run_id = new_id()
    failed: dict[str, str | None] = {"case_id": case_id, "eval_run_id": eval_run_id}

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, FakeStages())
        first = activities.mark_case_failed(task.ActivityContext(case_id, 2), failed)
        again = activities.mark_case_failed(task.ActivityContext(case_id, 2), failed)
        unknown = activities.mark_case_failed(
            task.ActivityContext(case_id, 2), {"case_id": new_id(), "eval_run_id": None}
        )

    assert first == {"outcome": "ok", "recorded": "recorded"}
    assert again == {"outcome": "ok", "recorded": "duplicate"}
    # Answered, not raised: nothing can be marked for a case nobody stored.
    assert unknown == {"outcome": "ok", "recorded": "unknown_case"}
    assert store.cases[case_id].case_status.value == "failed"
    ((_, recording),) = store.events
    audit = recording.audit
    assert (audit.action.value, audit.page_id, audit.ref) == (
        "stage.failed",
        None,
        case_id,
    )
    assert audit.eval_run_id == eval_run_id


def test_story_1_6_a_closed_service_loop_is_an_activity_failure_the_engine_can_retry(
    store: MemoryCaseStore, case_id: str
) -> None:
    loop = asyncio.new_event_loop()
    loop.close()
    activities = Activities(store, loop, 5.0, FakeStages())

    with pytest.raises(ActivityFailed) as raised:
        activities.confirm_case_started(task.ActivityContext(case_id, 1), case_id)

    assert raised.value.reason == "LoopClosed"


def test_story_1_6_work_cancelled_under_an_activity_is_an_activity_failure(
    case_id: str,
) -> None:
    store = HangingStore()
    raised: list[BaseException] = []

    with service_loop() as loop:
        activities = Activities(store, loop, 30.0, FakeStages())

        def run() -> None:
            try:
                activities.confirm_case_started(
                    task.ActivityContext(case_id, 1), case_id
                )
            except BaseException as error:  # noqa: BLE001 - whatever escapes is what the test looks at
                raised.append(error)

        activity = threading.Thread(target=run)
        activity.start()
        asyncio.run_coroutine_threadsafe(store.entered.wait(), loop).result(5)

        # The service shuts down: every task on its loop is cancelled.
        def cancel_all() -> None:
            for pending in asyncio.all_tasks(loop):
                pending.cancel()

        loop.call_soon_threadsafe(cancel_all)
        activity.join(timeout=5)

    (error,) = raised
    assert isinstance(error, ActivityFailed)
    assert error.reason == "Cancelled"


def test_story_1_6_a_failing_activity_tells_the_engine_no_detail(
    store: MemoryCaseStore, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    start(store, case_id)
    store.fail = True

    with service_loop() as loop, caplog.at_level(logging.ERROR):
        activities = Activities(store, loop, 5.0, FakeStages())
        with pytest.raises(ActivityFailed) as raised:
            activities.confirm_case_started(task.ActivityContext(case_id, 1), case_id)

    # The engine keeps this message in the orchestration's history.
    assert str(raised.value) == "activity confirm_case_started failed: StoreDown"
    assert "reason=StoreDown retry=True" in caplog.text
    # ... together with its causes, so the original error is not attached.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "secret-store-detail" not in caplog.text


class HangingStore(MemoryCaseStore):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def status(self, case_id: str) -> None:
        # Never answers.
        self.entered.set()
        await asyncio.Event().wait()


def test_story_1_6_an_activity_whose_database_work_hangs_is_given_up(
    case_id: str,
) -> None:
    with service_loop() as loop:
        activities = Activities(HangingStore(), loop, 0.05, FakeStages())
        with pytest.raises(ActivityFailed) as raised:
            activities.confirm_case_started(task.ActivityContext(case_id, 1), case_id)

    assert raised.value.reason == "TimeoutError"


def test_story_1_6_stage_activities_record_their_result_through_one_path(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    result = redaction_done(case_id, [new_id()])

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, FakeStages())
        first = activities.record("redact_document", result)
        # The engine ran the activity again: nothing new is written.
        second = activities.record("redact_document", result)

    assert (first, second) == (RecordOutcome.RECORDED, RecordOutcome.DUPLICATE)
    assert len(store.events) == 1


def test_story_1_6_a_result_that_cannot_be_recorded_fails_the_activity_for_a_retry(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    result = redaction_failed(case_id)
    store.fail_audit_insert = True

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, FakeStages())
        with pytest.raises(ActivityFailed) as raised:
            activities.record("redact_document", result)
        assert store.events == []
        assert store.cases[case_id].case_status.value == "running"

        # The retry, once the database is back.
        store.fail_audit_insert = False
        assert activities.record("redact_document", result) is RecordOutcome.RECORDED

    assert raised.value.activity == "redact_document"
    assert store.cases[case_id].case_status.value == "failed"
    assert len(store.events) == 1


# --- The scheduler client ------------------------------------------------------


class FakeRpcError(grpc.RpcError):  # type: ignore[misc]  # grpc ships no type hints
    def __init__(self, status: grpc.StatusCode) -> None:
        super().__init__("secret-scheduler-detail")
        self._status = status

    def code(self) -> grpc.StatusCode:
        return self._status


@dataclass
class FakeSchedulerClient:
    existing: object | None = None
    schedule_error: Exception | None = None
    scheduled: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    def get_orchestration_state(
        self, instance_id: str, **options: Any
    ) -> object | None:
        return self.existing

    def schedule_new_orchestration(self, orchestrator: str, **options: Any) -> str:
        if self.schedule_error is not None:
            raise self.schedule_error
        self.scheduled.append({"orchestrator": orchestrator, **options})
        return str(options["instance_id"])

    def close(self) -> None:
        self.closed = True


@dataclass
class Instance:
    runtime_status: OrchestrationStatus


def ensure_started(client: FakeSchedulerClient, case_id: str) -> EngineState:
    engine = SchedulerEngine(client)  # type: ignore[arg-type]  # stands in for the library's client
    case = new_case(case_id, PARAMETERS, datetime.fromisoformat("2026-10-06T12:00:00Z"))
    return asyncio.run(engine.ensure_started(case))


def test_story_1_6_a_new_case_gets_an_orchestration_whose_instance_id_is_the_case_id(
    case_id: str,
) -> None:
    client = FakeSchedulerClient()

    assert ensure_started(client, case_id) is EngineState.CREATED

    (scheduled,) = client.scheduled
    assert scheduled["orchestrator"] == "case_lifecycle"
    assert scheduled["instance_id"] == case_id
    # Ids and the start parameters only (AD-6).
    case = new_case(case_id, PARAMETERS, datetime.fromisoformat("2026-10-06T12:00:00Z"))
    assert scheduled["input"] == case_started(case).model_dump(mode="json")
    # No existing instance may be replaced, whatever its state.
    assert list(scheduled["reuse_id_policy"].replaceableStatus) == []


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (OrchestrationStatus.PENDING, EngineState.ACTIVE),
        (OrchestrationStatus.RUNNING, EngineState.ACTIVE),
        (OrchestrationStatus.SUSPENDED, EngineState.ACTIVE),
        (OrchestrationStatus.COMPLETED, EngineState.COMPLETED),
        (OrchestrationStatus.FAILED, EngineState.DEAD),
        (OrchestrationStatus.TERMINATED, EngineState.DEAD),
    ],
)
def test_story_1_6_a_case_that_has_an_orchestration_gets_no_second_one(
    case_id: str, status: OrchestrationStatus, state: EngineState
) -> None:
    client = FakeSchedulerClient(existing=Instance(status))

    # Its state is reported; nothing is created in its place.
    assert ensure_started(client, case_id) is state
    assert client.scheduled == []


def test_story_1_6_two_starts_at_once_are_settled_by_the_scheduler(
    case_id: str,
) -> None:
    # Both saw no instance; the scheduler refuses the second create.
    client = FakeSchedulerClient(
        schedule_error=FakeRpcError(grpc.StatusCode.ALREADY_EXISTS)
    )

    assert ensure_started(client, case_id) is EngineState.ACTIVE


def test_story_1_6_any_other_scheduler_failure_is_raised(case_id: str) -> None:
    client = FakeSchedulerClient(
        schedule_error=FakeRpcError(grpc.StatusCode.UNAVAILABLE)
    )

    with pytest.raises(FakeRpcError):
        ensure_started(client, case_id)


def test_story_1_6_closing_the_engine_closes_its_client() -> None:
    client = FakeSchedulerClient()

    asyncio.run(SchedulerEngine(client).aclose())  # type: ignore[arg-type]  # as above

    assert client.closed is True


@dataclass
class CallDetails:
    method: str = "/TaskHubSidecarService/StartInstance"
    timeout: float | None = None
    metadata: Any = None
    credentials: Any = None
    wait_for_ready: Any = None
    compression: Any = None


def test_story_1_6_every_scheduler_call_has_a_deadline() -> None:
    seen: list[Any] = []

    def continuation(details: Any, request: Any) -> str:
        seen.append(details)
        return "answer"

    interceptor = DeadlineInterceptor(7.5)

    assert (
        interceptor.intercept_unary_unary(continuation, CallDetails(), "r") == "answer"
    )
    assert interceptor.intercept_unary_unary(continuation, CallDetails(timeout=60), "r")

    assert seen[0].timeout == 7.5
    assert seen[0].method == "/TaskHubSidecarService/StartInstance"
    # A call that set its own deadline (a wait for completion) keeps it.
    assert seen[1].timeout == 60


def test_story_1_6_the_emulator_is_reached_without_a_credential_on_plain_http() -> None:
    options = scheduler_options(Settings())

    assert options["host_address"] == "http://127.0.0.1:8080"
    assert options["taskhub"] == "default"
    assert options["token_credential"] is None
    assert options["secure_channel"] is False


def test_story_1_6_in_azure_the_scheduler_is_reached_with_the_service_identity() -> (
    None
):
    settings = Settings(
        scheduler_endpoint="https://dts-aiuw-demo-wus3.westus3.durabletask.io",
        scheduler_task_hub="aiuw-demo",
        scheduler_entra_auth=True,
        azure_client_id=CLIENT_ID,
    )

    options = scheduler_options(settings)

    assert options["taskhub"] == "aiuw-demo"
    assert options["secure_channel"] is True
    assert isinstance(options["token_credential"], ManagedIdentityCredential)


@pytest.mark.parametrize(
    "changes",
    [
        {"scheduler_endpoint": "127.0.0.1:8080"},
        # A token is never sent over an unencrypted channel.
        {
            "scheduler_endpoint": "http://scheduler.example.invalid",
            "scheduler_entra_auth": True,
        },
        {"default_retriever_configs": []},
        {"default_retriever_configs": ["r3", "r3"]},
        {"default_classifier_contender": "guess"},
        {"activity_max_attempts": 0},
        # Each activity needs a connection: never more at once than the pool holds.
        {"database_pool_size": 4, "worker_max_concurrent_activities": 5},
    ],
    ids=[
        "more-activities-than-connections",
        "no-scheme",
        "token-over-http",
        "no-configs",
        "repeated",
        "contender",
        "attempts",
    ],
)
def test_story_1_6_settings_that_cannot_work_are_refused(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**changes)


def test_story_1_6_building_the_client_and_the_worker_opens_no_connection(
    store: MemoryCaseStore,
) -> None:
    # A port nothing listens on: building must not need it.
    settings = Settings(scheduler_endpoint="http://127.0.0.1:1")
    loop = asyncio.new_event_loop()
    try:
        client = build_client(settings)
        worker = build_worker(settings, Activities(store, loop, 1.0, FakeStages()))
    finally:
        loop.close()

    client.close()
    assert worker is not None


def test_story_1_6_the_worker_runs_no_more_activities_at_once_than_the_pool_has_connections(
    store: MemoryCaseStore,
) -> None:
    settings = Settings(database_pool_size=3, worker_max_concurrent_activities=3)
    loop = asyncio.new_event_loop()
    try:
        worker = build_worker(settings, Activities(store, loop, 1.0, FakeStages()))
    finally:
        loop.close()

    assert worker.concurrency_options.maximum_concurrent_activity_work_items == 3
    defaults = Settings()
    assert defaults.worker_max_concurrent_activities <= defaults.database_pool_size


# --- The scheduler library's own log records -------------------------------------


def test_story_1_6_the_scheduler_librarys_log_records_lose_their_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = logging.getLogger("workflow.adapters.scheduler.sdk")
    address = "ipv4:10.1.2.3:8080"

    with caplog.at_level(logging.WARNING, logger=sdk.name):
        sdk.warning(f"Error in work item stream: failed to connect to {address}")
        sdk.error("Failed to deliver %s to %s", "payload-text", address)
        try:
            raise ConnectionError(f"refused by {address}")
        except ConnectionError:
            sdk.exception(f"Unexpected failure talking to {address}")
        # Below the logger's level: not written at all.
        sdk.info(f"Starting instance at {address}")

    records = [r for r in caplog.records if r.name == sdk.name]
    assert len(records) == 3
    for record in records:
        text = logging.Formatter("%(message)s").format(record)
        assert text.startswith(f"{SDK_EVENT} at=")
        assert "10.1.2.3" not in text
        assert "payload-text" not in text
        assert "Traceback" not in text
        assert (record.args, record.exc_info, record.stack_info) == (None, None, None)
    assert records[0].getMessage().endswith("type=none")
    # The exception's type is kept, its message is not.
    assert records[2].getMessage().endswith("type=ConnectionError")
    assert any(isinstance(item, SdkLogFilter) for item in sdk.filters)
