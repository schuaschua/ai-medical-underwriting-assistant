"""Story 3.4: the pure parts of the bake-off runner: what is searched, what counts, who wins."""

from pathlib import Path
from typing import Any

import pytest
from bakeoff_fakes import key_entry
from pydantic import ValidationError

from bakeoff.answer_key import AnswerKeyEntry, ExpectedVerdict
from bakeoff.recall import fact_searches, is_hit, percentile
from bakeoff.redaction import RedactionCheck, leaked_categories
from bakeoff.scoreboard import pick_winner
from bakeoff.settings import PUBLISHED_SCOREBOARDS, SCRATCH, Settings
from bakeoff.static_metrics import read_static_metrics
from bakeoff.verdicts import decision_for, run_is_right
from contracts.enums import Decision, PageStatus, RetrieverConfig
from contracts.models.retrieval import SearchResponse
from contracts.models.verdict import SUGGESTION_LABEL, VerdictRun
from contracts.models.web import RetrievalRowScore
from contracts.query import build_fact_query
from retrieval.domain.rows import ROWS

RUN_ID = "0199b7a0-0000-7000-8000-000000000005"
CASE_ID = "0199b7a0-0000-7000-8000-000000000001"


def answered(*rule_ids: str) -> SearchResponse:
    """A search answer whose items define these rules, one each, in this order."""
    return SearchResponse.model_validate(
        {
            "retriever_config": "r3",
            "latency_ms": 12,
            "items": [
                {
                    "chunk_id": f"smart-{rule_id}-{rank}",
                    "rule_ids": [rule_id],
                    "rank": rank,
                    "score": 0.5,
                    "text": "A made-up rule.",
                    "manual_page": 1,
                    "impairment": "Made-up impairment",
                }
                for rank, rule_id in enumerate(rule_ids, start=1)
            ],
        }
    )


def test_story_3_4_recall_searches_each_fact_that_meets_a_rule_with_the_built_query_and_counts_a_hit_in_the_top_5() -> (
    None
):
    entry = AnswerKeyEntry.model_validate(
        key_entry(
            "case-901",
            facts=(
                ("Type 2 diabetes mellitus:   HbA1c 7.4 %", ("UW-DM-002",)),
                # A fact that meets no rule: no search, and in no denominator.
                ("body mass index 23.5 kg/m2", ()),
                ("LDL cholesterol 4.2 mmol/L", ("UW-LDL-001", "UW-LDL-002")),
            ),
        )
    )

    first, second = fact_searches([entry])

    # The query is the contracts' builder's and nothing else, per fact.
    assert (first.case_key, first.fact_number, first.query) == (
        "case-901",
        1,
        build_fact_query("Type 2 diabetes mellitus:   HbA1c 7.4 %"),
    )
    assert first.query == "Type 2 diabetes mellitus: HbA1c 7.4 %"
    assert (second.fact_number, second.query) == (3, "LDL cholesterol 4.2 mmol/L")
    other = ["UW-HT-001", "UW-HT-002", "UW-HT-003", "UW-HT-004"]
    # An expected rule among the first five answered items is a hit, whatever its place.
    assert is_hit(first, answered(*other, "UW-DM-002"), top_k=5)
    assert not is_hit(first, answered(*other, "UW-HT-005", "UW-DM-002"), top_k=5)
    assert not is_hit(first, answered(*other), top_k=5)
    assert not is_hit(first, answered(), top_k=5)
    # Any one of a fact's expected rules is enough.
    assert is_hit(second, answered("UW-HT-001", "UW-LDL-002"), top_k=5)
    # Latency: the median and the 95th percentile of the searches that answered.
    assert (percentile([40, 10, 30, 20], 0.5), percentile([40, 10, 30, 20], 0.95)) == (
        20,
        40,
    )
    assert percentile([], 0.5) is None


def run_of(verdict: str | None, loading_pct: int | None = None) -> VerdictRun:
    done = verdict is not None
    return VerdictRun.model_validate(
        {
            "verdict_run_id": RUN_ID,
            "case_id": CASE_ID,
            "retriever_config": "r3",
            "status": "done" if done else "failed",
            "label": SUGGESTION_LABEL,
            "verdict": verdict,
            "loading_pct": loading_pct,
            "confidence": 0.9 if done else None,
            "reasons": [],
            "system_reasons": ["no_matching_rule"] if verdict == "refer" else [],
            "error_code": None if done else "stage_failed",
        }
    )


def expected(verdict: str, loading_pct: int | None = None) -> ExpectedVerdict:
    return ExpectedVerdict.model_validate(
        {"verdict": verdict, "loading_pct": loading_pct}
    )


def test_story_3_4_a_run_is_right_on_the_expected_verdict_and_for_a_loaded_case_the_expected_loading() -> (
    None
):
    loaded, refer = expected("loaded", 75), expected("refer")

    assert run_is_right(run_of("loaded", 75), loaded)
    assert not run_is_right(run_of("loaded", 50), loaded)
    assert not run_is_right(run_of("decline"), loaded)
    assert run_is_right(run_of("refer"), refer)
    assert run_is_right(run_of("standard"), expected("standard"))
    # A run that failed, and a row that left no run, are wrong.
    assert not run_is_right(run_of(None), refer)
    assert not run_is_right(None, refer)


def test_story_3_4_a_human_wait_is_answered_from_the_page_label_and_nothing_else() -> (
    None
):
    # The customer's wait: kept if medical, discarded if not.
    assert decision_for(PageStatus.AWAITING_CUSTOMER, True) is Decision.KEEP
    assert decision_for(PageStatus.AWAITING_CUSTOMER, False) is Decision.DISCARD
    # Triage: accepted if medical, denied if not.
    assert decision_for(PageStatus.AWAITING_TRIAGE, True) is Decision.ACCEPT
    assert decision_for(PageStatus.AWAITING_TRIAGE, False) is Decision.DENY
    # Every other page takes no decision from the runner.
    for status in set(PageStatus) - {
        PageStatus.AWAITING_CUSTOMER,
        PageStatus.AWAITING_TRIAGE,
    }:
        assert decision_for(status, True) is None
        assert decision_for(status, False) is None


def scored(
    config: str, right: int, hits: int, latency: int | None
) -> RetrievalRowScore:
    return RetrievalRowScore.model_validate(
        {
            "retriever_config": config,
            "store": "pgvector",
            "chunk_set": "smart",
            "method": "Made up",
            "measured": True,
            "rule_recall": hits / 4,
            "recall_hits": hits,
            "recall_searches": 4,
            "verdict_accuracy": right / 4,
            "right_runs": right,
            "cases": 4,
            "failed_runs": 0,
            "latency_ms_median": latency,
            "latency_ms_p95": latency,
            "latency_searches": 4 if latency is not None else 0,
            "cost": None,
            "effort": None,
        }
    )


def test_story_3_4_the_winner_is_by_verdict_accuracy_then_rule_recall_then_lower_latency() -> (
    None
):
    not_measured = RetrievalRowScore.model_validate(
        {
            **dict.fromkeys(RetrievalRowScore.model_fields),
            "retriever_config": "r6",
            "store": "Azure AI Search",
            "chunk_set": "smart",
            "method": "Made up",
            "measured": False,
        }
    )

    # Accuracy first, whatever the recall and the latency.
    assert pick_winner([scored("r1", 2, 4, 5), scored("r2", 3, 1, 90)]) == "r2"
    # Two rows tie on accuracy: the higher recall wins.
    assert pick_winner([scored("r1", 3, 2, 5), scored("r2", 3, 3, 90)]) == "r2"
    # They tie on recall too: the lower latency wins, and a row that timed
    # no search loses to one that did.
    assert pick_winner([scored("r1", 3, 3, 40), scored("r3", 3, 3, 15)]) == "r3"
    assert pick_winner([scored("r1", 3, 3, None), scored("r3", 3, 3, 15)]) == "r3"
    # A row that was not measured never wins, and with none measured nobody does.
    assert pick_winner([not_measured, scored("r1", 0, 0, 500)]) == "r1"
    assert pick_winner([not_measured]) is None


def test_story_3_4_a_planted_identifier_or_a_part_of_a_planted_name_in_normalised_text_is_a_leak() -> (
    None
):
    entry = AnswerKeyEntry.model_validate(
        key_entry(
            "case-901",
            identifiers=(
                ("person_name", "Avery Lee"),
                ("phone_number", "(303) 555-0142"),
                ("policy_number", "POL-SYN-0001234"),
            ),
            may_also_be_redacted=("Avery", "Lee", "Samplestead", "Graphic designer"),
        )
    )
    clean = (
        "Applicant: [Person]\nTelephone [PhoneNumber]\nSleep apnoea, town Samplestead"
    )

    # A surname inside another word is not the surname.
    assert leaked_categories(entry, clean) == set()
    # A part of a planted name is a leak, in any case and across PDF artefacts.
    assert leaked_categories(entry, "Signed: A. LEE, applicant") == {"person_name"}
    assert leaked_categories(entry, "Dear Av­ery,") == {"person_name"}
    # A planted identifier is found whatever the spacing and the line breaks.
    assert leaked_categories(entry, "Tel (303)\n555-0142\npol-syn-0001234") == {
        "phone_number",
        "policy_number",
    }

    check = RedactionCheck()
    check.add(entry, {1: clean, 2: "", 3: "Policy POL-SYN-0001234 of [Person]"})
    check.add(entry, {})
    # The report holds the place and the category of a leak, never the value.
    assert [leak.model_dump() for leak in check.leaks] == [
        {"case_key": "case-901", "page_number": 3, "category": "policy_number"}
    ]
    assert (check.cases_checked, check.pages_checked, check.identifiers_checked) == (
        1,
        3,
        3,
    )
    # Of the strings that may also be redacted, three are in no page text any more.
    assert (check.may_also_be_redacted, check.may_also_be_redacted_masked) == (4, 3)
    assert check.cases_not_checked == ["case-901"]


def test_story_3_4_static_metrics_describe_every_row_as_retrieval_builds_it_and_source_every_figure(
    tmp_path: Path,
) -> None:
    static = read_static_metrics(Settings().static_metrics_file)

    assert set(static.rows) == set(RetrieverConfig)
    for config, facts in static.rows.items():
        # What the scoreboard prints beside a row is what `retrieval` builds.
        assert facts.chunk_set is ROWS[config].chunk_set, config
        assert (facts.store == "Azure AI Search") is ROWS[config].needs_search_service
        assert facts.store in {"pgvector", "Azure AI Search"}
        for figure in (facts.cost, facts.effort):
            assert figure is None or (figure.source.strip() and figure.unit.strip())
        assert facts.effort is not None, config
    # A figure without its source, and a row left out, are refused.
    row: dict[str, Any] = {"store": "pgvector", "chunk_set": "smart", "method": "Any"}
    stated = {"amount": "1.50", "unit": "USD per 1,000 searches"}
    for rows in (
        {config.value: row for config in list(RetrieverConfig)[:5]},
        {config.value: {**row, "cost": stated} for config in RetrieverConfig},
    ):
        broken = tmp_path / "static-metrics.yaml"
        broken.write_text(str({"rows": rows}), encoding="utf-8")
        with pytest.raises(ValidationError):
            read_static_metrics(broken)


def test_story_3_4_only_a_run_against_the_deployed_environment_may_write_the_published_scoreboards() -> (
    None
):
    # A local run writes under the scratch folder.
    assert Settings().scoreboard_dir == SCRATCH / "scoreboards"
    assert Settings().case_concurrency == 2
    deployed = Settings(web_address="https://web.example.test/", deployed=True)
    assert deployed.scoreboard_dir == PUBLISHED_SCOREBOARDS
    assert deployed.web_address == "https://web.example.test"
    refusals: tuple[dict[str, Any], ...] = (
        # Stand-in figures are never published.
        {"output_dir": PUBLISHED_SCOREBOARDS},
        {"output_dir": PUBLISHED_SCOREBOARDS / "local"},
        # This machine is not the deployed environment.
        {"deployed": True},
        {"web_address": "http://127.0.0.1:8000", "deployed": True},
        # The case documents are not sent in the clear to another machine.
        {"web_address": "http://web.example.test"},
        {"web_address": "https://web.example.test/api"},
        {"case_concurrency": 0},
        # More results than a search gives, and a list of no rows.
        {"top_k": 51},
        {"rows": []},
        # A part of the bake-off never replaces the published whole.
        {"web_address": "https://web.example.test", "deployed": True, "rows": ["r3"]},
    )
    for refused in refusals:
        with pytest.raises(ValidationError):
            Settings(**refused)
