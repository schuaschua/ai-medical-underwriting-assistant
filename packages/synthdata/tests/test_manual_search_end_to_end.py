"""Story 2.3: the search and the rule read over the project's manual, against the rule table.

The manual is ingested by `retrieval`'s job into a real PostgreSQL, and the
service searches that index, with this package's stand-in where the embedding
deployment would be. Only here, outside `services/`, are the answers compared
with the answer key's rule table: the service never reads it (spine AD-17).

The stand-in's vectors only say which words two texts share. What the real
`text-embedding-3-large` vectors do to the same queries is a check of the
final Azure test session.

Run `docker compose up --detach --wait` first.
"""

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from synthdata_stack import LocalRetrieval, rule_table

from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import RuleText, SearchResponse
from synthdata.foundry_standin import LOCAL_EMBEDDING_DEPLOYMENT, embed_text
from synthdata.foundry_standin import Mode as ModelMode

pytestmark = pytest.mark.integration


def named_query(rule: dict[str, Any]) -> str:
    """A query that names a rule as a fact would: its impairment and its threshold."""
    return f"{rule['impairment']}: {rule['threshold']['words']}"


def search(client: TestClient, query: str, **body: Any) -> SearchResponse:
    response = client.post(
        "/searches", json={"query": query, "retriever_config": "r3", **body}
    )
    assert response.status_code == 200, response.text
    return SearchResponse.model_validate(response.json())


def place_of(rule_id: str, result: SearchResponse) -> int | None:
    """The rank at which a search answered the chunk that defines the rule."""
    for item in result.items:
        if item.rule_ids == [rule_id]:
            return item.rank
    return None


def test_story_2_3_every_rule_named_by_impairment_and_threshold_is_in_the_top_five(
    ingested_manual: LocalRetrieval, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = rule_table()
    places: dict[str, int | None] = {}

    with ingested_manual.service() as client:
        for rule_id, rule in rules.items():
            result = search(client, named_query(rule), top_k=5)
            places[rule_id] = place_of(rule_id, result)
            assert len(result.items) == 5
            assert [item.rank for item in result.items] == [1, 2, 3, 4, 5]
            assert all(0 < item.score <= 2 / 61 for item in result.items)

    def within(rank: int) -> int:
        return sum(
            1 for place in places.values() if place is not None and place <= rank
        )

    total = len(rules)
    with capsys.disabled():
        print(
            f"\nstory 2.3, r3 over the manual with the stand-in's vectors: "
            f"{total} named queries; first {within(1)}, in the top 3 {within(3)}, "
            f"in the top 5 {within(5)}"
        )
    # Every rule of the rule table is found in the top 3, and so in the top
    # 5 the acceptance criterion asks for.
    beyond = sorted(
        rule_id for rule_id, place in places.items() if place is None or place > 3
    )
    assert beyond == [], f"not in the top 3: {beyond}"
    assert within(3) == within(5) == total == len(rule_table())
    # The floor for first place, with the stand-in's vectors: three in four.
    # (85 of 111 when this was written; the others are a band of the same
    # impairment, one or two places down.)
    assert within(1) >= total * 3 // 4


def test_story_2_3_a_rule_id_as_the_query_finds_that_rule_for_every_rule(
    ingested_manual: LocalRetrieval, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = rule_table()

    with ingested_manual.service() as client:
        places = {
            rule_id: place_of(rule_id, search(client, rule_id, top_k=5))
            for rule_id in rules
        }
        # In a sentence, and in small letters, it is the same rule.
        sentence = search(client, "what does rule uw-dm-003 say about loading?")

    first = sum(1 for place in places.values() if place == 1)
    with capsys.disabled():
        print(
            f"\nstory 2.3, r3 over the manual with the stand-in's vectors: "
            f"{len(rules)} rule ids as queries; first {first}, "
            f"in the top 5 {sum(1 for place in places.values() if place)}"
        )
    # The full-text side puts the chunk that defines the rule first. The
    # fusion weighs the vector side as much, and the stand-in's vector of a
    # bare id is nearest the shortest chunk that prints it, which for a few
    # rules is one that refers to the rule: those come second or third.
    assert [rule_id for rule_id, place in places.items() if place is None] == []
    assert first >= 100
    assert places["UW-DM-001"] == 1
    assert sentence.items[0].chunk_id == "smart-UW-DM-003"


def test_story_2_3_a_search_answers_the_chunk_as_the_manual_prints_it(
    ingested_manual: LocalRetrieval,
) -> None:
    rules = rule_table()
    rule = rules["UW-DM-002"]
    chunks = ingested_manual.chunks()
    embeddings_before = ingested_manual.model.embedding_calls

    with ingested_manual.service() as client:
        result = search(client, named_query(rule))
        again = search(client, named_query(rule))

    assert result.retriever_config.value == "r3"
    assert result.latency_ms >= 0
    item = next(item for item in result.items if item.rule_ids == ["UW-DM-002"])
    stored = chunks["smart-UW-DM-002"]
    assert item.chunk_id == "smart-UW-DM-002"
    assert item.text == stored["text"]
    assert (item.manual_page, item.impairment) == (
        rule["manual_page"],
        rule["impairment"],
    )
    # Only the rule the chunk defines, never the ones it refers to (AD-12).
    assert rule["refers_to"] and not set(rule["refers_to"]) & set(item.rule_ids)
    # The same query on the same index: the same items, order and scores.
    assert again.items == result.items
    # Each search embedded its query once, on the deployment the chunks were
    # embedded with, and exactly as it was asked.
    requests = ingested_manual.model.embedding_requests[embeddings_before:]
    assert (
        requests
        == [
            {
                "model": LOCAL_EMBEDDING_DEPLOYMENT,
                "input": [named_query(rule)],
                "encoding_format": "float",
            }
        ]
        * 2
    )
    assert {chunk["chunk_id"] for chunk in chunks.values()} >= {
        item.chunk_id for item in result.items
    }
    # A search writes nothing.
    assert ingested_manual.chunks() == chunks


def test_story_2_3_a_chunk_that_only_one_side_finds_is_still_answered(
    ingested_manual: LocalRetrieval,
) -> None:
    # No word of this query is in the manual's rules, so the full-text side
    # finds nothing; the stand-in still gives the query a vector, and the
    # vector side answers its nearest chunks.
    words = "zzyzx quorvel blixt"

    with ingested_manual.service() as client:
        by_vector_only = search(client, words)
        by_stop_words = search(client, "the and of it")

    for result in (by_vector_only, by_stop_words):
        assert len(result.items) == 5
        # One side's share each: 1/61, 1/62, ...
        assert [item.score for item in result.items] == pytest.approx(
            [1 / (60 + rank) for rank in range(1, 6)]
        )
    assert len(embed_text(words)) == 3072


def test_story_2_3_every_rule_is_read_by_its_id_with_the_references_of_the_rule_table(
    ingested_manual: LocalRetrieval,
) -> None:
    rules = rule_table()
    chunks = ingested_manual.chunks()
    model_calls = ingested_manual.model_calls

    with ingested_manual.service() as client:
        for rule_id, rule in rules.items():
            response = client.get(f"/rules/{rule_id}")
            assert response.status_code == 200, rule_id
            read = RuleText.model_validate(response.json())
            assert read.rule_id == rule_id
            assert (read.chunk_id, read.chunk_set.value) == (
                f"smart-{rule_id}",
                "smart",
            )
            assert read.text == chunks[f"smart-{rule_id}"]["text"]
            assert read.text.startswith(f"Rule {rule_id}: {rule['impairment']}")
            assert (read.manual_page, read.impairment) == (
                rule["manual_page"],
                rule["impairment"],
            )
            # The rules it refers to are those of the rule table, in the
            # order the definition mentions them (AD-15 follows these).
            assert read.reference_rule_ids == rule["refers_to"]
            assert read.reference_rule_ids == [
                reference["rule_id"] for reference in rule["references"]
            ]
        with_row = client.get("/rules/UW-DM-001", params={"retriever_config": "r3"})
        fixed = client.get("/rules/UW-DM-001", params={"retriever_config": "r1"})
        unknown = client.get("/rules/UW-ZZ-999")

    assert with_row.json() == {
        **with_row.json(),
        "rule_id": "UW-DM-001",
        "chunk_id": "smart-UW-DM-001",
    }
    assert (fixed.status_code, unknown.status_code) == (409, 404)
    assert ErrorBody.model_validate(fixed.json()).error.code is (
        ErrorCode.RETRIEVER_NOT_AVAILABLE
    )
    # A rule read asks no model.
    assert ingested_manual.model_calls == model_calls
    assert sum(1 for rule in rules.values() if rule["refers_to"]) > 10


def test_story_2_3_when_the_embedding_deployment_is_throttled_a_search_is_model_unavailable(
    ingested_manual: LocalRetrieval, caplog: pytest.LogCaptureFixture
) -> None:
    before = ingested_manual.model.embedding_calls
    mode = ingested_manual.model.mode
    ingested_manual.model.mode = ModelMode.THROTTLED

    try:
        with ingested_manual.service() as client, caplog.at_level(logging.INFO):
            response = client.post(
                "/searches",
                json={"query": "HbA1c below 7.0 %", "retriever_config": "r3"},
            )
            not_built = client.post(
                "/searches",
                json={"query": "HbA1c below 7.0 %", "retriever_config": "r4"},
            )
    finally:
        ingested_manual.model.mode = mode

    # The first attempt and the search's one retry (its own budget, not the
    # ingestion job's three), then no partial result.
    assert ingested_manual.model.embedding_calls == before + 2
    assert response.status_code == 503
    assert ErrorBody.model_validate(response.json()).error.code is (
        ErrorCode.MODEL_UNAVAILABLE
    )
    assert "items" not in response.json()
    # A row that is not built is refused before the model is asked.
    assert not_built.status_code == 409
    assert ErrorBody.model_validate(not_built.json()).error.code is (
        ErrorCode.RETRIEVER_NOT_AVAILABLE
    )
    assert ingested_manual.model.embedding_calls == before + 2
    # No word of the query in the log.
    assert "HbA1c" not in caplog.text


def test_story_2_3_the_service_as_the_server_builds_it_searches_with_its_settings(
    ingested_manual: LocalRetrieval,
) -> None:
    rule = rule_table()["UW-DM-003"]

    with ingested_manual.service(
        search_candidate_depth=75, search_deadline_seconds=6.0
    ) as client:
        options = client.app.state.dependencies.options  # type: ignore[attr-defined]  # the app is FastAPI's
        ready = client.get("/ready")
        result = search(client, named_query(rule))

    # `create_app(settings)` itself: the settings reach the search's options,
    # and the deployment it embeds with is the one the chunks were embedded with.
    assert (options.candidate_depth, options.deadline_seconds) == (75, 6.0)
    assert options.embedding_deployment == LOCAL_EMBEDDING_DEPLOYMENT
    assert ready.status_code == 200
    assert place_of("UW-DM-003", result) is not None


def test_story_2_3_a_service_set_to_another_embedding_deployment_than_the_index_refuses_to_search(
    ingested_manual: LocalRetrieval,
) -> None:
    before = ingested_manual.model.embedding_calls

    with ingested_manual.service(embedding_deployment="another-embedding") as client:
        response = client.post(
            "/searches", json={"query": "HbA1c below 7.0 %", "retriever_config": "r3"}
        )
        read = client.get("/rules/UW-DM-001")

    assert response.status_code == 503
    assert ErrorBody.model_validate(response.json()).error.code is (
        ErrorCode.MODEL_UNAVAILABLE
    )
    assert "another embedding model" in response.json()["error"]["message"]
    assert read.status_code == 200
    assert ingested_manual.model.embedding_calls == before + 1
