"""The model gateway: the one module that calls the chat deployment (spine AD-16).

The verdict agent is run by Microsoft Agent Framework, which makes the
model's calls itself: one chat completion per turn of the agent. It is given
the client built here, and that client sends every chat completion through
the gateway. So the rules of the other services' gateways hold for the
agent's calls too. A call answered 429 or 5xx (or 408 or 409), or not
answered at all, is sent again up to three times, waiting as long as
`Retry-After` asks or else longer each time, and then the model is
`model_unavailable`. Every call has a span with its token usage, and every
HTTP attempt a span of its own below it. No more than a set number of calls
are under way at once in the whole process. The deployment and the token
limit of an answer are set here, whatever the caller asks for. Nothing that
is sent or answered is logged: only codes, counts and timings.
"""

import asyncio
import logging
import math
import random
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import cached_property
from typing import Any

import httpx2
import openai
from openai.resources.chat import AsyncChat, AsyncCompletions
from opentelemetry import trace

from verdict.adapters.credential import azure_credential
from verdict.adapters.db import EntraToken
from verdict.adapters.telemetry import adapter_span
from verdict.domain.ports import ModelCallFailed, ModelUnavailable
from verdict.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# The scope of an Entra token for Azure AI services, the Foundry account among them.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
# Where the Foundry account serves the OpenAI API, below its endpoint.
OPENAI_API_PATH = "/openai/v1/"
# What the client is given in place of a key for the local stand-in, which
# takes no credential. It is no secret, and it is never used in Azure, where
# the service signs in with its identity and key access is off.
NO_KEY = "no-key-local-stand-in"
RETRY_AFTER = "retry-after"
# Statuses that say "not now", besides every 5xx: the call is sent again.
_NOT_NOW = frozenset({408, 409, 429})


def model_base_url(settings: Settings) -> str:
    """The address of the OpenAI API on the configured endpoint."""
    if settings.model_endpoint is None:
        raise ValueError(
            "Set VERDICT_MODEL_ENDPOINT: the Foundry account, or the local stand-in."
        )
    return settings.model_endpoint.rstrip("/") + OPENAI_API_PATH


def chat_deployment(settings: Settings) -> str:
    """AD-16: the deployment name reaches code only as a setting."""
    if settings.chat_deployment is None:
        raise ValueError(
            "Set VERDICT_CHAT_DEPLOYMENT: the name of the chat deployment."
        )
    return settings.chat_deployment


def model_token_for(settings: Settings) -> EntraToken | None:
    """The token source for the model in Azure; None for the local stand-in."""
    if not settings.model_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


class _GatedCompletions(AsyncCompletions):
    """The chat completions of a `GatewayClient`: `create` goes through the gateway."""

    # The SDK's `create` is a set of overloads with a parameter per request
    # field; this one takes the request whole and hands it to the gateway.
    async def create(self, **request: Any) -> Any:
        gate = getattr(self._client, "gate", None)
        if gate is None:
            raise RuntimeError("no model gateway holds this client")
        return await gate(request)

    async def ungated(self, **request: Any) -> Any:
        """The SDK's own call: one HTTP request. Only the gateway uses it."""
        opened = _THROUGH_THE_GATE.set(True)
        try:
            return await super().create(**request)
        finally:
            _THROUGH_THE_GATE.reset(opened)


class _GatedChat(AsyncChat):
    @cached_property
    def completions(self) -> AsyncCompletions:
        return _GatedCompletions(self._client)


# Set while the gateway makes its one HTTP call, and at no other time.
_THROUGH_THE_GATE: ContextVar[bool] = ContextVar("through_the_gate", default=False)
NOT_THROUGH_THE_GATE_MESSAGE = (
    "The model deployment is called through the model gateway only."
)


class GatewayClient(openai.AsyncOpenAI):
    """The OpenAI client the Agent Framework is given: every call it makes goes through the gateway, or is not made.

    The framework calls `client.chat.completions.create(...)` for every turn
    of the agent. On this client that call is the gateway's (`gate`). Every
    other way to send something with the client (the Responses API,
    embeddings, a raw `post`, the ungated resource itself) ends in
    `request`, which raises unless the gateway is the caller: nothing
    reaches the deployment without its retries, its cap, its spans and its
    token counts.
    """

    # Set by the `ModelGateway` that holds the client.
    gate: Callable[[dict[str, Any]], Awaitable[Any]] | None = None

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        if not _THROUGH_THE_GATE.get():
            raise RuntimeError(NOT_THROUGH_THE_GATE_MESSAGE)
        return await super().request(*args, **kwargs)

    @cached_property
    def chat(self) -> AsyncChat:
        return _GatedChat(self)


def build_model_client(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
    token: EntraToken | None = None,
) -> GatewayClient:
    """The client for the chat deployment. Tests pass a transport that stands in for it.

    Building it makes no network call. The library's own retries are off:
    the gateway retries, so that it can be seen and tested here. A
    `ModelGateway` must be built around it before it is used.
    """

    async def bearer() -> str:
        if token is None:
            return NO_KEY
        try:
            # Fetched off the event loop, and kept until it is near its end.
            await token.refresh()
            return token.value()
        except Exception as error:  # noqa: BLE001 - whatever kept the token away, the call was not made
            # security rule 31: the type only; the identity library's
            # message can hold an address or a tenant.
            raise TokenUnavailable(type(error).__qualname__) from None

    return GatewayClient(
        base_url=model_base_url(settings),
        # Managed identity (security rule 9): an Entra token, never a key.
        api_key=bearer,
        max_retries=0,
        timeout=settings.model_timeout_seconds,
        http_client=httpx2.AsyncClient(
            transport=transport,
            timeout=settings.model_timeout_seconds,
            # Never follow a redirect: the token must not leave the endpoint.
            follow_redirects=False,
            trust_env=False,
        ),
    )


class TokenUnavailable(Exception):
    """The Entra token for the model could not be had just now."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _retry_after_seconds(headers: Any) -> float | None:
    """What `Retry-After` asks for, when it is a finite number of seconds."""
    try:
        seconds = float(headers.get(RETRY_AFTER, ""))
    except (TypeError, ValueError):
        return None
    # `nan` and `inf` are numbers to `float`, and no wait at all.
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class ModelGateway:
    """Every chat completion of the verdict agent, one call of the model at a time."""

    def __init__(
        self,
        client: GatewayClient,
        *,
        deployment: str,
        max_retries: int = 3,
        retry_seconds: float = 1.0,
        max_retry_seconds: float = 30.0,
        max_completion_tokens: int = 4000,
        max_concurrent_calls: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client
        # From here on the client's chat completions are this gateway's.
        client.gate = self.complete
        self._completions = _GatedCompletions(client)
        self._deployment = deployment
        self._max_retries = max_retries
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._max_completion_tokens = max_completion_tokens
        # One for the whole process: however many runs are under way at
        # once, no more calls than this are with the deployment at a time.
        self._calls = asyncio.Semaphore(max_concurrent_calls)
        self._sleep = sleep
        self._jitter = jitter

    @property
    def client(self) -> GatewayClient:
        """The client to hand to the Agent Framework."""
        return self._client

    @property
    def deployment(self) -> str:
        return self._deployment

    async def aclose(self) -> None:
        await self._client.close()

    def _wait_before(self, retry: int, retry_after: float | None) -> float:
        """How long to wait before the `retry`-th retry (counted from 1).

        What `Retry-After` asks for, if it asks. Otherwise longer each time:
        the setting's wait, doubled for every retry so far, with up to half
        of it taken off at random so that runs throttled together do not all
        come back together. Never longer than the stage can afford.
        """
        if retry_after is not None:
            return min(retry_after, self._max_retry_seconds)
        longest = min(self._retry_seconds * 2.0 ** (retry - 1), self._max_retry_seconds)
        return longest * (0.5 + 0.5 * self._jitter())

    def _request(self, asked: dict[str, Any]) -> dict[str, Any]:
        """The request as it is sent: what the framework asked, with what the gateway fixes.

        AD-16: the deployment is this service's, whatever the caller named,
        and so is the most an answer may cost. A streamed answer is not
        taken: its usage and its end could not be read here.
        """
        if asked.get("stream"):
            raise ModelCallFailed("stream_not_supported")
        request = {
            name: value
            for name, value in asked.items()
            if name not in ("stream", "max_tokens")
        }
        request["model"] = self._deployment
        request["max_completion_tokens"] = self._max_completion_tokens
        return request

    async def complete(self, asked: dict[str, Any]) -> Any:
        """One chat completion for the agent: the model's answer as it gave it.

        Raises `ModelUnavailable` when the model could not be had after the
        retries, and `ModelCallFailed` when it refused the call.
        """
        request = self._request(asked)
        with adapter_span(tracer, "verdict.model.chat_completion") as span:
            span.set_attribute("gen_ai.request.model", self._deployment)
            # AD-16: the first call, then up to `max_retries` more.
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("verdict.model.attempts", attempt)
                try:
                    completion = await self._attempt(request, attempt)
                except _NotAnswered as not_answered:
                    logger.warning(
                        "model call not answered: deployment=%s attempt=%d code=%s",
                        self._deployment,
                        attempt,
                        not_answered.code,
                    )
                    if attempt <= self._max_retries:
                        await self._sleep(not_answered.wait_seconds)
                    continue
                input_tokens, output_tokens = _usage_of(completion)
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                finish_reason = _finish_reason_of(completion)
                span.set_attribute("gen_ai.response.finish_reason", finish_reason)
                # Counts only: what the call cost, never what it said.
                logger.info(
                    "model call: deployment=%s attempts=%d input_tokens=%d "
                    "output_tokens=%d finish_reason=%s",
                    self._deployment,
                    attempt,
                    input_tokens,
                    output_tokens,
                    finish_reason,
                )
                return completion
            logger.error(
                "model unavailable: deployment=%s attempts=%d",
                self._deployment,
                self._max_retries + 1,
            )
            raise ModelUnavailable

    async def _attempt(self, request: dict[str, Any], attempt: int) -> Any:
        """One HTTP call, in a span of its own; `_NotAnswered` when it is worth sending again."""
        with adapter_span(tracer, "verdict.model.attempt") as span:
            span.set_attribute("verdict.model.attempt", attempt)
            try:
                # Held only while the call is under way, not while waiting
                # to retry.
                async with self._calls:
                    completion = await self._completions.ungated(**request)
            except openai.APIStatusError as error:
                status = error.status_code
                span.set_attribute("http.response.status_code", status)
                if status in _NOT_NOW or status >= 500:
                    raise self._not_answered(
                        span,
                        f"status_{status}",
                        attempt,
                        _retry_after_seconds(error.response.headers),
                    ) from None
                # Refused, and it would be again: not signed in, no such
                # deployment, a request the model does not take.
                logger.error(
                    "model call refused: deployment=%s status=%d",
                    self._deployment,
                    status,
                )
                raise ModelCallFailed(f"model_status_{status}") from None
            except TokenUnavailable as error:
                # No token, so no call: as good as no answer.
                raise self._not_answered(
                    span, f"token_{error.kind}", attempt, None
                ) from None
            except openai.APIConnectionError as error:
                # No answer at all, or none in time; or the token could not
                # be had and the library reports it as its own error.
                # security rule 31: the type only.
                cause = error.__cause__
                code = (
                    f"token_{cause.kind}"
                    if isinstance(cause, TokenUnavailable)
                    else type(error).__qualname__
                )
                raise self._not_answered(span, code, attempt, None) from None
            span.set_attribute("http.response.status_code", 200)
            return completion

    def _not_answered(
        self, span: Any, code: str, attempt: int, retry_after: float | None
    ) -> "_NotAnswered":
        """Note on the attempt's span why it got no answer, and the wait that follows."""
        wait_seconds = self._wait_before(attempt, retry_after)
        span.set_attribute("error.type", code)
        if attempt <= self._max_retries:
            span.set_attribute("verdict.model.wait_seconds", wait_seconds)
        return _NotAnswered(code, wait_seconds)


class _NotAnswered(Exception):
    """The model could not answer just now: 429, a 5xx, or no answer at all."""

    def __init__(self, code: str, wait_seconds: float) -> None:
        super().__init__(code)
        self.code = code
        self.wait_seconds = wait_seconds


def _usage_of(completion: Any) -> tuple[int, int]:
    """The prompt and completion token counts of an answer; 0 where it names none."""
    usage = getattr(completion, "usage", None)
    counts = (
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )
    first, second = (count if isinstance(count, int) else 0 for count in counts)
    return first, second


_FINISH_REASON = re.compile(r"[a-z_]{1,32}")


def _finish_reason_of(completion: Any) -> str:
    """Why the model stopped, as a code: never the model's own text beyond a plain word."""
    try:
        reason = completion.choices[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return "none"
    if reason is None:
        return "none"
    return (
        reason
        if isinstance(reason, str) and _FINISH_REASON.fullmatch(reason)
        else "other"
    )
