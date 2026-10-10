"""A local stand-in for Azure AI Search (stories 3.3 and 3.8, retrieval rows `r5` and `r6`).

The Azure environment is down while the stories are built, so `retrieval`'s
index load and its searches with rows `r5` and `r6` are proven against
this: an HTTP app with the REST routes `retrieval` calls, in the service's
shapes. It keeps its indexes, their documents, and its knowledge sources and
knowledge bases in memory, and loses them when it stops.

- `GET` and `PUT /indexes/{name}`: read an index definition, create one.
- `POST /indexes/{name}/docs/index`: upload, merge and delete documents.
- `POST /indexes/{name}/docs/search`: list documents (`search: "*"`), or
  answer a hybrid query.
- `GET` and `PUT /knowledgesources('{name}')` and `/knowledgebases('{name}')`:
  read one, create one. Preview REST versions only, as on the service.
- `POST /knowledgebases('{name}')/retrieve`: agentic retrieval (row `r6`).

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and `retrieval` refuses a plain-HTTP search endpoint
that is not on loopback, so it cannot stand in for the service in Azure.

It is not a search engine. A hybrid query is answered from two lists: the
documents nearest the query's vector by cosine, every document compared, and
the documents that hold words of the query, most words first. The lists are
fused by rank. Then, where the query asks for the semantic ranker, each
document gets a "reranker score" from 0 to 4, made of the share of the
query's words it holds and of its cosine, and the answer is ordered by it.
The real ranker is a language model: what it does to the same queries, and
its scores, are a check of the final Azure test session.

Nor does it plan. The service has a model turn the query of a retrieve
request into queries of its own. The stand-in's "plan" is the query whole,
and each of its parts between commas, colons and semicolons, at most four
queries in all. Each is run as a hybrid query with the semantic ranker over
the knowledge source's index, with a vector from the stand-in's own
embedding where the index names a vectorizer (text alone where it names
none, as on the service); a document is ranked by the best score any of the
queries gave it. It answers references and an activity log with made-up
token counts, and never writes an answer. What a real planning model makes
of the same queries is a check of the final Azure test session too.

Run it: `uv run python -m synthdata.search_standin` (see README, 'Run locally').
"""

import argparse
import asyncio
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from synthdata.foundry_standin import embed_text

DEFAULT_PORT = 5103
# What the stand-in answers a read of an index it does not hold with; the
# local tools ask for one to see that it is the stand-in that listens.
NO_SUCH_INDEX = "No index of that name was found."
# A header on every answer, in every mode: how the local tools tell the
# stand-in from anything else on its port, also when it is told to fail.
STAND_IN_HEADER = "x-synthdata-stand-in"
STAND_IN_NAME = "search"
VECTOR_TYPE = "Collection(Edm.Single)"
RERANKER_MAX_SCORE = 4.0
# Reciprocal rank fusion, as the service fuses a text list and a vector list.
_RRF_K = 60
# How many fused documents the semantic ranker looks at.
_RERANKED = 50
_DEFAULT_TOP = 50
_WORD = re.compile(r"[a-z0-9]+")
_UPLOADS = frozenset({"upload", "mergeOrUpload", "merge"})
# Agentic retrieval (story 3.8): what a knowledge source over an index is,
# the output a knowledge base may be asked for that this stand-in gives,
# the efforts that plan with a model, and how many queries a plan holds.
SEARCH_INDEX_KIND = "searchIndex"
EXTRACTIVE_DATA = "extractiveData"
_PLANNING_EFFORTS = frozenset({"low", "medium"})
_MAX_SUBQUERIES = 4
_PARTS = re.compile(r"[,:;]")
# How many vector candidates each planned query hands its fusion.
_SUBQUERY_VECTOR_CANDIDATES = 50


class Mode(StrEnum):
    """What the stand-in does with a call; tests switch it to see failures."""

    OK = "ok"
    # Every call is answered 503.
    UNAVAILABLE = "unavailable"
    # A query, and a retrieve request, is answered only after `delay_seconds`.
    SLOW = "slow"


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _cosine(one: list[float], other: list[float]) -> float:
    dot = sum(a * b for a, b in zip(one, other, strict=True))
    lengths = math.sqrt(sum(a * a for a in one) * sum(b * b for b in other))
    return dot / lengths if lengths else 0.0


@dataclass
class Index:
    """One index: its definition as it was created, and its documents by key."""

    definition: dict[str, Any]
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def fields(self) -> dict[str, dict[str, Any]]:
        return {entry["name"]: entry for entry in self.definition["fields"]}

    @property
    def key(self) -> str:
        return next(name for name, entry in self.fields.items() if entry.get("key"))

    @property
    def vector_fields(self) -> dict[str, int]:
        """The vector fields and their dimensions."""
        return {
            name: int(entry["dimensions"])
            for name, entry in self.fields.items()
            if entry.get("type") == VECTOR_TYPE and "dimensions" in entry
        }

    @property
    def searchable(self) -> list[str]:
        """The text fields a query's words are looked for in."""
        return [
            name
            for name, entry in self.fields.items()
            if entry.get("searchable") and entry.get("type") == "Edm.String"
        ]

    @property
    def semantic_configurations(self) -> set[str]:
        configurations = self.definition.get("semantic", {}).get("configurations", [])
        return {entry["name"] for entry in configurations}

    @property
    def vectorized_fields(self) -> list[str]:
        """The vector fields whose profile names a vectorizer of the index: the service can embed a query for them."""
        vector_search = self.definition.get("vectorSearch", {})
        vectorizers = {
            entry.get("name") for entry in vector_search.get("vectorizers", [])
        }
        with_one = {
            entry.get("name")
            for entry in vector_search.get("profiles", [])
            if entry.get("vectorizer") in vectorizers
        }
        return [
            name
            for name in self.vector_fields
            if self.fields[name].get("vectorSearchProfile") in with_one
        ]


def _invalid_definition(name: str, body: dict[str, Any]) -> str | None:
    """Why the service would refuse an index definition; None if it would take it."""
    fields = body.get("fields")
    if body.get("name") != name or not isinstance(fields, list) or not fields:
        return "The index needs its name and its fields."
    if not all(isinstance(entry, dict) and entry.get("name") for entry in fields):
        return "Every field needs a name."
    if sum(1 for entry in fields if entry.get("key")) != 1:
        return "The index needs exactly one key field."
    vector_search = body.get("vectorSearch", {})
    profiles = {
        entry.get("name")
        for entry in vector_search.get("profiles", [])
        if isinstance(entry, dict)
    }
    vectorizers = {
        entry.get("name")
        for entry in vector_search.get("vectorizers", [])
        if isinstance(entry, dict)
    }
    for entry in vector_search.get("profiles", []):
        if isinstance(entry, dict) and entry.get("vectorizer") not in {
            None,
            *vectorizers,
        }:
            return "A profile's vectorizer must be a vectorizer of the index."
    for entry in vector_search.get("vectorizers", []):
        parameters = entry.get("azureOpenAIParameters") or {}
        if entry.get("kind") != "azureOpenAI" or not all(
            parameters.get(name) for name in ("resourceUri", "deploymentId")
        ):
            return "A vectorizer needs its resource and its deployment."
    for entry in fields:
        if entry.get("type") != VECTOR_TYPE:
            continue
        if not isinstance(entry.get("dimensions"), int):
            return "A vector field needs its dimensions."
        if entry.get("vectorSearchProfile") not in profiles:
            return "A vector field needs a vector search profile of the index."
    return None


@dataclass
class SearchStandIn:
    """The stand-in's state and its HTTP app. Tests look at what it holds and was asked."""

    mode: Mode = Mode.OK
    # How long a query waits in the `slow` mode.
    delay_seconds: float = 30.0
    indexes: dict[str, Index] = field(default_factory=dict)
    # What it was asked, for tests: how many documents were uploaded and
    # deleted in all, and every hybrid query's body, in order.
    uploaded: int = 0
    deleted: int = 0
    queries: list[dict[str, Any]] = field(default_factory=list)
    # Agentic retrieval: the knowledge sources and knowledge bases by name,
    # as they were created, how often one was created, and every retrieve
    # request's body with the queries the stand-in planned for it.
    knowledge_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge_bases: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge_created: int = 0
    retrievals: list[dict[str, Any]] = field(default_factory=list)
    planned: list[list[str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def documents(self, name: str) -> dict[str, dict[str, Any]]:
        """The documents of one index, by key; empty when there is no such index."""
        index = self.indexes.get(name)
        return dict(index.documents) if index is not None else {}

    # --- Documents --------------------------------------------------------------------

    def change(self, index: Index, body: dict[str, Any]) -> Response:
        """Apply one batch of document actions, all of it or none of it."""
        actions = body.get("value")
        if not isinstance(actions, list) or not actions:
            return _error(400, "InvalidRequest", "Send the actions as value.")
        key, fields, vectors = index.key, index.fields, index.vector_fields
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get(key), str):
                return _error(400, "InvalidRequest", "Every action needs the key.")
            kind = action.get("@search.action", "upload")
            if kind != "delete" and kind not in _UPLOADS:
                return _error(400, "InvalidRequest", "No such action.")
            document = {
                name: value
                for name, value in action.items()
                if name != "@search.action"
            }
            if set(document) - set(fields):
                return _error(400, "InvalidRequest", "A field is not in the index.")
            for name, dimensions in vectors.items():
                vector = document.get(name)
                if vector is not None and (
                    not isinstance(vector, list) or len(vector) != dimensions
                ):
                    return _error(
                        400, "InvalidRequest", "A vector has the wrong dimensions."
                    )
        results: list[dict[str, Any]] = []
        with self._lock:
            for action in actions:
                kind = action.get("@search.action", "upload")
                chunk_key = action[key]
                document = {
                    name: value
                    for name, value in action.items()
                    if name != "@search.action"
                }
                status = 200
                if kind == "delete":
                    index.documents.pop(chunk_key, None)
                    self.deleted += 1
                else:
                    before = index.documents.get(chunk_key)
                    status = 200 if before is not None else 201
                    index.documents[chunk_key] = (
                        document if kind == "upload" else {**(before or {}), **document}
                    )
                    self.uploaded += 1
                results.append(
                    {
                        "key": chunk_key,
                        "status": True,
                        "errorMessage": None,
                        "statusCode": status,
                    }
                )
        return JSONResponse({"value": results})

    # --- Queries ----------------------------------------------------------------------

    def _selected(
        self, index: Index, document: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        select = body.get("select")
        names = (
            [name.strip() for name in select.split(",")]
            if isinstance(select, str) and select.strip() not in ("", "*")
            else list(index.fields)
        )
        return {name: document.get(name) for name in names}

    def listing(self, index: Index, body: dict[str, Any]) -> Response:
        """Every document, in the order asked for, a page at a time."""
        documents = list(index.documents.values())
        order = str(body.get("orderby") or f"{index.key} asc").split()
        if order[0] not in index.fields or not index.fields[order[0]].get("sortable"):
            return _error(400, "InvalidRequest", "That field cannot be sorted by.")
        documents.sort(
            key=lambda document: document.get(order[0]) or "",
            reverse=order[1:] == ["desc"],
        )
        skip, top = int(body.get("skip", 0)), int(body.get("top", _DEFAULT_TOP))
        answer: dict[str, Any] = {
            "value": [
                {"@search.score": 1.0, **self._selected(index, document, body)}
                for document in documents[skip : skip + top]
            ]
        }
        if body.get("count"):
            answer["@odata.count"] = len(documents)
        return JSONResponse(answer)

    def query(self, index: Index, body: dict[str, Any]) -> Response:
        """A text query, a vector query, or both fused; reranked where the query asks."""
        answer = self.ranked(index, body)
        return answer if isinstance(answer, Response) else JSONResponse(answer)

    def ranked(self, index: Index, body: dict[str, Any]) -> dict[str, Any] | Response:
        """The answer to a query as the service's JSON, or the refusal of a query it would not take."""
        semantic = body.get("queryType") == "semantic"
        if semantic and (
            body.get("semanticConfiguration") not in index.semantic_configurations
        ):
            return _error(
                400, "InvalidRequest", "No such semantic configuration in the index."
            )
        # An escaped character is the character itself.
        text = re.sub(r"\\(.)", r"\1", str(body.get("search") or ""))
        asked = _words(text)
        documents = index.documents
        held = {
            key: _words(
                " ".join(str(document.get(name) or "") for name in index.searchable)
            )
            for key, document in documents.items()
        }
        by_words = sorted(
            (key for key in documents if asked & held[key]),
            key=lambda key: (-len(asked & held[key]), key),
        )
        lists = [by_words]
        cosines: dict[str, float] = {}
        for vector_query in body.get("vectorQueries") or []:
            name, vector = vector_query.get("fields"), vector_query.get("vector")
            dimensions = index.vector_fields.get(name)
            if dimensions is None or not isinstance(vector, list):
                return _error(400, "InvalidRequest", "No such vector field.")
            if len(vector) != dimensions:
                return _error(
                    400, "InvalidRequest", "The vector has the wrong dimensions."
                )
            # Every document is compared: exhaustive, whatever the query says.
            cosines = {
                key: _cosine(vector, document[name])
                for key, document in documents.items()
                if document.get(name)
            }
            nearest = sorted(cosines, key=lambda key: (-cosines[key], key))
            lists.append(nearest[: int(vector_query.get("k", _DEFAULT_TOP))])
        fused: dict[str, float] = {}
        for ranked in lists:
            for rank, key in enumerate(ranked, start=1):
                fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        order = sorted(fused, key=lambda key: (-fused[key], key))
        reranker: dict[str, float] = {}
        if semantic:
            order = order[:_RERANKED]
            for key in order:
                share = len(asked & held[key]) / len(asked) if asked else 0.0
                near = max(cosines.get(key, 0.0), 0.0)
                reranker[key] = round(RERANKER_MAX_SCORE * (share + near) / 2, 6)
            # A stable sort: documents the ranker scores alike keep the fused order.
            order.sort(key=lambda key: -reranker[key])
        top = int(body.get("top", _DEFAULT_TOP))
        value: list[dict[str, Any]] = []
        for key in order[int(body.get("skip", 0)) :][:top]:
            entry: dict[str, Any] = {"@search.score": round(fused[key], 8)}
            if semantic:
                entry["@search.rerankerScore"] = reranker[key]
            value.append({**entry, **self._selected(index, documents[key], body)})
        answer: dict[str, Any] = {"value": value}
        if body.get("count"):
            answer["@odata.count"] = len(order)
        return answer

    # --- Agentic retrieval (story 3.8) --------------------------------------------------

    def invalid_knowledge_source(self, name: str, body: dict[str, Any]) -> str | None:
        """Why the service would refuse a knowledge source; None if it would take it."""
        parameters = body.get("searchIndexParameters")
        if body.get("name") != name or body.get("kind") != SEARCH_INDEX_KIND:
            return "The knowledge source needs its name and the kind searchIndex."
        if not isinstance(parameters, dict):
            return "A searchIndex knowledge source needs its searchIndexParameters."
        index = self.indexes.get(str(parameters.get("searchIndexName")))
        if index is None:
            return "The knowledge source names no index of this service."
        if not index.semantic_configurations:
            return "The index of a knowledge source needs a semantic configuration."
        configuration = parameters.get("semanticConfigurationName")
        if configuration is not None and (
            configuration not in index.semantic_configurations
        ):
            return "No such semantic configuration in the index."
        fields = parameters.get("sourceDataFields") or []
        if not all(
            isinstance(entry, dict) and entry.get("name") in index.fields
            for entry in fields
        ):
            return "A source data field is not in the index."
        return None

    def invalid_knowledge_base(self, name: str, body: dict[str, Any]) -> str | None:
        """Why the service would refuse a knowledge base; None if it would take it."""
        sources = body.get("knowledgeSources")
        if body.get("name") != name or not isinstance(sources, list) or not sources:
            return "The knowledge base needs its name and its knowledge sources."
        if not all(
            isinstance(entry, dict) and entry.get("name") in self.knowledge_sources
            for entry in sources
        ):
            return "The knowledge base names no knowledge source of this service."
        effort = (body.get("retrievalReasoningEffort") or {}).get("kind", "minimal")
        models = body.get("models") or []
        if effort in _PLANNING_EFFORTS and not any(
            isinstance(model, dict)
            and model.get("kind") == "azureOpenAI"
            and all(
                (model.get("azureOpenAIParameters") or {}).get(name)
                for name in ("resourceUri", "deploymentId", "modelName")
            )
            for model in models
        ):
            return "Query planning needs a model: its resource, deployment and name."
        if body.get("outputMode", EXTRACTIVE_DATA) != EXTRACTIVE_DATA:
            return "This stand-in synthesises no answer."
        return None

    def retrieve(self, name: str, body: dict[str, Any]) -> Response:
        """Agentic retrieval: plan queries, run each on the index, answer the documents found as references."""
        base = self.knowledge_bases.get(name)
        if base is None:
            return _error(404, "ResourceNotFound", "No knowledge base of that name.")
        try:
            texts = [
                part["text"]
                for message in body["messages"]
                if message.get("role") == "user"
                for part in message["content"]
                if part.get("type") == "text"
            ]
        except (KeyError, TypeError, AttributeError):
            texts = []
        if not texts or not all(isinstance(text, str) for text in texts):
            return _error(400, "InvalidRequest", "Send the query as a user message.")
        if body.get("outputMode", EXTRACTIVE_DATA) != EXTRACTIVE_DATA:
            return _error(400, "InvalidRequest", "This stand-in synthesises no answer.")
        effort = (
            body.get("retrievalReasoningEffort")
            or base.get("retrievalReasoningEffort")
            or {}
        ).get("kind", "minimal")
        source_name = base["knowledgeSources"][0]["name"]
        parameters = self.knowledge_sources[source_name]["searchIndexParameters"]
        index = self.indexes.get(parameters["searchIndexName"])
        if index is None:
            return _error(
                400, "InvalidRequest", "The knowledge source's index is gone."
            )
        asked = " ".join(texts)
        # The "plan": with an effort that plans, the query and its parts.
        parts = [part.strip() for part in _PARTS.split(asked) if part.strip()]
        planned = list(dict.fromkeys([asked, *(parts if len(parts) > 1 else [])]))
        subqueries = (
            planned[:_MAX_SUBQUERIES] if effort in _PLANNING_EFFORTS else [asked]
        )
        configuration = parameters.get("semanticConfigurationName") or min(
            index.semantic_configurations
        )
        best: dict[str, float] = {}
        counts: list[int] = []
        for subquery in subqueries:
            found = self.ranked(
                index,
                {
                    "search": subquery,
                    "queryType": "semantic",
                    "semanticConfiguration": configuration,
                    # The service embeds the query itself where the index
                    # names a vectorizer, and searches text alone where not.
                    "vectorQueries": [
                        {
                            "fields": name,
                            "vector": embed_text(subquery, index.vector_fields[name]),
                            "k": _SUBQUERY_VECTOR_CANDIDATES,
                        }
                        for name in index.vectorized_fields
                    ],
                    "select": index.key,
                    "top": _RERANKED,
                },
            )
            if isinstance(found, Response):
                return found
            counts.append(len(found["value"]))
            for entry in found["value"]:
                key, score = entry[index.key], entry["@search.rerankerScore"]
                best[key] = max(best.get(key, 0.0), score)
        order = sorted(best, key=lambda key: (-best[key], key))
        wanted = [entry["name"] for entry in parameters.get("sourceDataFields") or []]
        with_data = any(
            params.get("includeReferenceSourceData")
            for params in body.get("knowledgeSourceParams") or []
            if params.get("knowledgeSourceName") == source_name
        )
        top = int(body.get("maxOutputDocuments", _DEFAULT_TOP))
        references: list[dict[str, Any]] = []
        for place, key in enumerate(order[:top]):
            reference: dict[str, Any] = {
                "type": SEARCH_INDEX_KIND,
                "id": str(place),
                "activitySource": 1,
                "docKey": key,
                "rerankerScore": best[key],
            }
            if with_data:
                reference["sourceData"] = {
                    field_name: index.documents[key].get(field_name)
                    for field_name in wanted
                }
            references.append(reference)
        answer: dict[str, Any] = {"response": [], "references": references}
        if body.get("includeActivity"):
            answer["activity"] = [
                {
                    "type": "modelQueryPlanning",
                    "id": 0,
                    "inputTokens": 100 + len(asked.split()),
                    "outputTokens": sum(len(query.split()) for query in subqueries),
                    "elapsedMs": 1,
                },
                *(
                    {
                        "type": SEARCH_INDEX_KIND,
                        "id": place,
                        "knowledgeSourceName": source_name,
                        "count": count,
                        "elapsedMs": 1,
                        "searchIndexArguments": {"search": subquery},
                    }
                    for place, (subquery, count) in enumerate(
                        zip(subqueries, counts, strict=True), start=1
                    )
                ),
            ]
        with self._lock:
            self.retrievals.append(body)
            self.planned.append(subqueries)
        return JSONResponse(answer)

    # --- The app ----------------------------------------------------------------------

    def _refused(self, request: Request) -> Response | None:
        """What every route answers before it looks at the call, if anything."""
        if self.mode is Mode.UNAVAILABLE:
            return _error(503, "ServiceUnavailable", "The service is unavailable.")
        if not request.query_params.get("api-version"):
            return _error(400, "MissingApiVersion", "Name the api-version.")
        return None

    def _knowledge_refused(self, request: Request) -> Response | None:
        """As `_refused`, for the routes of agentic retrieval: those are on preview versions only."""
        refused = self._refused(request)
        if refused is not None:
            return refused
        if not request.query_params["api-version"].endswith("-preview"):
            return _error(
                400,
                "InvalidApiVersion",
                "Knowledge bases with query planning need a preview api-version.",
            )
        return None

    def _knowledge_routes(self, app: FastAPI) -> None:
        """The routes of a knowledge source and of a knowledge base: read, create, retrieve."""

        def routes_of(
            collection: str,
            held: dict[str, dict[str, Any]],
            invalid_of: Callable[[str, dict[str, Any]], str | None],
        ) -> None:
            path = f"/{collection}('{{name}}')"

            @app.get(path)
            def read(name: str, request: Request) -> Response:
                refused = self._knowledge_refused(request)
                if refused is not None:
                    return refused
                if name not in held:
                    return _error(404, "ResourceNotFound", "Nothing of that name.")
                return JSONResponse(held[name])

            @app.put(path)
            def create(name: str, request: Request, body: dict[str, Any]) -> Response:
                refused = self._knowledge_refused(request)
                if refused is not None:
                    return refused
                invalid = invalid_of(name, body)
                if invalid is not None:
                    return _error(400, "InvalidRequest", invalid)
                with self._lock:
                    existed = name in held
                    held[name] = body
                    self.knowledge_created += not existed
                return JSONResponse(body, status_code=200 if existed else 201)

        routes_of(
            "knowledgesources", self.knowledge_sources, self.invalid_knowledge_source
        )
        routes_of("knowledgebases", self.knowledge_bases, self.invalid_knowledge_base)

        @app.post("/knowledgebases('{name}')/retrieve")
        async def retrieve(
            name: str, request: Request, body: dict[str, Any]
        ) -> Response:
            refused = self._knowledge_refused(request)
            if refused is not None:
                return refused
            if self.mode is Mode.SLOW:
                await asyncio.sleep(self.delay_seconds)
            # Comparing every vector takes a moment: off the event loop.
            return await asyncio.to_thread(self.retrieve, name, body)

    def app(self) -> FastAPI:
        """The HTTP app: the service's routes for an index, its documents and a knowledge base over it."""
        app = FastAPI(
            title="search-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        @app.middleware("http")
        async def name_itself(request: Request, call_next: Any) -> Any:
            response = await call_next(request)
            response.headers[STAND_IN_HEADER] = STAND_IN_NAME
            return response

        @app.get("/indexes/{name}")
        def read_index(name: str, request: Request) -> Response:
            refused = self._refused(request)
            if refused is not None:
                return refused
            index = self.indexes.get(name)
            if index is None:
                return _error(404, "ResourceNotFound", NO_SUCH_INDEX)
            return JSONResponse(index.definition)

        @app.put("/indexes/{name}")
        def create_index(name: str, request: Request, body: dict[str, Any]) -> Response:
            refused = self._refused(request)
            if refused is not None:
                return refused
            invalid = _invalid_definition(name, body)
            if invalid is not None:
                return _error(400, "InvalidRequest", invalid)
            with self._lock:
                existing = self.indexes.get(name)
                if existing is not None:
                    # As the service: an update keeps the documents.
                    existing.definition = body
                    return Response(status_code=204)
                self.indexes[name] = Index(body)
            return JSONResponse(body, status_code=201)

        @app.post("/indexes/{name}/docs/index")
        def change(name: str, request: Request, body: dict[str, Any]) -> Response:
            refused = self._refused(request)
            if refused is not None:
                return refused
            index = self.indexes.get(name)
            if index is None:
                return _error(404, "ResourceNotFound", NO_SUCH_INDEX)
            return self.change(index, body)

        @app.post("/indexes/{name}/docs/search")
        async def search(name: str, request: Request, body: dict[str, Any]) -> Response:
            refused = self._refused(request)
            if refused is not None:
                return refused
            index = self.indexes.get(name)
            if index is None:
                return _error(404, "ResourceNotFound", NO_SUCH_INDEX)
            if body.get("search") == "*" and not body.get("vectorQueries"):
                return self.listing(index, body)
            with self._lock:
                self.queries.append(body)
            if self.mode is Mode.SLOW:
                await asyncio.sleep(self.delay_seconds)
            # Comparing every vector takes a moment: off the event loop.
            return await asyncio.to_thread(self.query, index, body)

        self._knowledge_routes(app)
        return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="synthdata.search_standin", description=__doc__
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=Mode.OK,
        help="what happens to every call (default: ok)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=30.0,
        help="seconds a query or a retrieve waits in the slow mode (default: 30)",
    )
    args = parser.parse_args(argv)
    stand_in = SearchStandIn(args.mode, delay_seconds=args.delay)
    # Loopback only: it is never reachable from another machine.
    uvicorn.run(stand_in.app(), host="127.0.0.1", port=args.port, server_header=False)


if __name__ == "__main__":
    main()
