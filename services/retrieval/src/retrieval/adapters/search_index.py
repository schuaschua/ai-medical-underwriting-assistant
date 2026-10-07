"""Azure AI Search adapter: the index of the `smart` chunks, over REST (spine AD-11, AD-12; row `r5`).

One client for both sides: the ingestion job creates the index, uploads and
deletes documents and lists what the index holds; a search with `r5` sends
one hybrid query with the semantic ranker.

REST with the HTTP client the other adapters use, not the service's SDK. The
spine pins `azure-search-documents` 12.1.0b2, a pre-release, for row `r6`
alone, which needs its agentic retrieval. Row `r5` needs five plain calls of
the stable REST version, and written out they follow the patterns of the
other adapters: a transport a test can replace, no redirect followed, an
Entra token and never a key, a span per call and a retry that can be seen.

The index (`index_definition`): one document per chunk, keyed by `chunk_id`,
with the fields of the chunk record. The vector field has the dimensions of
the one embedding deployment and is searched exhaustively: the service
compares the query with every document, as pgvector does (AD-12), and
approximates nothing. One semantic configuration names the fields the
ranker reads.

Nothing the service answers with is logged except statuses and codes, and
neither a query nor a document's text ever is (security rule 31).
"""

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx2
from opentelemetry import trace

from retrieval.adapters.credential import azure_credential
from retrieval.adapters.db import EntraToken
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    IndexDocument,
    IndexHoldings,
    RankedDocument,
)
from retrieval.domain.ports import SearchServiceUnavailable
from retrieval.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# The scope of an Entra token for Azure AI Search.
SEARCH_SCOPE = "https://search.azure.com/.default"
RETRY_AFTER = "retry-after"
# The longest wait the service may ask for with `Retry-After`.
MAX_RETRY_AFTER_SECONDS = 30.0

# The names inside the index definition.
KEY_FIELD = "chunk_id"
VECTOR_FIELD = "embedding"
HASH_FIELD = "document_hash"
VECTOR_ALGORITHM = "exact"
VECTOR_PROFILE = "exact-cosine"
SEMANTIC_CONFIGURATION = "rules"
# English, with stemming: what the chunk table's full-text column uses too.
TEXT_ANALYZER = "en.lucene"
# What a query asks back of each document: every field of the common
# result shape, and the deployment its vector was made with.
_ANSWERED_FIELDS = (
    KEY_FIELD,
    "rule_ids",
    "text",
    "manual_page",
    "impairment",
    "embedding_deployment",
    "content_hash",
)
RERANKER_SCORE = "@search.rerankerScore"
COUNT = "@odata.count"
# The most documents one page of a listing holds, and one delete carries.
PAGE_SIZE = 1000
# The service pages no further than this with `$skip`; the manual has about
# a hundred chunks.
MAX_LISTED = 100_000

# Characters the simple query syntax reads as operators. Each is escaped, so
# that nothing a caller writes is read as one: a query is a sentence about a
# fact, not an expression.
_OPERATORS = re.compile(r'([\\+|"()\'*~])')
# A dash is "not" only at the start of a word.
_LEADING_DASH = re.compile(r"(^|\s)-")


def plain_query_text(query: str) -> str:
    """The query as text the service searches for word by word, with no operator in it."""
    return _LEADING_DASH.sub(r"\1\\-", _OPERATORS.sub(r"\\\1", query))


def index_definition(name: str) -> dict[str, Any]:
    """The index of the `smart` chunk records, as the service is asked to create it."""

    def text(field: str, **more: Any) -> dict[str, Any]:
        return {"name": field, "type": "Edm.String", "searchable": False, **more}

    def words(field: str) -> dict[str, Any]:
        return text(field, searchable=True, analyzer=TEXT_ANALYZER)

    def ids(field: str) -> dict[str, Any]:
        return {
            "name": field,
            "type": "Collection(Edm.String)",
            "searchable": False,
            "filterable": True,
        }

    return {
        "name": name,
        "fields": [
            # The chunk table's key: letters, digits and dashes.
            text(KEY_FIELD, key=True, filterable=True, sortable=True),
            text("chunk_set", filterable=True),
            ids("rule_ids"),
            ids("reference_rule_ids"),
            text("section_id", filterable=True),
            words("section_title"),
            words("impairment"),
            {"name": "manual_page", "type": "Edm.Int32", "filterable": True},
            words("text"),
            words("context_line"),
            {
                "name": VECTOR_FIELD,
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "dimensions": EMBEDDING_DIMENSIONS,
                "vectorSearchProfile": VECTOR_PROFILE,
            },
            text("content_hash"),
            text("embedding_deployment", filterable=True),
            text(HASH_FIELD),
        ],
        "vectorSearch": {
            # AD-12: exact nearest neighbour, as in pgvector; no HNSW graph.
            "algorithms": [
                {
                    "name": VECTOR_ALGORITHM,
                    "kind": "exhaustiveKnn",
                    "exhaustiveKnnParameters": {"metric": "cosine"},
                }
            ],
            "profiles": [{"name": VECTOR_PROFILE, "algorithm": VECTOR_ALGORITHM}],
        },
        "semantic": {
            "configurations": [
                {
                    "name": SEMANTIC_CONFIGURATION,
                    "prioritizedFields": {
                        "titleField": {"fieldName": "impairment"},
                        "prioritizedContentFields": [
                            {"fieldName": "text"},
                            {"fieldName": "context_line"},
                        ],
                        "prioritizedKeywordsFields": [{"fieldName": "section_title"}],
                    },
                }
            ]
        },
    }


def document_body(document: IndexDocument) -> dict[str, Any]:
    """One document as it is uploaded: the stored record's fields, under the index's names."""
    return {
        KEY_FIELD: document.chunk_id,
        "chunk_set": document.chunk_set.value,
        "rule_ids": list(document.rule_ids),
        "reference_rule_ids": list(document.reference_rule_ids),
        "section_id": document.section_id,
        "section_title": document.section_title,
        "impairment": document.impairment,
        "manual_page": document.manual_page,
        "text": document.text,
        "context_line": document.context_line,
        VECTOR_FIELD: list(document.embedding),
        "content_hash": document.content_hash,
        "embedding_deployment": document.embedding_deployment,
        HASH_FIELD: document.document_hash,
    }


def build_search_http(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
    timeout_seconds: float | None = None,
) -> httpx2.AsyncClient:
    """The HTTP client for the search service. Tests pass a transport that stands in for it.

    Without `timeout_seconds` a call may take as long as the job's setting says.
    """
    if settings.search_service_endpoint is None:
        raise ValueError(
            "Set RETRIEVAL_SEARCH_SERVICE_ENDPOINT: the Azure AI Search service, or "
            "the local stand-in."
        )
    return httpx2.AsyncClient(
        base_url=settings.search_service_endpoint.rstrip("/"),
        timeout=settings.search_service_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds,
        transport=transport,
        # Never follow a redirect: the token must not leave the endpoint.
        follow_redirects=False,
        trust_env=False,
    )


def search_token_for(settings: Settings) -> EntraToken | None:
    """The token source for the search service in Azure; None for the local stand-in."""
    if not settings.search_service_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=SEARCH_SCOPE)


def _try_again(response: httpx2.Response) -> bool:
    """Whether the service could not answer just now: 429 or a 5xx."""
    return response.status_code == 429 or response.status_code >= 500


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    try:
        seconds = float(response.headers.get(RETRY_AFTER, ""))
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


class SearchIndex:
    """The one index of the `smart` chunks on the search service."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        index_name: str,
        api_version: str,
        max_retries: int = 3,
        retry_seconds: float = 1.0,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._index = f"/indexes/{index_name}"
        self._name = index_name
        self._parameters = {"api-version": api_version}
        self._max_retries = max_retries
        self._retry_seconds = retry_seconds
        self._token = token
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        try:
            # Fetched off the event loop, and kept until it is near its end.
            await self._token.refresh()
            return {"Authorization": f"Bearer {self._token.value()}"}
        except Exception as error:  # noqa: BLE001 - whatever kept the token away, the call was not made
            # security rule 31: the type only; the identity library's
            # message can hold an address or a tenant.
            raise SearchServiceUnavailable(
                f"search_token_{type(error).__qualname__}"
            ) from None

    async def _send(
        self,
        call: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        accepted: frozenset[int] = frozenset({200}),
    ) -> httpx2.Response:
        """Make one call, again when the service could not answer just now; its answer.

        Every call here may be repeated: a create, an upload and a delete
        each end in the same index when they are sent twice. A status
        outside `accepted` ends the call with its code.
        """
        with adapter_span(tracer, f"retrieval.search_service.{call}") as span:
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("retrieval.search_service.attempts", attempt)
                answered: httpx2.Response | None = None
                try:
                    response = await self._http.request(
                        method,
                        path,
                        params=self._parameters,
                        json=body,
                        headers=await self._headers(),
                    )
                except httpx2.HTTPError as error:
                    # security rule 31: the type only.
                    code = f"search_{call}_{type(error).__qualname__}"
                else:
                    if not _try_again(response):
                        break
                    answered = response
                    code = f"search_{call}_status_{response.status_code}"
                logger.warning(
                    "search service not answered: call=%s attempt=%d code=%s",
                    call,
                    attempt,
                    code,
                )
                if attempt > self._max_retries:
                    raise SearchServiceUnavailable(code)
                asked = _retry_after_seconds(answered) if answered is not None else None
                await self._sleep(
                    self._retry_seconds
                    if asked is None
                    else min(asked, MAX_RETRY_AFTER_SECONDS)
                )
            span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code not in accepted:
            raise SearchServiceUnavailable(
                f"search_{call}_status_{response.status_code}"
            )
        return response

    async def _answer(
        self, call: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """A POST's JSON answer, which must be an object."""
        response = await self._send(call, "POST", path, body)
        try:
            answer = response.json()
        except ValueError:
            raise SearchServiceUnavailable(f"search_{call}_not_json") from None
        if not isinstance(answer, dict):
            raise SearchServiceUnavailable(f"search_{call}_not_an_object")
        return answer

    # --- The job's side -------------------------------------------------------------

    async def ensure(self) -> bool:
        """Create the index if the service has none of its name; whether it was created.

        An index that is there is left as it is: its definition is not
        compared, and a changed one is a new index name.
        """
        found = await self._send(
            "read_index", "GET", self._index, accepted=frozenset({200, 404})
        )
        if found.status_code == 200:
            return False
        await self._send(
            "create_index",
            "PUT",
            self._index,
            index_definition(self._name),
            accepted=frozenset({200, 201, 204}),
        )
        logger.info("search index created: index=%s", self._name)
        return True

    async def held(self) -> IndexHoldings:
        """The service's count of its documents, and each one's hash by `chunk_id`."""
        hashes: dict[str, str] = {}
        count: int | None = None
        for skip in range(0, MAX_LISTED, PAGE_SIZE):
            answer = await self._answer(
                "list",
                f"{self._index}/docs/search",
                {
                    "search": "*",
                    "select": f"{KEY_FIELD},{HASH_FIELD}",
                    "orderby": f"{KEY_FIELD} asc",
                    "top": PAGE_SIZE,
                    "skip": skip,
                    "count": True,
                },
            )
            page = answer.get("value")
            if not isinstance(page, list):
                raise SearchServiceUnavailable("search_list_malformed")
            if count is None:
                counted = answer.get(COUNT)
                if not isinstance(counted, int) or isinstance(counted, bool):
                    raise SearchServiceUnavailable("search_list_no_count")
                count = counted
            for entry in page:
                chunk_id = entry.get(KEY_FIELD) if isinstance(entry, dict) else None
                if not isinstance(chunk_id, str) or not chunk_id:
                    raise SearchServiceUnavailable("search_list_malformed")
                held_hash = entry.get(HASH_FIELD)
                hashes[chunk_id] = held_hash if isinstance(held_hash, str) else ""
            if len(page) < PAGE_SIZE:
                break
        return IndexHoldings(count=count or 0, document_hashes=hashes)

    async def _change(self, call: str, actions: list[dict[str, Any]]) -> None:
        """Send one batch of document actions; every one of them must have been taken."""
        response = await self._send(
            call,
            "POST",
            f"{self._index}/docs/index",
            {"value": actions},
            # 207: the service took some of the batch and not all of it.
            accepted=frozenset({200, 207}),
        )
        try:
            results = response.json()["value"]
            refused = sorted(
                {
                    int(result["statusCode"])
                    for result in results
                    if not result["status"]
                }
            )
        except (ValueError, KeyError, TypeError):
            raise SearchServiceUnavailable(f"search_{call}_malformed") from None
        if refused or len(results) != len(actions):
            # Status codes only: the service's message could name a document.
            code = refused[0] if refused else "count"
            raise SearchServiceUnavailable(f"search_{call}_item_{code}")

    async def upload(self, documents: Sequence[IndexDocument]) -> None:
        """Store the documents, each whole, in place of the one of its `chunk_id`."""
        if not documents:
            return
        await self._change(
            "upload",
            [
                {"@search.action": "upload", **document_body(document)}
                for document in documents
            ],
        )
        # Ids and counts only (security rule 31).
        logger.info(
            "search index documents uploaded: index=%s count=%d",
            self._name,
            len(documents),
        )

    async def remove(self, chunk_ids: Sequence[str]) -> None:
        """Delete the documents of these ids; an id the index does not hold is no error."""
        for start in range(0, len(chunk_ids), PAGE_SIZE):
            await self._change(
                "delete",
                [
                    {"@search.action": "delete", KEY_FIELD: chunk_id}
                    for chunk_id in chunk_ids[start : start + PAGE_SIZE]
                ],
            )

    # --- A search's side ------------------------------------------------------------

    async def hybrid(
        self, query: str, vector: Sequence[float], top: int, candidates: int
    ) -> Sequence[RankedDocument]:
        """One hybrid query with the semantic ranker; the documents in the service's order.

        `candidates` is how many documents the vector side hands to the
        fusion (`k`); `top` how many documents are answered.

        The text and the vector go in one request: the service searches its
        text fields for the words and its vector field, exhaustively, for
        the vector, fuses the two lists and has the semantic ranker order
        the result. A request the ranker could not serve fails
        (`semanticErrorHandling`): an order without the ranker would be
        another row's.
        """
        answer = await self._answer(
            "query",
            f"{self._index}/docs/search",
            {
                "search": plain_query_text(query),
                "queryType": "semantic",
                "semanticConfiguration": SEMANTIC_CONFIGURATION,
                "semanticErrorHandling": "fail",
                "vectorQueries": [
                    {
                        "kind": "vector",
                        "vector": list(vector),
                        "fields": VECTOR_FIELD,
                        "k": candidates,
                        # AD-12: exact, whatever the index's algorithm.
                        "exhaustive": True,
                    }
                ],
                "select": ",".join(_ANSWERED_FIELDS),
                "top": top,
            },
        )
        try:
            return [_ranked(entry) for entry in answer["value"]]
        except (KeyError, TypeError, ValueError):
            # Also an answer without the ranker's score: no partial answer.
            raise SearchServiceUnavailable("search_query_malformed") from None


def _ranked(entry: dict[str, Any]) -> RankedDocument:
    """One document of a query's answer; an error if a field is missing or of another kind."""
    score = entry[RERANKER_SCORE]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("the reranker score is not a number")
    if not math.isfinite(score):
        raise ValueError("the reranker score is not finite")
    page = entry["manual_page"]
    if isinstance(page, bool) or not isinstance(page, int):
        raise TypeError("the manual page is not a whole number")
    texts = [entry[name] for name in (KEY_FIELD, "text", "impairment")]
    texts += [entry["embedding_deployment"], entry["content_hash"]]
    rule_ids = entry["rule_ids"]
    if not all(isinstance(value, str) for value in (*texts, *rule_ids)):
        raise TypeError("a text field is not text")
    chunk_id, text, impairment, deployment, content_hash = texts
    return RankedDocument(
        chunk_id=chunk_id,
        rule_ids=tuple(rule_ids),
        text=text,
        manual_page=page,
        impairment=impairment,
        reranker_score=float(score),
        embedding_deployment=deployment,
        content_hash=content_hash,
    )
