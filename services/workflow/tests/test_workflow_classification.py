"""Story 1.8: `workflow` has every page classified once redaction is done.

Unit tests: the orchestrator's steps after redaction, the classify activity
and the client module that reaches `classification` through the Dapr sidecar.
No scheduler, no database, no network.
"""

import asyncio
import inspect
import json
import logging
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
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from workflow_fakes import (
    TRACE_ID,
    FakeStages,
    MemoryCaseStore,
    SidecarStandIn,
    classification_done,
)

from contracts.enums import ClassifierContender, PageStatus, RetrieverConfig
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.operations import get_operation
from workflow.adapters.dapr import StageClient, build_http_client, invoke_path
from workflow.adapters.db import build_database
from workflow.adapters.orchestration import (
    CLASSIFY_PAGE,
    CONFIRM_CASE_STARTED,
    MARK_CASE_FAILED,
    REDACT_DOCUMENT,
    build_case_lifecycle,
)
from workflow.adapters.scheduler import Activities, ActivityFailed, build_worker
from workflow.adapters.telemetry import adapter_span
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import new_case
from workflow.domain.transitions import PAGE_TRANSITIONS
from workflow.settings import Settings

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
CONFIRMED = {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}
# Story 1.9: a done classification hands on what the gate needs.
CLASSIFIED = {
    "outcome": "ok",
    "case_status": "running",
    "classification_id": new_id(),
    "is_medical": True,
    "confidence": 0.95,
}
ROUTED = {"outcome": "ok", "case_status": "running", "route": "extracting"}
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


# --- The orchestrator --------------------------------------------------------------


class RecordingContext:
    """Stands in for the engine's context: it notes what the orchestrator asks for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        return task.CompletableTask[Any]()


def run_lifecycle(
    started: dict[str, Any], answers: list[object]
) -> tuple[list[dict[str, Any]], Any, list[int]]:
    """Run the orchestrator to its end, answering each wait in turn.

    Also returns how many activities had been asked for at each wait, so a
    test can see which were asked for together.
    """
    context = RecordingContext()
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the one method used
        started,
    )
    assert inspect.isgenerator(steps)
    steps = cast(Any, steps)
    next(steps)
    asked_at_each_wait = [len(context.asked)]
    for answer in answers:
        try:
            if isinstance(answer, Exception):
                steps.throw(answer)
            else:
                steps.send(answer)
        except StopIteration as done:
            return context.asked, done.value, asked_at_each_wait
        asked_at_each_wait.append(len(context.asked))
    raise AssertionError("the orchestrator waited for more than it was answered")


def redacted(page_ids: list[str]) -> dict[str, Any]:
    return {"outcome": "ok", "case_status": "running", "page_ids": page_ids}


def test_story_1_8_after_redaction_every_page_is_classified_by_a_command_of_its_own(
    case_id: str,
) -> None:
    eval_run_id = new_id()
    page_ids = [new_id(), new_id(), new_id()]
    started = {
        "case_id": case_id,
        "eval_run_id": eval_run_id,
        "classifier_contender": "llm",
    }

    asked, result, asked_at_each_wait = run_lifecycle(
        started, [CONFIRMED, redacted(page_ids), [CLASSIFIED] * 3, [ROUTED] * 3]
    )

    # One command per page, in document order, each with ids only (AD-6), the
    # contender the case was started with and the stage's retry policy.
    assert [step["activity"] for step in asked[:5]] == [
        CONFIRM_CASE_STARTED,
        REDACT_DOCUMENT,
        CLASSIFY_PAGE,
        CLASSIFY_PAGE,
        CLASSIFY_PAGE,
    ]
    assert asked[2:5] == [
        {
            "activity": CLASSIFY_PAGE,
            "input": {
                "case_id": case_id,
                "eval_run_id": eval_run_id,
                "page_id": page_id,
                "contender": "llm",
            },
            "retry_policy": STAGE_RETRY,
        }
        for page_id in page_ids
    ]
    # In parallel: all three were asked for before the orchestrator waited.
    assert asked_at_each_wait[:3] == [1, 2, 5]
    # The pages are classified and the case goes on running; what follows is
    # the gate (story 1.9, test_workflow_gate.py).
    assert result == {"case_id": case_id, "case_status": "running"}


def test_story_1_8_a_case_started_with_another_contender_is_classified_with_that_one(
    case_id: str,
) -> None:
    asked, _, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "doc-intelligence"},
        [
            CONFIRMED,
            redacted([new_id()]),
            [{"outcome": "refused", "reason": "validation_failed"}],
            {"outcome": "ok", "recorded": "recorded"},
        ],
    )

    # AD-7: a case is classified with the one contender it was started with.
    assert asked[2]["input"]["contender"] == "doc-intelligence"


def test_story_1_8_a_failed_classification_ends_the_case_as_failed_without_a_second_event(
    case_id: str,
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id(), new_id()]),
            [CLASSIFIED, {"outcome": "ok", "case_status": "failed"}],
        ],
    )

    # The recording of the failed result failed the page and the case and
    # wrote the `stage.failed` event: the failure activity is not asked for.
    assert MARK_CASE_FAILED not in [step["activity"] for step in asked]
    assert result == {"case_id": case_id, "case_status": "failed"}


@pytest.mark.parametrize(
    "answer",
    [
        # The page is not the case's, or the contender cannot be run: answered,
        # never retried.
        [CLASSIFIED, {"outcome": "refused", "reason": "not_found"}],
        [{"outcome": "refused", "reason": "validation_failed"}, CLASSIFIED],
        # Every attempt for one page failed: the stage could not be reached,
        # or was still at it.
        task.TaskFailedError("activity failed", RuntimeError("secret")),
    ],
    ids=["refused-not-found", "refused-contender", "retries-exhausted"],
)
def test_story_1_8_a_classification_that_is_refused_or_never_answers_marks_the_case_failed(
    case_id: str, answer: object
) -> None:
    about = {"case_id": case_id, "eval_run_id": None}

    asked, result, _ = run_lifecycle(
        {**about, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id(), new_id()]),
            answer,
            {"outcome": "ok", "recorded": "recorded"},
        ],
    )

    assert asked[-1] == {
        "activity": MARK_CASE_FAILED,
        "input": about,
        "retry_policy": RETRY,
    }
    assert [step["activity"] for step in asked].count(MARK_CASE_FAILED) == 1
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_8_a_done_redaction_without_a_page_marks_the_case_failed(
    case_id: str,
) -> None:
    about = {"case_id": case_id, "eval_run_id": None}

    asked, result, _ = run_lifecycle(
        {**about, "classifier_contender": "llm"},
        [CONFIRMED, redacted([]), {"outcome": "ok", "recorded": "recorded"}],
    )

    # Nothing to classify, and nothing that would ever move the case on: it
    # is not left `running` for ever.
    assert [step["activity"] for step in asked] == [
        CONFIRM_CASE_STARTED,
        REDACT_DOCUMENT,
        MARK_CASE_FAILED,
    ]
    assert asked[-1]["input"] == about
    assert result == {"case_id": case_id, "case_status": "failed"}
    # The same for an answer that names no pages at all.
    _, result, _ = run_lifecycle(
        {**about, "classifier_contender": "llm"},
        [CONFIRMED, CONFIRMED, {"outcome": "ok", "recorded": "recorded"}],
    )
    assert result["case_status"] == "failed"


def test_story_1_8_a_failed_redaction_classifies_nothing(case_id: str) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [CONFIRMED, {"outcome": "ok", "case_status": "failed"}],
    )

    assert CLASSIFY_PAGE not in [step["activity"] for step in asked]
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_8_classification_itself_routes_nothing_and_waits_for_no_human() -> (
    None
):
    # The classify activity never routes (AD-7): the gate does, after it
    # (story 1.9). The waits for decisions are the orchestration's, after
    # the gate (story 1.10): no activity waits for a person.
    classify_source = inspect.getsource(Activities.classify_page)
    assert "record_route" not in classify_source
    assert "Route(" not in classify_source
    assert "wait_for_external_event" not in inspect.getsource(Activities)
    assert PageStatus.AWAITING_TRIAGE in PAGE_TRANSITIONS[PageStatus.CLASSIFIED]


def test_story_1_8_the_worker_runs_the_classify_activity_under_the_name_the_engine_keeps(
    store: MemoryCaseStore, settings: Settings
) -> None:
    loop = asyncio.new_event_loop()
    try:
        activities = Activities(store, loop, 1.0, FakeStages())
        worker = build_worker(settings, activities)
    finally:
        loop.close()

    assert task.get_name(activities.classify_page) == CLASSIFY_PAGE
    assert CLASSIFY_PAGE == "classify_page"
    assert worker is not None


# --- The classify activity -------------------------------------------------------------


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


def redacted_case(
    store: MemoryCaseStore, stages: FakeStages, case_id: str
) -> list[str]:
    """A started case whose redaction is recorded; returns its page ids in order."""
    case = new_case(case_id, PARAMETERS, datetime.fromisoformat("2026-10-06T12:00:00Z"))
    asyncio.run(store.start(case))
    with service_loop() as loop:
        answer = Activities(store, loop, 5.0, stages, 5.0).redact_document(
            task.ActivityContext(case_id, 2), {"case_id": case_id, "eval_run_id": None}
        )
    return list(answer["page_ids"])


def classify(
    store: MemoryCaseStore,
    stages: FakeStages,
    case_id: str,
    page_id: str,
    times: int = 1,
    **command: Any,
) -> list[dict[str, str]]:
    asked: dict[str, str | None] = {
        "case_id": case_id,
        "page_id": page_id,
        "contender": "llm",
        "eval_run_id": None,
        **command,
    }
    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        return [
            activities.classify_page(task.ActivityContext(case_id, 3), asked)
            for _ in range(times)
        ]


def handed_on(stages: FakeStages, case_id: str, page_id: str) -> dict[str, Any]:
    """What a done classification answers: its outcome and what the gate needs (story 1.9)."""
    stored = stages.classifications[(case_id, page_id)]
    assert stored.classification is not None
    return {
        "outcome": "ok",
        "case_status": "running",
        "classification_id": stored.classification_id,
        "is_medical": stored.classification.is_medical,
        "confidence": stored.classification.confidence,
    }


def test_story_1_8_a_done_classification_moves_its_page_to_classified_with_one_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=2)
    first, second = redacted_case(store, stages, case_id)

    # The engine ran the activity twice: the stage answers the repeat with
    # its stored result, and recording it again writes nothing new.
    answers = classify(store, stages, case_id, first, times=2)

    assert answers == [handed_on(stages, case_id, first)] * 2
    assert store.pages[first].page_status is PageStatus.CLASSIFIED
    assert store.pages[second].page_status is PageStatus.UPLOADED
    # The case goes on running: nothing routes the page yet.
    assert store.cases[case_id].case_status.value == "running"
    (_, recording) = store.events[-1]
    audit = recording.audit
    assert len(store.events) == 2
    assert (audit.action.value, audit.page_id) == ("page.classified", first)
    # AD-8: the actor names the service and the model deployment; the
    # reference is the classification, and there is no detail.
    assert audit.actor == "classification:chat-main"
    assert audit.ref == stages.classifications[(case_id, first)].classification_id
    assert audit.detail is None


class SlowStages(FakeStages):
    """A stage that takes longer than the activity's general timeout to classify."""

    def __init__(self, release: asyncio.Event) -> None:
        super().__init__(pages=1)
        self._release = release

    async def classify_page(self, *ids: Any, **options: Any) -> Any:
        await self._release.wait()
        return await super().classify_page(*ids, **options)


def test_story_1_8_the_classify_activity_waits_the_stage_timeout_not_the_general_one(
    store: MemoryCaseStore, case_id: str
) -> None:
    general_timeout = 0.05

    with service_loop() as loop:
        release = asyncio.Event()
        stages = SlowStages(release)
        case = new_case(
            case_id, PARAMETERS, datetime.fromisoformat("2026-10-06T12:00:00Z")
        )
        asyncio.run(store.start(case))
        # The redaction's own wait is the stage's too (story 1.7).
        activities = Activities(store, loop, general_timeout, stages, 5.0)
        (page_id,) = activities.redact_document(
            task.ActivityContext(case_id, 2), {"case_id": case_id, "eval_run_id": None}
        )["page_ids"]
        # Released on the loop only after the general timeout has passed.
        loop.call_soon_threadsafe(loop.call_later, general_timeout * 4, release.set)
        answer = activities.classify_page(
            task.ActivityContext(case_id, 3),
            {
                "case_id": case_id,
                "page_id": page_id,
                "contender": "llm",
                "eval_run_id": None,
            },
        )

    # Waited for with the stage's 200 s setting (here 5 s), so it is heard.
    assert answer == handed_on(stages, case_id, page_id)
    assert store.pages[page_id].page_status is PageStatus.CLASSIFIED


def test_story_1_8_a_failed_classification_fails_its_page_and_the_case_with_one_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(
        pages=3,
        failing_page_numbers=frozenset({2, 3}),
        classification_error_code="invalid_model_output",
    )
    first, second, third = redacted_case(store, stages, case_id)

    answers = [
        classify(store, stages, case_id, page_id)[0]
        for page_id in (first, second, third)
    ]

    # The rule recorded in story 1.6: a failed page stage fails the page and
    # the case. A failed case takes no further result, so the third page's
    # failure writes nothing and the page stays where it was.
    assert answers == [
        handed_on(stages, case_id, first),
        {"outcome": "ok", "case_status": "failed"},
        {"outcome": "ok", "case_status": "failed"},
    ]
    assert store.cases[case_id].case_status.value == "failed"
    assert [store.pages[page].page_status.value for page in (first, second, third)] == [
        "classified",
        "failed",
        "uploaded",
    ]
    failures = [
        recording
        for _, recording in store.events
        if recording.audit.action.value == "stage.failed"
    ]
    (failure,) = failures
    assert failure.audit.page_id == second
    assert failure.error_code is ErrorCode.INVALID_MODEL_OUTPUT
    assert failure.audit.actor == "classification:chat-main"


def test_story_1_8_in_progress_fails_the_activity_so_the_engine_sends_the_command_again(
    store: MemoryCaseStore, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)
    stages.classify_script = ["in_progress", "down"]
    command: dict[str, str | None] = {
        "case_id": case_id,
        "page_id": page_id,
        "contender": "llm",
        "eval_run_id": None,
    }

    with service_loop() as loop, caplog.at_level(logging.ERROR):
        activities = Activities(store, loop, 5.0, stages, 5.0)
        reasons = []
        for _ in range(2):
            with pytest.raises(ActivityFailed) as raised:
                activities.classify_page(task.ActivityContext(case_id, 3), command)
            reasons.append(raised.value.reason)
        assert store.pages[page_id].page_status is PageStatus.UPLOADED
        # The attempt after those: the stage has ended, and answers.
        answer = activities.classify_page(task.ActivityContext(case_id, 3), command)

    # Raised, so the engine retries with its backoff (AD-6); a code, no detail.
    assert reasons == ["in_progress", "upstream_unavailable"]
    assert (
        f"activity failed: activity=classify_page case_id={case_id} "
        "reason=in_progress retry=True"
    ) in caplog.text
    assert answer == handed_on(stages, case_id, page_id)
    assert len(stages.classify_calls) == 3
    assert store.pages[page_id].page_status is PageStatus.CLASSIFIED


@pytest.mark.parametrize(
    ("step", "reason"),
    [("not_found", "not_found"), ("invalid", "validation_failed")],
)
def test_story_1_8_a_page_the_stage_does_not_know_is_refused_without_a_retry(
    store: MemoryCaseStore, case_id: str, step: str, reason: str
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)
    stages.classify_script = [step]

    (answer,) = classify(store, stages, case_id, page_id)

    # Answered, not raised: the engine would only run a raised failure again.
    assert answer == {"outcome": "refused", "reason": reason}
    assert len(stages.classify_calls) == 1
    assert store.pages[page_id].page_status is PageStatus.UPLOADED
    assert len(store.events) == 1


def test_story_1_8_a_page_of_another_case_is_refused(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    redacted_case(store, stages, case_id)
    (other_page,) = redacted_case(store, stages, new_id())

    (answer,) = classify(store, stages, case_id, other_page)

    assert answer == {"outcome": "refused", "reason": "not_found"}
    assert store.pages[other_page].page_status is PageStatus.UPLOADED


def test_story_1_8_the_contender_that_is_not_built_is_refused_and_not_retried(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)

    (answer,) = classify(store, stages, case_id, page_id, contender="doc-intelligence")

    # Story 4.2 builds it. Until then the stage answers 422, once.
    assert answer == {"outcome": "refused", "reason": "validation_failed"}
    assert [call[2] for call in stages.classify_calls] == ["doc-intelligence"]
    assert store.pages[page_id].page_status is PageStatus.UPLOADED


@pytest.mark.parametrize("contender", [None, "guess"])
def test_story_1_8_a_command_without_a_known_contender_is_refused_before_any_call(
    store: MemoryCaseStore, case_id: str, contender: str | None
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)

    (answer,) = classify(store, stages, case_id, page_id, contender=contender)

    assert answer == {"outcome": "refused", "reason": "validation_failed"}
    assert stages.classify_calls == []


def test_story_1_8_a_result_the_trail_cannot_take_is_refused_not_left_running(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)
    classify(store, stages, case_id, page_id)
    # A second classification of the page, under another reference: the page
    # is `classified` already and may not be classified again.
    stages.classifications[(case_id, page_id)] = classification_done(case_id, page_id)

    (answer,) = classify(store, stages, case_id, page_id)

    assert answer == {"outcome": "refused", "reason": "out_of_order"}
    assert len(store.events) == 2


def test_story_1_8_the_command_carries_the_eval_run_id_and_the_trace_context(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = redacted_case(store, stages, case_id)
    eval_run_id = new_id()
    provider = TracerProvider()

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        # The activity runs inside a span, as it does when telemetry is on.
        with provider.get_tracer("test").start_as_current_span("activity") as span:
            activities.classify_page(
                task.ActivityContext(case_id, 3),
                {
                    "case_id": case_id,
                    "page_id": page_id,
                    "contender": "llm",
                    "eval_run_id": eval_run_id,
                },
            )
            trace_id = trace.format_trace_id(span.get_span_context().trace_id)

    ((called_case, called_page, contender, called_run, context),) = (
        stages.classify_calls
    )
    assert (called_case, called_page, contender, called_run) == (
        case_id,
        page_id,
        "llm",
        eval_run_id,
    )
    # Read on the activity's thread and handed over to the service's loop.
    assert context["traceparent"].split("-")[1] == trace_id
    # The audit record keeps the run id the stage was given.
    assert store.events[-1][1].audit.eval_run_id == eval_run_id


# --- The client module: `classification` through the Dapr sidecar ----------------------------


def sidecar_with_a_redacted_case(case_id: str, pages: int = 1) -> SidecarStandIn:
    sidecar = SidecarStandIn(FakeStages(pages=pages))
    asyncio.run(
        sidecar.stages.redact_document(case_id, eval_run_id=None, trace_context={})
    )
    return sidecar


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


def call(client: StageClient, case_id: str, page_id: str, **options: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await client.classify_page(
                case_id,
                page_id,
                options.get("contender", ClassifierContender.LLM),
                eval_run_id=options.get("eval_run_id"),
                trace_context=options.get("trace_context", {}),
            )
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_1_8_classification_is_commanded_through_the_sidecar_by_app_id_with_the_trace(
    case_id: str,
) -> None:
    sidecar = sidecar_with_a_redacted_case(case_id)
    (page_id,) = sidecar.stages.results[case_id].page_ids
    client, settings = client_for(sidecar.handle)
    eval_run_id = new_id()

    result = call(
        client,
        case_id,
        page_id,
        eval_run_id=eval_run_id,
        trace_context={"traceparent": TRACEPARENT},
    )

    (request,) = sidecar.requests
    operation = get_operation("classify_page")
    # AD-3: the sidecar on loopback, `classification` by its Dapr app id, the
    # path from the contracts. No hostname of another service.
    assert str(request.url) == (
        f"http://127.0.0.1:{settings.dapr_http_port}/v1.0/invoke/classification"
        "/method/classifications"
    )
    assert request.url.path == invoke_path(operation.owner, operation.path)
    assert request.method == "POST"
    # Ids only (AD-6): no page text and no image go with the command.
    assert json.loads(request.content) == {
        "case_id": case_id,
        "page_id": page_id,
        "contender": "llm",
        "eval_run_id": eval_run_id,
    }
    assert request.headers["traceparent"] == TRACEPARENT
    assert result == sidecar.stages.classifications[(case_id, page_id)]
    assert result.classification is not None
    assert 0.0 <= result.classification.confidence <= 1.0


@pytest.mark.parametrize(
    ("step", "code"),
    [
        ("in_progress", ErrorCode.IN_PROGRESS),
        ("not_found", ErrorCode.NOT_FOUND),
        ("invalid", ErrorCode.VALIDATION_FAILED),
    ],
)
def test_story_1_8_the_stages_own_answers_are_passed_on_with_their_codes(
    case_id: str, step: str, code: ErrorCode
) -> None:
    sidecar = sidecar_with_a_redacted_case(case_id)
    (page_id,) = sidecar.stages.results[case_id].page_ids
    sidecar.stages.classify_script = [step]
    client, _ = client_for(sidecar.handle)

    with pytest.raises(DomainError) as raised:
        call(client, case_id, page_id)

    assert raised.value.code is code


def test_story_1_8_the_contender_that_is_not_built_is_answered_422_and_passed_on(
    case_id: str,
) -> None:
    sidecar = sidecar_with_a_redacted_case(case_id)
    (page_id,) = sidecar.stages.results[case_id].page_ids
    client, _ = client_for(sidecar.handle)

    with pytest.raises(DomainError) as raised:
        call(client, case_id, page_id, contender=ClassifierContender.DOC_INTELLIGENCE)

    assert raised.value.code is ErrorCode.VALIDATION_FAILED
    assert raised.value.http_status == 422


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE", "message": "x"}),
        # A stage failure is a stored result, never an error status: a 503
        # with the code of one is the sidecar's or the platform's, and retried.
        httpx.Response(
            503,
            json={
                "error": {
                    "code": "model_unavailable",
                    "message": "No.",
                    "trace_id": "0" * 32,
                }
            },
        ),
        httpx.Response(200, json={"status": "done"}),
        httpx.Response(200, content=b"<html>"),
    ],
    ids=["sidecar-error", "error-status", "not-a-result", "not-json"],
)
def test_story_1_8_any_other_answer_is_upstream_unavailable_and_so_retried(
    case_id: str, response: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = client_for(lambda request: response)

    with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as raised:
        call(client, case_id, new_id())

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert "operation=classify_page" in caplog.text
    assert f"case_id={case_id}" in caplog.text


@pytest.mark.parametrize("wrong", ["case", "page", "contender"])
def test_story_1_8_a_result_about_another_case_or_page_is_never_recorded(
    case_id: str, wrong: str
) -> None:
    page_id = new_id()
    other = {
        "case": classification_done(new_id(), page_id),
        "page": classification_done(case_id, new_id()),
        # The right case and page, as another classifier read it: the case
        # would be routed on a contender it was not started with.
        "contender": classification_done(case_id, page_id),
    }[wrong]
    asked = (
        ClassifierContender.DOC_INTELLIGENCE
        if wrong == "contender"
        else ClassifierContender.LLM
    )
    assert (other.contender is asked) is (wrong != "contender")
    client, _ = client_for(
        lambda request: httpx.Response(200, json=other.model_dump(mode="json"))
    )

    with pytest.raises(DomainError) as raised:
        call(client, case_id, page_id, contender=asked)

    # No retry can mend it: the activity answers with it as refused.
    assert raised.value.code is ErrorCode.VALIDATION_FAILED


def test_story_1_8_an_error_inside_an_adapters_span_leaves_its_type_and_no_message(
    case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("workflow.adapters.dapr.tracer", provider.get_tracer("test"))

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot reach SECRET-ADDRESS 10.0.0.9:3500")

    client, _ = client_for(unreachable)
    with pytest.raises(DomainError):
        call(client, case_id, new_id())
    # As a database error would: its message holds the statement and its values.
    with (
        pytest.raises(RuntimeError),
        adapter_span(provider.get_tracer("test"), "workflow.db.record"),
    ):
        raise RuntimeError("INSERT INTO audit_event VALUES ('SECRET-VALUE')")

    stage_call, database = exporter.get_finished_spans()
    assert stage_call.name == "workflow.stage.classify_page"
    for span, error_type in ((stage_call, "DomainError"), (database, "RuntimeError")):
        (event,) = span.events
        assert span.status.status_code is trace.StatusCode.ERROR
        assert span.status.description is None
        assert set(event.attributes or {}) == {
            "exception.type",
            "exception.stacktrace",
        }
        assert (event.attributes or {})["exception.type"] == error_type
        assert "SECRET" not in repr(dict(event.attributes or {}))
    # The engine's own errors never carry the values of a statement either.
    assert build_database(Settings()).engine.sync_engine.hide_parameters is True
    adapters = Path(inspect.getfile(Activities)).parent
    for source in adapters.rglob("*.py"):
        if source.name != "telemetry.py":
            assert "start_as_current_span" not in source.read_text(), source.name
