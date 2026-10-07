"""Story 2.2: the adapters of `retrieval`, without a database, a model or a network.

Unit tests: the settings, the Document Intelligence client and the model
gateway (each against a transport that stands in for the service), the
manual's blob, the Entra token, telemetry and the bundled migrations.
"""

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    PDF,
    FakeCredential,
    FakeSearchService,
    analyze_result,
    completion,
    context_answer,
    embedding_answer,
    manual,
    vector_for,
)

from retrieval.adapters.layout import (
    DocumentLayout,
    build_layout_http,
)
from retrieval.adapters.model import (
    COGNITIVE_SERVICES_SCOPE,
    NO_KEY,
    ModelGateway,
    build_model_client,
    chat_deployment,
    embedding_deployment,
    model_token_for,
)
from retrieval.adapters.search_index import (
    SEARCH_SCOPE,
    SearchIndex,
    build_search_http,
    search_token_for,
)
from retrieval.domain.ports import (
    LayoutFailed,
    ModelUnavailable,
)
from retrieval.settings import Settings

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVICE_DIR.parents[1]
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
FOUNDRY = "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com"
DOCUMENT_INTELLIGENCE = "https://di-aiuw-demo-wus3.cognitiveservices.azure.com"
SEARCH_SERVICE = "https://srch-aiuw-demo-wus3.search.windows.net"
RULE_PLACE = "Section: 2 SECRET-SECTION\nRule text:\nRule UW-AA-001: SECRET-RULE."


# --- Settings ----------------------------------------------------------------------------


def test_story_2_2_settings_that_would_reach_a_service_unsafely_are_refused() -> None:
    for values in (
        # The real service without the identity.
        {"model_endpoint": FOUNDRY},
        {"search_service_endpoint": SEARCH_SERVICE},
        # Story 3.3: the stand-in for the search service is on loopback and
        # takes no token, so it can never be the endpoint in Azure.
        {"search_service_endpoint": "http://search-stand-in.example:5103"},
        {
            "search_service_endpoint": "http://127.0.0.1:5103",
            "search_service_entra_auth": True,
        },
    ):
        with pytest.raises(ValidationError):
            Settings(**values)
    # The stand-in on this machine, and the real service with the identity.
    Settings(search_service_endpoint="http://127.0.0.1:5103")
    Settings(search_service_endpoint=SEARCH_SERVICE, search_service_entra_auth=True)


# --- The model gateway -------------------------------------------------------------------


@dataclass
class Deployments:
    """Stands in for the two deployments: an `httpx2` transport handler.

    Answers each call from a script of statuses (200 once it runs out).
    """

    statuses: list[int] = field(default_factory=list)
    content: Any = field(default_factory=context_answer)
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    dimensions: int | None = None
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
        if self.body is not None:
            return httpx2.Response(200, json=self.body)
        if request.url.path.endswith("/embeddings"):
            texts = json.loads(request.content)["input"]
            return httpx2.Response(200, json=embedding_answer(texts, self.dimensions))
        return httpx2.Response(200, json=completion(self.content))


def gateway_for(
    deployments: Deployments | Any, settings: Settings, **options: Any
) -> tuple[ModelGateway, list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    handler = (
        deployments.handle if isinstance(deployments, Deployments) else deployments
    )
    gateway = ModelGateway(
        build_model_client(
            settings, httpx2.MockTransport(handler), options.pop("token", None)
        ),
        chat_deployment=chat_deployment(settings),
        embedding_deployment=embedding_deployment(settings),
        sleep=sleep,
        # No random part in a wait, unless a test asks for one.
        **{"jitter": lambda: 1.0, **options},
    )
    return gateway, waits


def on(gateway: ModelGateway, call: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await call(gateway)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def context_line(gateway: ModelGateway) -> str:
    return str(on(gateway, lambda g: g.context_line(RULE_PLACE)))


def test_story_2_2_a_call_answered_not_now_is_retried_and_after_three_retries_the_model_is_unavailable(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # Answered in the end: the waits are what `Retry-After` asks for.
    deployments = Deployments(statuses=[429, 429], headers={"retry-after": "2"})
    gateway, waits = gateway_for(deployments, settings)

    assert on(gateway, lambda g: g.embed(["a"])) == [vector_for("a")]

    assert len(deployments.requests) == 3
    assert waits == [2.0, 2.0]

    # Never answered.
    deployments = Deployments(statuses=[429] * 10)
    gateway, waits = gateway_for(deployments, settings, retry_seconds=1.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        context_line(gateway)

    # AD-16: the first call and three more, with waits that grow.
    assert len(deployments.requests) == 4
    assert waits == [1.0, 2.0, 4.0]
    assert (
        f"model unavailable: operation=context_line deployment={CHAT} attempts=4"
        in (caplog.text)
    )
    assert "SECRET" not in caplog.text


def azure_settings() -> Settings:
    return Settings(
        model_endpoint=FOUNDRY,
        model_entra_auth=True,
        layout_endpoint=DOCUMENT_INTELLIGENCE,
        layout_entra_auth=True,
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
        azure_client_id="client-id",
    )


def test_story_2_2_in_azure_the_models_are_signed_in_to_with_an_entra_token_never_a_key(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.model.azure_credential", lambda settings: credential
    )
    azure = azure_settings()
    deployments = Deployments()
    token = model_token_for(azure)
    assert token is not None
    gateway, _ = gateway_for(deployments, azure, token=token)

    async def both(g: ModelGateway) -> None:
        await g.context_line(RULE_PLACE)
        await g.embed(["a"])

    on(gateway, both)

    first, second = deployments.requests
    assert str(first.url) == f"{FOUNDRY}/openai/v1/chat/completions"
    assert str(second.url) == f"{FOUNDRY}/openai/v1/embeddings"
    # The token of the service identity, fetched once and kept.
    assert first.headers["authorization"] == "Bearer entra-token-1"
    assert second.headers["authorization"] == "Bearer entra-token-1"
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    assert "api-key" not in first.headers
    # The local stand-in is sent a fixed word in the place of a key.
    local = Deployments()
    stand_in, _ = gateway_for(local, settings)
    context_line(stand_in)
    assert local.requests[0].headers["authorization"] == f"Bearer {NO_KEY}"

    # Story 3.3: the search service likewise, with a token for its own
    # scope and never a key; the local stand-in is sent no credential.
    monkeypatch.setattr(
        "retrieval.adapters.search_index.azure_credential", lambda settings: credential
    )
    in_azure = azure.model_copy(
        update={
            "search_service_endpoint": SEARCH_SERVICE,
            "search_service_entra_auth": True,
        }
    )
    service, stand_in_service = FakeSearchService(), FakeSearchService()

    async def created(of: Settings, fake: FakeSearchService) -> bool:
        index = SearchIndex(
            build_search_http(of, fake.transport()),
            index_name=of.search_service_index_name,
            api_version=of.search_service_api_version,
            token=search_token_for(of),
        )
        try:
            return await index.ensure()
        finally:
            await index.aclose()

    assert asyncio.run(created(in_azure, service))
    read, create = service.requests
    assert (
        str(read.url) == f"{SEARCH_SERVICE}/indexes/manual-smart?api-version=2024-07-01"
    )
    assert (read.method, create.method) == ("GET", "PUT")
    assert create.headers["authorization"] == "Bearer entra-token-2"
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE, SEARCH_SCOPE]
    assert "api-key" not in read.headers and "api-key" not in create.headers
    local_search = settings.model_copy(
        update={"search_service_endpoint": "http://127.0.0.1:5103"}
    )
    assert asyncio.run(created(local_search, stand_in_service))
    assert "authorization" not in stand_in_service.requests[0].headers


# --- Document Intelligence ---------------------------------------------------------------


@dataclass
class LayoutService:
    """Stands in for Document Intelligence: an `httpx2` transport handler."""

    # What each submit and each look is answered with, in order; the last again.
    submits: list[Any] = field(default_factory=lambda: [202])
    looks: list[Any] = field(default_factory=lambda: ["succeeded"])
    result: Any = field(default_factory=analyze_result)
    location: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Sent with every look that is answered 200 or refused for now.
    look_headers: dict[str, str] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.method == "POST":
            given = self._next(self.submits)
            if isinstance(given, Exception):
                raise given
            if given != 202:
                return httpx2.Response(given, headers=self.headers, json={})
            location = self.location
            if location is None:
                # Another host than the endpoint: only the id may be taken from it.
                location = (
                    "https://elsewhere.example.com/documentintelligence/documentModels/"
                    "prebuilt-layout/analyzeResults/result-1?api-version=2024-11-30"
                )
            return httpx2.Response(202, headers={"operation-location": location})
        given = self._next(self.looks)
        if isinstance(given, Exception):
            raise given
        if isinstance(given, int):
            return httpx2.Response(given, headers=self.look_headers, json={})
        if given == "not-json":
            return httpx2.Response(200, content=b"SECRET not json")
        if given == "not-an-object":
            return httpx2.Response(200, json=["SECRET"])
        body: dict[str, Any] = {"status": given}
        if given == "succeeded":
            body["analyzeResult"] = self.result
        if given == "failed":
            body["error"] = {
                "code": "Invalid-Content Length!",
                "message": "SECRET-SERVICE-MESSAGE",
            }
        return httpx2.Response(200, headers=self.look_headers, json=body)

    @staticmethod
    def _next(script: list[Any]) -> Any:
        return script.pop(0) if len(script) > 1 else script[0]


def layout_for(
    service: LayoutService, settings: Settings, **options: Any
) -> tuple[DocumentLayout, list[float]]:
    waits: list[float] = []
    now = [0.0]

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        # The clock moves only when the adapter waits: no test sleeps.
        now[0] += seconds

    return (
        DocumentLayout(
            build_layout_http(settings, httpx2.MockTransport(service.handle)),
            api_version=settings.layout_api_version,
            model=settings.layout_model,
            poll_seconds=options.pop("poll_seconds", 2.0),
            deadline_seconds=options.pop("deadline_seconds", 600.0),
            max_retries=settings.layout_max_retries,
            sleep=sleep,
            clock=lambda: now[0],
            **options,
        ),
        waits,
    )


def parse(layout: DocumentLayout) -> Any:
    async def scenario() -> Any:
        try:
            return await layout.parse(PDF)
        finally:
            await layout.aclose()

    return asyncio.run(scenario())


def test_story_2_2_the_manual_is_parsed_by_the_layout_model_over_rest(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    service = LayoutService(looks=["notStarted", "running", "succeeded"])
    layout, waits = layout_for(service, settings)

    with caplog.at_level(logging.DEBUG):
        parsed = parse(layout)

    assert parsed == manual()
    submit, *looks = service.requests
    assert (submit.method, submit.url.path) == (
        "POST",
        "/documentintelligence/documentModels/prebuilt-layout:analyze",
    )
    assert submit.url.params["api-version"] == "2024-11-30"
    # The manual's bytes are sent; the service is given no address to read.
    assert json.loads(submit.content) == {
        "base64Source": base64.b64encode(PDF).decode()
    }
    # Every look goes to the configured endpoint: only the id is taken from
    # the address the service named.
    result_address = (
        "http://127.0.0.1:5102/documentintelligence/documentModels/prebuilt-layout"
        "/analyzeResults/result-1?api-version=2024-11-30"
    )
    assert {str(look.url) for look in looks} == {result_address}
    assert len(looks) == 3
    assert waits == [2.0, 2.0]
    assert "authorization" not in submit.headers
    assert "layout parsed: result_id=result-1 pages=4" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    ("service", "reason"),
    [
        (
            LayoutService(submits=[httpx2.ReadTimeout("SECRET")]),
            "layout_submit_ReadTimeout",
        ),
        # A failed analysis, with the service's own code as an identifier.
        (
            LayoutService(looks=["running", "failed"]),
            "layout_failed_InvalidContentLength",
        ),
    ],
)
def test_story_2_2_a_layout_error_ends_the_parse_with_a_code(
    settings: Settings,
    service: LayoutService,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    layout, _ = layout_for(service, settings)

    with caplog.at_level(logging.DEBUG), pytest.raises(LayoutFailed) as raised:
        parse(layout)

    assert raised.value.reason == reason
    # A code, never the service's message or the document.
    assert "SECRET" not in caplog.text
    assert "SECRET" not in str(raised.value)


# --- What the service is made of -------------------------------------------------------------


def test_story_2_2_the_service_shares_no_code_with_and_calls_no_other_service() -> None:
    pyproject = (SERVICE_DIR / "pyproject.toml").read_text()

    # Contracts is the only shared code (AD-3); the spine's pins.
    assert '"openai==3.24.0"' in pyproject
    assert '"pgvector==0.5.0"' in pyproject
    assert re.findall(r"^(\w+) = \{ workspace = true \}", pyproject, re.MULTILINE) == [
        "contracts"
    ]
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text()
        assert not re.search(
            r"^\s*(from|import) (intake|workflow|web|classification)\b",
            text,
            re.MULTILINE,
        ), source.name
    # The job calls no other service of ours.
    # No Dapr sidecar, no app id, no service invocation anywhere in the package.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text().lower()
        assert "/v1.0/invoke" not in text, source.name
        assert "dapr" not in text.replace("the dapr app id", ""), source.name
