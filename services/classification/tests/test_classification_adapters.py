"""Story 1.8: the adapters of `classification`, without a database, a model or a network.

Unit tests: the settings, the model gateway (against a transport that stands
in for the deployment), the client that reads pages from `intake` through the
Dapr sidecar, the Entra token, telemetry and the bundled migrations.
"""

import asyncio
import base64
import json
import logging
import random
import re
import runpy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from classification_fakes import (
    DEPLOYMENT,
    PAGE_TEXT,
    PNG,
    TRACE_ID,
    TRACEPARENT,
    IntakeSidecar,
    answer,
    completion,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine

from classification.adapters import credential as credential_module
from classification.adapters.dapr import (
    IntakeClient,
    build_http_client,
    invoke_path,
    sidecar_base_url,
)
from classification.adapters.db import (
    POSTGRESQL_TOKEN_SCOPE,
    EntraToken,
    build_database,
    database_url,
    metadata,
    use_entra_token,
)
from classification.adapters.http.app import classify_options, create_app
from classification.adapters.http.routes import Dependencies
from classification.adapters.migrations import (
    MIGRATIONS_DIR,
    alembic_config,
    bundled_head,
    include_name,
)
from classification.adapters.model import (
    COGNITIVE_SERVICES_SCOPE,
    NO_KEY,
    OUTPUT_SCHEMA,
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_base_url,
    model_token_for,
)
from classification.adapters.telemetry import (
    EXCLUDED_URLS,
    QUIET_LOGGERS,
    adapter_span,
    configure_logging,
    configure_telemetry,
    current_trace_id,
    instrument_app,
)
from classification.domain.entities import PageContent
from classification.domain.ports import ModelCallFailed, ModelUnavailable
from classification.prompts import CLASSIFY_PAGE, load_prompt
from classification.settings import APP_ID, SCHEMA, Settings, get_settings
from contracts.enums import PageType, Service
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassifierOutput
from contracts.operations import get_operation

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVICE_DIR.parents[1]
PAGE = PageContent(text=PAGE_TEXT, image=PNG)


# --- Settings ----------------------------------------------------------------------------


def test_story_1_8_settings_read_the_classification_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLASSIFICATION_PORT", "9003")
    monkeypatch.setenv("CLASSIFICATION_MODEL_ENDPOINT", "http://127.0.0.1:5999")
    monkeypatch.setenv("CLASSIFICATION_CHAT_DEPLOYMENT", "chat-from-env")
    monkeypatch.setenv("CLASSIFICATION_CLASSIFIER_RUNS", "3")
    monkeypatch.setenv("INTAKE_PORT", "1")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert get_settings() is settings
    finally:
        get_settings.cache_clear()

    assert (settings.port, settings.classifier_runs) == (9003, 3)
    assert settings.model_endpoint == "http://127.0.0.1:5999"
    assert settings.chat_deployment == "chat-from-env"
    assert (APP_ID, SCHEMA) == ("classification", "classification")
    assert APP_ID == Service.CLASSIFICATION.value


def test_story_1_8_settings_default_to_the_local_containers_and_the_spines_numbers() -> (
    None
):
    settings = Settings()

    assert (settings.host, settings.port) == ("127.0.0.1", 8003)
    assert (settings.database_host, settings.database_name, settings.database_user) == (
        "127.0.0.1",
        "aiuw",
        "aiuw",
    )
    assert settings.database_entra_auth is False
    # AD-13: five runs. AD-6: 180 s, shorter than `workflow`'s 200 s.
    assert (settings.classifier_runs, settings.classifier_max_concurrent_runs) == (5, 5)
    assert settings.classify_deadline_seconds == 180.0
    assert settings.classify_stale_margin_seconds == 60.0
    # AD-16: three retries.
    assert settings.model_max_retries == 3
    # The endpoint and the deployment name have no default: they are settings.
    assert (settings.model_endpoint, settings.chat_deployment) == (None, None)
    assert settings.model_entra_auth is False


def test_story_1_8_blank_variables_count_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "AZURE_CLIENT_ID",
        "CHAT_DEPLOYMENT",
        "MODEL_ENDPOINT",
    ):
        monkeypatch.setenv(f"CLASSIFICATION_{name}", "  ")

    settings = Settings()

    assert settings.applicationinsights_connection_string is None
    assert settings.azure_client_id is None
    assert (settings.chat_deployment, settings.model_endpoint) == (None, None)


def test_story_1_8_the_sidecar_port_is_a_setting_and_dapr_may_name_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().dapr_http_port == 3500
    assert sidecar_base_url(Settings()) == "http://127.0.0.1:3500"
    # Dapr tells the app its port in DAPR_HTTP_PORT; the service's own name wins.
    monkeypatch.setenv("DAPR_HTTP_PORT", "3503")
    assert Settings().dapr_http_port == 3503
    monkeypatch.setenv("CLASSIFICATION_DAPR_HTTP_PORT", "3600")
    settings = Settings()
    assert settings.dapr_http_port == 3600
    client = build_http_client(settings)
    assert str(client.base_url).rstrip("/") == "http://127.0.0.1:3600"
    # The sidecar is on this machine: no proxy setting applies to it.
    assert client.trust_env is False


@pytest.mark.parametrize(
    "values",
    [
        # Plain HTTP is the stand-in: on loopback only, and never with a token.
        {"model_endpoint": "http://example.com:5101"},
        {"model_endpoint": "http://127.0.0.1:5101", "model_entra_auth": True},
        # The real deployment is reached with the identity.
        {"model_endpoint": "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com/"},
        {"model_endpoint": "ftp://127.0.0.1"},
        # Anything after the host would end up in the middle of the address called.
        {"model_endpoint": "http://127.0.0.1:5101/openai/v1"},
        {"model_endpoint": "http://127.0.0.1:5101/?api-version=1"},
        {"model_endpoint": "http://127.0.0.1:5101#x"},
        {"model_max_completion_tokens": 0},
        {"model_max_concurrent_calls": 0},
        {"model_endpoint": "127.0.0.1:5101"},
        {"chat_deployment": " padded "},
        {"model_retry_seconds": 60.0, "model_max_retry_seconds": 30.0},
        {"classifier_runs": 0},
    ],
)
def test_story_1_8_settings_that_would_reach_the_model_unsafely_are_refused(
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_story_1_8_the_two_safe_ways_to_the_model_are_accepted() -> None:
    local = Settings(model_endpoint="http://localhost:5101")
    azure = Settings(
        model_endpoint="https://aif-aiuw-demo-wus3.cognitiveservices.azure.com/",
        model_entra_auth=True,
    )

    assert model_token_for(local) is None
    assert (
        model_base_url(azure)
        == "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com/openai/v1/"
    )
    assert model_base_url(local) == "http://localhost:5101/openai/v1/"


def test_story_1_8_the_endpoint_and_the_deployment_name_must_be_configured() -> None:
    with pytest.raises(ValueError, match="CLASSIFICATION_MODEL_ENDPOINT"):
        model_base_url(Settings())
    with pytest.raises(ValueError, match="CLASSIFICATION_CHAT_DEPLOYMENT"):
        chat_deployment(Settings(model_endpoint="http://127.0.0.1:5101"))
    # The app is not built without them: no name is guessed.
    with pytest.raises(ValueError, match="CLASSIFICATION_"):
        create_app(Settings(applicationinsights_connection_string=None))


def test_story_1_8_the_actor_of_a_classification_names_the_service_and_the_deployment(
    settings: Settings,
) -> None:
    options = classify_options(
        settings.model_copy(
            update={"classifier_runs": 9, "classify_deadline_seconds": 12.0}
        )
    )

    assert options.actor == f"classification:{DEPLOYMENT}"
    assert (options.runs, options.deadline_seconds) == (9, 12.0)
    # The deployment name is nowhere in the service's code: it is a setting.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        assert "gpt-" not in source.read_text(), source.name


def test_story_1_8_a_refused_model_endpoint_names_its_variable() -> None:
    with pytest.raises(ValidationError, match="CLASSIFICATION_MODEL_ENDPOINT must be"):
        Settings(model_endpoint="http://127.0.0.1:5101/openai/v1")
    # The endpoint alone, with or without its closing slash, is taken.
    for endpoint in ("http://127.0.0.1:5101", "http://127.0.0.1:5101/"):
        assert model_base_url(Settings(model_endpoint=endpoint)) == (
            "http://127.0.0.1:5101/openai/v1/"
        )


def test_story_1_8_every_tuning_setting_reaches_what_it_configures(
    settings: Settings,
) -> None:
    tuned = settings.model_copy(
        update={
            "classifier_runs": 7,
            "classifier_max_concurrent_runs": 2,
            "classify_deadline_seconds": 33.0,
            "classify_stale_margin_seconds": 11.0,
            "model_max_retries": 1,
            "model_retry_seconds": 0.5,
            "model_max_retry_seconds": 4.0,
            "model_max_completion_tokens": 123,
            "model_max_concurrent_calls": 6,
        }
    )

    options = classify_options(tuned)
    app = create_app(tuned)

    assert (
        options.runs,
        options.max_concurrent_runs,
        options.deadline_seconds,
        options.stale_margin_seconds,
    ) == (7, 2, 33.0, 11.0)
    # The app is built around the same options, and a gateway with the
    # model's settings.
    built = app.state.dependencies
    assert built.options == options
    gateway = built.classify.model
    assert isinstance(gateway, ModelGateway)
    assert (
        gateway._max_retries,
        gateway._retry_seconds,
        gateway._max_retry_seconds,
        gateway._max_completion_tokens,
        gateway._max_concurrent_calls,
    ) == (1, 0.5, 4.0, 123, 6)
    asyncio.run(gateway.aclose())


def test_story_1_8_connection_strings_are_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr("InstrumentationKey=secret")
    )

    assert "secret" not in repr(settings)


def test_story_1_8_the_app_stack_sets_only_variables_the_settings_read() -> None:
    main = (REPOSITORY_ROOT / "infra" / "demo" / "app" / "main.tf").read_text()
    names = set(re.findall(r'name\s*=\s*"CLASSIFICATION_([A-Z_]+)"', main))

    assert names, "the app stack does not configure classification"
    assert names <= {name.upper() for name in Settings.model_fields}
    # What only the environment can say (AD-16): where the model is, what the
    # deployment is called, and that the identity is used for both.
    assert {
        "MODEL_ENDPOINT",
        "MODEL_ENTRA_AUTH",
        "CHAT_DEPLOYMENT",
        "DATABASE_ENTRA_AUTH",
        "AZURE_CLIENT_ID",
        "DAPR_HTTP_PORT",
        # What an operator tunes in the Azure session without a new image.
        "CLASSIFIER_RUNS",
        "CLASSIFIER_MAX_CONCURRENT_RUNS",
        "MODEL_MAX_CONCURRENT_CALLS",
        "MODEL_MAX_RETRIES",
    } <= names
    # No key and no password is handed to the service.
    assert not any("KEY" in name or "PASSWORD" in name for name in names)


def test_story_1_8_the_local_start_runs_the_service_with_its_sidecar_and_the_stand_in() -> (
    None
):
    run_file = (REPOSITORY_ROOT / "dapr.yaml").read_text()

    assert "appID: classification" in run_file
    assert 'CLASSIFICATION_DAPR_HTTP_PORT: "3503"' in run_file
    assert "daprHTTPPort: 3503" in run_file
    assert 'CLASSIFICATION_MODEL_ENDPOINT: "http://127.0.0.1:5101"' in run_file
    # `classification` never holds `intake`'s address: it knows the app id only.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        assert "8001" not in source.read_text(), source.name


# --- The prompt --------------------------------------------------------------------------


def test_story_1_8_the_prompt_is_a_file_in_the_package_under_version_control() -> None:
    prompt_file = SERVICE_DIR / "src" / "classification" / "prompts" / CLASSIFY_PAGE

    prompt = load_prompt(CLASSIFY_PAGE)

    assert prompt == prompt_file.read_text().strip()
    # It names every page type of the contracts, and no other answer field.
    for page_type in PageType:
        assert page_type.value in prompt
    assert "is_medical" not in prompt
    assert "confidence" not in prompt
    # azure.md rule 27: where its evaluation set is said beside the prompt.
    note = (prompt_file.parent / "__init__.py").read_text()
    assert "Epic 4" in note
    assert "rule 27" in note


def test_story_1_8_the_answer_schema_is_built_from_the_contracts() -> None:
    assert OUTPUT_SCHEMA["properties"] == {
        "page_type": {
            "type": "string",
            "enum": [page_type.value for page_type in PageType],
        },
        "reason": {"type": "string"},
    }
    assert OUTPUT_SCHEMA["required"] == list(ClassifierOutput.model_fields)
    assert OUTPUT_SCHEMA["additionalProperties"] is False


# --- The model gateway -------------------------------------------------------------------


@dataclass
class Deployment:
    """Stands in for the chat deployment: an `httpx2` transport handler.

    Answers each call from a script of statuses (200 once it runs out), with
    `content` as the message of a 200.
    """

    statuses: list[int] = field(default_factory=list)
    content: Any = field(default_factory=answer)
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    requests: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return httpx2.Response(
                status,
                headers=self.headers,
                json={"error": {"code": "x", "message": "SECRET-ENDPOINT-MESSAGE"}},
            )
        return httpx2.Response(
            200, json=self.body if self.body is not None else completion(self.content)
        )


def gateway_for(
    deployment: Deployment | Any, settings: Settings, **options: Any
) -> tuple[ModelGateway, list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    handler = deployment.handle if isinstance(deployment, Deployment) else deployment
    gateway = ModelGateway(
        build_model_client(
            settings, httpx2.MockTransport(handler), options.pop("token", None)
        ),
        deployment=chat_deployment(settings),
        sleep=sleep,
        # No random part in a wait, unless a test asks for one.
        **{"jitter": lambda: 1.0, **options},
    )
    return gateway, waits


def classify(gateway: ModelGateway) -> str:
    async def scenario() -> str:
        try:
            return await gateway.classify(PAGE)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def test_story_1_8_one_run_is_one_chat_completion_on_the_shared_deployment(
    settings: Settings,
) -> None:
    deployment = Deployment()
    gateway, waits = gateway_for(deployment, settings)

    given = classify(gateway)

    # The answer comes back as the model gave it: the domain parses it.
    assert given == answer()
    (request,) = deployment.requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:5101/openai/v1/chat/completions"
    body = json.loads(request.content)
    # AD-16: the deployment name, from the settings, is the model that is asked.
    assert body["model"] == DEPLOYMENT
    system, user = body["messages"]
    # The prompt from the package is the instruction; the page is data, in
    # the user turn: its text and its picture, one page.
    assert system == {"role": "system", "content": load_prompt(CLASSIFY_PAGE)}
    assert user["role"] == "user"
    text_part, image_part = user["content"]
    assert text_part == {"type": "text", "text": PAGE_TEXT}
    assert image_part == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
    }
    # Structured output: the object the contracts model describes.
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "classifier_output",
            "strict": True,
            "schema": OUTPUT_SCHEMA,
        },
    }
    # The answer is two short fields: it is given a small room, from a setting.
    assert body["max_completion_tokens"] == 2000
    assert Settings().model_max_completion_tokens == 2000
    assert set(body) == {
        "model",
        "messages",
        "response_format",
        "max_completion_tokens",
    }
    assert waits == []


def test_story_1_8_a_429_or_5xx_is_retried_honouring_retry_after(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[429, 503, 500], headers={"retry-after": "7"})
    gateway, waits = gateway_for(deployment, settings)

    with caplog.at_level(logging.WARNING):
        given = classify(gateway)

    # Three retries, each after the wait the answer asked for, then the answer.
    assert given == answer()
    assert len(deployment.requests) == 4
    assert waits == [7.0, 7.0, 7.0]
    assert "attempt=1 code=status_429" in caplog.text
    assert "attempt=3 code=status_500" in caplog.text


def test_story_1_8_after_three_retries_the_model_is_unavailable(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[429] * 20)
    gateway, waits = gateway_for(deployment, settings, retry_seconds=2.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        classify(gateway)

    # AD-16: the first call and three more, and no fifth.
    assert len(deployment.requests) == 4
    # Without a `Retry-After` the setting's wait is used, doubled each time;
    # none after the last.
    assert waits == [2.0, 4.0, 8.0]
    assert f"model unavailable: deployment={DEPLOYMENT} attempts=4" in caplog.text
    # security rule 31: nothing of the endpoint's answer is logged.
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    ("header", "waited"),
    [
        ("0", 0.0),
        ("1.5", 1.5),
        # A wait longer than the stage can afford is cut short.
        ("600", 30.0),
        # Not a finite number of seconds: the setting's wait.
        ("Wed, 07 Oct 2026 12:00:00 GMT", 1.0),
        ("-5", 1.0),
        ("nan", 1.0),
        ("inf", 1.0),
        ("-inf", 1.0),
    ],
)
def test_story_1_8_the_wait_before_a_retry_is_what_retry_after_says_within_a_limit(
    settings: Settings, header: str, waited: float
) -> None:
    deployment = Deployment(statuses=[429], headers={"retry-after": header})
    gateway, waits = gateway_for(deployment, settings)

    classify(gateway)

    assert waits == [waited]


def test_story_1_8_without_a_retry_after_the_waits_grow_to_a_limit_with_a_random_part(
    settings: Settings,
) -> None:
    deployment = Deployment(statuses=[503] * 20)
    gateway, waits = gateway_for(
        deployment,
        settings,
        max_retries=6,
        retry_seconds=1.0,
        max_retry_seconds=10.0,
    )

    with pytest.raises(ModelUnavailable):
        classify(gateway)

    # Doubled with every retry, and never longer than the limit.
    assert waits == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    # The random part takes up to half of a wait off, so that runs throttled
    # together do not all come back together.
    for jitter, expected in ((0.0, [0.5, 1.0, 2.0]), (0.5, [0.75, 1.5, 3.0])):
        deployment = Deployment(statuses=[503] * 20)
        gateway, waits = gateway_for(deployment, settings, jitter=lambda j=jitter: j)
        with pytest.raises(ModelUnavailable):
            classify(gateway)
        assert waits == expected
    # By default the random part is really random.
    assert ModelGateway(
        build_model_client(settings), deployment=DEPLOYMENT
    )._jitter is (random.random)


@pytest.mark.parametrize("status", [408, 409])
def test_story_1_8_a_408_or_409_is_not_now_and_is_sent_again(
    settings: Settings, status: int
) -> None:
    deployment = Deployment(statuses=[status])
    gateway, waits = gateway_for(deployment, settings)

    assert classify(gateway) == answer()
    assert (len(deployment.requests), waits) == (2, [1.0])


class NoTokenCredential:
    """A credential whose token service cannot be reached, a number of times."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def get_token(self, *scopes: str, **options: Any) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("SECRET token endpoint 169.254.169.254")
        return FakeCredential().get_token(*scopes)


@pytest.mark.parametrize(("failures", "answered"), [(2, True), (50, False)])
def test_story_1_8_a_token_that_cannot_be_fetched_is_retried_then_model_unavailable(
    caplog: pytest.LogCaptureFixture, failures: int, answered: bool
) -> None:
    credential = NoTokenCredential(failures)
    settings = Settings(
        model_endpoint="https://aif-aiuw-demo-wus3.cognitiveservices.azure.com",
        model_entra_auth=True,
        chat_deployment=DEPLOYMENT,
    )
    deployment = Deployment()
    gateway, waits = gateway_for(
        deployment,
        settings,
        token=EntraToken(credential, scope=COGNITIVE_SERVICES_SCOPE),
    )

    with caplog.at_level(logging.WARNING):
        if answered:
            assert classify(gateway) == answer()
        else:
            with pytest.raises(ModelUnavailable):
                classify(gateway)

    # No token, no call: the deployment was reached only once there was one.
    assert len(deployment.requests) == (1 if answered else 0)
    assert credential.calls == (3 if answered else 4)
    assert len(waits) == (2 if answered else 3)
    # The error's type only; its message can hold an address.
    assert "code=token_ConnectionError" in caplog.text
    assert "SECRET" not in caplog.text
    assert "169.254" not in caplog.text


def test_story_1_8_no_more_calls_than_the_setting_are_under_way_in_the_whole_process(
    settings: Settings,
) -> None:
    under_way = 0
    most = 0

    async def slow(request: httpx2.Request) -> httpx2.Response:
        nonlocal under_way, most
        under_way += 1
        most = max(most, under_way)
        await asyncio.sleep(0.01)
        under_way -= 1
        return httpx2.Response(200, json=completion(answer()))

    gateway, _ = gateway_for(slow, settings, max_concurrent_calls=3)

    async def many_pages() -> list[str]:
        try:
            # As many runs as several pages classified at once would start.
            return await asyncio.gather(*(gateway.classify(PAGE) for _ in range(12)))
        finally:
            await gateway.aclose()

    answers = asyncio.run(many_pages())

    assert len(answers) == 12
    assert most == 3
    assert Settings().model_max_concurrent_calls == 10


def test_story_1_8_a_call_that_gets_no_answer_is_retried_too(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ConnectError("cannot reach 10.0.0.9:443 SECRET")

    gateway, waits = gateway_for(unreachable, settings)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        classify(gateway)

    assert calls == 4
    assert len(waits) == 3
    # The error's type, never its message, which can hold an address.
    assert "code=APIConnectionError" in caplog.text
    assert "10.0.0.9" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_story_1_8_a_call_the_model_refuses_is_not_retried(
    settings: Settings, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    deployment = Deployment(statuses=[status])
    gateway, waits = gateway_for(deployment, settings)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelCallFailed) as raised:
        classify(gateway)

    # It would be refused again: one call, a code for the log, no detail.
    assert raised.value.reason == f"model_status_{status}"
    assert (len(deployment.requests), waits) == (1, [])
    assert f"model call refused: deployment={DEPLOYMENT} status={status}" in caplog.text
    assert "SECRET" not in caplog.text
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "body",
    [
        # A refusal or a filtered answer has no content.
        completion(None),
        {"id": "x", "object": "chat.completion", "choices": []},
        {"id": "x", "object": "chat.completion"},
        completion([{"type": "text", "text": "parts, not a string"}]),
    ],
    ids=["no-content", "no-choice", "no-choices", "content-in-parts"],
)
def test_story_1_8_an_answer_without_text_comes_back_empty_for_the_domain_to_refuse(
    settings: Settings, body: dict[str, Any]
) -> None:
    gateway, _ = gateway_for(Deployment(body=body), settings)

    given = classify(gateway)

    assert given == ""
    # ... which is not a `ClassifierOutput`.
    with pytest.raises(ValidationError):
        ClassifierOutput.model_validate_json(given)


def test_story_1_8_the_gateway_does_not_judge_the_answer(settings: Settings) -> None:
    gateway, _ = gateway_for(Deployment(content="not what was asked for"), settings)

    assert classify(gateway) == "not what was asked for"


class FakeCredential:
    """Stands in for the Azure identity library's credential."""

    def __init__(self, lifetime_seconds: float = 3600.0) -> None:
        self.scopes: list[str] = []
        self.lifetime_seconds = lifetime_seconds

    def get_token(self, *scopes: str, **options: Any) -> Any:
        self.scopes.extend(scopes)

        @dataclass
        class Token:
            token: str
            expires_on: float

        return Token(
            f"entra-token-{len(self.scopes)}", time.time() + self.lifetime_seconds
        )


def test_story_1_8_in_azure_the_model_is_signed_in_to_with_an_entra_token_never_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "classification.adapters.model.azure_credential", lambda settings: credential
    )
    settings = Settings(
        model_endpoint="https://aif-aiuw-demo-wus3.cognitiveservices.azure.com",
        model_entra_auth=True,
        chat_deployment=DEPLOYMENT,
        azure_client_id="client-id",
    )
    deployment = Deployment()
    token = model_token_for(settings)
    assert token is not None
    gateway, _ = gateway_for(deployment, settings, token=token)

    async def twice() -> None:
        try:
            await gateway.classify(PAGE)
            await gateway.classify(PAGE)
        finally:
            await gateway.aclose()

    asyncio.run(twice())

    first, second = deployment.requests
    assert str(first.url) == (
        "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com"
        "/openai/v1/chat/completions"
    )
    # The token of the service identity, for the scope of Azure AI services,
    # fetched once and kept.
    assert first.headers["authorization"] == "Bearer entra-token-1"
    assert second.headers["authorization"] == "Bearer entra-token-1"
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    assert "api-key" not in first.headers


def test_story_1_8_the_local_stand_in_is_sent_no_token(settings: Settings) -> None:
    deployment = Deployment()
    gateway, _ = gateway_for(deployment, settings)

    classify(gateway)

    # A fixed word in the place of a key: the stand-in takes no credential.
    assert deployment.requests[0].headers["authorization"] == f"Bearer {NO_KEY}"
    assert "api-key" not in deployment.requests[0].headers


def test_story_1_8_the_client_never_follows_a_redirect_or_retries_by_itself(
    settings: Settings,
) -> None:
    client = build_model_client(settings)

    # The gateway retries, where it can be seen; and a token never leaves
    # the endpoint for another address.
    assert client.max_retries == 0
    assert client._client.follow_redirects is False
    assert client._client.trust_env is False
    asyncio.run(client.close())


def test_story_1_8_every_model_call_has_a_span_of_its_own(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "classification.adapters.model.tracer", provider.get_tracer("test")
    )
    deployment = Deployment(statuses=[429, 400], headers={"retry-after": "7"})
    gateway, _ = gateway_for(deployment, settings)

    async def two_runs() -> None:
        try:
            # The first run is throttled once and then refused; the second is answered.
            with pytest.raises(ModelCallFailed):
                await gateway.classify(PAGE)
            await gateway.classify(PAGE)
        finally:
            await gateway.aclose()

    with caplog.at_level(logging.INFO):
        asyncio.run(two_runs())

    spans = exporter.get_finished_spans()
    runs = [span for span in spans if span.name == "classification.model.classify_page"]
    attempts = [span for span in spans if span.name == "classification.model.attempt"]
    # One span per HTTP call, each below the span of its run.
    assert len(attempts) == len(deployment.requests) == 3
    assert len(runs) == 2
    assert [span.parent.span_id for span in attempts if span.parent] == [
        runs[0].context.span_id,
        runs[0].context.span_id,
        runs[1].context.span_id,
    ]
    # Each says how the call ended, and the wait that followed if one did.
    assert [dict(span.attributes or {}) for span in attempts] == [
        {
            "classification.model.attempt": 1,
            "http.response.status_code": 429,
            "error.type": "status_429",
            "classification.model.wait_seconds": 7.0,
        },
        {"classification.model.attempt": 2, "http.response.status_code": 400},
        {"classification.model.attempt": 1, "http.response.status_code": 200},
    ]
    # The run's span carries the model, the attempts and the token counts.
    assert dict(runs[1].attributes or {}) == {
        "gen_ai.request.model": DEPLOYMENT,
        "classification.model.attempts": 1,
        "gen_ai.usage.input_tokens": 1,
        "gen_ai.usage.output_tokens": 1,
    }
    assert dict(runs[0].attributes or {})["classification.model.attempts"] == 2
    # ... and one log line per answered run says what it cost: counts only.
    assert (
        f"model run: deployment={DEPLOYMENT} attempts=1 input_tokens=1 output_tokens=1"
    ) in caplog.text
    assert caplog.text.count("model run:") == 1
    # Nothing of the page, the answer or the endpoint's message is on a span.
    for span in spans:
        assert "SECRET" not in repr(dict(span.attributes or {}))
        for event in span.events:
            assert "exception.message" not in (event.attributes or {})
            assert "SECRET" not in repr(dict(event.attributes or {}))


def test_story_1_8_an_error_inside_an_adapters_span_leaves_its_type_and_no_message() -> (
    None
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # As a database error would: its message holds the statement and its values.
    with (
        pytest.raises(RuntimeError),
        adapter_span(provider.get_tracer("test"), "classification.db.find"),
    ):
        raise RuntimeError("SELECT secret FROM t WHERE id = 'SECRET-VALUE'")

    (span,) = exporter.get_finished_spans()
    (event,) = span.events
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.status.description is None
    assert event.name == "exception"
    assert set(event.attributes or {}) == {"exception.type", "exception.stacktrace"}
    assert (event.attributes or {})["exception.type"] == "RuntimeError"
    assert "SECRET" not in repr(dict(event.attributes or {}))
    # The engine's own errors never carry the values of a statement either.
    assert build_database(Settings()).engine.sync_engine.hide_parameters is True
    # No adapter opens a span any other way.
    for source in (SERVICE_DIR / "src" / "classification" / "adapters").rglob("*.py"):
        if source.name != "telemetry.py":
            assert "start_as_current_span" not in source.read_text(), source.name


def test_story_1_8_an_answer_without_usage_counts_no_tokens(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    body = completion(answer())
    del body["usage"]
    gateway, _ = gateway_for(Deployment(body=body), settings)

    with caplog.at_level(logging.INFO):
        assert classify(gateway) == answer()

    assert "input_tokens=0 output_tokens=0" in caplog.text


def test_story_1_8_the_gateway_logs_nothing_of_what_it_sends_or_is_answered(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[500], content=answer(reason="SECRET-REASON"))
    gateway, _ = gateway_for(deployment, settings)

    with caplog.at_level(logging.DEBUG, logger="classification"):
        classify(gateway)

    assert caplog.text
    for secret in ("SECRET", "lab_report", "Laboratory", "iVBOR"):
        assert secret not in caplog.text


# --- The client module: `intake` through the Dapr sidecar ----------------------------------


def reader(handler: Any, settings: Settings | None = None) -> IntakeClient:
    return IntakeClient(
        build_http_client(settings or Settings(), httpx.MockTransport(handler))
    )


def on_reader(client: IntakeClient, work: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await work(client)
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_1_8_a_page_is_read_from_intake_through_the_sidecar_by_app_id() -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    first, second = sidecar.pages.add(case_id), sidecar.pages.add(case_id, "two")
    context = {"traceparent": TRACEPARENT}

    async def work(client: IntakeClient) -> tuple[Any, Any]:
        return (
            await client.page_ids_of_case(case_id, context),
            await client.read_page(first, context),
        )

    page_ids, page = on_reader(reader(sidecar.handle), work)

    assert page_ids == [first, second]
    # One page: its text and its thumbnail, and nothing else of the case.
    assert page == PageContent(text=PAGE_TEXT, image=PNG)
    # AD-3: the sidecar on loopback, `intake` by its Dapr app id, the paths
    # from the contracts. Reads only, and no hostname of another service.
    assert [(request.method, request.url.path) for request in sidecar.requests] == [
        ("GET", f"/v1.0/invoke/intake/method/cases/{case_id}/pages"),
        ("GET", f"/v1.0/invoke/intake/method/pages/{first}/text"),
        ("GET", f"/v1.0/invoke/intake/method/pages/{first}/thumbnail"),
    ]
    assert sidecar.requests[0].url.host == "127.0.0.1"
    for name in ("list_pages", "read_page_text", "read_page_thumbnail"):
        operation = get_operation(name)
        assert Service.CLASSIFICATION in operation.callers
        assert invoke_path(operation.owner, "/x") == "/v1.0/invoke/intake/method/x"
    # The W3C trace context goes with every read.
    assert {request.headers["traceparent"] for request in sidecar.requests} == {
        TRACEPARENT
    }


def test_story_1_8_with_telemetry_on_the_reads_stay_in_the_requests_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    sidecar.pages.add(case_id)
    provider = TracerProvider()
    monkeypatch.setattr("classification.adapters.dapr.tracer", provider.get_tracer("t"))

    async def work(client: IntakeClient) -> Any:
        with provider.get_tracer("t").start_as_current_span("request") as span:
            await client.page_ids_of_case(case_id, {"traceparent": TRACEPARENT})
            return trace.format_trace_id(span.get_span_context().trace_id)

    trace_id = on_reader(reader(sidecar.handle), work)

    # The read's own span is the caller of `intake`, in the request's trace.
    _, sent_trace, _, _ = sidecar.requests[0].headers["traceparent"].split("-")
    assert sent_trace == trace_id


def test_story_1_8_a_case_or_page_intake_does_not_hold_is_none() -> None:
    sidecar = IntakeSidecar()

    async def work(client: IntakeClient) -> tuple[Any, Any]:
        return (
            await client.page_ids_of_case(new_id(), {}),
            await client.read_page(new_id(), {}),
        )

    assert on_reader(reader(sidecar.handle), work) == (None, None)


def test_story_1_8_a_page_whose_thumbnail_is_gone_is_none() -> None:
    page_id = new_id()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text"):
            return httpx.Response(
                200, json={"page_id": page_id, "page_number": 1, "text": "x"}
            )
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    assert on_reader(reader(handler), lambda c: c.read_page(page_id, {})) is None


@pytest.mark.parametrize(
    "response",
    [
        # The sidecar's own answer when `intake` cannot be reached.
        httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE", "message": "x"}),
        httpx.Response(502, json={"error": {"code": "upstream_unavailable"}}),
        # 2xx with something that is not the contracts' shape.
        httpx.Response(200, json={"pages": "SECRET"}),
        httpx.Response(200, content=b"<html>SECRET</html>"),
    ],
    ids=["sidecar-error", "intake-error", "not-the-shape", "not-json"],
)
def test_story_1_8_any_other_answer_of_intake_is_upstream_unavailable(
    response: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    case_id, page_id = new_id(), new_id()

    for work in (
        lambda c: c.page_ids_of_case(case_id, {}),
        lambda c: c.read_page(page_id, {}),
    ):
        with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as raised:
            on_reader(reader(lambda request: response), work)
        assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE

    assert f"id={case_id}" in caplog.text
    assert f"id={page_id}" in caplog.text
    assert "SECRET" not in caplog.text
    assert "ERR_DIRECT_INVOKE" not in caplog.text


def test_story_1_8_an_answer_about_another_case_or_page_is_never_used() -> None:
    other = new_id()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pages"):
            return httpx.Response(200, json={"case_id": other, "pages": []})
        if request.url.path.endswith("/text"):
            # Another page's text must never be classified as this page's.
            return httpx.Response(
                200, json={"page_id": other, "page_number": 1, "text": "x"}
            )
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    for work in (
        lambda c: c.page_ids_of_case(new_id(), {}),
        lambda c: c.read_page(new_id(), {}),
    ):
        with pytest.raises(DomainError) as raised:
            on_reader(reader(handler), work)
        assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE


@pytest.mark.parametrize(
    ("content", "media_type"),
    [(b"", "image/png"), (b"<html>", "text/html"), (b"{}", "application/json")],
)
def test_story_1_8_a_thumbnail_that_is_not_a_png_is_never_sent_to_the_model(
    content: bytes, media_type: str
) -> None:
    page_id = new_id()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text"):
            return httpx.Response(
                200, json={"page_id": page_id, "page_number": 1, "text": "x"}
            )
        return httpx.Response(
            200, content=content, headers={"content-type": media_type}
        )

    with pytest.raises(DomainError) as raised:
        on_reader(reader(handler), lambda c: c.read_page(page_id, {}))

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE


def test_story_1_8_a_sidecar_that_cannot_be_reached_is_upstream_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot reach 127.0.0.1:3500")

    with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as raised:
        on_reader(reader(unreachable), lambda c: c.page_ids_of_case(new_id(), {}))

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert "type=ConnectError" in caplog.text
    # security rule 31: the error's type, never its message.
    assert "cannot reach" not in caplog.text


# --- The Entra token and the database's address ----------------------------------------------


def test_story_1_8_the_database_url_holds_no_password_and_requires_tls_in_azure() -> (
    None
):
    local = database_url(Settings())
    azure = database_url(
        Settings(
            database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
            database_user="id-aiuw-demo-wus3-classification",
            database_entra_auth=True,
        )
    )

    assert local.password is None
    assert azure.password is None
    assert "sslmode" not in local.query
    assert azure.query["sslmode"] == "require"
    assert azure.username == "id-aiuw-demo-wus3-classification"


def test_story_1_8_database_connections_sign_in_with_an_entra_token() -> None:
    credential = FakeCredential()
    token = EntraToken(credential)
    engine = create_engine("postgresql+psycopg://user@127.0.0.1:1/none")
    use_entra_token(engine, token)
    parameters: dict[str, Any] = {}

    engine.dialect.dispatch.do_connect(engine.dialect, None, (), parameters)

    # The token is the password; none is ever stored.
    assert list(parameters.values()) == ["entra-token-1"]
    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]


def test_story_1_8_the_token_is_fetched_off_the_event_loop_kept_and_replaced_in_time() -> (
    None
):
    credential = FakeCredential()
    now = [1000.0]
    token = EntraToken(credential, clock=lambda: now[0])

    async def scenario() -> list[str]:
        values = []
        await token.refresh()
        values.append(token.value())
        # Still fresh: nothing is fetched.
        await token.refresh()
        values.append(token.value())
        # Within five minutes of its end: replaced before it is used.
        now[0] = time.time() + credential.lifetime_seconds - 200
        await token.refresh()
        values.append(token.value())
        return values

    assert asyncio.run(scenario()) == [
        "entra-token-1",
        "entra-token-1",
        "entra-token-2",
    ]
    # A caller without an event loop (migrations) fetches on the spot.
    assert EntraToken(FakeCredential()).value() == "entra-token-1"


def test_story_1_8_the_service_refreshes_the_token_before_it_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "classification.adapters.db.azure_credential", lambda settings: credential
    )
    # A port nothing listens on: the connection fails, after the token was fetched.
    settings = Settings(
        database_entra_auth=True,
        database_port=1,
        database_connect_timeout_seconds=1,
    )
    database = build_database(settings)

    async def scenario() -> None:
        try:
            with pytest.raises(Exception):  # noqa: B017 - whatever the driver raises for a closed port
                async with database.connect():
                    pass
            with pytest.raises(Exception):  # noqa: B017 - as above
                async with database.begin():
                    pass
        finally:
            await database.dispose()

    asyncio.run(scenario())

    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]
    # Without Entra sign-in no token source is built at all.
    assert build_database(Settings())._token is None


def test_story_1_8_azure_calls_sign_in_with_the_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, Any]] = []

    class Managed:
        def __init__(self, client_id: str) -> None:
            built.append(("managed", client_id))

    class Default:
        def __init__(self) -> None:
            built.append(("default", None))

    import azure.identity

    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", Managed)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", Default)
    credential_module._credential.cache_clear()
    try:
        in_azure = credential_module.azure_credential(
            Settings(azure_client_id="client-id")
        )
        again = credential_module.azure_credential(
            Settings(azure_client_id="client-id")
        )
        credential_module.azure_credential(Settings())
    finally:
        credential_module._credential.cache_clear()

    # One credential per process, so its token cache is shared.
    assert in_azure is again
    assert built == [("managed", "client-id"), ("default", None)]


# --- Migrations ----------------------------------------------------------------------------


def test_story_1_8_the_bundled_head_is_the_first_migration_in_the_package() -> None:
    versions = sorted(path.name for path in (MIGRATIONS_DIR / "versions").glob("v*.py"))

    assert versions == ["v0001_classification.py"]
    assert bundled_head() == "0001"
    # Inside the package, so the image carries it.
    assert MIGRATIONS_DIR == SERVICE_DIR / "src" / "classification" / "migrations"
    ini = (SERVICE_DIR / "alembic.ini").read_text()
    assert "script_location = %(here)s/src/classification/migrations" in ini


def test_story_1_8_migrations_describe_only_the_services_own_schema(
    settings: Settings,
) -> None:
    assert include_name("classification", "schema", {}) is True
    assert include_name("intake", "schema", {}) is False
    assert include_name("workflow", "schema", {}) is False
    assert include_name("classification", "table", {}) is True
    assert alembic_config(settings).attributes["settings"] is settings
    # One table, in the service's schema, with no foreign key into another's.
    (table,) = metadata.tables.values()
    assert (table.schema, table.name) == ("classification", "classification")
    assert table.foreign_keys == set()
    assert {"page_type", "is_medical", "confidence", "contender"} <= set(
        table.columns.keys()
    )


# --- Start-up and telemetry ------------------------------------------------------------------


def test_story_1_8_the_server_starts_on_the_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}
    monkeypatch.setenv("CLASSIFICATION_HOST", "0.0.0.0")  # noqa: S104 - what the container image sets
    monkeypatch.setenv("CLASSIFICATION_PORT", "9103")
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **options: started.update(app=app, **options)
    )
    get_settings.cache_clear()
    try:
        runpy.run_module("classification", run_name="__main__")
    finally:
        get_settings.cache_clear()

    assert started == {
        "app": "classification.adapters.http.app:create_app",
        "factory": True,
        "host": "0.0.0.0",  # noqa: S104 - as above
        "port": 9103,
        "server_header": False,
    }


def test_story_1_8_an_app_built_from_the_settings_uses_the_real_adapters_and_closes_them(
    settings: Settings,
) -> None:
    app = create_app(settings)

    # Building it opened no connection; it starts and stops cleanly.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert isinstance(app, FastAPI)


def test_story_1_8_logging_is_configured_at_start_up_and_libraries_are_kept_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: dict[str, Any] = {}
    monkeypatch.setattr(
        logging, "basicConfig", lambda **options: configured.update(options)
    )

    configure_logging()

    assert configured["level"] == logging.INFO
    # The model client and its HTTP library never log a call themselves.
    assert {"openai", "httpx2", "azure"} <= set(QUIET_LOGGERS)
    for name in QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


class FakeExporterSetup:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    def __call__(self, **options: Any) -> None:
        self.options = options


def test_story_1_8_telemetry_is_reported_as_classification_with_the_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = FakeExporterSetup()
    assert configure_telemetry(Settings(), exporter_setup=exporter) is False
    assert exporter.options is None
    credential = FakeCredential()
    monkeypatch.setattr(
        "classification.adapters.telemetry.azure_credential",
        lambda settings: credential,
    )
    settings = Settings(
        applicationinsights_connection_string=SecretStr("InstrumentationKey=k"),
        otel_sampling_ratio=0.25,
    )

    assert configure_telemetry(settings, exporter_setup=exporter) is True

    assert exporter.options is not None
    assert exporter.options["credential"] is credential
    assert exporter.options["sampling_ratio"] == 0.25
    assert exporter.options["resource"].attributes["service.name"] == "classification"
    assert exporter.options["instrumentation_options"] == {
        "fastapi": {"enabled": False}
    }


def test_story_1_8_requests_are_traced_and_the_probes_are_not(
    settings: Settings, dependencies: Dependencies
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings, dependencies=dependencies)
    instrument_app(app, tracer_provider=provider)
    case_id = new_id()

    with TestClient(app) as client:
        client.get("/health")
        client.get("/ready")
        client.get(f"/cases/{case_id}/classifications")

    assert EXCLUDED_URLS == "/health$,/ready$"
    servers = [
        span.name
        for span in exporter.get_finished_spans()
        if span.kind is trace.SpanKind.SERVER
    ]
    assert servers == ["GET /cases/{case_id}/classifications"]


def test_story_1_8_the_trace_id_falls_back_to_the_callers_traceparent() -> None:
    assert current_trace_id(TRACEPARENT) == TRACE_ID
    assert current_trace_id("not-a-traceparent") is None
    assert current_trace_id(None) is None


def test_story_1_8_the_service_shares_no_code_with_another_service() -> None:
    pyproject = (SERVICE_DIR / "pyproject.toml").read_text()

    # Contracts is the only shared code (AD-3); the model client is pinned.
    # That no stand-in ships with the service is checked beside the stand-in,
    # in `packages/`: nothing under `services/` may name its package.
    assert '"openai==3.24.0"' in pyproject
    assert re.findall(r"^(\w+) = \{ workspace = true \}", pyproject, re.MULTILINE) == [
        "contracts"
    ]
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text()
        assert not re.search(
            r"^\s*(from|import) (intake|workflow|web)\b", text, re.MULTILINE
        ), source.name
