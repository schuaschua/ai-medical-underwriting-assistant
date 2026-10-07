"""Stories 2.3, 3.2, 3.3 and 3.7: the search and the rule read, on fakes.

No database, no model and no network: the index is a list a test writes, and
the gateway a stub. The two statements themselves are tested against
PostgreSQL in `test_retrieval_search_integration.py`. For row `r5` the real
client of the search service runs against a transport that stands in for it,
and for row `r4` the real model gateway against one that stands in for the
deployments.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from retrieval_fakes import (
    EMBEDDING,
    RULE_A,
    RULE_B,
    RULE_C,
    FakeSearchService,
    MemoryIndex,
    MemorySchemaRevision,
    StubModel,
    axis,
    chunk_record,
    completion,
    embedding_answer,
    fixed_record,
    rerank_answer,
    search_document,
    vector_for,
)

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import (
    MAX_TOP_K,
    RuleText,
    SearchResponse,
)
from retrieval.adapters.http import routes
from retrieval.adapters.http.app import (
    build_query_gateway,
    create_app,
    search_options,
)
from retrieval.adapters.http.routes import Dependencies
from retrieval.adapters.search_index import SearchIndex, build_search_http
from retrieval.domain.fusion import RRF_K, Fused, reciprocal_rank_fusion
from retrieval.domain.ports import ModelCallFailed, ModelUnavailable
from retrieval.domain.rows import BUILT_ROWS, available_rows
from retrieval.domain.search import (
    SearchOptions,
    SearchPorts,
)
from retrieval.settings import Settings

# Words no log line and no error may ever hold.
QUERY = "SECRET-QUERY the applicant has a raised reading"
RULE_D = "UW-BB-002"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def error_of(response: httpx2.Response) -> tuple[int, ErrorCode]:
    return response.status_code, ErrorBody.model_validate(response.json()).error.code


@pytest.fixture
def index() -> MemoryIndex:
    """Four chunks. The vector side finds A, B, C; the full-text side B, D, A."""
    return MemoryIndex(
        records=[
            chunk_record(RULE_A, "Mild.", axis(1), references=[RULE_B], manual_page=12),
            chunk_record(RULE_B, "Worse.", axis(2), manual_page=13),
            chunk_record(RULE_C, "Gout.", axis(3), impairment="Gout", manual_page=40),
            chunk_record(RULE_D, "Severe gout.", axis(4), impairment="Gout"),
        ],
        vector_order=[RULE_A, RULE_B, RULE_C],
        text_order=[RULE_B, RULE_D, RULE_A],
    )


SEARCH_ENDPOINT = "http://127.0.0.1:5103"


def search_client(
    settings: Settings, service: FakeSearchService, **options: Any
) -> SearchIndex:
    """The real client of the search service, with the fake where the service would be."""
    of = settings.model_copy(update={"search_service_endpoint": SEARCH_ENDPOINT})

    async def at_once(seconds: float) -> None:
        return None

    return SearchIndex(
        build_search_http(of, service.transport()),
        index_name=of.search_service_index_name,
        api_version=of.search_service_api_version,
        sleep=at_once,
        **options,
    )


@contextmanager
def service_with(
    settings: Settings,
    model: Any,
    index: MemoryIndex,
    search_service: SearchIndex | None = None,
    reranker: Any = None,
    **options: Any,
) -> Iterator[TestClient]:
    """The app around a model and an index of the test's own; a gateway is closed afterwards."""
    dependencies = Dependencies(
        search=SearchPorts(
            model=model, index=index, search_service=search_service, reranker=reranker
        ),
        schema_revision=MemorySchemaRevision("head"),
        head_revision="head",
        options=SearchOptions(**options),
    )
    app = create_app(settings, dependencies=dependencies)
    with TestClient(app, raise_server_exceptions=False) as client:
        try:
            yield client
        finally:
            close = getattr(model, "aclose", None)
            if close is not None:
                # On the client's own event loop, which the gateway was used on.
                assert client.portal is not None
                client.portal.call(close)


def search(client: TestClient, **body: Any) -> httpx2.Response:
    return client.post(
        "/searches", json={"query": QUERY, "retriever_config": "r3", **body}
    )


def found(response: httpx2.Response) -> SearchResponse:
    assert response.status_code == 200, response.text
    return SearchResponse.model_validate(response.json())


# --- Reciprocal rank fusion, alone ---------------------------------------------------------


def test_story_2_3_fusion_adds_one_over_sixty_plus_rank_for_each_list() -> None:
    fused = reciprocal_rank_fusion(["a", "b", "c"], ["b", "d", "a"])

    assert RRF_K == 60
    assert fused == [
        Fused("b", 1 / 62 + 1 / 61),
        Fused("a", 1 / 61 + 1 / 63),
        # Found by one side only, and still ranked.
        Fused("d", 1 / 62),
        Fused("c", 1 / 63),
    ]
    # The largest score two lists can give is inside the contract's 0 to 1.
    assert reciprocal_rank_fusion(["a"], ["a"]) == [Fused("a", 2 / 61)]
    # Ties are broken by chunk id. Each chunk is first in one list and
    # second in the other: equal scores.
    one_way = reciprocal_rank_fusion(["z", "a"], ["a", "z"])
    other_way = reciprocal_rank_fusion(["a", "z"], ["z", "a"])

    assert [entry.chunk_id for entry in one_way] == ["a", "z"]
    assert one_way == other_way
    # The same for chunks only one side found, at the same rank.
    assert [entry.chunk_id for entry in reciprocal_rank_fusion(["m"], ["b"])] == [
        "b",
        "m",
    ]


# --- POST /searches ------------------------------------------------------------------------


def test_story_2_3_a_search_with_r3_answers_ranked_items_with_every_field(
    client: TestClient, index: MemoryIndex, model: StubModel
) -> None:
    result = found(search(client, top_k=5))

    assert result.retriever_config is RetrieverConfig.R3
    assert result.latency_ms >= 0
    # B is high on both sides, A on both, D and C on one side each.
    assert [item.rule_ids for item in result.items] == [
        [RULE_B],
        [RULE_A],
        [RULE_D],
        [RULE_C],
    ]
    assert [item.rank for item in result.items] == [1, 2, 3, 4]
    assert [item.score for item in result.items] == pytest.approx(
        [1 / 62 + 1 / 61, 1 / 61 + 1 / 63, 1 / 62, 1 / 63]
    )
    first = result.items[0]
    assert first.model_dump() == {
        "chunk_id": f"smart-{RULE_B}",
        "rule_ids": [RULE_B],
        "rank": 1,
        "score": pytest.approx(1 / 62 + 1 / 61),
        "text": f"Rule {RULE_B}: Worse.",
        "manual_page": 13,
        "impairment": "Raised blood sugar",
    }
    # The query was embedded once, and each side searched once with that
    # vector and that text, over the `smart` chunks, to the depth set.
    assert model.embedded == [[QUERY]]
    assert index.asked == [
        ("nearest", ChunkSet.SMART, tuple(vector_for(QUERY)), 50),
        ("matching", ChunkSet.SMART, QUERY, (), 50),
    ]


def test_story_2_3_an_unknown_row_or_a_top_k_outside_its_bounds_is_refused_by_validation(
    client: TestClient, index: MemoryIndex, model: StubModel
) -> None:
    response = search(client, top_k=MAX_TOP_K + 1)

    assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
    assert model.calls == 0 and index.asked == []
    for config in ("r9", "R3", "", None):
        response = search(client, retriever_config=config)

        assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
        assert response.json()["error"]["message"] == "The request is not valid."
    assert model.calls == 0
    # Nothing but the three fields is taken.
    assert error_of(search(client, rerank=True)) == (422, ErrorCode.VALIDATION_FAILED)


# --- The search's own budget ----------------------------------------------------------------


class NeverAnswers(StubModel):
    """A model that is asked and does not answer while the test runs."""

    async def embed(self, texts: Any) -> list[list[float]]:
        self.embedded.append(list(texts))
        await asyncio.Event().wait()
        raise AssertionError("never reached")


class NeverReads(MemoryIndex):
    """An index whose vector read does not end while the test runs."""

    async def nearest(self, *arguments: Any) -> Any:
        self.asked.append(("nearest", *arguments))
        await asyncio.Event().wait()


def test_story_2_3_a_search_that_outlasts_its_deadline_says_what_did_not_answer_in_time(
    settings: Settings,
    index: MemoryIndex,
    model: StubModel,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answering = model
    model = NeverAnswers()

    with (
        service_with(settings, model, index, deadline_seconds=0.05) as client,
        caplog.at_level(logging.INFO),
    ):
        response = search(client)

    assert error_of(response) == (503, ErrorCode.MODEL_UNAVAILABLE)
    assert "did not answer in time" in response.json()["error"]["message"]
    assert model.embedded == [[QUERY]] and index.asked == []
    assert "search deadline passed: retriever_config=r3 waited_for=model" in caplog.text
    assert "SECRET" not in caplog.text

    # The model answers and the index does not.
    slow = NeverReads(index.records, index.vector_order, index.text_order)

    with service_with(settings, answering, slow, deadline_seconds=0.05) as client:
        response = search(client)

    assert error_of(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "items" not in response.json()
    # The view was closed when the search was ended.
    assert (slow.opened, slow.closed) == (1, 1)


# --- The deployment the index was embedded with ---------------------------------------------


def test_story_2_3_a_search_is_refused_when_the_index_was_embedded_with_another_deployment(
    settings: Settings,
    model: StubModel,
    index: MemoryIndex,
    caplog: pytest.LogCaptureFixture,
) -> None:
    index.embedding_deployment = "another-embedding"
    options: dict[str, Any] = {"embedding_deployment": EMBEDDING}

    with (
        service_with(settings, model, index, **options) as client,
        caplog.at_level(logging.INFO),
    ):
        refused = search(client)
        read = client.get(f"/rules/{RULE_A}")
        # The run record changed: the next search sees it, with no restart.
        index.embedding_deployment = EMBEDDING
        again = search(client)

    assert error_of(refused) == (503, ErrorCode.MODEL_UNAVAILABLE)
    assert "another embedding model" in refused.json()["error"]["message"]
    assert "items" not in refused.json()
    # Neither list was read: vectors of two models are not compared.
    assert [call[0] for call in index.asked][:1] == ["defining"]
    assert (
        "search refused: embedding deployment differs: "
        f"configured={EMBEDDING} index=another-embedding"
    ) in caplog.text
    # A rule read compares no vectors and is answered.
    assert read.status_code == 200
    assert len(found(again).items) == 4


# --- The search's span ------------------------------------------------------------------------


def test_story_2_3_a_search_has_one_span_with_the_row_and_counts_and_never_the_query(
    client: TestClient, index: MemoryIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(routes, "tracer", provider.get_tracer("test"))

    found(search(client, top_k=2))
    index.fail_matching = True
    search(client, top_k=2)
    search(client, retriever_config="r4")

    done, failed, refused = [
        dict(span.attributes or {})
        for span in exporter.get_finished_spans()
        if span.name == "retrieval.search"
    ]
    assert done == {
        "retrieval.retriever_config": "r3",
        "retrieval.top_k": 2,
        "retrieval.candidate_depth": 50,
        "retrieval.vector_candidates": 3,
        "retrieval.full_text_candidates": 3,
        "retrieval.items": 2,
    }
    assert failed == {
        **done,
        "error.type": "upstream_unavailable",
        "retrieval.vector_candidates": 0,
        "retrieval.full_text_candidates": 0,
        "retrieval.items": 0,
    }
    assert refused["error.type"] == "retriever_not_available"
    assert "SECRET" not in str(exporter.get_finished_spans()[0].to_json())


# --- GET /rules/{rule_id} ---------------------------------------------------------------------


def test_story_2_3_a_rule_is_read_by_its_id_with_the_rules_it_refers_to(
    client: TestClient, index: MemoryIndex, model: StubModel
) -> None:
    response = client.get(f"/rules/{RULE_A}")

    assert response.status_code == 200
    assert RuleText.model_validate(response.json()) == RuleText(
        rule_id=RULE_A,
        chunk_id=f"smart-{RULE_A}",
        chunk_set=ChunkSet.SMART,
        text=f"Rule {RULE_A}: Mild.",
        manual_page=12,
        impairment="Raised blood sugar",
        reference_rule_ids=[RULE_B],
    )
    # A rule that refers to none says so.
    assert client.get(f"/rules/{RULE_B}").json()["reference_rule_ids"] == []
    # No model is asked, and only the `smart` chunk that defines the rule is read.
    assert model.calls == 0
    assert index.asked[0] == ("defining", ChunkSet.SMART, RULE_A)


def test_story_2_3_an_unknown_rule_is_not_found_and_a_malformed_id_is_refused(
    client: TestClient, index: MemoryIndex
) -> None:
    unknown = client.get("/rules/UW-QQ-999")

    assert error_of(unknown) == (404, ErrorCode.NOT_FOUND)
    assert (
        unknown.json()["error"]["message"] == "The manual defines no rule of that id."
    )
    asked = len(index.asked)
    for malformed in ("uw-aa-001", "UW-AA-1", "UW-AA-0011", "smart-UW-AA-001", "x"):
        assert error_of(client.get(f"/rules/{malformed}")) == (
            422,
            ErrorCode.VALIDATION_FAILED,
        )
    # A malformed id is refused before anything is looked up.
    assert len(index.asked) == asked


# --- The baseline rows (story 3.2) ------------------------------------------------------------


def test_story_3_2_a_row_whose_chunk_set_was_never_ingested_is_refused_not_answered_empty(
    client: TestClient, index: MemoryIndex
) -> None:
    # No run record: the job never wrote the set this row reads.
    index.embedding_deployment = None

    searched = search(client, retriever_config="r1")
    read = client.get(f"/rules/{RULE_A}", params={"retriever_config": "r1"})

    # Not "nothing found": a search with no items and a rule that is not
    # in the manual would both be untrue.
    assert error_of(searched) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    assert error_of(read) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    assert "not been ingested" in read.json()["error"]["message"]
    # Neither list was read, and no chunk looked for.
    assert index.asked == []


def test_story_3_2_a_rule_read_on_r1_answers_the_references_of_that_rules_own_definition(
    client: TestClient, index: MemoryIndex
) -> None:
    # One `fixed` chunk: two definitions, each a paragraph, and a worked
    # example after them that mentions a rule neither refers to.
    index.records.append(
        fixed_record(
            1,
            f"Rule {RULE_A}: Mild. See rule {RULE_B}.\n"
            f"Rule {RULE_B}: Worse. See rule {RULE_C}.\n"
            f"Worked example: under rule {RULE_D} nothing changes.",
            axis(1),
            [RULE_A, RULE_B],
            references=[RULE_C, RULE_D],
        )
    )

    first = client.get(f"/rules/{RULE_A}", params={"retriever_config": "r1"}).json()
    second = client.get(f"/rules/{RULE_B}", params={"retriever_config": "r1"}).json()

    # The rule it refers to is defined in the same chunk, and is answered;
    # what its neighbour and the example mention is not: reading one rule
    # does not open the others' references to the agent.
    assert (first["chunk_id"], first["reference_rule_ids"]) == ("fixed-0001", [RULE_B])
    assert second["reference_rule_ids"] == [RULE_C]
    # The text is the chunk, whole.
    assert first["text"] == second["text"]


# --- Row r5 on Azure AI Search (story 3.3) ----------------------------------------------------


@pytest.fixture
def search_service(index: MemoryIndex) -> FakeSearchService:
    """The search service's index, holding the four chunks pgvector holds."""
    return FakeSearchService(
        definition={"name": "manual-smart"},
        documents={
            record.chunk.chunk_id: search_document(record) for record in index.records
        },
    )


def test_story_3_3_a_search_with_r5_answers_the_services_reranked_order_in_the_common_shape(
    settings: Settings,
    model: StubModel,
    index: MemoryIndex,
    search_service: FakeSearchService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The index is behind pgvector: it still holds a chunk the manual lost.
    gone = chunk_record("UW-ZZ-009", "Gone.", axis(5))
    search_service.documents[gone.chunk.chunk_id] = search_document(gone)
    search_service.ranked = [
        (f"smart-{RULE_C}", 3.0),
        ("smart-UW-ZZ-009", 2.5),
        (f"smart-{RULE_A}", 1.0),
        # Outside the documented range: cut to it.
        (f"smart-{RULE_B}", 4.6),
    ]
    odd = 'SECRET-QUERY "HbA1c" (7.4 %) -stable + raised|high'
    options = {"embedding_deployment": EMBEDDING}

    with (
        service_with(
            settings, model, index, search_client(settings, search_service), **options
        ) as client,
        caplog.at_level(logging.INFO),
    ):
        result = found(search(client, query=odd, retriever_config="r5", top_k=5))
        rule = client.get(f"/rules/{RULE_A}", params={"retriever_config": "r5"})
        # A stale document, of an id pgvector holds with another content
        # hash (a load failed after the chunk changed), is left out too.
        search_service.documents[f"smart-{RULE_C}"]["content_hash"] = "1" * 64
        without_stale = found(search(client, retriever_config="r5"))
        # The run record names another deployment while every document
        # carries the configured one: refused before the service is asked.
        asked = len(search_service.queries)
        index.embedding_deployment = "another-embedding"
        by_record = search(client, retriever_config="r5")
        not_asked = len(search_service.queries) == asked
        index.embedding_deployment = EMBEDDING
        # A document whose vector another deployment made is not answered.
        search_service.documents[f"smart-{RULE_A}"]["embedding_deployment"] = "other"
        refused = search(client, retriever_config="r5")

    # The service's order, kept; the chunk pgvector does not hold left out
    # and the ranks counted on without a gap.
    assert result.retriever_config is RetrieverConfig.R5
    assert [item.rule_ids for item in result.items] == [[RULE_C], [RULE_A], [RULE_B]]
    assert [item.rank for item in result.items] == [1, 2, 3]
    # The reranker's 0 to 4, divided by 4.
    assert [item.score for item in result.items] == [0.75, 0.25, 1.0]
    assert result.items[0].model_dump() == {
        "chunk_id": f"smart-{RULE_C}",
        "rule_ids": [RULE_C],
        "rank": 1,
        "score": 0.75,
        "text": f"Rule {RULE_C}: Gout.",
        "manual_page": 40,
        "impairment": "Gout",
    }
    # One embedding call, with the query as it was asked; one hybrid query
    # with the semantic ranker, exact vector search, and no operator in its text.
    assert model.embedded[0] == [odd]
    sent = search_service.queries[0]
    assert sent["search"] == (
        r"SECRET-QUERY \"HbA1c\" \(7.4 %\) \-stable \+ raised\|high"
    )
    assert (sent["queryType"], sent["semanticErrorHandling"]) == ("semantic", "fail")
    assert sent["semanticConfiguration"] == "rules" and sent["top"] == 5
    assert sent["vectorQueries"] == [
        {
            "kind": "vector",
            "vector": vector_for(odd),
            "fields": "embedding",
            # As many vector candidates as row `r3` hands its fusion.
            "k": 50,
            "exhaustive": True,
        }
    ]
    assert "left_out=1 max_reranker_score=4.6 items=3" in caplog.text
    assert [item.rule_ids for item in without_stale.items] == [[RULE_A], [RULE_B]]
    assert "left_out=2 max_reranker_score=4.6 items=2" in caplog.text
    assert error_of(by_record) == (503, ErrorCode.MODEL_UNAVAILABLE) and not_asked
    assert (
        "search service answered chunks pgvector does not hold: count=1 "
        "chunk_ids=smart-UW-ZZ-009"
    ) in caplog.text
    # A rule read for `r5` answers the `smart` chunk from pgvector.
    assert rule.json()["chunk_id"] == f"smart-{RULE_A}"
    # Neither of pgvector's two lists was read by the searches.
    assert index.asked == [("defining", ChunkSet.SMART, RULE_A)]
    assert error_of(refused) == (503, ErrorCode.MODEL_UNAVAILABLE)
    assert "another embedding model" in refused.json()["error"]["message"]
    assert "SECRET" not in caplog.text


def test_story_3_3_without_a_search_endpoint_r5_is_refused_and_the_other_rows_answer(
    settings: Settings,
    model: StubModel,
    index: MemoryIndex,
    search_service: FakeSearchService,
) -> None:
    index.records.append(fixed_record(1, f"Rule {RULE_A}: Mild.", axis(1), [RULE_A]))

    with service_with(settings, model, index) as client:
        refused = search(client, retriever_config="r5")
        calls = model.calls
        answered = {
            row: len(found(search(client, retriever_config=row)).items)
            for row in ("r1", "r2", "r3")
        }
        # Story 3.7: row `r4` needs a reranker, and this service was given
        # none. A row that is not built is refused the same way.
        no_reranker = search(client, retriever_config="r4")
        not_built = search(client, retriever_config="r6")

    assert error_of(refused) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    assert refused.json()["error"]["message"] == (
        "That retrieval row is not available here. "
        "Rows r1, r2 and r3 can be used for now."
    )
    # Refused before anything is spent, and nothing was asked of a service.
    assert calls == 0 and search_service.requests == []
    assert answered == {"r1": 1, "r2": 3, "r3": 4}
    for response in (no_reranker, not_built):
        assert error_of(response) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
        assert response.json()["error"]["message"] == refused.json()["error"]["message"]
    assert model.reranked == []
    # The rows a service answers: without a search service or a reranker,
    # with each of them, and with both.
    rows = {
        given: sorted(row.value for row in available_rows(*given))
        for given in ((False, False), (True, False), (False, True))
    }
    assert rows == {
        (False, False): ["r1", "r2", "r3"],
        (True, False): ["r1", "r2", "r3", "r5"],
        (False, True): ["r1", "r2", "r3", "r4"],
    }
    assert available_rows(search_service=True, reranker=True) == BUILT_ROWS
    with service_with(
        settings, model, index, search_client(settings, search_service)
    ) as client:
        assert found(search(client, retriever_config="r5")).items == []
        assert "r5" in search(client, retriever_config="r4").json()["error"]["message"]


def test_story_3_3_a_search_service_that_is_down_or_slow_gives_no_partial_answer(
    settings: Settings,
    model: StubModel,
    index: MemoryIndex,
    search_service: FakeSearchService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    search_service.ranked = [(f"smart-{RULE_A}", 2.0), (f"smart-{RULE_B}", None)]
    once_more = search_client(settings, search_service, max_retries=1)

    with (
        service_with(settings, model, index, once_more) as client,
        caplog.at_level(logging.INFO),
    ):
        # An answer without the ranker's score for one document.
        unranked = search(client, retriever_config="r5")
        search_service.status = 503
        down = search(client, retriever_config="r5")
        asked = search_service.count("query"), len(search_service.requests)
        # The pgvector rows do not need the search service.
        assert len(found(search(client)).items) == 4
    search_service.status, search_service.never_answers = None, True
    with service_with(
        settings,
        model,
        index,
        search_client(settings, search_service),
        deadline_seconds=0.05,
    ) as client:
        slow = search(client, retriever_config="r5")

    for response in (unranked, down, slow):
        assert error_of(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
        assert "items" not in response.json()
    assert "search service" in slow.json()["error"]["message"]
    # Down: the one query, and once more.
    assert asked == (1, 3)
    assert "reason=search_query_malformed" in caplog.text
    assert "reason=search_query_status_503" in caplog.text
    assert "SECRET" not in caplog.text


# --- Row r4 with a reranker (story 3.7) -------------------------------------------------------


@dataclass
class ChatAndEmbedding:
    """Stands where the two deployments would be, for the real gateway: an `httpx2` handler.

    A query is embedded as the stub embeds it. A rerank request is answered
    with the relevance a test gives each chunk, for the candidates the
    request names and no other.
    """

    relevance: dict[str, float]
    # A rerank request gets no answer in its time.
    chat_times_out: bool = False
    chats: list[dict[str, Any]] = field(default_factory=list)
    # The time each call was allowed, by its path.
    timeouts: list[tuple[str, float]] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        path = request.url.path.rsplit("/", 1)[-1]
        self.timeouts.append((path, request.extensions["timeout"]["read"]))
        if path == "embeddings":
            return httpx2.Response(200, json=embedding_answer(body["input"]))
        self.chats.append(body)
        if self.chat_times_out:
            raise httpx2.ReadTimeout("SECRET", request=request)
        given = json.loads(body["messages"][1]["content"])["candidates"]
        rated = {c["chunk_id"]: self.relevance[c["chunk_id"]] for c in given}
        return httpx2.Response(200, json=completion(rerank_answer(rated)))


def candidates_of(chat: dict[str, Any]) -> list[str]:
    """The chunk ids a rerank request showed the model, in its order."""
    shown = json.loads(chat["messages"][1]["content"])["candidates"]
    return [candidate["chunk_id"] for candidate in shown]


def test_story_3_7_a_search_with_r4_answers_r3s_candidates_in_the_rerankers_order(
    settings: Settings, index: MemoryIndex, caplog: pytest.LogCaptureFixture
) -> None:
    # The fused order of the fixture is B, A, D, C. The reranker puts the
    # last first, rates two alike and one as of no use.
    deployments = ChatAndEmbedding(
        {
            f"smart-{RULE_C}": 0.9,
            f"smart-{RULE_A}": 0.4,
            f"smart-{RULE_B}": 0.4,
            f"smart-{RULE_D}": 0,
        }
    )
    # The row's settings, none at its default: they reach the gateway and
    # the search as the service builds both from them.
    settings = settings.model_copy(
        update={
            "search_rerank_depth": 3,
            "search_rerank_timeout_seconds": 7.0,
            "search_rerank_max_completion_tokens": 1234,
            "search_rerank_deadline_seconds": 9.0,
        }
    )
    options = asdict(search_options(settings))
    assert (options["rerank_depth"], options["rerank_deadline_seconds"]) == (3, 9.0)
    gateway = build_query_gateway(settings, httpx2.MockTransport(deployments.handle))
    assert gateway is not None

    with (
        service_with(settings, gateway, index, reranker=gateway, **options) as client,
        caplog.at_level(logging.INFO),
    ):
        on_r3 = found(search(client, top_k=5))
        asked_for_r3 = list(index.asked)
        # More items than the rerank depth: that many candidates are rated.
        result = found(search(client, retriever_config="r4", top_k=5))
        # Fewer: the three best fused candidates are rated, as the depth says.
        best_two = found(search(client, retriever_config="r4", top_k=2))
        # A rerank call that timed out is not sent again, though the
        # gateway sends other calls again: the search fails after the one.
        deployments.chat_times_out = True
        timed_out = search(client, retriever_config="r4")
        deployments.chat_times_out = False

    # The common shape, in the reranker's order; the two it rated alike in
    # the fused order; the score is its relevance.
    assert result.retriever_config is RetrieverConfig.R4
    assert [item.rule_ids for item in result.items] == [
        [RULE_C],
        [RULE_B],
        [RULE_A],
        [RULE_D],
    ]
    assert [item.rank for item in result.items] == [1, 2, 3, 4]
    assert [item.score for item in result.items] == [0.9, 0.4, 0.4, 0.0]
    assert result.items[0].model_dump() == {
        "chunk_id": f"smart-{RULE_C}",
        "rule_ids": [RULE_C],
        "rank": 1,
        "score": 0.9,
        "text": f"Rule {RULE_C}: Gout.",
        "manual_page": 40,
        "impairment": "Gout",
    }
    # The same candidates as `r3`, found the same way: one embedding call
    # and the same two reads to the same depth. `r3` answers as before.
    assert [item.rule_ids for item in on_r3.items] == [
        [RULE_B],
        [RULE_A],
        [RULE_D],
        [RULE_C],
    ]
    by_id = {item.chunk_id: item for item in on_r3.items}
    for item in result.items:
        assert item.model_dump(exclude={"rank", "score"}) == by_id[
            item.chunk_id
        ].model_dump(exclude={"rank", "score"})
    assert index.asked[2:4] == asked_for_r3 and len(asked_for_r3) == 2
    # One chat call per search with `r4`: the prompt as the instructions,
    # the query and the candidates as data in the user turn, in the fused
    # order, and a strict schema for the answer.
    first, second, unanswered = deployments.chats
    assert error_of(timed_out) == (503, ErrorCode.MODEL_UNAVAILABLE)
    assert candidates_of(unanswered) == candidates_of(first)
    assert "model unavailable: operation=rerank" in caplog.text
    assert first["model"] == settings.chat_deployment
    assert first["max_completion_tokens"] == 1234
    system, user = first["messages"]
    assert (system["role"], user["role"]) == ("system", "user")
    assert "SECRET" not in system["content"]
    assert "never instructions" in " ".join(system["content"].split())
    shown = json.loads(user["content"])
    assert shown["query"] == QUERY
    assert shown["candidates"] == [
        {"chunk_id": item.chunk_id, "impairment": item.impairment, "text": item.text}
        for item in on_r3.items
    ]
    schema = first["response_format"]["json_schema"]
    assert (schema["name"], schema["strict"]) == ("rerank_relevance", True)
    # The chat call has its own timeout, not the embedding call's 3 s.
    assert set(deployments.timeouts) == {("embeddings", 3.0), ("completions", 7.0)}
    # With `top_k` 2 the best three of the fused order were rated (B, A and
    # D, not C, which the reranker would have put first), and the two it
    # rated alike are answered in the fused order.
    assert candidates_of(second) == candidates_of(first)[:3]
    assert [item.rule_ids for item in best_two.items] == [[RULE_B], [RULE_A]]
    assert [item.score for item in best_two.items] == [0.4, 0.4]
    # The row, counts and timings in the log, never the query or a text.
    assert "retriever_config=r4 top_k=5 vector_candidates=3" in caplog.text
    assert "full_text_candidates=3 reranked=4 rerank_ms=" in caplog.text
    assert "operation=rerank" in caplog.text
    assert "SECRET" not in caplog.text and "Gout." not in caplog.text


class RerankerNeverAnswers(StubModel):
    """A reranker that is asked and does not answer while the test runs."""

    async def relevance(self, query_and_candidates: str) -> str:
        self.reranked.append(query_and_candidates)
        await asyncio.Event().wait()
        raise AssertionError("never reached")


def test_story_3_7_a_reranker_with_no_usable_answer_fails_the_search_and_never_answers_r3s_order(
    settings: Settings,
    index: MemoryIndex,
    model: StubModel,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = [f"smart-{rule}" for rule in (RULE_B, RULE_A, RULE_D, RULE_C)]
    whole: dict[str, Any] = dict.fromkeys(ids, 0.5)
    bad_answers = {
        "rerank_not_json": "The first candidate is the most relevant, I think.",
        # A refusal, a filter, or an answer cut off at the token limit.
        "rerank_empty": "",
        "rerank_candidate_twice": json.dumps(
            {
                "ranking": [
                    {"chunk_id": chunk_id, "relevance": 0.5}
                    for chunk_id in [*ids, ids[0]]
                ]
            }
        ),
        "rerank_candidate_left_out": rerank_answer(dict.fromkeys(ids[:3], 0.5)),
        "rerank_candidate_not_given": rerank_answer({**whole, "smart-UW-ZZ-009": 1}),
        # A whole number too large for a float is out of range like any other.
        "rerank_relevance_out_of_range": rerank_answer({**whole, ids[0]: 10**400}),
        "rerank_relevance_not_a_number": rerank_answer({**whole, ids[0]: "high"}),
    }
    answers: list[httpx2.Response] = []

    with (
        service_with(settings, model, index, reranker=model) as client,
        caplog.at_level(logging.INFO),
    ):
        for answer in bad_answers.values():
            model.rerank_answer = answer
            answers.append(search(client, retriever_config="r4"))
        # The gateway gave up after its retries.
        model.rerank_error = ModelUnavailable()
        answers.append(search(client, retriever_config="r4"))
        # The model refused the call, and would again: said as an embedding
        # call's refusal is.
        model.rerank_error = ModelCallFailed("model_status_400")
        refused = search(client, retriever_config="r4")
        # The other rows ask no reranker.
        asked = len(model.reranked)
        assert len(found(search(client)).items) == 4
        assert len(model.reranked) == asked
    # The chat call outlasts the row's own deadline, which is not the
    # deadline of the other rows.
    slow = RerankerNeverAnswers()
    with (
        service_with(
            settings,
            slow,
            index,
            reranker=slow,
            deadline_seconds=30.0,
            rerank_deadline_seconds=0.05,
        ) as client,
        caplog.at_level(logging.INFO),
    ):
        answers.append(search(client, retriever_config="r4"))

    for response in answers:
        assert error_of(response) == (503, ErrorCode.MODEL_UNAVAILABLE)
        assert "items" not in response.json()
    for reason in bad_answers:
        assert f"rerank answer invalid: reason={reason} candidates=4" in caplog.text
    assert error_of(refused) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "items" not in refused.json()
    assert "rerank refused: reason=model_status_400" in caplog.text
    assert "reranker" in answers[-1].json()["error"]["message"]
    assert len(slow.reranked) == 1
    # The time the reranker was waited for is in the line, though it never answered.
    (passed,) = re.findall(
        r"search deadline passed: retriever_config=r4 waited_for=reranker "
        r"rerank_ms=(\d+)",
        caplog.text,
    )
    assert int(passed) > 0
    assert "SECRET" not in caplog.text
