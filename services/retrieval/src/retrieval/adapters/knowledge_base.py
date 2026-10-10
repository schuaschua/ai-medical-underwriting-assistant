"""Azure AI Search's agentic retrieval: the knowledge base of row `r6`, over REST (spine AD-11).

A knowledge source names the index row `r5` already uses, and a knowledge
base names that source and the chat deployment the search service plans
its queries with. The ingestion job creates both where they are missing; a
search with `r6` sends one retrieve request. Nothing is chunked or embedded
again: the knowledge base reads the same documents and the same vectors as
row `r5`.

These calls alone use the preview REST version (`2026-08-01-preview`, a
setting): the stable one has no model query planning.

REST, like the index's calls, and not the SDK the spine pins for this row
(`azure-search-documents` 12.1.0b2). The package installs, and its models
are where the request and answer shapes here were read from. Its async
client does not fit the patterns the other adapters keep: it sends through
azure-core's own transport (`aiohttp`, which nothing here depends on), so
neither the `httpx2` transport a test stands a service in with nor the one
client that follows no redirect can be given to it; its bearer policy
refuses the plain-HTTP loopback stand-in; and its retries and HTTP logging
are its own, where this service wants a retry that can be seen and a log
that holds codes only (security rule 31). So the package is not a
dependency, and the three calls are written out on `SearchServiceClient`.

The model is reached by the search service, not from here, with the
service's own identity: no key and no identity is named in a definition.

Nothing the service answers with is logged except statuses, codes and
counts: never a query, a query the service planned, or a document's text.
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

from retrieval.adapters.db import EntraToken
from retrieval.adapters.search_index import (
    ANSWERED_FIELDS,
    KEY_FIELD,
    SEMANTIC_CONFIGURATION,
    ModelConnection,
    SearchServiceClient,
)
from retrieval.domain.entities import (
    KnowledgeBaseReport,
    Retrieval,
    RetrievedReference,
)
from retrieval.domain.ports import (
    KnowledgeBaseMissing,
    KnowledgeBaseUnusable,
    SearchServiceUnavailable,
)
from retrieval.settings import Settings

logger = logging.getLogger(__name__)

SEARCH_INDEX_KIND = "searchIndex"
# The service returns what it found and writes no answer of its own.
EXTRACTIVE_DATA = "extractiveData"
# The records of a retrieve's activity that are counted: a query the
# service ran on the index, and a call of its planning model.
_SUBQUERY = "searchIndex"
_PLANNING = "modelQueryPlanning"


def _connection(
    settings: Settings, deployment: str | None, model_name: str | None
) -> ModelConnection | None:
    if settings.model_endpoint is None or deployment is None:
        return None
    return ModelConnection(
        resource_uri=settings.model_endpoint.rstrip("/"),
        deployment=deployment,
        # Unset: the deployment is named after its model.
        model_name=model_name or deployment,
    )


def planning_model(settings: Settings) -> ModelConnection | None:
    """AD-16: the shared chat deployment, as the knowledge base is told to reach it; None when none is set."""
    return _connection(
        settings, settings.chat_deployment, settings.search_agentic_chat_model_name
    )


def query_vectorizer(settings: Settings) -> ModelConnection | None:
    """AD-16: the one embedding deployment, as the index is told to reach it; None when none is set.

    The deployment the chunks' vectors were made with: a query the search
    service embeds itself is then comparable with them.
    """
    return _connection(
        settings,
        settings.embedding_deployment,
        settings.search_agentic_embedding_model_name,
    )


def knowledge_source_definition(name: str, index_name: str) -> dict[str, Any]:
    """The knowledge source over the index of the `smart` chunks, as the service is asked to create it."""
    return {
        "name": name,
        "kind": SEARCH_INDEX_KIND,
        "description": "The underwriting manual's rules, one document per rule.",
        "searchIndexParameters": {
            "searchIndexName": index_name,
            "semanticConfigurationName": SEMANTIC_CONFIGURATION,
            # What a reference carries back: the fields of the common
            # result shape, and what says whether the document is stale.
            "sourceDataFields": [{"name": field} for field in ANSWERED_FIELDS],
        },
    }


def knowledge_base_definition(
    name: str, source_name: str, model: ModelConnection, reasoning_effort: str
) -> dict[str, Any]:
    """The knowledge base over that one source, as the service is asked to create it.

    One source, the search index, and no other; the chat deployment as the
    planning model; and no synthesised answer.
    """
    return {
        "name": name,
        "description": "Agentic retrieval over the underwriting manual's rules.",
        "knowledgeSources": [{"name": source_name}],
        "models": [
            {"kind": "azureOpenAI", "azureOpenAIParameters": model.parameters()}
        ],
        "retrievalReasoningEffort": {"kind": reasoning_effort},
        "outputMode": EXTRACTIVE_DATA,
    }


def retrieve_request(
    query: str, top: int, source_name: str, reasoning_effort: str
) -> dict[str, Any]:
    """One retrieve request: the query as it was asked, and references only."""
    return {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        "retrievalReasoningEffort": {"kind": reasoning_effort},
        # No answer synthesis: the documents the service found, as they are.
        "outputMode": EXTRACTIVE_DATA,
        "maxOutputDocuments": top,
        # For the counts of the log: how many queries the service ran and
        # what its planning cost.
        "includeActivity": True,
        "knowledgeSourceParams": [
            {
                "knowledgeSourceName": source_name,
                "kind": SEARCH_INDEX_KIND,
                "includeReferences": True,
                "includeReferenceSourceData": True,
                # The one source there is: nothing to select among.
                "alwaysQuerySource": True,
                # No partial answer: a source that failed fails the request.
                "failOnError": True,
            }
        ],
    }


class KnowledgeBase(SearchServiceClient):
    """The knowledge source and the knowledge base of row `r6` on the search service."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        index_name: str,
        source_name: str,
        base_name: str,
        api_version: str,
        reasoning_effort: str,
        model: ModelConnection | None = None,
        max_retries: int = 3,
        retry_seconds: float = 1.0,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(
            http,
            api_version=api_version,
            max_retries=max_retries,
            retry_seconds=retry_seconds,
            token=token,
            sleep=sleep,
        )
        self._index_name = index_name
        self._source_name = source_name
        self._base_name = base_name
        self._source = f"/knowledgesources('{source_name}')"
        self._base = f"/knowledgebases('{base_name}')"
        self._reasoning_effort = reasoning_effort
        # Only the job, which creates the knowledge base, names its model.
        self._model = model

    # --- The job's side -------------------------------------------------------------

    async def _ensure(
        self,
        what: str,
        path: str,
        definition: dict[str, Any],
        read: Callable[[dict[str, Any]], object],
    ) -> bool:
        """Create one of the two if the service has none of its name; whether it was created.

        One that is there must read what the one to create would (`read`
        says what of a definition counts): an older one, left over from
        another index name or another deployment, would be asked by every
        search with nobody the wiser.
        """
        found = await self._send(
            f"read_{what}", "GET", path, accepted=frozenset({200, 404})
        )
        if found.status_code == 200:
            try:
                same = read(self._object(f"read_{what}", found)) == read(definition)
            except (KeyError, TypeError, IndexError, AttributeError):
                same = False
            if not same:
                raise KnowledgeBaseUnusable(f"search_{what}_differs")
            return False
        await self._send(
            f"create_{what}",
            "PUT",
            path,
            definition,
            accepted=frozenset({200, 201, 204}),
            # As the service's SDK sends a create: the definition comes back.
            headers={"Prefer": "return=representation"},
        )
        return True

    async def _check_the_index(self) -> None:
        """Refuse an index that names no vectorizer for its vector field.

        The service embeds the queries it plans only through a vectorizer:
        over an index without one, row `r6` would search by text alone and
        still call itself agentic retrieval over the same vectors.
        """
        found = await self._send("read_index", "GET", f"/indexes/{self._index_name}")
        definition = self._object("read_index", found)
        try:
            vector_search = definition["vectorSearch"]
            vectorizers = {entry["name"] for entry in vector_search["vectorizers"]}
            named = {entry.get("vectorizer") for entry in vector_search["profiles"]}
        except (KeyError, TypeError, AttributeError):
            vectorizers, named = set(), set()
        if not vectorizers & named:
            raise KnowledgeBaseUnusable("search_index_without_vectorizer")

    async def ensure(self) -> KnowledgeBaseReport:
        """Create the knowledge source, then the knowledge base, where the service has none of that name.

        First the index is looked at: it must name a vectorizer. One of
        the two that is there is compared with what would be created (the
        source's index; the base's source and its planning model) and left
        as it is; a difference is `KnowledgeBaseUnusable`, and mended by a
        new name or by deleting the old one on the service.
        """
        if self._model is None:
            raise ValueError("the knowledge base needs its planning model to be made")
        await self._check_the_index()
        source_created = await self._ensure(
            "knowledge_source",
            self._source,
            knowledge_source_definition(self._source_name, self._index_name),
            lambda source: (
                source["kind"],
                source["searchIndexParameters"]["searchIndexName"],
            ),
        )
        base_created = await self._ensure(
            "knowledge_base",
            self._base,
            knowledge_base_definition(
                self._base_name, self._source_name, self._model, self._reasoning_effort
            ),
            lambda base: (
                [source["name"] for source in base["knowledgeSources"]],
                [_planning_model_of(model) for model in base["models"]],
            ),
        )
        return KnowledgeBaseReport(source_created, base_created)

    # --- A search's side ------------------------------------------------------------

    async def retrieve(self, query: str, top: int) -> Retrieval:
        """One retrieve request; the references the service returns, in its order.

        The service's model plans queries of its own from the query, the
        service runs them on the index and reranks what they find. Only
        the references are read: a `response` the service may add is not.
        The request is sent once and never again, whatever became of it:
        the planning is paid for each time, and the search has no time for
        a second. A service that holds no such knowledge base is
        `KnowledgeBaseMissing`.
        """
        response = await self._send(
            "retrieve",
            "POST",
            f"{self._base}/retrieve",
            retrieve_request(query, top, self._source_name, self._reasoning_effort),
            accepted=frozenset({200, 404}),
            once=True,
        )
        if response.status_code == 404:
            raise KnowledgeBaseMissing
        answer = self._object("retrieve", response)
        try:
            references = answer["references"]
            if not isinstance(references, list):
                raise TypeError("the references are not a list")
            found = tuple(_reference(entry) for entry in references)
        except (KeyError, TypeError, ValueError):
            # No list of references at all, a reference of another kind of
            # source, or one that is not whole: no partial answer. Only an
            # empty list says that nothing was found.
            raise SearchServiceUnavailable("search_retrieve_malformed") from None
        subqueries, input_tokens, output_tokens = _activity(answer.get("activity"))
        # Counts only (security rule 31): what the planning cost.
        logger.info(
            "knowledge base retrieve: base=%s references=%d subqueries=%d "
            "planning_input_tokens=%d planning_output_tokens=%d",
            self._base_name,
            len(found),
            subqueries,
            input_tokens,
            output_tokens,
        )
        return Retrieval(found, subqueries, input_tokens, output_tokens)


def _planning_model_of(model: dict[str, Any]) -> tuple[str, str, str]:
    """What of a knowledge base's model counts: where it is, and the deployment."""
    parameters = model["azureOpenAIParameters"]
    return (
        model["kind"],
        str(parameters["resourceUri"]).rstrip("/"),
        parameters["deploymentId"],
    )


def _reference(entry: dict[str, Any]) -> RetrievedReference:
    """One reference of a retrieve's answer; an error if it is not a document of the index, whole."""
    if entry["type"] != SEARCH_INDEX_KIND:
        raise ValueError("a reference of another source than the index")
    data = entry["sourceData"]
    # The document's own key; a reference that names another key is of no
    # document this service can tell.
    chunk_id = data[KEY_FIELD]
    if entry.get("docKey", chunk_id) != chunk_id:
        raise ValueError("the reference's key is not its document's")
    score = entry.get("rerankerScore")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise TypeError("the reranker score is not a number")
        if not math.isfinite(score):
            raise ValueError("the reranker score is not finite")
    page = data["manual_page"]
    if isinstance(page, bool) or not isinstance(page, int):
        raise TypeError("the manual page is not a whole number")
    texts = [chunk_id, data["text"], data["impairment"]]
    texts += [data["embedding_deployment"], data["content_hash"]]
    rule_ids = data["rule_ids"]
    if not isinstance(rule_ids, list):
        # A text would be read as a list of its characters.
        raise TypeError("the rule ids are not a list")
    if not all(isinstance(value, str) for value in (*texts, *rule_ids)):
        raise TypeError("a text field is not text")
    chunk_id, text, impairment, deployment, content_hash = texts
    return RetrievedReference(
        chunk_id=chunk_id,
        rule_ids=tuple(rule_ids),
        text=text,
        manual_page=page,
        impairment=impairment,
        reranker_score=None if score is None else float(score),
        embedding_deployment=deployment,
        content_hash=content_hash,
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _activity(activity: object) -> tuple[int, int, int]:
    """How many queries the service ran on the index, and its planning model's input and output tokens.

    For the log alone: whatever of it cannot be read counts as nothing.
    """
    records = [
        record
        for record in (activity if isinstance(activity, list) else [])
        if isinstance(record, dict)
    ]
    planning = [record for record in records if record.get("type") == _PLANNING]
    return (
        sum(1 for record in records if record.get("type") == _SUBQUERY),
        sum(_count(record.get("inputTokens")) for record in planning),
        sum(_count(record.get("outputTokens")) for record in planning),
    )
