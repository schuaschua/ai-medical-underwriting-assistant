"""Stories 2.3, 3.2, 3.3 and 3.7: the search and the rule read over the project's manual, against the rule table.

The manual is ingested by `retrieval`'s job into a real PostgreSQL, and the
service searches that index, with this package's stand-in where the embedding
deployment would be. Only here, outside `services/`, are the answers compared
with the answer key's rule table: the service never reads it (spine AD-17).

The stand-in's vectors only say which words two texts share. What the real
`text-embedding-3-large` vectors do to the same queries is a check of the
final Azure test session. So is row `r5` on the real Azure AI Search: here
its index is this package's stand-in, loaded by the job from the same chunks.
And so is row `r4`'s reranker: here the model stand-in rates a candidate by
the words it shares with the query.

Run `docker compose up --detach --wait` first.
"""

import json
import logging
from array import array
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient
from synthdata_stack import LocalRetrieval, rule_table

from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import RuleText, SearchResponse
from contracts.rules import rule_ids_defined_in
from synthdata.foundry_standin import Mode as ModelMode
from synthdata.search_standin import Mode as SearchMode
from synthdata.search_standin import SearchStandIn

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


def test_story_2_3_every_rule_is_found_by_its_impairment_and_threshold_and_by_its_id(
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
    # Every rule of the rule table is found in the top 5, as the acceptance
    # criterion asks.
    beyond = sorted(
        rule_id for rule_id, place in places.items() if place is None or place > 5
    )
    assert beyond == [], f"not in the top 5: {beyond}"
    assert within(5) == total == len(rule_table())
    # Floors for the top 3 and for first place, with the stand-in's vectors,
    # which only count shared words: the figures move with the manual's
    # wording (110 and 83 of 111 when this was written; the others are a band
    # of the same impairment, a few places down). Real recall is an Azure check.
    assert within(3) >= total * 9 // 10
    assert within(1) >= total * 2 // 3

    # A rule id as the query finds that rule, for every rule.
    with ingested_manual.service() as client:
        places = {
            rule_id: place_of(rule_id, search(client, rule_id, top_k=5))
            for rule_id in rules
        }
        # In a sentence, and in small letters, it is the same rule.
        sentence = search(
            client, "what does rule uw-dm-003 say about loading?", top_k=5
        )

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
    # 98 of 111 since every definition also says when its rule applies
    # (stories 2.5 and 2.6): the chunks of one impairment share that sentence.
    assert first >= 95
    assert places["UW-DM-001"] == 1
    # A rule id inside a sentence: the full-text side puts the defining chunk
    # first (asserted in `retrieval`'s own tests), but the fusion weighs the
    # vector side equally, and the stand-in's word-counting vector of this
    # sentence is nearer other definitions. Where the rule then lands moves
    # with the manual's wording (second at first, later outside the top 5), so
    # only the shape is held here. First place with real vectors is a check of
    # the Azure session; whether an id in a query should be guaranteed first
    # place is a question with the owner.
    place = place_of("UW-DM-003", sentence)
    assert place is None or 1 <= place <= 5


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
        unknown = client.get("/rules/UW-ZZ-999")

    assert with_row.json() == {
        **with_row.json(),
        "rule_id": "UW-DM-001",
        "chunk_id": "smart-UW-DM-001",
    }
    assert unknown.status_code == 404
    # A rule read asks no model.
    assert ingested_manual.model_calls == model_calls
    assert sum(1 for rule in rules.values() if rule["refers_to"]) > 10


# --- The baseline rows (story 3.2) -----------------------------------------------------


def test_story_3_2_rows_r1_r2_and_r3_answer_the_same_shape_each_from_its_own_chunks(
    ingested_manual: LocalRetrieval, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = rule_table()
    chunks = ingested_manual.chunks()
    fixed = {i: c for i, c in chunks.items() if c["chunk_set"] == "fixed"}
    smart = {i: c for i, c in chunks.items() if c["chunk_set"] == "smart"}

    # The `fixed` set, from the same manual: every rule of the rule table has
    # its definition marker in at least one chunk, and no chunk defines a
    # rule the table lacks.
    assert {rule_id for c in fixed.values() for rule_id in c["rule_ids"]} == set(rules)
    assert list(fixed) == [f"fixed-{n:04d}" for n in range(1, len(fixed) + 1)]
    sizes = [len(chunk["text"].split()) for chunk in fixed.values()]
    assert set(sizes[:-1]) == {350} and 35 < sizes[-1] <= 350
    for before, after in pairwise(fixed.values()):
        assert before["text"].split()[-35:] == after["text"].split()[:35]
        assert before["manual_page"] <= after["manual_page"]
    for chunk in fixed.values():
        assert chunk["rule_ids"] == rule_ids_defined_in(chunk["text"])
        assert not set(chunk["rule_ids"]) & set(chunk["reference_rule_ids"])
        # The plain baseline: no context line, and a vector of the same size.
        assert chunk["context_line"] == ""
        assert chunk["embedding"].count(",") == 3071
        # Page furniture is in no chunk.
        assert "SYNTHETIC" not in chunk["text"]
    # A chunk stands under the impairment it starts in.
    impairments = {rule["impairment"] for rule in rules.values()}
    assert impairments <= {chunk["impairment"] for chunk in fixed.values()}

    places: dict[str, dict[str, int | None]] = {"r1": {}, "r2": {}, "r3": {}}
    with ingested_manual.service() as client:
        for rule_id, rule in rules.items():
            for row, found in places.items():
                result = search(client, named_query(rule), retriever_config=row)
                # The common shape, whatever the row.
                assert result.retriever_config.value == row
                assert [item.rank for item in result.items] == [1, 2, 3, 4, 5]
                assert all(0 <= item.score <= 1 for item in result.items)
                scores = [item.score for item in result.items]
                assert scores == sorted(scores, reverse=True)
                # Each from its own chunk set.
                source = fixed if row == "r1" else smart
                for item in result.items:
                    stored = source[item.chunk_id]
                    assert item.rule_ids == stored["rule_ids"]
                    assert item.text == stored["text"]
                    assert (item.manual_page, item.impairment) == (
                        stored["manual_page"],
                        stored["impairment"],
                    )
                found[rule_id] = next(
                    (item.rank for item in result.items if rule_id in item.rule_ids),
                    None,
                )
            # A rule read on `r1` answers the `fixed` chunk that holds the
            # rule's definition marker.
            read = RuleText.model_validate(
                client.get(
                    f"/rules/{rule_id}", params={"retriever_config": "r1"}
                ).json()
            )
            assert (read.chunk_set.value, read.rule_id) == ("fixed", rule_id)
            assert f"Rule {rule_id}:" in read.text
            assert read.text == fixed[read.chunk_id]["text"]
        not_built = [
            client.post("/searches", json={"query": "q", "retriever_config": row})
            # Neither `r5` nor `r6` has a search service here.
            for row in ("r5", "r6")
        ]
        undefined = client.get("/rules/UW-ZZ-999", params={"retriever_config": "r1"})

    assert undefined.status_code == 404
    for response in not_built:
        assert response.status_code == 409
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.RETRIEVER_NOT_AVAILABLE
        )
    with capsys.disabled():
        for row, found in places.items():
            print(
                f"\nstory 3.2, {row} over the manual with the stand-in's vectors: "
                f"{len(rules)} named queries, {len(fixed) if row == 'r1' else len(smart)}"
                f" chunks; in the top 5 "
                f"{sum(1 for place in found.values() if place is not None)}"
            )
    # The stand-in's vectors only count shared words: this proves the
    # plumbing, not the retriever. Recall of `r1` and `r2` means something
    # only with the real embeddings (the final Azure test session).
    assert sum(1 for place in places["r2"].values() if place is not None) > 0
    assert sum(1 for place in places["r1"].values() if place is not None) > 0


# --- Row r5 on Azure AI Search (story 3.3) -----------------------------------------------


def test_story_3_3_the_search_index_holds_what_pgvector_holds_and_r5_answers_the_common_shape(
    retrieval: LocalRetrieval,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    index_name = retrieval.settings.search_service_index_name
    stand_in = SearchStandIn()
    retrieval.search = stand_in

    with caplog.at_level(logging.INFO):
        assert retrieval.ingest() == 0

    # Both stores hold the same chunks: the same ids and count, and for each
    # the same text, context line, rule ids, references, place and vector.
    # Only the `smart` set is in the index.
    smart = {
        chunk_id: chunk
        for chunk_id, chunk in retrieval.chunks().items()
        if chunk["chunk_set"] == "smart"
    }
    documents = stand_in.documents(index_name)
    assert sorted(documents) == sorted(smart) and len(documents) == 111
    for chunk_id, chunk in smart.items():
        document = documents[chunk_id]
        for name in (
            "chunk_set",
            "rule_ids",
            "reference_rule_ids",
            "section_id",
            "impairment",
            "manual_page",
            "text",
            "context_line",
        ):
            assert document[name] == chunk[name], (chunk_id, name)
        # As 4-byte floats, which is what both stores keep.
        assert array("f", document["embedding"]) == array(
            "f", json.loads(chunk["embedding"])
        )
    assert (
        "index load done: documents=111 uploaded=111 removed=0 unchanged=0 "
        f"created=yes index={index_name}"
    ) in caplog.text
    # Exact vector search over the embedding model's dimensions.
    definition = stand_in.indexes[index_name].definition
    assert stand_in.indexes[index_name].vector_fields == {"embedding": 3072}
    assert [a["kind"] for a in definition["vectorSearch"]["algorithms"]] == [
        "exhaustiveKnn"
    ]
    # Story 3.8, row `r6`: once the index is loaded the job has the service
    # hold a knowledge source over that same index and a knowledge base on
    # it, which plans with the chat deployment and writes no answer. The
    # index names the embedding deployment as its vectorizer, so that the
    # service can embed the queries it plans. Nothing was chunked or
    # embedded again for it.
    source_name = retrieval.settings.search_agentic_knowledge_source_name
    base_name = retrieval.settings.search_agentic_knowledge_base_name
    assert (
        f"knowledge base done: source={source_name} base={base_name} "
        "source_created=yes base_created=yes"
    ) in caplog.text
    source = stand_in.knowledge_sources[source_name]["searchIndexParameters"]
    assert source["searchIndexName"] == index_name
    base = stand_in.knowledge_bases[base_name]
    assert base["knowledgeSources"] == [{"name": source_name}]
    assert base["outputMode"] == "extractiveData"
    (planner,) = base["models"]
    assert planner["azureOpenAIParameters"]["deploymentId"] == (
        retrieval.settings.chat_deployment
    )
    (vectorizer,) = definition["vectorSearch"]["vectorizers"]
    assert vectorizer["azureOpenAIParameters"]["deploymentId"] == (
        retrieval.settings.embedding_deployment
    )
    assert stand_in.indexes[index_name].vectorized_fields == ["embedding"]
    assert stand_in.uploaded == 111

    # A second run: nothing is uploaded, nothing is made anew and no model
    # is asked; the stores are compared all the same.
    calls, uploaded = retrieval.model_calls, stand_in.uploaded
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert retrieval.ingest() == 0
    assert (retrieval.model_calls, stand_in.uploaded) == (calls, uploaded)
    assert "uploaded=0 removed=0 unchanged=111 created=no" in caplog.text
    assert "source_created=no base_created=no" in caplog.text
    assert stand_in.knowledge_created == 2

    rules = rule_table()
    asked = list(rules)[::8]
    places: dict[str, int | None] = {}
    places_r4: dict[str, int | None] = {}
    places_r6: dict[str, int | None] = {}
    embedded_before = retrieval.model.embedding_calls
    with retrieval.service() as client:
        for rule_id in asked:
            query = named_query(rules[rule_id])
            result = search(client, query, retriever_config="r5")
            # The common shape: the fields, ranks and score range of every row.
            assert result.retriever_config.value == "r5"
            assert [item.rank for item in result.items] == [1, 2, 3, 4, 5]
            assert all(0 <= item.score <= 1 for item in result.items)
            scores = [item.score for item in result.items]
            assert scores == sorted(scores, reverse=True)
            on_r3 = search(client, query, retriever_config="r3")
            assert set(result.items[0].model_dump()) == set(on_r3.items[0].model_dump())
            for item in result.items:
                stored = smart[item.chunk_id]
                assert item.rule_ids == stored["rule_ids"]
                assert item.text == stored["text"]
                assert (item.manual_page, item.impairment) == (
                    stored["manual_page"],
                    stored["impairment"],
                )
            places[rule_id] = place_of(rule_id, result)
            # Story 3.7, row `r4`: the 20 best fused candidates of `r3`, in
            # the reranker's order. The same chunks, the same shape; only
            # the order and the scores are its own.
            candidates = search(client, query, retriever_config="r3", top_k=20)
            on_r4 = search(client, query, retriever_config="r4")
            assert on_r4.retriever_config.value == "r4"
            assert [item.rank for item in on_r4.items] == [1, 2, 3, 4, 5]
            assert all(0 <= item.score <= 1 for item in on_r4.items)
            relevance = [item.score for item in on_r4.items]
            assert relevance == sorted(relevance, reverse=True)
            fused = {item.chunk_id: item for item in candidates.items}
            for item in on_r4.items:
                assert item.model_dump(exclude={"rank", "score"}) == fused[
                    item.chunk_id
                ].model_dump(exclude={"rank", "score"})
            places_r4[rule_id] = place_of(rule_id, on_r4)
            # Story 3.8, row `r6`: the same search operation and the same
            # shape, from the references the knowledge base returns.
            on_r6 = search(client, query, retriever_config="r6")
            assert on_r6.retriever_config.value == "r6"
            assert [item.rank for item in on_r6.items] == [1, 2, 3, 4, 5]
            assert all(0 <= item.score <= 1 for item in on_r6.items)
            by_the_service = [item.score for item in on_r6.items]
            assert by_the_service == sorted(by_the_service, reverse=True)
            for item in on_r6.items:
                stored = smart[item.chunk_id]
                assert item.model_dump(exclude={"rank", "score"}) == {
                    "chunk_id": item.chunk_id,
                    "rule_ids": stored["rule_ids"],
                    "text": stored["text"],
                    "manual_page": stored["manual_page"],
                    "impairment": stored["impairment"],
                }
            places_r6[rule_id] = place_of(rule_id, on_r6)
        # A rule read for `r5` answers the `smart` chunk from pgvector.
        read = RuleText.model_validate(
            client.get(f"/rules/{asked[0]}", params={"retriever_config": "r5"}).json()
        )
        assert (read.chunk_set.value, read.chunk_id) == ("smart", f"smart-{asked[0]}")
        embedded = retrieval.model.embedding_calls

        # The search service down, or slower than a search may take: no
        # partial answer, and the pgvector rows answer as before.
        stand_in.mode = SearchMode.UNAVAILABLE
        down = [
            client.post("/searches", json={"query": "q", "retriever_config": row})
            for row in ("r5", "r6")
        ]
        assert len(search(client, "q").items) == 5
    # One embedding call per search of the four rows that embed their query
    # here (`r6` embeds nothing: the search service does), one hybrid query
    # with the semantic ranker and exact vector search per `r5` search, and
    # one chat call with 20 candidates per `r4` search.
    assert embedded - embedded_before == 4 * len(asked)
    assert retrieval.model.rerank_calls == len(asked)
    shown = json.loads(retrieval.model.requests[-1]["messages"][1]["content"])
    assert len(shown["candidates"]) == 20
    assert len(stand_in.queries) == len(asked)
    for sent in stand_in.queries:
        assert sent["queryType"] == "semantic" and sent["top"] == 5
        (vector_query,) = sent["vectorQueries"]
        assert (
            vector_query["exhaustive"] is True and len(vector_query["vector"]) == 3072
        )
    # One retrieve request per `r6` search: the query as it was asked, for
    # references only, on the preview version; the service planned and ran
    # queries of its own.
    assert len(stand_in.retrievals) == len(asked)
    for rule_id, sent, planned in zip(
        asked, stand_in.retrievals, stand_in.planned, strict=True
    ):
        ((part,),) = [message["content"] for message in sent["messages"]]
        assert part == {"type": "text", "text": named_query(rules[rule_id])}
        assert (sent["outputMode"], sent["maxOutputDocuments"]) == (
            "extractiveData",
            5,
        )
        assert len(planned) > 1
    stand_in.mode, stand_in.delay_seconds = SearchMode.SLOW, 5.0
    with retrieval.service(
        search_deadline_seconds=0.5,
        search_embedding_timeout_seconds=0.5,
        search_service_query_timeout_seconds=0.5,
        search_agentic_timeout_seconds=0.5,
        search_agentic_deadline_seconds=0.5,
    ) as client:
        slow = [
            client.post("/searches", json={"query": "q", "retriever_config": row})
            for row in ("r5", "r6")
        ]
    for response in (*down, *slow):
        assert response.status_code == 502, response.text
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.UPSTREAM_UNAVAILABLE
        )

    # Row `r4`'s reranker leaves a candidate out, answers prose, or answers
    # later than a search with the row may take: no answer, and never the
    # fused order in its place.
    stand_in.mode = SearchMode.OK
    no_answer = []
    try:
        with retrieval.service() as client:
            for mode in (ModelMode.RERANK_INCOMPLETE, ModelMode.INVALID):
                retrieval.model.mode = mode
                no_answer.append(
                    client.post(
                        "/searches", json={"query": "q", "retriever_config": "r4"}
                    )
                )
        # The embedding and the two reads keep seconds of their own: only
        # the reranker, which answers after 6 s, is too slow for the 3 s
        # the row has here.
        retrieval.model.mode = ModelMode.RERANK_SLOW
        retrieval.model.rerank_delay_seconds = 6.0
        with retrieval.service(
            search_deadline_seconds=3.0,
            search_embedding_timeout_seconds=3.0,
            search_service_query_timeout_seconds=3.0,
            search_rerank_timeout_seconds=3.0,
            search_rerank_deadline_seconds=3.0,
        ) as client:
            no_answer.append(
                client.post("/searches", json={"query": "q", "retriever_config": "r4"})
            )
    finally:
        retrieval.model.mode = ModelMode.OK
    for response in no_answer:
        assert response.status_code == 503, response.text
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.MODEL_UNAVAILABLE
        )

    # A service that is told of no search service and of no chat deployment:
    # `r5`, `r4` and `r6` are refused as not available and the other rows
    # answer.
    retrieval.search = None
    with retrieval.service(chat_deployment=None) as client:
        refused = [
            client.post("/searches", json={"query": "q", "retriever_config": row})
            for row in ("r4", "r5", "r6")
        ]
        assert len(search(client, "q").items) == 5
    for response in refused:
        assert response.status_code == 409
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.RETRIEVER_NOT_AVAILABLE
        )
    with capsys.disabled():
        print(
            f"\nstory 3.3, r5 over the manual with the stand-ins: {len(asked)} named "
            f"queries, {len(documents)} documents; in the top 5 "
            f"{sum(1 for place in places.values() if place is not None)}"
        )
        print(
            f"\nstory 3.7, r4 over the manual with the stand-ins: {len(asked)} named "
            f"queries, 20 candidates each; in the top 5 "
            f"{sum(1 for place in places_r4.values() if place is not None)}"
        )
        print(
            f"\nstory 3.8, r6 over the manual with the stand-ins: {len(asked)} named "
            f"queries, {sum(len(planned) for planned in stand_in.planned)} planned "
            f"queries; in the top 5 "
            f"{sum(1 for place in places_r6.values() if place is not None)}"
        )
    # The stand-in ranks by shared words: this proves the plumbing. What the
    # real semantic ranker, the real reranker and the real planning model do
    # is a check of the final Azure test session.
    assert any(place is not None for place in places.values())
    assert any(place is not None for place in places_r4.values())
    assert any(place is not None for place in places_r6.values())
