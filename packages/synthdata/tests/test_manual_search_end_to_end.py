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

from typing import Any

import pytest
from fastapi.testclient import TestClient
from synthdata_stack import LocalRetrieval, rule_table

from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import RuleText, SearchResponse

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
    # First on the full-text side. Since every definition says when its rule
    # applies, the stand-in's vector of this sentence shares more words with
    # another definition, which the fusion then puts ahead: second, as for
    # the few bare ids above. (First with the real vectors is a check of the
    # Azure session.)
    place = place_of("UW-DM-003", sentence)
    assert place is not None and place <= 2


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
