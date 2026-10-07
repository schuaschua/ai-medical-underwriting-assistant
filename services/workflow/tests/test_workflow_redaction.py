"""Story 1.7: redaction is the first stage `workflow` commands.

Unit tests: the orchestrator's next step, the redaction activity and the
client module that reaches `intake` through the Dapr sidecar. No scheduler,
no database, no network.
"""

import asyncio
import inspect
import json
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from durabletask import task
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from workflow_fakes import (
    TRACE_ID,
    FakeStages,
    MemoryCaseStore,
    SidecarStandIn,
    redaction_done,
)

from contracts.enums import ClassifierContender, RetrieverConfig, StopAfter
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.operations import get_operation
from workflow.adapters.dapr import (
    StageClient,
    build_http_client,
    invoke_path,
    sidecar_base_url,
)
from workflow.adapters.orchestration import (
    CONFIRM_CASE_STARTED,
    MARK_CASE_FAILED,
    REDACT_DOCUMENT,
    build_case_lifecycle,
    stage_retry_policy,
)
from workflow.adapters.scheduler import (
    Activities,
    ActivityFailed,
    build_worker,
)
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import plan_recording
from workflow.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=3
)
STAGE_RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=9
)
CONFIRMED = {"outcome": "ok", "case_status": "running"}
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


# --- The orchestrator --------------------------------------------------------------


class RecordingContext:
    """Stands in for the engine's context: it notes what the orchestrator asks for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        # A task of the engine's own kind: the orchestrator may wait for
        # several of them together (story 1.8).
        return task.CompletableTask[Any]()


def run_lifecycle(
    started: dict[str, Any], answers: list[object]
) -> tuple[list[dict[str, Any]], Any]:
    """Run the orchestrator to its end, answering each activity in turn."""
    context = RecordingContext()
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(context, started)  # type: ignore[arg-type]  # the stand-in has the one method used
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


def test_story_1_7_redaction_is_the_first_stage_the_orchestration_commands(
    case_id: str,
) -> None:
    eval_run_id = new_id()
    started = {"case_id": case_id, "eval_run_id": eval_run_id}

    asked, result = run_lifecycle(
        started,
        [
            CONFIRMED,
            {"outcome": "ok", "case_status": "running", "page_ids": [new_id()]},
            [{"outcome": "ok", "case_status": "running"}],
        ],
    )

    # Right after the confirm step. Ids only go in (AD-6), and the command is
    # retried with the stage policy.
    assert [step["activity"] for step in asked[:2]] == [
        CONFIRM_CASE_STARTED,
        REDACT_DOCUMENT,
    ]
    assert asked[1] == {
        "activity": REDACT_DOCUMENT,
        "input": {"case_id": case_id, "eval_run_id": eval_run_id},
        "retry_policy": STAGE_RETRY,
    }
    # A done redaction leaves the case running. What follows it, one classify
    # command per page, is story 1.8's (test_workflow_classification.py).
    assert result == {"case_id": case_id, "case_status": "running"}


def test_story_1_7_a_failed_redaction_ends_the_case_as_failed_without_a_second_event(
    case_id: str,
) -> None:
    asked, result = run_lifecycle(
        {"case_id": case_id}, [CONFIRMED, {"outcome": "ok", "case_status": "failed"}]
    )

    # The recording of the failed result failed the case and wrote its one
    # `stage.failed` event: the failure activity is not asked for as well.
    assert [step["activity"] for step in asked] == [
        CONFIRM_CASE_STARTED,
        REDACT_DOCUMENT,
    ]
    assert result == {"case_id": case_id, "case_status": "failed"}


@pytest.mark.parametrize(
    "answer",
    [
        # `intake` does not hold the case: answered, never retried.
        {"outcome": "refused", "reason": "not_found"},
        # Every attempt failed: `intake` could not be reached, or was still at it.
        task.TaskFailedError("activity failed", RuntimeError("secret")),
    ],
    ids=["refused", "retries-exhausted"],
)
def test_story_1_7_a_redaction_that_is_refused_or_never_answers_marks_the_case_failed(
    case_id: str, answer: object
) -> None:
    asked, result = run_lifecycle(
        {"case_id": case_id, "eval_run_id": None},
        [CONFIRMED, answer, {"outcome": "ok", "recorded": "recorded"}],
    )

    assert asked[2] == {
        "activity": MARK_CASE_FAILED,
        "input": {"case_id": case_id, "eval_run_id": None},
        "retry_policy": RETRY,
    }
    assert len(asked) == 3
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_7_stop_after_names_no_stage_at_or_before_redaction(
    case_id: str,
) -> None:
    # The one place a case may be told to stop is the gate, which is later:
    # a case started with it is redacted like any other.
    assert {stop.value for stop in StopAfter} == {"gate"}

    asked, _ = run_lifecycle(
        {"case_id": case_id, "stop_after": "gate"},
        [
            CONFIRMED,
            {"outcome": "ok", "case_status": "running", "page_ids": [new_id()]},
            [{"outcome": "ok", "case_status": "running"}],
        ],
    )

    assert asked[1]["activity"] == REDACT_DOCUMENT


def test_story_1_7_a_stage_command_is_retried_for_longer_than_the_stages_deadline() -> (
    None
):
    settings = Settings()
    policy = stage_retry_policy(settings)

    # AD-6: the activity waits 200 s for a stage whose own deadline is 180 s.
    assert settings.stage_timeout_seconds == 200.0
    # A command repeated while the stage still works on it is answered
    # `in_progress`. The waits between the attempts, each longer than the
    # last up to a limit, add up to more than the stage's 180 s.
    waits, wait = [], policy.first_retry_interval.total_seconds()
    assert policy.max_retry_interval is not None
    for _ in range(policy.max_number_of_attempts - 1):
        waits.append(min(wait, policy.max_retry_interval.total_seconds()))
        wait *= policy.backoff_coefficient or 1.0
    assert waits[0] < waits[1] < waits[2]
    # ... and to more than the 180 s plus the 60 s margin after which `intake`
    # settles a redaction left running: the last attempt comes after that.
    stale_after = 180 + 60
    assert sum(waits) >= stale_after + 30
    assert policy.max_retry_interval == timedelta(seconds=30)


def test_story_1_7_the_worker_runs_the_redaction_activity_under_the_name_the_engine_keeps(
    store: MemoryCaseStore, settings: Settings
) -> None:
    loop = asyncio.new_event_loop()
    try:
        activities = Activities(store, loop, 1.0, FakeStages())
        worker = build_worker(settings, activities)
    finally:
        loop.close()

    assert task.get_name(activities.redact_document) == REDACT_DOCUMENT
    assert REDACT_DOCUMENT == "redact_document"
    assert worker is not None


# --- The redaction activity ------------------------------------------------------------


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


def redact(
    store: MemoryCaseStore, stages: FakeStages, case_id: str, times: int = 1
) -> list[dict[str, Any]]:
    command: dict[str, str | None] = {"case_id": case_id, "eval_run_id": None}
    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        return [
            activities.redact_document(task.ActivityContext(case_id, 2), command)
            for _ in range(times)
        ]


def test_story_1_7_a_done_redaction_is_recorded_with_its_pages_and_one_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    stages = FakeStages(pages=3)

    # The engine ran the activity twice: `intake` answers the repeat with its
    # stored result, and recording it again writes nothing new.
    answers = redact(store, stages, case_id, times=2)

    # The answer hands the page ids on to the orchestration (story 1.8): ids
    # only, in document order, and the same on the repeat.
    result = stages.results[case_id]
    assert (
        answers
        == [{"outcome": "ok", "case_status": "running", "page_ids": result.page_ids}]
        * 2
    )
    case = store.cases[case_id]
    assert (case.case_status.value, case.redaction_status.value) == ("running", "done")
    # The pages are tracked as `uploaded`, numbered in the order of the result.
    assert [
        (page_id, store.pages[page_id].page_number, store.pages[page_id].page_status)
        for page_id in result.page_ids
    ] == [(page_id, n, "uploaded") for n, page_id in enumerate(result.page_ids, 1)]
    # One `document.redacted` event: counts per category, never a value.
    ((_, recording),) = store.events
    audit = recording.audit
    assert audit.action.value == "document.redacted"
    assert audit.actor == "intake:azure-ai-language"
    assert audit.detail == {"Person": 2, "PhoneNumber": 1}
    assert audit.page_id is None


class SlowStages(FakeStages):
    """A stage that takes longer than the activity's general timeout to answer."""

    def __init__(self, release: asyncio.Event) -> None:
        super().__init__()
        self._release = release

    async def redact_document(self, case_id: str, **options: Any) -> Any:
        await self._release.wait()
        return await super().redact_document(case_id, **options)


def test_story_1_7_the_redaction_activity_waits_the_stage_timeout_not_the_general_one(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    general_timeout = 0.05

    with service_loop() as loop:
        release = asyncio.Event()
        # Released on the loop only after the general timeout has passed.
        loop.call_soon_threadsafe(loop.call_later, general_timeout * 4, release.set)
        activities = Activities(store, loop, general_timeout, SlowStages(release), 5.0)
        answer = activities.redact_document(
            task.ActivityContext(case_id, 2), {"case_id": case_id, "eval_run_id": None}
        )

    # Waited for with the stage's 200 s setting (here 5 s), so it is heard.
    assert (answer["outcome"], answer["case_status"]) == ("ok", "running")
    assert len(store.events) == 1


def test_story_1_7_a_failed_redaction_fails_the_case_with_one_case_level_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    stages = FakeStages(redaction="failed", error_code="stage_timeout")

    answers = redact(store, stages, case_id, times=2)

    assert answers == [{"outcome": "ok", "case_status": "failed"}] * 2
    case = store.cases[case_id]
    assert (case.case_status.value, case.redaction_status.value) == ("failed", "failed")
    assert store.pages == {}
    ((_, recording),) = store.events
    assert recording.audit.action.value == "stage.failed"
    assert recording.audit.page_id is None
    assert recording.error_code is ErrorCode.STAGE_TIMEOUT


def test_story_1_7_in_progress_fails_the_activity_so_the_engine_sends_the_command_again(
    store: MemoryCaseStore, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    start(store, case_id)
    stages = FakeStages(script=["in_progress", "down"])
    command: dict[str, str | None] = {"case_id": case_id, "eval_run_id": None}

    with service_loop() as loop, caplog.at_level(logging.ERROR):
        activities = Activities(store, loop, 5.0, stages, 5.0)
        reasons = []
        for _ in range(2):
            with pytest.raises(ActivityFailed) as raised:
                activities.redact_document(task.ActivityContext(case_id, 2), command)
            reasons.append(raised.value.reason)
        assert store.events == []
        # The attempt after those: the stage has ended, and answers.
        answer = activities.redact_document(task.ActivityContext(case_id, 2), command)

    # Raised, so the engine retries with its backoff (AD-6); a code, no detail.
    assert reasons == ["in_progress", "upstream_unavailable"]
    assert (
        f"activity failed: activity=redact_document case_id={case_id} "
        "reason=in_progress retry=True"
    ) in caplog.text
    assert (answer["outcome"], answer["case_status"]) == ("ok", "running")
    assert len(stages.calls) == 3
    assert len(store.events) == 1


def test_story_1_7_a_case_intake_does_not_hold_is_refused_without_a_retry(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    stages = FakeStages(script=["not_found"])

    (answer,) = redact(store, stages, case_id)

    # Answered, not raised: the engine would only run a raised failure again.
    assert answer == {"outcome": "refused", "reason": "not_found"}
    assert len(stages.calls) == 1
    assert store.events == []
    assert store.cases[case_id].case_status.value == "running"


def test_story_1_7_a_result_the_trail_cannot_take_is_refused_not_left_running(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    # Pages are tracked for the case already, from a redaction with another
    # document: this result contradicts what is stored.
    asyncio.run(
        store.record(
            plan_recording(redaction_done(case_id, [new_id()])),
            datetime.fromisoformat("2026-10-06T12:00:00Z"),
        )
    )

    (answer,) = redact(store, FakeStages(), case_id)

    assert answer["outcome"] == "refused"
    assert len(store.events) == 1


def test_story_1_7_the_command_carries_the_eval_run_id_and_the_trace_context(
    store: MemoryCaseStore, case_id: str
) -> None:
    start(store, case_id)
    stages = FakeStages()
    eval_run_id = new_id()
    provider = TracerProvider()

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        # The activity runs inside a span, as it does when telemetry is on.
        with provider.get_tracer("test").start_as_current_span("activity") as span:
            activities.redact_document(
                task.ActivityContext(case_id, 2),
                {"case_id": case_id, "eval_run_id": eval_run_id},
            )
            trace_id = trace.format_trace_id(span.get_span_context().trace_id)

    ((called_case, called_run, context),) = stages.calls
    assert (called_case, called_run) == (case_id, eval_run_id)
    # Read on the activity's thread and handed over to the service's loop.
    assert context["traceparent"].split("-")[1] == trace_id
    # The audit record keeps the run id the stage was given.
    assert store.events[0][1].audit.eval_run_id == eval_run_id


# --- The client module: `intake` through the Dapr sidecar ----------------------------------


def client_for(
    handler: Any, settings: Settings | None = None
) -> tuple[StageClient, Settings]:
    settings = settings or Settings()
    return (
        StageClient(
            build_http_client(settings, httpx.MockTransport(handler)), settings
        ),
        settings,
    )


def call(client: StageClient, case_id: str, **options: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await client.redact_document(
                case_id,
                eval_run_id=options.get("eval_run_id"),
                trace_context=options.get("trace_context", {}),
            )
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_1_7_redaction_is_commanded_through_the_sidecar_by_app_id_with_the_trace(
    case_id: str,
) -> None:
    sidecar = SidecarStandIn()
    client, settings = client_for(sidecar.handle)
    eval_run_id = new_id()

    result = call(
        client,
        case_id,
        eval_run_id=eval_run_id,
        trace_context={"traceparent": TRACEPARENT},
    )

    (request,) = sidecar.requests
    operation = get_operation("redact_document")
    # AD-3: the sidecar on loopback, `intake` by its Dapr app id, the path
    # from the contracts. No hostname of another service.
    assert str(request.url) == (
        f"http://127.0.0.1:{settings.dapr_http_port}/v1.0/invoke/intake/method"
        f"/cases/{case_id}/redaction"
    )
    assert request.url.path == invoke_path(
        operation.owner, operation.path.format(case_id=case_id)
    )
    assert request.method == "POST"
    # Ids only (AD-6), and the W3C trace context goes with the call.
    assert json.loads(request.content) == {"eval_run_id": eval_run_id}
    assert request.headers["traceparent"] == TRACEPARENT
    assert result == sidecar.stages.results[case_id]


def test_story_1_7_with_telemetry_on_the_call_stays_in_the_activitys_trace(
    case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = SidecarStandIn()
    client, _ = client_for(sidecar.handle)
    # Telemetry is on: the client module opens a span of its own for the call.
    monkeypatch.setattr(
        "workflow.adapters.dapr.tracer", TracerProvider().get_tracer("test")
    )

    call(client, case_id, trace_context={"traceparent": TRACEPARENT})

    # One trace per request across services: the same trace id goes on to
    # `intake`, with the call's own span as the parent.
    _, trace_id, span_id, _ = sidecar.requests[0].headers["traceparent"].split("-")
    assert trace_id == TRACE_ID
    assert span_id != TRACEPARENT.split("-")[2]


@pytest.mark.parametrize(
    ("step", "code"),
    [
        ("in_progress", ErrorCode.IN_PROGRESS),
        ("not_found", ErrorCode.NOT_FOUND),
        # Refused every time it is sent: the activity answers, it is not retried.
        ("invalid", ErrorCode.VALIDATION_FAILED),
    ],
)
def test_story_1_7_in_progress_and_not_found_are_passed_on_with_their_codes(
    case_id: str, step: str, code: ErrorCode
) -> None:
    sidecar = SidecarStandIn(FakeStages(script=[step]))
    client, _ = client_for(sidecar.handle)

    with pytest.raises(DomainError) as raised:
        call(client, case_id)

    assert raised.value.code is code


@pytest.mark.parametrize(
    "response",
    [
        # The sidecar's own answer when `intake` cannot be reached.
        httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE", "message": "x"}),
        httpx.Response(502, json={"error": {"code": "upstream_unavailable"}}),
        # A code under a status that is not its own: not believed.
        httpx.Response(
            500,
            json={
                "error": {
                    "code": "in_progress",
                    "message": "Still.",
                    "trace_id": "0" * 32,
                }
            },
        ),
        # 2xx with something that is not a redaction result.
        httpx.Response(200, json={"status": "done"}),
        httpx.Response(200, content=b"<html>"),
    ],
    ids=["sidecar-error", "bad-error-body", "wrong-status", "not-a-result", "not-json"],
)
def test_story_1_7_any_other_answer_is_upstream_unavailable_and_so_retried(
    case_id: str, response: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = client_for(lambda request: response)

    with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as raised:
        call(client, case_id)

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert f"case_id={case_id}" in caplog.text
    assert "ERR_DIRECT_INVOKE" not in caplog.text


def test_story_1_7_a_sidecar_that_cannot_be_reached_or_never_answers_is_upstream_unavailable(
    case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot reach 127.0.0.1:3500")

    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with caplog.at_level(logging.ERROR):
        for handler, settings in (
            (unreachable, Settings()),
            # The call's own deadline, made short: the stand-in never answers.
            (never, Settings(stage_timeout_seconds=0.05)),
        ):
            client, _ = client_for(handler, settings)
            with pytest.raises(DomainError) as raised:
                call(client, case_id)
            assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE

    assert "type=ConnectError" in caplog.text
    assert "type=TimeoutError" in caplog.text
    # security rule 31: the error's type, never its message.
    assert "cannot reach" not in caplog.text


def test_story_1_7_a_result_about_another_case_is_never_recorded(case_id: str) -> None:
    other = redaction_done(new_id(), [new_id()])
    client, _ = client_for(
        lambda request: httpx.Response(200, json=other.model_dump(mode="json"))
    )

    with pytest.raises(DomainError) as raised:
        call(client, case_id)

    # No retry can mend it: the activity answers with it as refused.
    assert raised.value.code is ErrorCode.VALIDATION_FAILED


# --- Settings ----------------------------------------------------------------------------


def test_story_1_7_the_sidecar_port_and_the_stage_timeout_are_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings()
    assert (defaults.dapr_http_port, defaults.stage_timeout_seconds) == (3500, 200.0)
    assert sidecar_base_url(defaults) == "http://127.0.0.1:3500"

    # Dapr tells the app its port in DAPR_HTTP_PORT; the service's own name wins.
    monkeypatch.setenv("DAPR_HTTP_PORT", "3502")
    assert Settings().dapr_http_port == 3502
    monkeypatch.setenv("WORKFLOW_DAPR_HTTP_PORT", "3600")
    monkeypatch.setenv("WORKFLOW_STAGE_TIMEOUT_SECONDS", "90")
    settings = Settings()
    assert (settings.dapr_http_port, settings.stage_timeout_seconds) == (3600, 90.0)
    client = build_http_client(settings)
    assert str(client.base_url).rstrip("/") == "http://127.0.0.1:3600"
    # The sidecar is on this machine: no proxy setting applies to it.
    assert client.trust_env is False


def test_story_1_7_the_local_start_and_the_app_stack_set_the_sidecar_port() -> None:
    main = (REPOSITORY_ROOT / "infra" / "demo" / "app" / "main.tf").read_text()
    run_file = (REPOSITORY_ROOT / "dapr.yaml").read_text()

    assert re.search(r'name\s*=\s*"WORKFLOW_DAPR_HTTP_PORT"', main)
    # Locally each sidecar has a port of its own; `workflow` is told its one.
    assert 'WORKFLOW_DAPR_HTTP_PORT: "3502"' in run_file
    assert "daprHTTPPort: 3502" in run_file
    # `workflow` never holds `intake`'s address: it knows the app id only.
    source = (REPOSITORY_ROOT / "services" / "workflow" / "src" / "workflow").rglob(
        "*.py"
    )
    assert not any("8001" in path.read_text() for path in source)
