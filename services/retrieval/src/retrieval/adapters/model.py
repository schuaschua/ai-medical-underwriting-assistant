"""The model gateway: the one module that calls the Foundry deployments (spine AD-16).

Three calls: the shared chat deployment writes one chunk's context line at
ingestion and, for a search with row `r4`, says how relevant each fused
candidate is to the query; and the one embedding deployment turns texts
into vectors: a chunk's at ingestion and a query's at search, the same
way. The gateway does not
judge what comes back; the domain does. A call answered 429 or 5xx (or 408
or 409), or not answered at all, is sent again up to three times, waiting as
long as `Retry-After` asks or else longer each time, and then the model is
`model_unavailable`. An embedding answer that cannot be matched to the texts
it was asked for is refused. Every call has a span, and every HTTP attempt a span of
its own below it. No more than a set number of attempts are under way at once
in the whole process. Nothing that is sent or answered is logged: only codes,
counts and timings.
"""

import asyncio
import contextlib
import logging
import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema
from opentelemetry import trace

from retrieval.adapters.credential import azure_credential
from retrieval.adapters.db import EntraToken
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.ingest import CONTEXT_FIELD
from retrieval.domain.ports import (
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelUnavailable,
)
from retrieval.domain.rerank import CHUNK_ID_FIELD, RANKING_FIELD, RELEVANCE_FIELD
from retrieval.prompts import CHUNK_CONTEXT, RERANK, load_prompt
from retrieval.settings import APP_ID, Settings

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

# Structured output: the chat model may answer with this object and nothing else.
CONTEXT_SCHEMA_NAME = "chunk_context"
CONTEXT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {CONTEXT_FIELD: {"type": "string"}},
    "required": [CONTEXT_FIELD],
    "additionalProperties": False,
}
CONTEXT_FORMAT: ResponseFormatJSONSchema = {
    "type": "json_schema",
    "json_schema": {
        "name": CONTEXT_SCHEMA_NAME,
        "strict": True,
        "schema": CONTEXT_SCHEMA,
    },
}


# Row `r4`: the reranker's structured output, one entry per candidate. The
# schema says nothing of the range of a relevance or of which ids may be
# named: the domain checks both, as it would have to anyway.
RERANK_SCHEMA_NAME = "rerank_relevance"
RERANK_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        RANKING_FIELD: {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    CHUNK_ID_FIELD: {"type": "string"},
                    RELEVANCE_FIELD: {"type": "number"},
                },
                "required": [CHUNK_ID_FIELD, RELEVANCE_FIELD],
                "additionalProperties": False,
            },
        }
    },
    "required": [RANKING_FIELD],
    "additionalProperties": False,
}
RERANK_FORMAT: ResponseFormatJSONSchema = {
    "type": "json_schema",
    "json_schema": {
        "name": RERANK_SCHEMA_NAME,
        "strict": True,
        "schema": RERANK_SCHEMA,
    },
}


def model_base_url(settings: Settings) -> str:
    """The address of the OpenAI API on the configured endpoint."""
    if settings.model_endpoint is None:
        raise ValueError(
            "Set RETRIEVAL_MODEL_ENDPOINT: the Foundry account, or the local stand-in."
        )
    return settings.model_endpoint.rstrip("/") + OPENAI_API_PATH


def chat_deployment(settings: Settings) -> str:
    """AD-16: the deployment name reaches code only as a setting."""
    if settings.chat_deployment is None:
        raise ValueError(
            "Set RETRIEVAL_CHAT_DEPLOYMENT: the name of the chat deployment."
        )
    return settings.chat_deployment


def embedding_deployment(settings: Settings) -> str:
    """AD-16: the one embedding deployment, named only in the settings."""
    if settings.embedding_deployment is None:
        raise ValueError(
            "Set RETRIEVAL_EMBEDDING_DEPLOYMENT: the name of the embedding deployment."
        )
    return settings.embedding_deployment


def model_token_for(settings: Settings) -> EntraToken | None:
    """The token source for the models in Azure; None for the local stand-in."""
    if not settings.model_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


def build_model_client(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
    token: EntraToken | None = None,
    timeout_seconds: float | None = None,
) -> openai.AsyncOpenAI:
    """The client for the deployments. Tests pass a transport that stands in for them.

    Building it makes no network call. The library's own retries are off:
    the gateway retries, so that it can be seen and tested here. Without
    `timeout_seconds` a call may take as long as the ingestion's setting says.
    """
    timeout = (
        settings.model_timeout_seconds if timeout_seconds is None else timeout_seconds
    )

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
        timeout=timeout,
        http_client=httpx2.AsyncClient(
            transport=transport,
            timeout=timeout,
            # Never follow a redirect: the token must not leave the endpoint.
            follow_redirects=False,
            trust_env=False,
        ),
    )


class TokenUnavailable(Exception):
    """The Entra token for the models could not be had just now."""

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
    """Writes context lines and rates candidates on the chat deployment, and vectors on the embedding deployment."""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        embedding_deployment: str,
        # None where the service was told of no chat deployment: it then
        # embeds queries and rates no candidates (row `r4` is off).
        chat_deployment: str | None = None,
        max_retries: int = 3,
        retry_seconds: float = 1.0,
        max_retry_seconds: float = 30.0,
        max_completion_tokens: int = 2000,
        max_concurrent_calls: int = 5,
        # Row `r4`: how long the one chat call of a search may take, whatever
        # the client's own timeout is, and the most tokens of its answer.
        rerank_timeout_seconds: float = 15.0,
        rerank_max_completion_tokens: int = 4000,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client
        self._chat_deployment = chat_deployment
        self._embedding_deployment = embedding_deployment
        self._max_retries = max_retries
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._max_completion_tokens = max_completion_tokens
        # One for the whole process: however many chunks are written at
        # once, no more calls than this are with the deployments at a time.
        self._calls = asyncio.Semaphore(max_concurrent_calls)
        # Row `r4`: a rerank call is slow, and takes its place under the
        # same cap. Rerank calls together may hold all of it but one slot,
        # so that a query's embedding, which has a few seconds, never
        # waits behind a full house of them. (With a cap of one there is
        # no slot to leave.)
        self._rerank_calls = asyncio.Semaphore(max(1, max_concurrent_calls - 1))
        self._sleep = sleep
        self._jitter = jitter
        self._prompt = load_prompt(CHUNK_CONTEXT)
        self._rerank_timeout_seconds = rerank_timeout_seconds
        self._rerank_max_completion_tokens = rerank_max_completion_tokens
        self._rerank_prompt = load_prompt(RERANK)

    async def aclose(self) -> None:
        await self._client.close()

    async def context_line(self, rule_in_its_place: str) -> str:
        """One chat completion: the model's answer as it gave it, or an empty one."""
        deployment = self._chat_deployment
        if deployment is None:
            raise ModelCallFailed("chat_deployment_not_set")
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self._prompt},
            # The rule is data: it goes in the user turn, never in the
            # instructions (security rule 14).
            {"role": "user", "content": rule_in_its_place},
        ]

        async def send() -> Any:
            return await self._client.chat.completions.create(
                model=deployment,
                messages=messages,
                response_format=CONTEXT_FORMAT,
                max_completion_tokens=self._max_completion_tokens,
            )

        completion = await self._call("context_line", deployment, send)
        return _answer_of(completion)

    async def relevance(self, query_and_candidates: str) -> str:
        """Row `r4`: one chat completion that rates the candidates; the answer as the model gave it.

        It shares the cap on concurrent calls with every other call of this
        gateway, of which rerank calls leave one slot free, and has a
        timeout of its own: a chat call does not fit the few seconds a
        query's embedding gets. A call that timed out is not sent again:
        the search's deadline would pass before a second one is answered,
        and its prompt would be paid for and thrown away.
        """
        deployment = self._chat_deployment
        if deployment is None:
            raise ModelCallFailed("chat_deployment_not_set")
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self._rerank_prompt},
            # The query and the chunks are data: they go in the user turn,
            # never in the instructions (security rule 14).
            {"role": "user", "content": query_and_candidates},
        ]

        async def send() -> Any:
            return await self._client.chat.completions.create(
                model=deployment,
                messages=messages,
                response_format=RERANK_FORMAT,
                max_completion_tokens=self._rerank_max_completion_tokens,
                timeout=self._rerank_timeout_seconds,
            )

        completion = await self._call(
            "rerank",
            deployment,
            send,
            part=self._rerank_calls,
            again_after_timeout=False,
        )
        return _answer_of(completion)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One embedding call: a vector per text, in the order of the texts."""

        async def send() -> Any:
            return await self._client.embeddings.create(
                model=self._embedding_deployment,
                input=list(texts),
                encoding_format="float",
            )

        answer = await self._call("embed", self._embedding_deployment, send)
        return _vectors_of(answer, len(texts))

    def _wait_before(self, retry: int, retry_after: float | None) -> float:
        """How long to wait before the `retry`-th retry (counted from 1).

        What `Retry-After` asks for, if it asks. Otherwise longer each time:
        the setting's wait, doubled for every retry so far, with up to half
        of it taken off at random so that calls throttled together do not all
        come back together. Never longer than the longest wait set.
        """
        if retry_after is not None:
            return min(retry_after, self._max_retry_seconds)
        longest = min(self._retry_seconds * 2.0 ** (retry - 1), self._max_retry_seconds)
        return longest * (0.5 + 0.5 * self._jitter())

    @contextlib.asynccontextmanager
    async def _slot(self, part: asyncio.Semaphore | None) -> AsyncIterator[None]:
        """One slot of the cap; for a call that may use only a part of it, a slot of that part first.

        The part is waited for before the cap, never while holding a slot
        of it: calls that wait for their part hold up nobody else.
        """
        if part is None:
            async with self._calls:
                yield
            return
        async with part, self._calls:
            yield

    async def _call(
        self,
        operation: str,
        deployment: str,
        send: Callable[[], Awaitable[Any]],
        *,
        part: asyncio.Semaphore | None = None,
        again_after_timeout: bool = True,
    ) -> Any:
        """Send one call until it is answered, at most `max_retries` more times.

        `part` limits how many such calls are under way at once, inside
        the cap. Without `again_after_timeout` a call that got no answer
        in its time is not sent again.
        """
        with adapter_span(tracer, f"retrieval.model.{operation}") as span:
            span.set_attribute("gen_ai.request.model", deployment)
            # AD-16: the first attempt, then up to `max_retries` more.
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("retrieval.model.attempts", attempt)
                try:
                    answer = await self._attempt(deployment, send, attempt, part)
                except _NotAnswered as not_answered:
                    logger.warning(
                        "model call not answered: operation=%s deployment=%s "
                        "attempt=%d code=%s",
                        operation,
                        deployment,
                        attempt,
                        not_answered.code,
                    )
                    if not_answered.timed_out and not again_after_timeout:
                        break
                    if attempt <= self._max_retries:
                        await self._sleep(not_answered.wait_seconds)
                    continue
                input_tokens, output_tokens = _usage_of(answer)
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                # Counts only: what the call cost, never what it said.
                logger.info(
                    "model call: operation=%s deployment=%s attempts=%d "
                    "input_tokens=%d output_tokens=%d",
                    operation,
                    deployment,
                    attempt,
                    input_tokens,
                    output_tokens,
                )
                return answer
            logger.error(
                "model unavailable: operation=%s deployment=%s attempts=%d",
                operation,
                deployment,
                attempt,
            )
            raise ModelUnavailable

    async def _attempt(
        self,
        deployment: str,
        send: Callable[[], Awaitable[Any]],
        attempt: int,
        part: asyncio.Semaphore | None = None,
    ) -> Any:
        """One HTTP call, in a span of its own; `_NotAnswered` when it is worth sending again."""
        with adapter_span(tracer, "retrieval.model.attempt") as span:
            span.set_attribute("retrieval.model.attempt", attempt)
            try:
                # Held only while the call is under way, not while waiting
                # to retry.
                async with self._slot(part):
                    answer = await send()
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
                    "model call refused: deployment=%s status=%d", deployment, status
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
                not_answered = self._not_answered(span, code, attempt, None)
                not_answered.timed_out = isinstance(error, openai.APITimeoutError)
                raise not_answered from None
            span.set_attribute("http.response.status_code", 200)
            return answer

    def _not_answered(
        self, span: Any, code: str, attempt: int, retry_after: float | None
    ) -> "_NotAnswered":
        """Note on the attempt's span why it got no answer, and the wait that follows."""
        wait_seconds = self._wait_before(attempt, retry_after)
        span.set_attribute("error.type", code)
        if attempt <= self._max_retries:
            span.set_attribute("retrieval.model.wait_seconds", wait_seconds)
        return _NotAnswered(code, wait_seconds)


class _NotAnswered(Exception):
    """The model could not answer just now: 429, a 5xx, or no answer at all."""

    def __init__(self, code: str, wait_seconds: float) -> None:
        super().__init__(code)
        self.code = code
        self.wait_seconds = wait_seconds
        # Whether the call was sent and got no answer in its time.
        self.timed_out = False


def _usage_of(answer: Any) -> tuple[int, int]:
    """The prompt and completion token counts of an answer; 0 where it names none."""
    usage = getattr(answer, "usage", None)
    counts = (
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )
    first, second = (count if isinstance(count, int) else 0 for count in counts)
    return first, second


def _answer_of(completion: Any) -> str:
    """The text of the chat model's answer; empty when it gave none (a refusal, a filter)."""
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else ""


def _vectors_of(answer: Any, asked: int) -> list[list[float]]:
    """The vectors of an embedding answer, in the order of the texts they were asked for.

    Each item says with its `index` which text it is the vector of. An answer
    whose indexes are not exactly one per text (one twice, one missing, one
    out of range) cannot be matched to the texts, and a vector stored with
    the wrong chunk would never be noticed: it is refused. What a vector
    holds is the domain's to judge.
    """
    try:
        items = list(answer.data)
        indexes = [item.index for item in items]
        vectors = {item.index: list(item.embedding) for item in items}
    except (AttributeError, TypeError):
        raise ModelAnswerInvalid("embedding_answer_malformed") from None
    whole = all(type(index) is int for index in indexes)
    if not whole or sorted(indexes) != list(range(asked)):
        raise ModelAnswerInvalid("embedding_index_invalid")
    return [vectors[index] for index in range(asked)]
