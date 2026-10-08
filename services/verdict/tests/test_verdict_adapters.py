"""Story 2.5: the adapters of `verdict`, without a database, a model or a network.

Unit tests: the settings, the model gateway (against a transport that stands
in for the deployment), the clients that read facts from `extraction` and
rules from `retrieval` through the Dapr sidecar, the Entra token, telemetry
and the bundled migrations. The agent on the framework has its own file.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import ValidationError
from verdict_fakes import (
    DEPLOYMENT,
    DM_25,
    DM_50,
    FakeRules,
    ScriptedModel,
    Turn,
    fact,
    list_facts_call,
)

from contracts.enums import RetrieverConfig, Service
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.operations import OPERATIONS
from verdict.adapters.dapr import (
    ExtractionClient,
    RetrievalClient,
    Upstreams,
    build_http_client,
)
from verdict.adapters.model import (
    COGNITIVE_SERVICES_SCOPE,
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_token_for,
)
from verdict.domain.ports import ModelCallFailed
from verdict.settings import Settings

SERVICE_DIR = Path(__file__).resolve().parents[1]
# As the framework hands them over: plain mappings.
MESSAGES: list[Any] = [
    {"role": "system", "content": "SECRET-PROMPT"},
    {"role": "user", "content": "SECRET-TASK HbA1c 7.4 %"},
]
ANSWER = "SECRET-ANSWER of the model"


# --- Settings ----------------------------------------------------------------------------


def test_story_2_5_settings_that_would_reach_the_model_unsafely_or_run_without_bounds_are_refused() -> (
    None
):
    refused: tuple[dict[str, Any], ...] = (
        # Plain HTTP is the stand-in: on loopback only, and never with a token.
        {"model_endpoint": "http://example.com:5101"},
        {"model_endpoint": "http://127.0.0.1:5101", "model_entra_auth": True},
        # The agent's time budget ends safely inside the stage deadline.
        {"agent_time_budget_seconds": 175.0},
    )
    for values in refused:
        with pytest.raises(ValidationError):
            Settings(**values)


# --- The model gateway -------------------------------------------------------------------


def gateway_for(
    model: ScriptedModel | Any, settings: Settings, **options: Any
) -> tuple[ModelGateway, list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    handler = model.handle if isinstance(model, ScriptedModel) else model
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


def ask(gateway: ModelGateway, **request: Any) -> Any:
    """One chat completion, asked as the Agent Framework asks: on the gateway's client."""

    async def scenario() -> Any:
        try:
            return await gateway.client.chat.completions.create(
                **{
                    "model": "whatever-the-caller-names",
                    "messages": MESSAGES,
                    **request,
                }
            )
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def model_answering(*turns: Turn, **options: Any) -> ScriptedModel:
    return ScriptedModel(turns=[*turns, ANSWER], **options)


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


AZURE_ENDPOINT = "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com"


def test_story_2_5_in_azure_the_model_is_signed_in_to_with_an_entra_token_never_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "verdict.adapters.model.azure_credential", lambda settings: credential
    )
    settings = Settings(
        model_endpoint=AZURE_ENDPOINT,
        model_entra_auth=True,
        chat_deployment=DEPLOYMENT,
        azure_client_id="client-id",
    )
    model = model_answering()
    token = model_token_for(settings)
    assert token is not None
    gateway, _ = gateway_for(model, settings, token=token)

    async def twice() -> None:
        try:
            await gateway.complete({"messages": MESSAGES})
            await gateway.complete({"messages": MESSAGES})
        finally:
            await gateway.aclose()

    asyncio.run(twice())

    first, second = model.requests
    assert str(first.url) == f"{AZURE_ENDPOINT}/openai/v1/chat/completions"
    # The token of the service identity, for the scope of Azure AI services,
    # fetched once and kept.
    assert first.headers["authorization"] == "Bearer entra-token-1"
    assert second.headers["authorization"] == "Bearer entra-token-1"
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    assert "api-key" not in first.headers


def test_story_2_5_the_gateway_logs_nothing_of_what_it_sends_or_is_answered(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedModel(turns=[500, [list_facts_call()], 404])
    gateway, _ = gateway_for(model, settings)

    async def scenario() -> None:
        try:
            await gateway.complete({"messages": MESSAGES})
            with pytest.raises(ModelCallFailed):
                await gateway.complete({"messages": MESSAGES})
        finally:
            await gateway.aclose()

    with caplog.at_level(logging.DEBUG, logger="verdict"):
        asyncio.run(scenario())

    assert caplog.text
    for secret in ("SECRET", "HbA1c", "7.4", "list_facts"):
        assert secret not in caplog.text


# --- The client module: `extraction` and `retrieval` through the Dapr sidecar --------------


@dataclass
class Clients:
    extraction: ExtractionClient
    retrieval: RetrievalClient
    waits: list[float]
    upstreams: Upstreams


def on_clients(handler: Any, work: Any, settings: Settings | None = None) -> Any:
    """Run `work` on the two clients, with `handler` where the sidecar would be."""
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def scenario() -> Any:
        used = settings or Settings()
        upstreams = Upstreams(
            build_http_client(used, httpx.MockTransport(handler)), used, sleep
        )
        try:
            return await work(
                Clients(upstreams.extraction, upstreams.retrieval, waits, upstreams)
            )
        finally:
            await upstreams.aclose()

    return asyncio.run(scenario()), waits


def test_story_2_5_these_three_reads_are_the_only_calls_verdict_makes() -> None:
    # The agent has no other way to any data (AD-3, AD-15).
    called = {
        operation.name
        for operation in OPERATIONS
        if Service.VERDICT in operation.callers
    }

    assert called == {"list_facts", "search_rules", "read_rule"}
    source = (SERVICE_DIR / "src" / "verdict" / "adapters" / "dapr.py").read_text()
    assert set(re.findall(r'get_operation\("(\w+)"\)', source)) == called
    # No Dapr SDK, and no other module of the service talks HTTP to a service.
    for module in (SERVICE_DIR / "src").rglob("*.py"):
        text = module.read_text()
        assert not re.search(r"^\s*(from|import) dapr\b", text, re.MULTILINE)
        if module.name != "dapr.py":
            assert "/v1.0/invoke" not in text, module.name


def test_story_2_5_an_answer_about_another_case_row_or_rule_is_never_used() -> None:
    case_id, other_case = new_id(), new_id()
    rules = FakeRules(default=[DM_50])
    other_fact = fact(other_case).model_dump(mode="json")
    # A search answered for another row would be logged, and compared, as this row's.
    other_row = asyncio.run(rules.search("q", RetrieverConfig.R2, 5, {}))
    answers = {
        # Another case's list; and this case's list with another case's fact in it.
        "another-case": {"case_id": other_case, "facts": []},
        "another-cases-fact": {"case_id": case_id, "facts": [other_fact]},
        "another-row": other_row.model_dump(mode="json"),
        # Another rule's text must never be read as this rule's.
        "another-rule": rules.manual[DM_25].model_dump(mode="json"),
    }
    work = {
        "another-case": lambda c: c.extraction.facts_of_case(case_id, {}),
        "another-cases-fact": lambda c: c.extraction.facts_of_case(case_id, {}),
        "another-row": lambda c: c.retrieval.search("q", RetrieverConfig.R3, 5, {}),
        "another-rule": lambda c: c.retrieval.read(DM_50, RetrieverConfig.R3, {}),
    }

    for name, body in answers.items():
        calls = 0

        def handler(request: httpx.Request, body: Any = body) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=body)

        with pytest.raises(DomainError) as raised:
            on_clients(handler, work[name])
        assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE, name
        assert calls == 1, name
