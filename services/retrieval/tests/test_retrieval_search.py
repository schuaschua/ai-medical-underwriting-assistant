"""Story 2.3: the search and the rule read, on fakes.

No database, no model and no network: the index is a list a test writes, and
the gateway a stub. The two statements themselves are tested against
PostgreSQL in `test_retrieval_search_integration.py`.
"""

import asyncio
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
    fixed_record,
    vector_for,
)

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.models.retrieval import (
    MAX_TOP_K,
    RuleText,
    SearchResponse,
)
from retrieval.adapters.http import routes
from retrieval.adapters.http.app import (
    create_app,
)
from retrieval.adapters.http.routes import Dependencies
from retrieval.domain.fusion import RRF_K, Fused, reciprocal_rank_fusion
from retrieval.domain.rows import (
    row_to_search,
)
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


@pytest.mark.parametrize("config", ["r4"])
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


@pytest.mark.parametrize("top_k", [MAX_TOP_K + 1])
def test_story_2_3_top_k_outside_its_bounds_is_refused(
    client: TestClient, index: MemoryIndex, model: StubModel, top_k: Any
) -> None:
    response = search(client, top_k=top_k)

    assert error_of(response) == (422, ErrorCode.VALIDATION_FAILED)
    assert model.calls == 0 and index.asked == []


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
