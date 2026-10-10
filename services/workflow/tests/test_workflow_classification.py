"""Story 1.8: `workflow` has every page classified once redaction is done.

Unit tests: the orchestrator's steps after redaction, the classify activity
and the client module that reaches `classification` through the Dapr sidecar.
No scheduler, no database, no network.
"""

import asyncio
import inspect
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
    FakeStages,
    MemoryCaseStore,
    classification_done,
)

from contracts.enums import ClassifierContender
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from workflow.adapters.dapr import StageClient, build_http_client
from workflow.adapters.db import build_database
from workflow.adapters.scheduler import Activities
from workflow.adapters.telemetry import adapter_span
from workflow.settings import Settings

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


# --- The client module: `classification` through the Dapr sidecar ----------------------------


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


@pytest.mark.parametrize("wrong", ["page"])
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
