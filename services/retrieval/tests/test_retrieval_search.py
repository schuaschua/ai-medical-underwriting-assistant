"""Story 2.3: the search and the rule read, on fakes.

No database, no model and no network: the index is a list a test writes, and
the gateway a stub. The two statements themselves are tested against
PostgreSQL in `test_retrieval_search_integration.py`.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
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
    MemoryIndex,
    MemorySchemaRevision,
    StubModel,
    axis,
    chunk_record,
    embedding_answer,
    indexed,
    vector_for,
)

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.models.retrieval import (
    DEFAULT_TOP_K,
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    RuleText,
    SearchRequest,
    SearchResponse,
)
from contracts.rules import is_rule_id
from retrieval.adapters.http import routes
from retrieval.adapters.http.app import (
    NoEmbeddingModel,
    build_query_gateway,
    create_app,
    search_options,
)
from retrieval.adapters.http.routes import Dependencies
from retrieval.domain.fusion import RRF_K, Fused, reciprocal_rank_fusion
from retrieval.domain.ports import (
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelUnavailable,
)
from retrieval.domain.rows import (
    INGESTED_CHUNK_SETS,
    ROWS,
    SearchMethod,
    chunk_set_to_read,
    row_to_search,
)
from retrieval.domain.search import (
    FLOAT4_MAX,
    SearchOptions,
    SearchPorts,
    candidate_depth,
    embed_query,
    rank_items,
    rule_ids_named_in,
    search_rules,
)
from retrieval.settings import Settings

# Words no log line and no error may ever hold.
QUERY = "SECRET-QUERY the applicant has a raised reading"
RULE_D = "UW-BB-002"
NOT_BUILT = ("r1", "r2", "r4", "r5", "r6")
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


@contextmanager
def service_with(
    settings: Settings, model: Any, index: MemoryIndex, **options: Any
) -> Iterator[TestClient]:
    """The app around a model and an index of the test's own; a gateway is closed afterwards."""
    dependencies = Dependencies(
        search=SearchPorts(model=model, index=index),
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


# --- The row table ----------------------------------------------------------------------


def test_story_2_3_every_ladder_row_is_named_and_only_r3_is_built() -> None:
    assert set(ROWS) == set(RetrieverConfig)
    assert [row.config for row in ROWS.values() if row.built] == [RetrieverConfig.R3]
    r3 = row_to_search(RetrieverConfig.R3)
    assert (r3.chunk_set, r3.method) == (ChunkSet.SMART, SearchMethod.HYBRID)
    # Only `r1` reads the `fixed` set, which no ingestion writes yet.
    assert [row.config for row in ROWS.values() if row.chunk_set is ChunkSet.FIXED] == [
        RetrieverConfig.R1
    ]
    assert INGESTED_CHUNK_SETS == {ChunkSet.SMART}


@pytest.mark.parametrize("config", NOT_BUILT)
def test_story_2_3_a_row_that_is_not_built_is_refused_as_not_available(
    config: str,
) -> None:
    with pytest.raises(DomainError) as refused:
        row_to_search(RetrieverConfig(config))

    assert refused.value.code is ErrorCode.RETRIEVER_NOT_AVAILABLE
    assert "not available" in refused.value.message


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


def test_story_2_3_fusion_breaks_ties_by_chunk_id() -> None:
    # Each chunk is first in one list and second in the other: equal scores.
    one_way = reciprocal_rank_fusion(["z", "a"], ["a", "z"])
    other_way = reciprocal_rank_fusion(["a", "z"], ["z", "a"])

    assert [entry.chunk_id for entry in one_way] == ["a", "z"]
    assert one_way == other_way
    # The same for chunks only one side found, at the same rank.
    assert [entry.chunk_id for entry in reciprocal_rank_fusion(["m"], ["b"])] == [
        "b",
        "m",
    ]


def test_story_2_3_fusion_takes_empty_lists_and_counts_a_repeated_id_once() -> None:
    assert reciprocal_rank_fusion([], []) == []
    assert reciprocal_rank_fusion(["a", "b"], []) == [
        Fused("a", 1 / 61),
        Fused("b", 1 / 62),
    ]
    assert reciprocal_rank_fusion(["a", "a", "b"]) == [
        Fused("a", 1 / 61),
        Fused("b", 1 / 62),
    ]


# --- The steps of a search, alone ----------------------------------------------------------


def test_story_2_3_the_query_is_embedded_once_exactly_as_it_was_asked(
    model: StubModel,
) -> None:
    padded = f"  {QUERY}\n"

    vector = asyncio.run(embed_query(padded, model))

    # One call with one text: nothing before it, nothing taken from it.
    assert model.embedded == [[padded]]
    assert list(vector) == vector_for(padded)


def test_story_2_3_rule_ids_written_in_a_query_are_read_whole() -> None:
    assert rule_ids_named_in("What does UW-DM-001 say, and uw-ht-002?") == (
        "UW-DM-001",
        "UW-HT-002",
    )
    assert rule_ids_named_in("UW-DM-001 and uw-dm-001") == ("UW-DM-001",)
    # Not a part of a longer word, and not something of another shape.
    assert rule_ids_named_in("XUW-DM-001 UW-DM-0011 UW-D-001 HbA1c 7.0 %") == ()
    assert rule_ids_named_in("_UW-DM-001 UW-DM-001_ UW_DM_001") == ()
    # Each one is an id of the contracts' form.
    assert all(is_rule_id(found) for found in rule_ids_named_in("uw-abcd-123."))


def test_story_2_3_the_rule_ids_a_query_names_reach_the_full_text_side(
    client: TestClient, index: MemoryIndex
) -> None:
    query = f"what does rule {RULE_D.lower()} say? And {RULE_A}."

    found(search(client, query=query))

    (asked,) = [call for call in index.asked if call[0] == "matching"]
    # The query as it was asked, and the ids it names as the manual prints them.
    assert asked == ("matching", ChunkSet.SMART, query, (RULE_D, RULE_A), 50)


def test_story_2_3_ranks_are_dense_from_one_and_scores_are_the_fused_ones(
    index: MemoryIndex,
) -> None:
    chunks = {record.chunk.chunk_id: indexed(record) for record in index.records}
    fused = [Fused(f"smart-{RULE_B}", 0.03), Fused(f"smart-{RULE_A}", 0.02)]

    items = rank_items(fused, chunks, top_k=5)

    assert [(item.rank, item.chunk_id, item.score) for item in items] == [
        (1, f"smart-{RULE_B}", 0.03),
        (2, f"smart-{RULE_A}", 0.02),
    ]
    assert [item.rank for item in rank_items(fused, chunks, top_k=1)] == [1]


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


def test_story_2_3_a_chunk_found_by_one_side_only_is_still_returned(
    client: TestClient, index: MemoryIndex
) -> None:
    index.vector_order, index.text_order = [RULE_C], [RULE_D]

    result = found(search(client))

    # Equal scores, one from each side: in the order of their chunk ids.
    assert [item.chunk_id for item in result.items] == [
        f"smart-{RULE_C}",
        f"smart-{RULE_D}",
    ]
    assert [item.score for item in result.items] == pytest.approx([1 / 61, 1 / 61])


def test_story_2_3_the_same_query_twice_gives_the_same_items_scores_and_order(
    client: TestClient,
) -> None:
    first, second = found(search(client)), found(search(client))

    assert first.items == second.items


@pytest.mark.parametrize(("top_k", "expected"), [(1, 1), (5, 4), (MAX_TOP_K, 4)])
def test_story_2_3_top_k_caps_the_items(
    client: TestClient, top_k: int, expected: int
) -> None:
    result = found(search(client, top_k=top_k))

    assert len(result.items) == expected
    assert [item.rank for item in result.items] == list(range(1, expected + 1))


def test_story_2_3_each_side_is_asked_for_at_least_twice_top_k_and_never_under_the_setting(
    settings: Settings, model: StubModel
) -> None:
    rules = [f"UW-ZZ-{number:03d}" for number in range(1, 9)]
    index = MemoryIndex(
        records=[chunk_record(rule, "Text.", axis(1)) for rule in rules],
        vector_order=rules,
    )
    with service_with(settings, model, index, candidate_depth=11) as client:
        by_default = found(client.post("/searches", json=_body()))
        deeper = found(client.post("/searches", json=_body(top_k=6)))

    assert DEFAULT_TOP_K == 5
    # The setting while it is the deeper, then twice the items asked for.
    assert [call[-1] for call in index.asked] == [11, 11, 12, 12]
    assert (len(by_default.items), len(deeper.items)) == (5, 6)
    # With the default setting the largest `top_k` still gets a deeper list.
    assert candidate_depth(MAX_TOP_K, SearchOptions()) == 2 * MAX_TOP_K
    assert candidate_depth(DEFAULT_TOP_K, SearchOptions()) == 50


def _body(**changes: Any) -> dict[str, Any]:
    return {"query": QUERY, "retriever_config": "r3", **changes}


@pytest.mark.parametrize("top_k", [0, -1, MAX_TOP_K + 1, "many", 1.5, None])
def test_story_2_3_top_k_outside_its_bounds_is_refused(
    client: TestClient, index: MemoryIndex, model: StubModel, top_k: Any
) -> None:
    response = search(client, top_k=top_k)

    assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
    assert model.calls == 0 and index.asked == []


@pytest.mark.parametrize(
    "query",
    ["", "   ", "\n\t", None, 7, "urate\x00", "a" * (MAX_QUERY_CHARS + 1)],
)
def test_story_2_3_a_blank_or_overlong_query_is_refused(
    client: TestClient, model: StubModel, query: Any
) -> None:
    response = search(client, query=query)

    assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
    assert model.calls == 0


def test_story_2_3_a_query_the_text_search_keeps_no_word_of_gets_the_vector_sides_results(
    client: TestClient, index: MemoryIndex
) -> None:
    # What the full-text side answers for a query of stop words: nothing.
    index.text_order = []

    result = found(search(client, query="the and of it"))

    assert [item.rule_ids for item in result.items] == [[RULE_A], [RULE_B], [RULE_C]]
    assert [item.score for item in result.items] == pytest.approx(
        [1 / 61, 1 / 62, 1 / 63]
    )


def test_story_2_3_an_unknown_row_is_refused_by_validation_with_a_plain_message(
    client: TestClient, model: StubModel
) -> None:
    for config in ("r9", "R3", "", None):
        response = search(client, retriever_config=config)

        assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
        assert response.json()["error"]["message"] == "The request is not valid."
    assert model.calls == 0
    # Nothing but the three fields is taken.
    assert error_of(search(client, rerank=True)) == (422, ErrorCode.VALIDATION_FAILED)


@pytest.mark.parametrize("config", NOT_BUILT)
def test_story_2_3_a_row_not_built_is_refused_with_its_own_code_before_anything_is_spent(
    client: TestClient, index: MemoryIndex, model: StubModel, config: str
) -> None:
    response = search(client, retriever_config=config)

    # Its own code, distinct from an unknown row's, and no 5xx.
    assert error_of(response) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    assert "not available" in response.json()["error"]["message"]
    assert model.calls == 0 and index.asked == []


def test_story_2_3_an_empty_index_answers_no_items(
    client: TestClient, index: MemoryIndex
) -> None:
    index.records = []

    result = found(search(client))

    assert result.items == []
    assert result.retriever_config is RetrieverConfig.R3


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        # The gateway gave up after its retries.
        (ModelUnavailable(), 503, ErrorCode.MODEL_UNAVAILABLE),
        (ModelCallFailed("model_status_404"), 502, ErrorCode.UPSTREAM_UNAVAILABLE),
        (
            ModelAnswerInvalid("embedding_index_invalid"),
            502,
            ErrorCode.INVALID_MODEL_OUTPUT,
        ),
    ],
)
def test_story_2_3_when_the_embedding_fails_there_is_no_partial_result(
    client: TestClient,
    index: MemoryIndex,
    model: StubModel,
    failure: Exception,
    status: int,
    code: ErrorCode,
) -> None:
    model.embed_error = failure

    response = search(client)

    assert error_of(response) == (status, code)
    assert "items" not in response.json()
    # Neither side was searched: the full-text side alone is no answer of `r3`.
    assert index.asked == []
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"dimensions": 1536}, "embedding_wrong_size"),
        ({"vectors_per_call": 0}, "embedding_count_differs"),
    ],
)
def test_story_2_3_a_vector_that_is_not_one_of_the_index_is_refused(
    client: TestClient,
    index: MemoryIndex,
    model: StubModel,
    caplog: pytest.LogCaptureFixture,
    changes: dict[str, int],
    reason: str,
) -> None:
    for name, value in changes.items():
        setattr(model, name, value)

    with caplog.at_level(logging.INFO):
        response = search(client)

    assert error_of(response) == (502, ErrorCode.INVALID_MODEL_OUTPUT)
    assert f"query embedding invalid: reason={reason}" in caplog.text
    assert index.asked == []


@pytest.mark.parametrize(
    ("spoiled", "reason"),
    [
        ({5: float("nan")}, "embedding_not_numbers"),
        ({5: float("inf")}, "embedding_not_numbers"),
        # More than the stored vectors' 4-byte floats hold.
        ({5: FLOAT4_MAX * 10}, "embedding_out_of_range"),
        ({5: -1e39}, "embedding_out_of_range"),
        # No direction at all: equally near every chunk.
        ({1: 0.0}, "embedding_all_zero"),
    ],
)
def test_story_2_3_a_vector_that_cannot_be_compared_is_refused(
    caplog: pytest.LogCaptureFixture, spoiled: dict[int, float], reason: str
) -> None:
    class Spoiled:
        async def embed(self, texts: Any) -> list[list[float]]:
            vector = list(axis(1))
            for position, value in spoiled.items():
                vector[position] = value
            return [vector]

    with pytest.raises(DomainError) as refused, caplog.at_level(logging.ERROR):
        asyncio.run(embed_query(QUERY, Spoiled()))

    assert refused.value.code is ErrorCode.INVALID_MODEL_OUTPUT
    assert f"query embedding invalid: reason={reason}" in caplog.text


def test_story_2_3_the_largest_value_a_stored_vector_holds_is_still_taken() -> None:
    class AtTheEdge:
        async def embed(self, texts: Any) -> list[list[float]]:
            vector = list(axis(1))
            vector[5] = -FLOAT4_MAX
            return [vector]

    assert asyncio.run(embed_query(QUERY, AtTheEdge()))[5] == -FLOAT4_MAX


def test_story_2_3_an_index_that_cannot_be_read_is_upstream_unavailable_without_detail(
    client: TestClient, index: MemoryIndex, caplog: pytest.LogCaptureFixture
) -> None:
    index.fail = True

    with caplog.at_level(logging.INFO):
        searched = search(client)
        read = client.get(f"/rules/{RULE_A}")

    for response in (searched, read):
        assert error_of(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
        assert "StoreDown" not in response.text
    # The reason as a code, never the query.
    assert "search failed: index unavailable: reason=StoreDown" in caplog.text
    assert "rule read failed: index unavailable: reason=StoreDown" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_3_a_full_text_read_that_fails_after_the_vector_read_gives_no_partial_result(
    client: TestClient, index: MemoryIndex
) -> None:
    index.fail_matching = True

    response = search(client)

    # The vector side had answered: its list alone is no answer of `r3`.
    assert [call[0] for call in index.asked] == ["nearest", "matching"]
    assert error_of(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "items" not in response.json()
    # The view of the index was closed again.
    assert (index.opened, index.closed) == (1, 1)


def test_story_2_3_both_lists_are_read_from_one_view_of_the_index(
    client: TestClient, index: MemoryIndex
) -> None:
    found(search(client))

    assert (index.opened, index.closed) == (1, 1)
    assert [call[0] for call in index.asked] == ["nearest", "matching"]


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


def test_story_2_3_a_search_whose_model_does_not_answer_in_time_is_model_unavailable(
    settings: Settings, index: MemoryIndex, caplog: pytest.LogCaptureFixture
) -> None:
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


def test_story_2_3_a_search_whose_index_does_not_answer_in_time_is_upstream_unavailable(
    settings: Settings, model: StubModel, index: MemoryIndex
) -> None:
    slow = NeverReads(index.records, index.vector_order, index.text_order)

    with service_with(settings, model, slow, deadline_seconds=0.05) as client:
        response = search(client)

    assert error_of(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "items" not in response.json()
    # The view was closed when the search was ended.
    assert (slow.opened, slow.closed) == (1, 1)


def test_story_2_3_a_time_out_that_is_not_the_searchs_deadline_is_not_taken_for_it(
    index: MemoryIndex,
) -> None:
    class TimesOut(StubModel):
        async def embed(self, texts: Any) -> list[list[float]]:
            raise TimeoutError

    with pytest.raises(TimeoutError):
        asyncio.run(
            search_rules(
                SearchRequest(query=QUERY, retriever_config=RetrieverConfig.R3),
                ports=SearchPorts(model=TimesOut(), index=index),
                options=SearchOptions(deadline_seconds=None),
            )
        )


def test_story_2_3_a_search_has_a_budget_of_its_own_apart_from_the_ingestion_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings()

    # A few seconds, where the ingestion job's calls may take a minute and
    # be sent again three times.
    assert (
        defaults.search_embedding_timeout_seconds,
        defaults.search_embedding_max_retries,
        defaults.search_deadline_seconds,
    ) == (3.0, 1, 8.0)
    assert (defaults.model_timeout_seconds, defaults.model_max_retries) == (60.0, 3)
    monkeypatch.setenv("RETRIEVAL_SEARCH_EMBEDDING_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("RETRIEVAL_SEARCH_EMBEDDING_MAX_RETRIES", "0")
    monkeypatch.setenv("RETRIEVAL_SEARCH_DEADLINE_SECONDS", "4")
    changed = Settings()
    assert search_options(changed).deadline_seconds == 4.0
    assert (
        changed.search_embedding_timeout_seconds,
        changed.search_embedding_max_retries,
    ) == (1.5, 0)
    with pytest.raises(ValueError, match="RETRIEVAL_SEARCH_DEADLINE_SECONDS"):
        Settings(search_embedding_timeout_seconds=9.0)


# --- The deployment the index was embedded with ---------------------------------------------


def test_story_2_3_a_search_is_refused_when_the_index_was_embedded_with_another_deployment(
    settings: Settings,
    model: StubModel,
    index: MemoryIndex,
    caplog: pytest.LogCaptureFixture,
) -> None:
    index.embedding_deployment = "another-embedding"
    options = {"embedding_deployment": EMBEDDING}

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


def test_story_2_3_an_index_that_records_no_run_has_nothing_to_compare(
    settings: Settings, model: StubModel, index: MemoryIndex
) -> None:
    index.embedding_deployment = None

    with service_with(settings, model, index, embedding_deployment=EMBEDDING) as client:
        assert len(found(search(client)).items) == 4


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


# --- The query's vector, through the real gateway -------------------------------------------


def test_story_2_3_the_query_is_embedded_on_the_one_embedding_deployment_through_the_gateway(
    settings: Settings, index: MemoryIndex, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[dict[str, Any]] = []

    def deployment(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append({"path": request.url.path, **body})
        return httpx2.Response(200, json=embedding_answer(body["input"]))

    gateway = build_query_gateway(settings, httpx2.MockTransport(deployment))
    assert gateway is not None
    with (
        service_with(settings, gateway, index) as client,
        caplog.at_level(logging.INFO),
    ):
        result = found(search(client))

    assert requests == [
        {
            "path": "/openai/v1/embeddings",
            "model": EMBEDDING,
            "input": [QUERY],
            "encoding_format": "float",
        }
    ]
    assert index.asked[0][2] == tuple(vector_for(QUERY))
    assert len(result.items) == 4
    # The gateway's own line: what the call cost, never what it said.
    assert "model call: operation=embed" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_3_an_embedding_deployment_that_keeps_failing_is_model_unavailable(
    settings: Settings, index: MemoryIndex
) -> None:
    calls: list[float | None] = []

    def throttled(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.extensions["timeout"]["read"])
        return httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {}})

    patient = settings.model_copy(update={"search_embedding_max_retries": 2})
    for of, attempts in ((settings, 2), (patient, 3)):
        calls.clear()
        gateway = build_query_gateway(of, httpx2.MockTransport(throttled))
        assert gateway is not None
        with service_with(of, gateway, index) as client:
            response = search(client)

        # The first attempt and the search's own retries (one by default,
        # not the ingestion job's three), then no partial result.
        assert len(calls) == attempts
        assert error_of(response) == (503, ErrorCode.MODEL_UNAVAILABLE)
        assert index.asked == []
    # Each attempt had the search's short time limit, not the job's minute.
    assert set(calls) == {settings.search_embedding_timeout_seconds} == {3.0}


def test_story_2_3_a_service_without_its_model_settings_says_once_that_searches_are_off(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    for missing in ("model_endpoint", "embedding_deployment"):
        assert build_query_gateway(settings.model_copy(update={missing: None})) is None
    without = settings.model_copy(update={"model_endpoint": None})

    # The app as the server builds it. A search is refused before anything
    # is read, so no database is needed here.
    with (
        caplog.at_level(logging.INFO),
        TestClient(create_app(without), raise_server_exceptions=False) as client,
    ):
        first, second = search(client), search(client)

    for response in (first, second):
        assert error_of(response) == (503, ErrorCode.MODEL_UNAVAILABLE)
        assert response.json()["error"]["message"] == (
            "This service is not configured to search the manual."
        )
    start_up = [
        record for record in caplog.records if "searches are off" in record.message
    ]
    refusals = [
        record for record in caplog.records if "search refused" in record.message
    ]
    # Why, once, at start-up; then a warning per refusal, and no error.
    assert [record.getMessage() for record in start_up] == [
        "searches are off: not configured: missing=RETRIEVAL_MODEL_ENDPOINT"
    ]
    assert len(refusals) == 2
    assert {record.levelno for record in start_up + refusals} == {logging.WARNING}
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_story_2_3_a_service_without_a_model_still_reads_rules(
    settings: Settings, index: MemoryIndex
) -> None:
    with service_with(settings, NoEmbeddingModel(), index) as client:
        assert client.get(f"/rules/{RULE_A}").status_code == 200
        assert error_of(search(client)) == (503, ErrorCode.MODEL_UNAVAILABLE)


def test_story_2_3_the_servers_own_wiring_takes_its_search_options_from_the_settings(
    settings: Settings,
) -> None:
    changed = settings.model_copy(
        update={"search_candidate_depth": 75, "search_deadline_seconds": 5.0}
    )

    # A deployment that is never called here.
    transport = httpx2.MockTransport(lambda request: httpx2.Response(500))
    app = create_app(changed, model_transport=transport)

    options = app.state.dependencies.options
    assert (
        options.candidate_depth,
        options.deadline_seconds,
        options.embedding_deployment,
    ) == (75, 5.0, EMBEDDING)
    with TestClient(app):
        # Started and stopped: the gateway and the engine it built are closed.
        pass


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


@pytest.mark.parametrize("config", ["r2", "r3", "r4", "r5", "r6"])
def test_story_2_3_every_row_on_the_smart_set_reads_the_same_chunk(
    client: TestClient, config: str
) -> None:
    plain = client.get(f"/rules/{RULE_A}")
    with_row = client.get(f"/rules/{RULE_A}", params={"retriever_config": config})

    assert with_row.status_code == 200
    assert with_row.json() == plain.json()
    assert chunk_set_to_read(RetrieverConfig(config)) is ChunkSet.SMART


def test_story_2_3_a_rule_read_with_r1_is_not_available_until_the_fixed_set_exists(
    client: TestClient, index: MemoryIndex
) -> None:
    response = client.get(f"/rules/{RULE_A}", params={"retriever_config": "r1"})

    assert error_of(response) == (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    assert "not available" in response.json()["error"]["message"]
    assert index.asked == []


def test_story_2_3_a_rule_read_refuses_a_row_or_a_parameter_it_does_not_know(
    client: TestClient,
) -> None:
    for params in ({"retriever_config": "r9"}, {"chunk_set": "smart"}):
        response = client.get(f"/rules/{RULE_A}", params=params)

        assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)


# --- Settings ---------------------------------------------------------------------------------


def test_story_2_3_each_side_hands_the_fusion_fifty_candidates_unless_set_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().search_candidate_depth == 50
    monkeypatch.setenv("RETRIEVAL_SEARCH_CANDIDATE_DEPTH", "120")
    assert Settings().search_candidate_depth == 120
    # Never shallower than the most items a search may ask for.
    for refused in (MAX_TOP_K - 1, 0, 1001):
        with pytest.raises(ValueError, match="search_candidate_depth"):
            Settings(search_candidate_depth=refused)


def test_story_2_3_the_service_is_told_of_the_embedding_deployment_locally_and_in_azure() -> (
    None
):
    run_file = (REPOSITORY_ROOT / "dapr.yaml").read_text()
    ingest = (REPOSITORY_ROOT / "tools" / "ingest-local.sh").read_text()
    stack = (REPOSITORY_ROOT / "infra" / "demo" / "app" / "locals.tf").read_text()

    # Locally: the stand-in, under the name the chunks were embedded with.
    assert 'RETRIEVAL_MODEL_ENDPOINT: "http://127.0.0.1:5101"' in run_file
    assert 'RETRIEVAL_EMBEDDING_DEPLOYMENT: "local-stand-in-embedding"' in run_file
    assert 'export RETRIEVAL_EMBEDDING_DEPLOYMENT="local-stand-in-embedding"' in ingest
    assert 'RETRIEVAL_SEARCH_CANDIDATE_DEPTH: "50"' in run_file
    # The search's own budget is passed in both places.
    for name in (
        "RETRIEVAL_SEARCH_EMBEDDING_TIMEOUT_SECONDS",
        "RETRIEVAL_SEARCH_EMBEDDING_MAX_RETRIES",
        "RETRIEVAL_SEARCH_DEADLINE_SECONDS",
    ):
        assert f"{name}:" in run_file
        assert f'"{name}"' in stack
    # In Azure the service and the job share one set of settings, so a query
    # is embedded on the deployment the job embedded the chunks with (AD-16).
    assert '"RETRIEVAL_EMBEDDING_DEPLOYMENT"' in stack
    assert '"RETRIEVAL_SEARCH_CANDIDATE_DEPTH"' in stack
