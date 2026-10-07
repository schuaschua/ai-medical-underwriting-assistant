"""A local stand-in for Azure AI Search (story 3.3, retrieval row `r5`).

The Azure environment is down while the stories are built, so `retrieval`'s
index load and its search with row `r5` are proven against this: an HTTP app
with the REST routes `retrieval` calls, in the service's shapes. It keeps
its indexes and their documents in memory, and loses them when it stops.

- `GET` and `PUT /indexes/{name}`: read an index definition, create one.
- `POST /indexes/{name}/docs/index`: upload, merge and delete documents.
- `POST /indexes/{name}/docs/search`: list documents (`search: "*"`), or
  answer a hybrid query.

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

Run it: `uv run python -m synthdata.search_standin` (see README, 'Run locally').
"""

import argparse
import asyncio
import math
import re
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

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


class Mode(StrEnum):
    """What the stand-in does with a call; tests switch it to see failures."""

    OK = "ok"
    # Every call is answered 503.
    UNAVAILABLE = "unavailable"
    # A query is answered only after `delay_seconds`.
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


def _invalid_definition(name: str, body: dict[str, Any]) -> str | None:
    """Why the service would refuse an index definition; None if it would take it."""
    fields = body.get("fields")
    if body.get("name") != name or not isinstance(fields, list) or not fields:
        return "The index needs its name and its fields."
    if not all(isinstance(entry, dict) and entry.get("name") for entry in fields):
        return "Every field needs a name."
    if sum(1 for entry in fields if entry.get("key")) != 1:
        return "The index needs exactly one key field."
    profiles = {
        entry.get("name")
        for entry in body.get("vectorSearch", {}).get("profiles", [])
        if isinstance(entry, dict)
    }
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
        return JSONResponse(answer)

    # --- The app ----------------------------------------------------------------------

    def _refused(self, request: Request) -> Response | None:
        """What every route answers before it looks at the call, if anything."""
        if self.mode is Mode.UNAVAILABLE:
            return _error(503, "ServiceUnavailable", "The service is unavailable.")
        if not request.query_params.get("api-version"):
            return _error(400, "MissingApiVersion", "Name the api-version.")
        return None

    def app(self) -> FastAPI:
        """The HTTP app: the service's routes for an index and its documents."""
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
        help="seconds a query waits in the slow mode (default: 30)",
    )
    args = parser.parse_args(argv)
    stand_in = SearchStandIn(args.mode, delay_seconds=args.delay)
    # Loopback only: it is never reachable from another machine.
    uvicorn.run(stand_in.app(), host="127.0.0.1", port=args.port, server_header=False)


if __name__ == "__main__":
    main()
