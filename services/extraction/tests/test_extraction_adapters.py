"""Story 2.4: the adapters of `extraction`, without a database, a model or a network.

Unit tests: the settings, the model gateway (against a transport that stands
in for the deployment), the client that reads pages from `intake` through the
Dapr sidecar, the Entra token, telemetry and the bundled migrations.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from extraction_fakes import (
    DEPLOYMENT,
    PAGE_TEXT,
    TRACEPARENT,
    IntakeSidecar,
    answer,
    completion,
)
from pydantic import ValidationError

from contracts.enums import Service
from contracts.ids import new_id
from contracts.operations import get_operation
from extraction.adapters.dapr import (
    IntakeClient,
    build_http_client,
    invoke_path,
)
from extraction.adapters.http.app import extract_options
from extraction.adapters.model import (
    COGNITIVE_SERVICES_SCOPE,
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_token_for,
)
from extraction.domain.entities import PageReading
from extraction.domain.ports import ModelUnavailable
from extraction.settings import Settings

SERVICE_DIR = Path(__file__).resolve().parents[1]


# --- Settings ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        # Plain HTTP is the stand-in: on loopback only, and never with a token.
        {"model_endpoint": "http://example.com:5101"},
    ],
)
def test_story_2_4_settings_that_would_reach_the_model_unsafely_are_refused(
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_story_2_4_the_actor_of_an_extraction_names_the_service_and_the_deployment(
    settings: Settings,
) -> None:
    options = extract_options(
        settings.model_copy(update={"extract_deadline_seconds": 12.0})
    )

    assert options.actor == f"extraction:{DEPLOYMENT}"
    assert options.deadline_seconds == 12.0
    # The deployment name is nowhere in the service's code: it is a setting.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        assert "gpt-" not in source.read_text(), source.name


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


def extract(gateway: ModelGateway) -> str:
    async def scenario() -> str:
        try:
            return (await gateway.extract(PAGE_TEXT)).text
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def test_story_2_4_a_429_or_5xx_is_retried_honouring_retry_after(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[429, 503, 500], headers={"retry-after": "7"})
    gateway, waits = gateway_for(deployment, settings)

    with caplog.at_level(logging.WARNING):
        given = extract(gateway)

    # Three retries, each after the wait the answer asked for, then the answer.
    assert given == answer()
    assert len(deployment.requests) == 4
    assert waits == [7.0, 7.0, 7.0]
    assert "attempt=1 code=status_429" in caplog.text
    assert "attempt=3 code=status_500" in caplog.text


def test_story_2_4_after_three_retries_the_model_is_unavailable(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[429] * 20)
    gateway, waits = gateway_for(deployment, settings, retry_seconds=2.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        extract(gateway)

    # AD-16: the first call and three more, and no fifth.
    assert len(deployment.requests) == 4
    # Without a `Retry-After` the setting's wait is used, doubled each time;
    # none after the last.
    assert waits == [2.0, 4.0, 8.0]
    assert f"model unavailable: deployment={DEPLOYMENT} attempts=4" in caplog.text
    # security rule 31: nothing of the endpoint's answer is logged.
    assert "SECRET" not in caplog.text


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


def test_story_2_4_in_azure_the_model_is_signed_in_to_with_an_entra_token_never_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "extraction.adapters.model.azure_credential", lambda settings: credential
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
            await gateway.extract(PAGE_TEXT)
            await gateway.extract(PAGE_TEXT)
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


def test_story_2_4_the_gateway_logs_nothing_of_what_it_sends_or_is_answered(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = Deployment(statuses=[500])
    gateway, _ = gateway_for(deployment, settings)

    with caplog.at_level(logging.DEBUG, logger="extraction"):
        extract(gateway)

    assert caplog.text
    for secret in ("SECRET", "HbA1c", "Laboratory", "7.4", "[Person]"):
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


def test_story_2_4_a_page_is_read_from_intake_through_the_sidecar_by_app_id() -> None:
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
    # One page: its number and its stored text, and nothing else of the case.
    assert page == PageReading(page_number=1, text=PAGE_TEXT)
    # AD-3: the sidecar on loopback, `intake` by its Dapr app id, the paths
    # from the contracts. Reads only, and no hostname of another service.
    assert [(request.method, request.url.path) for request in sidecar.requests] == [
        ("GET", f"/v1.0/invoke/intake/method/cases/{case_id}/pages"),
        ("GET", f"/v1.0/invoke/intake/method/pages/{first}/text"),
    ]
    # No picture and no file of the page is ever asked for: the text only.
    assert not any("thumbnail" in request.url.path for request in sidecar.requests)
    assert sidecar.requests[0].url.host == "127.0.0.1"
    for name in ("list_pages", "read_page_text"):
        operation = get_operation(name)
        assert Service.EXTRACTION in operation.callers
        assert invoke_path(operation.owner, "/x") == "/v1.0/invoke/intake/method/x"
    # The W3C trace context goes with every read.
    assert {request.headers["traceparent"] for request in sidecar.requests} == {
        TRACEPARENT
    }


# --- Start-up and telemetry ------------------------------------------------------------------


def test_story_2_4_the_service_shares_no_code_with_another_service() -> None:
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
