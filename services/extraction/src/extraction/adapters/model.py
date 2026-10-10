"""The model gateway: the one module that calls the chat deployment (spine AD-16).

A page's extraction is one chat completion on the shared deployment of the
Foundry account: the prompt and the page's text go in, and the model's
answer comes back as it gave it. The gateway does not judge the
answer; the domain parses it. A call answered 429 or 5xx (or 408 or 409), or
not answered at all, is sent again up to three times, waiting as long as
`Retry-After` asks or else longer each time, and then the model is
`model_unavailable`. Every extraction has a span, and every HTTP call a span of its
own below it. No more than a set number of calls are under way at once in the
whole process. Nothing that is sent or answered is logged: only codes, counts
and timings.
"""

import asyncio
import logging
import math
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema
from opentelemetry import trace

from extraction.adapters.credential import azure_credential
from extraction.adapters.db import EntraToken
from extraction.adapters.telemetry import adapter_span
from extraction.domain.entities import ModelAnswer
from extraction.domain.ports import ModelCallFailed, ModelUnavailable
from extraction.prompts import EXTRACT_FACTS, load_prompt
from extraction.settings import APP_ID, Settings

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

# Structured output: the model may answer with this object and nothing else.
# It is the shape of the contracts' `ExtractionOutput`: a list of facts, each
# a statement and a quote. Nothing else is asked of the model: no id, no page
# number, no offset and no word on whether the quote is on the page (AD-14).
OUTPUT_SCHEMA_NAME = "extraction_output"
OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["statement", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}
RESPONSE_FORMAT: ResponseFormatJSONSchema = {
    "type": "json_schema",
    "json_schema": {
        "name": OUTPUT_SCHEMA_NAME,
        "strict": True,
        "schema": OUTPUT_SCHEMA,
    },
}


def model_base_url(settings: Settings) -> str:
    """The address of the OpenAI API on the configured endpoint."""
    if settings.model_endpoint is None:
        raise ValueError(
            "Set EXTRACTION_MODEL_ENDPOINT: the Foundry account, or the local stand-in."
        )
    return settings.model_endpoint.rstrip("/") + OPENAI_API_PATH


def chat_deployment(settings: Settings) -> str:
    """AD-16: the deployment name reaches code only as a setting."""
    if settings.chat_deployment is None:
        raise ValueError(
            "Set EXTRACTION_CHAT_DEPLOYMENT: the name of the chat deployment."
        )
    return settings.chat_deployment


def model_token_for(settings: Settings) -> EntraToken | None:
    """The token source for the model in Azure; None for the local stand-in."""
    if not settings.model_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


def build_model_client(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
    token: EntraToken | None = None,
) -> openai.AsyncOpenAI:
    """The client for the chat deployment. Tests pass a transport that stands in for it.

    Building it makes no network call. The library's own retries are off:
    the gateway retries, so that it can be seen and tested here.
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

    return openai.AsyncOpenAI(
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
    """Asks the chat deployment for the facts of a page, one call per page."""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
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
        self._deployment = deployment
        self._max_retries = max_retries
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._max_completion_tokens = max_completion_tokens
        self._max_concurrent_calls = max_concurrent_calls
        # One for the whole process: however many pages are read at
        # once, no more calls than this are with the deployment at a time.
        self._calls = asyncio.Semaphore(max_concurrent_calls)
        self._sleep = sleep
        self._jitter = jitter
        self._prompt = load_prompt(EXTRACT_FACTS)

    async def aclose(self) -> None:
        await self._client.close()

    def _messages(self, page_text: str) -> list[ChatCompletionMessageParam]:
        return [
            {"role": "system", "content": self._prompt},
            # The page is data: it goes in the user turn, never in the
            # instructions (security rule 14).
            {"role": "user", "content": page_text},
        ]

    def _wait_before(self, retry: int, retry_after: float | None) -> float:
        """How long to wait before the `retry`-th retry (counted from 1).

        What `Retry-After` asks for, if it asks. Otherwise longer each time:
        the setting's wait, doubled for every retry so far, with up to half
        of it taken off at random so that pages throttled together do not all
        come back together. Never longer than the stage can afford.
        """
        if retry_after is not None:
            return min(retry_after, self._max_retry_seconds)
        longest = min(self._retry_seconds * 2.0 ** (retry - 1), self._max_retry_seconds)
        return longest * (0.5 + 0.5 * self._jitter())

    async def extract(self, page_text: str) -> ModelAnswer:
        """One page: the model's answer as it gave it (empty if it gave none), and why it stopped."""
        messages = self._messages(page_text)
        with adapter_span(tracer, "extraction.model.extract_facts") as span:
            span.set_attribute("gen_ai.request.model", self._deployment)
            # AD-16: the first call, then up to `max_retries` more.
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("extraction.model.attempts", attempt)
                try:
                    completion = await self._attempt(messages, attempt)
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
                    "model run: deployment=%s attempts=%d input_tokens=%d "
                    "output_tokens=%d finish_reason=%s",
                    self._deployment,
                    attempt,
                    input_tokens,
                    output_tokens,
                    finish_reason,
                )
                return ModelAnswer(_answer_of(completion), finish_reason)
            logger.error(
                "model unavailable: deployment=%s attempts=%d",
                self._deployment,
                self._max_retries + 1,
            )
            raise ModelUnavailable

    async def _attempt(
        self, messages: list[ChatCompletionMessageParam], attempt: int
    ) -> Any:
        """One HTTP call, in a span of its own; `_NotAnswered` when it is worth sending again."""
        with adapter_span(tracer, "extraction.model.attempt") as span:
            span.set_attribute("extraction.model.attempt", attempt)
            try:
                # Held only while the call is under way, not while waiting
                # to retry.
                async with self._calls:
                    completion = await self._client.chat.completions.create(
                        model=self._deployment,
                        messages=messages,
                        response_format=RESPONSE_FORMAT,
                        max_completion_tokens=self._max_completion_tokens,
                    )
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
            span.set_attribute("extraction.model.wait_seconds", wait_seconds)
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


def _answer_of(completion: Any) -> str:
    """The text of the model's answer; empty when it gave none (a refusal, a filter)."""
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else ""
