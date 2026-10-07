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

import pytest
from durabletask import task
from durabletask.client import OrchestrationStatus
from workflow_fakes import (
    FakeStages,
    MemoryCaseStore,
    activity_task,
    finish_lifecycle,
    starting,
)

from contracts.enums import ClassifierContender, RetrieverConfig
from contracts.ids import new_id
from workflow.adapters import orchestration
from workflow.adapters.orchestration import (
    build_case_lifecycle,
)
from workflow.adapters.scheduler import (
    SDK_EVENT,
    Activities,
    ActivityFailed,
    SchedulerEngine,
    SdkLogFilter,
)
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import new_case
from workflow.domain.ports import EngineState

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
        return activity_task(self, activity, options)


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
    # Stories 2.5 and 2.6: the case names the retriever configurations it
    # was started with; each gets a verdict run once every page is final.
    started = {"case_id": case_id, "retriever_configs": ["r3"]}
    steps: Any = lifecycle(context, started)  # type: ignore[arg-type]  # the stand-in has the one method used
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
    # Story 2.4: what is left to answer are the extractions of the pages
    # that reached `extracting`; each is answered as done. Stories 2.5 and
    # 2.6: then the verdict runs, each done and recorded, and the step that
    # completes the case.
    finished = finish_lifecycle(steps, context)
    if finished is None:
        raise AssertionError("the orchestrator waited for more than it was answered")
    return context.asked, finished


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
    asyncio.run(store.start(*starting(case)))


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


# --- The scheduler client ------------------------------------------------------


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


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (OrchestrationStatus.RUNNING, EngineState.ACTIVE),
        (OrchestrationStatus.COMPLETED, EngineState.COMPLETED),
    ],
)
def test_story_1_6_a_case_that_has_an_orchestration_gets_no_second_one(
    case_id: str, status: OrchestrationStatus, state: EngineState
) -> None:
    client = FakeSchedulerClient(existing=Instance(status))

    # Its state is reported; nothing is created in its place.
    assert ensure_started(client, case_id) is state
    assert client.scheduled == []


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
