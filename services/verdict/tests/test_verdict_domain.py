"""Stories 2.5, 2.6, 3.2 and 3.8: a verdict run, the three tools and the rules that decide what is stored, on fakes.

Unit tests of the domain: no database, no other service and no model. The
agent is a stub whose script names the tool calls it makes and the answer it
gives (coding-style rule 23).
"""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from verdict_fakes import (
    ACTOR,
    DIABETES,
    DM_25,
    DM_50,
    DM_DECLINE,
    HT_50,
    HYPERTENSION,
    NOT_IN_MANUAL,
    PD_NONE,
    TOB_25,
    TRACE_ID,
    FakeFacts,
    FakeRules,
    MemoryRepository,
    StubAgent,
    fact,
    final_answer,
    reason,
    rule_text,
)

from contracts.audit import AuditAction
from contracts.enums import (
    ActorKind,
    ChunkSet,
    ReasonEffect,
    RetrieverConfig,
    StageStatus,
    StepOutcome,
    SystemReason,
    ToolName,
    Verdict,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import is_uuid7, new_id
from contracts.models.extraction import Fact
from contracts.models.retrieval import RuleText
from contracts.models.verdict import (
    SUGGESTION_LABEL,
    Reason,
    VerdictRunCommand,
    VerdictRunResult,
)
from verdict.domain.decide import rules_conflict
from verdict.domain.effects import rating_in
from verdict.domain.entities import Rating
from verdict.domain.run import (
    RunOptions,
    RunPorts,
    run_verdict,
)
from verdict.domain.state import RunState
from verdict.domain.toolbox import Toolbox

SECRETS = ("SECRET", "HbA1c", "7.4", "Probable rating", "blood pressure query")

Call = tuple[str, dict[str, Any]]


def agent_that(*calls: Call, answers: str | None = None) -> StubAgent:
    """An agent that makes these tool calls in order and then gives this answer."""
    stub = StubAgent()

    async def script(toolbox: Toolbox) -> str | None:
        for tool, arguments in calls:
            stub.answers.append(await toolbox.call(tool, arguments))
        return answers

    stub.script = script
    return stub


def search(fact_id: str, query: str = "blood pressure query") -> Call:
    return ("search_rules", {"query": query, "fact_id": fact_id})


def read(rule_id: str) -> Call:
    return ("read_rule", {"rule_id": rule_id})


LIST: Call = ("list_facts", {})


def suggest(
    case_id: str,
    ports: RunPorts,
    options: RunOptions,
    now: datetime,
    *,
    retriever_config: RetrieverConfig = RetrieverConfig.R3,
    trace_id: str | None = TRACE_ID,
    eval_run_id: str | None = None,
) -> VerdictRunResult:
    return asyncio.run(
        run_verdict(
            VerdictRunCommand(
                case_id=case_id,
                retriever_config=retriever_config,
                eval_run_id=eval_run_id,
            ),
            ports=ports,
            options=options,
            trace_id=trace_id,
            now=lambda: now,
        )
    )


def with_agent(ports: RunPorts, agent: StubAgent) -> RunPorts:
    return RunPorts(
        repository=ports.repository, facts=ports.facts, rules=ports.rules, agent=agent
    )


def case_facts(facts: FakeFacts, case_id: str, *verified: bool) -> list[Fact]:
    """Give the case one fact per flag; answer them."""
    made = [
        fact(case_id, f"SECRET-FACT {number}", verified=flag)
        for number, flag in enumerate(verified or (True,))
    ]
    facts.facts.extend(made)
    return made


# --- the rating a rule's text names --------------------------------------------


def test_story_3_2_on_r1_a_reason_is_checked_against_the_rules_own_definition_inside_a_fixed_chunk(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Story 2.6: the rating is read off the rule's own definition, in the
    # forms the manual prints.
    for rating, expected in (
        ("a debit of +50 %", Rating(ReasonEffect.DEBIT, 50)),
        ("decline as a postponement", Rating(ReasonEffect.DECLINE)),
        ("to be agreed with the chief underwriter", None),
    ):
        assert rating_in(rule_text(DM_50, DIABETES, rating), DM_50) == expected
    # A `fixed` chunk as row `r1` reads it: the end of one definition, a
    # whole one, and the start of a third, cut off before its rating.
    cut_off = rule_text(HT_50, HYPERTENSION, "a debit of +50 %").partition("rating")[0]
    chunk = (
        f"{rule_text(DM_25, DIABETES, 'a debit of +25 %')} "
        f"{rule_text(DM_50, DIABETES, 'a debit of +50 %')} {cut_off}"
    )
    assert rating_in(chunk, DM_50) == Rating(ReasonEffect.DEBIT, 50)
    assert rating_in(chunk, DM_25) == Rating(ReasonEffect.DEBIT, 25)
    assert rating_in(chunk, HT_50) is None
    # A definition is one paragraph. A rating printed after it, in a worked
    # example of the same chunk, is not the rule's and bears out nothing.
    example = "Worked example. Probable rating: a debit of +75 %."
    assert rating_in(f"{cut_off}\n{example}", HT_50) is None
    whole = rule_text(HT_50, HYPERTENSION, "a debit of +50 %")
    assert rating_in(f"{whole}\n{example}", HT_50) == Rating(ReasonEffect.DEBIT, 50)
    for rule_id in (DM_50, HT_50):
        rules.manual[rule_id] = RuleText(
            rule_id=rule_id,
            chunk_id="fixed-0007",
            chunk_set=ChunkSet.FIXED,
            text=chunk,
            manual_page=12,
            impairment=DIABETES,
            reference_rule_ids=[],
        )
    glucose, pressure = case_facts(facts, case_id, True, True)
    rules.by_query = {"glucose": [DM_50], "pressure": [HT_50]}
    agent = agent_that(
        LIST,
        search(glucose.fact_id, "glucose"),
        read(DM_50),
        search(pressure.fact_id, "pressure"),
        read(HT_50),
        answers=final_answer(
            reason(DM_50, glucose.fact_id), reason(HT_50, pressure.fact_id)
        ),
    )

    with caplog.at_level(logging.INFO):
        result = suggest(
            case_id,
            with_agent(ports, agent),
            options,
            fixed_now,
            retriever_config=RetrieverConfig.R1,
        )

    # A run keyed on the case and `r1`, whose searches and reads all went
    # to `retrieval` with that row.
    assert (result.status, result.retriever_config) == (
        StageStatus.DONE,
        RetrieverConfig.R1,
    )
    assert {config for _, config, _ in rules.searches} == {RetrieverConfig.R1}
    assert {config for _, config in rules.reads} == {RetrieverConfig.R1}
    run = repository.stored_run()
    # The debit of the rule defined whole in the chunk is its own +50, not
    # its neighbour's +25. The definition cut off before its rating bears
    # out nothing: that reason is dropped, and the run refers.
    assert [(item.rule_id, item.effect, item.debit_pct) for item in run.reasons] == [
        (DM_50, ReasonEffect.DEBIT, 50)
    ]
    assert (run.verdict, run.loading_pct) == (Verdict.REFER, None)
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]
    assert "rating_not_read:1" in caplog.text


# --- the matrix: what a run stores ---------------------------------------------


def test_story_2_5_debits_of_cited_rules_give_loaded_with_their_sum(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    glucose, pressure = case_facts(facts, case_id, True, True)
    rules.by_query = {"glucose": [DM_50, DM_25], "pressure": [HT_50]}
    agent = agent_that(
        LIST,
        search(glucose.fact_id, "glucose"),
        read(DM_50),
        search(pressure.fact_id, "pressure"),
        read(HT_50),
        read(TOB_25),
        answers=final_answer(
            reason(DM_50, glucose.fact_id),
            reason(TOB_25, pressure.fact_id, debit_pct=25),
            verdict="decline",
        ),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    assert result.status is StageStatus.DONE
    assert result.verdict is Verdict.LOADED
    assert result.retriever_config is RetrieverConfig.R3
    assert is_uuid7(result.verdict_run_id)
    # AD-8: a case-level `verdict.suggested`, by the service and its model, ref the run.
    audit = result.audit
    assert audit.action is AuditAction.VERDICT_SUGGESTED
    assert (audit.actor_kind, audit.actor) == (ActorKind.AI, ACTOR)
    assert (audit.page_id, audit.ref) == (None, result.verdict_run_id)
    assert (audit.trace_id, audit.occurred_at) == (TRACE_ID, fixed_now)
    run = repository.stored_run()
    assert run.label == SUGGESTION_LABEL == "AI suggestion, not a decision"
    assert (run.verdict, run.loading_pct, run.confidence) == (Verdict.LOADED, 75, 0.9)
    assert run.system_reasons == []
    assert [
        (item.rule_id, item.fact_ids, item.effect, item.debit_pct)
        for item in run.reasons
    ] == [
        (DM_50, [glucose.fact_id], ReasonEffect.DEBIT, 50),
        (TOB_25, [pressure.fact_id], ReasonEffect.DEBIT, 25),
    ]
    # Every tool call is one step, in order, with what it returned or read.
    assert [
        (step.step_no, step.tool, step.fact_id, step.rule_ids, step.outcome)
        for step in repository.steps
    ] == [
        (1, ToolName.LIST_FACTS, None, [], StepOutcome.DONE),
        (2, ToolName.SEARCH_RULES, glucose.fact_id, [DM_50, DM_25], StepOutcome.DONE),
        (3, ToolName.READ_RULE, None, [DM_50], StepOutcome.DONE),
        (4, ToolName.SEARCH_RULES, pressure.fact_id, [HT_50], StepOutcome.DONE),
        (5, ToolName.READ_RULE, None, [HT_50], StepOutcome.DONE),
        (6, ToolName.READ_RULE, None, [TOB_25], StepOutcome.DONE),
    ]
    assert repository.steps[1].arguments == {
        "query": "glucose",
        "fact_id": glucose.fact_id,
    }
    assert all(
        step.verdict_run_id == result.verdict_run_id and step.case_id == case_id
        for step in repository.steps
    )
    # The server fixed the retriever row of every search and read.
    assert {config for _, config, _ in rules.searches} == {RetrieverConfig.R3}
    assert {config for _, config in rules.reads} == {RetrieverConfig.R3}


def test_story_2_6_a_kept_decline_gives_decline_and_neither_a_debit_nor_a_decline_gives_standard(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    first, second = case_facts(facts, case_id, True, True)
    rules.default = [DM_DECLINE, HT_50]
    agent = agent_that(
        LIST,
        search(first.fact_id),
        read(DM_DECLINE),
        read(HT_50),
        answers=final_answer(
            reason(HT_50, second.fact_id),
            reason(DM_DECLINE, first.fact_id, effect="decline"),
            verdict="loaded",
        ),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert result.verdict is Verdict.DECLINE
    assert (run.verdict, run.loading_pct) == (Verdict.DECLINE, None)
    assert [item.effect for item in run.reasons] == [
        ReasonEffect.DEBIT,
        ReasonEffect.DECLINE,
    ]

    # Another case, whose one fact meets a rule without a debit.
    other_case, stored = new_id(), MemoryRepository()
    (only,) = case_facts(facts, other_case)
    rules.default = [PD_NONE]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        # Called a debit of 0 by the agent: stored as effect `none`.
        answers=final_answer(
            reason(PD_NONE, only.fact_id, debit_pct=0), verdict="refer"
        ),
    )
    standard = suggest(
        other_case,
        RunPorts(repository=stored, facts=facts, rules=rules, agent=agent),
        options,
        fixed_now,
    )

    run = stored.stored_run()
    assert standard.verdict is Verdict.STANDARD
    assert (run.verdict, run.loading_pct, run.system_reasons) == (
        Verdict.STANDARD,
        None,
        [],
    )
    assert [(item.effect, item.debit_pct) for item in run.reasons] == [
        (ReasonEffect.NONE, None)
    ]


@pytest.mark.parametrize("unseen", ["rule", "fact"])
def test_story_2_5_a_reason_citing_what_the_run_did_not_see_is_not_stored(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
    unseen: str,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50] if unseen == "rule" else [DM_50, DM_25]
    cited = (
        # A rule of the manual that no search of this run returned.
        reason(HT_50, only.fact_id)
        if unseen == "rule"
        # A fact id that is well formed and not one the run listed.
        else reason(DM_25, new_id(), debit_pct=25)
    )
    agent = agent_that(
        LIST,
        search(only.fact_id),
        read(DM_50),
        answers=final_answer(reason(DM_50, only.fact_id), cited, verdict="loaded"),
    )

    with caplog.at_level(logging.INFO):
        suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert [item.rule_id for item in run.reasons] == [DM_50]
    # A proposed debit was dropped: what is left would be a lighter verdict
    # than the agent meant, so the case is referred and not loaded with less.
    assert (run.verdict, run.loading_pct) == (Verdict.REFER, None)
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]
    # The count is logged; the reason is stored nowhere.
    assert "reasons_dropped=1" in caplog.text
    assert (
        "rule_not_seen:1" if unseen == "rule" else "fact_not_listed:1"
    ) in caplog.text


@pytest.mark.parametrize(
    "wrong",
    [
        {"effect": "debit", "debit_pct": 100},
    ],
)
def test_story_2_6_an_effect_the_rule_does_not_say_is_not_stored(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    wrong: dict[str, Any],
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        read(DM_50),
        # The rule read says +50 %.
        answers=final_answer(reason(DM_50, only.fact_id, **wrong)),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert run.reasons == []
    # None could be kept of the reasons proposed: the case is referred.
    assert result.verdict is Verdict.REFER
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]


def test_story_2_5_read_rule_is_refused_for_a_rule_the_run_has_not_seen_and_the_run_goes_on(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        read(TOB_25),
        search(only.fact_id),
        read(DM_50),
        answers=final_answer(reason(DM_50, only.fact_id)),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    refused = repository.steps[1]
    assert (refused.tool, refused.outcome, refused.error_code) == (
        ToolName.READ_RULE,
        StepOutcome.REFUSED,
        ErrorCode.RULE_NOT_SEEN,
    )
    assert refused.arguments == {"rule_id": TOB_25}
    assert refused.rule_ids == []
    # `retrieval` was never asked for it, and the model was told why.
    assert rules.reads == [(DM_50, RetrieverConfig.R3)]
    assert agent.answers[1]["refused"] == "rule_not_seen"
    assert [step.step_no for step in repository.steps] == [1, 2, 3, 4]
    assert result.verdict is Verdict.LOADED


def test_story_2_5_read_rule_may_follow_a_reference_of_a_rule_already_read(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    (only,) = case_facts(facts, case_id)
    # The search returns DM_50 only; DM_50 refers to HT_50, and HT_50 to TOB_25.
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        # Referred to by DM_50's chunk, which was only returned, not read: refused.
        read(HT_50),
        read(DM_50),
        read(HT_50),
        read(TOB_25),
        answers=final_answer(
            reason(DM_50, only.fact_id),
            reason(TOB_25, only.fact_id, debit_pct=25),
        ),
    )

    suggest(case_id, with_agent(ports, agent), options, fixed_now)

    assert [(step.outcome, step.rule_ids) for step in repository.steps[2:]] == [
        (StepOutcome.REFUSED, []),
        (StepOutcome.DONE, [DM_50]),
        (StepOutcome.DONE, [HT_50]),
        (StepOutcome.DONE, [TOB_25]),
    ]
    assert repository.stored_run().loading_pct == 75


def test_story_2_6_a_debit_on_an_unverified_quote_refers_the_case(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    verified, unverified = case_facts(facts, case_id, True, False)
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        search(unverified.fact_id),
        read(DM_50),
        answers=final_answer(reason(DM_50, verified.fact_id, unverified.fact_id)),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert result.verdict is Verdict.REFER
    assert run.system_reasons == [SystemReason.UNVERIFIED_QUOTE]
    # The reason is kept and shown, with its fact flagged where facts are read.
    assert [item.rule_id for item in run.reasons] == [DM_50]
    assert run.loading_pct is None


@pytest.mark.parametrize(
    ("confidence", "floor", "low"),
    [(0.70, 0.70, False), (0.69999, 0.70, True)],
)
def test_story_2_6_confidence_under_the_floor_refers_and_at_the_floor_does_not(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    fixed_now: datetime,
    confidence: float,
    floor: float,
    low: bool,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        read(DM_50),
        answers=final_answer(reason(DM_50, only.fact_id), confidence=confidence),
    )
    options = RunOptions(actor=ACTOR, confidence_floor=floor)

    suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert run.confidence == confidence
    assert run.system_reasons == ([SystemReason.LOW_CONFIDENCE] if low else [])
    assert run.verdict is (Verdict.REFER if low else Verdict.LOADED)


def test_story_2_6_the_step_limit_stops_the_run_and_refers_the_case(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    fixed_now: datetime,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50]

    async def for_ever(toolbox: Toolbox) -> str | None:
        while True:
            await toolbox.call("search_rules", search(only.fact_id)[1])

    options = RunOptions(actor=ACTOR, step_limit=4)

    result = suggest(
        case_id, with_agent(ports, StubAgent(script=for_ever)), options, fixed_now
    )

    run = repository.stored_run()
    assert result.status is StageStatus.DONE
    assert result.verdict is Verdict.REFER
    assert run.system_reasons == [SystemReason.STEP_LIMIT]
    assert (run.confidence, run.reasons) == (None, [])
    # The limit is the number of tool calls made: the fifth was not, and
    # the log says so with a refused row.
    assert [(step.step_no, step.outcome) for step in repository.steps] == [
        *((number, StepOutcome.DONE) for number in (1, 2, 3, 4)),
        (5, StepOutcome.REFUSED),
    ]
    assert repository.steps[-1].error_code is ErrorCode.STEP_LIMIT
    assert len(rules.searches) == 4


def test_story_2_6_two_bands_of_one_impairment_conflict_also_on_different_facts(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    # Two readings of one measure, on two pages: an applicant meets one
    # band, so their debits are never added up to +75.
    first, second = case_facts(facts, case_id, True, True)
    rules.default = [DM_25, DM_50]
    agent = agent_that(
        LIST,
        search(first.fact_id),
        read(DM_25),
        read(DM_50),
        answers=final_answer(
            reason(DM_25, first.fact_id, debit_pct=25), reason(DM_50, second.fact_id)
        ),
    )

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert result.verdict is Verdict.REFER
    assert run.system_reasons == [SystemReason.CONFLICTING_RULES]
    assert [item.rule_id for item in run.reasons] == [DM_25, DM_50]
    assert run.loading_pct is None

    # Two rules of one impairment add where one's definition refers to the
    # other, as the manual does for a smoker's status and the lifetime total:
    # that is no conflict. A chunk that merely holds both definitions is.
    def cited(*texts: str) -> bool:
        state = RunState()
        for rule_id, text in zip((DM_25, DM_50), texts, strict=True):
            state.rule_texts[rule_id] = text
            state.impairments[rule_id] = DIABETES
        both = [
            Reason(
                rule_id=DM_25,
                fact_ids=[first.fact_id],
                effect=ReasonEffect.NONE,
                debit_pct=None,
            ),
            Reason(
                rule_id=DM_50,
                fact_ids=[second.fact_id],
                effect=ReasonEffect.NONE,
                debit_pct=None,
            ),
        ]
        return rules_conflict(both, state)

    plain = [
        rule_text(rule_id, DIABETES, "no debit, +0 %") for rule_id in (DM_25, DM_50)
    ]
    assert cited(*plain)
    assert cited("\n".join(plain), "\n".join(plain))
    assert not cited(rule_text(DM_25, DIABETES, "no debit, +0 %", DM_50), plain[1])
    assert not cited(plain[0], rule_text(DM_50, DIABETES, "no debit, +0 %", DM_25))


@pytest.mark.parametrize(
    ("reported", "stored"),
    [
        (
            ["conflicting_rules", "no_matching_rule"],
            [SystemReason.NO_MATCHING_RULE, SystemReason.CONFLICTING_RULES],
        ),
        # These three are set by code alone: the agent's word for them is not taken.
        (["low_confidence", "step_limit", "unverified_quote"], []),
    ],
)
def test_story_2_6_the_agent_may_report_no_matching_rule_and_conflicting_rules_only(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    reported: list[str],
    stored: list[SystemReason],
) -> None:
    (only,) = case_facts(facts, case_id)
    agent = agent_that(
        LIST, search(only.fact_id), answers=final_answer(system_reasons=reported)
    )

    suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert run.system_reasons == stored
    assert run.verdict is (Verdict.REFER if stored else Verdict.STANDARD)


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps(
            {
                "verdict": "standard",
                "confidence": 0.9,
                "reasons": [],
                "system_reasons": [],
                "decision": "final",
            }
        ),
    ],
)
def test_story_2_5_a_final_answer_that_is_not_the_contracts_shape_fails_the_run(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    answer: str,
) -> None:
    case_facts(facts, case_id)
    agent = agent_that(LIST, answers=answer)

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.INVALID_MODEL_OUTPUT,
    )
    assert result.verdict is None
    assert result.audit.action is AuditAction.STAGE_FAILED
    run = repository.stored_run()
    assert (run.status, run.error_code) == (
        StageStatus.FAILED,
        ErrorCode.INVALID_MODEL_OUTPUT,
    )
    assert (run.verdict, run.reasons, run.system_reasons) == (None, [], [])
    # The steps the run made stay in the log.
    assert len(repository.steps) == 1


def test_story_2_6_an_answer_given_without_searching_the_manual_is_referred_never_standard(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    case_facts(facts, case_id)
    # The agent lists the facts and answers at once, sure of itself, with
    # no reasons: it searched the manual for none of them.
    agent = agent_that(LIST, answers=final_answer(confidence=0.99))

    result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert (result.status, run.verdict) == (StageStatus.DONE, Verdict.REFER)
    assert run.system_reasons == [SystemReason.LOW_CONFIDENCE]


def test_story_2_6_the_agents_time_budget_stops_it_and_refers_the_case_before_the_deadline(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    repository: MemoryRepository,
    fixed_now: datetime,
) -> None:
    case_facts(facts, case_id)

    async def slow(toolbox: Toolbox) -> str | None:
        await toolbox.call("list_facts", {})
        await asyncio.sleep(30)
        return final_answer()

    options = RunOptions(actor=ACTOR, deadline_seconds=20, agent_budget_seconds=0.05)

    result = suggest(
        case_id, with_agent(ports, StubAgent(script=slow)), options, fixed_now
    )

    # Out of time is the step limit by another measure: a refer, which
    # `workflow` records, and not `stage_timeout`, which would fail the case.
    run = repository.stored_run()
    assert (result.status, result.error_code) == (StageStatus.DONE, None)
    assert (run.verdict, run.system_reasons, run.reasons) == (
        Verdict.REFER,
        [SystemReason.STEP_LIMIT],
        [],
    )
    assert len(repository.steps) == 1


def test_story_2_5_the_deadline_ends_the_run_as_stage_timeout(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    fixed_now: datetime,
) -> None:
    case_facts(facts, case_id)
    slow = StubAgent(delay_seconds=30)

    result = suggest(
        case_id,
        with_agent(ports, slow),
        RunOptions(actor=ACTOR, deadline_seconds=0.05),
        fixed_now,
    )

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_TIMEOUT,
    )


# --- logs (security rule 31) -------------------------------------------------------


def test_story_2_5_logs_hold_ids_codes_counts_and_timings_only(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    options: RunOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        read(DM_50),
        read(NOT_IN_MANUAL),
        answers=final_answer(reason(DM_50, only.fact_id), reason(HT_50, only.fact_id)),
    )

    with caplog.at_level(logging.DEBUG):
        result = suggest(case_id, with_agent(ports, agent), options, fixed_now)

    assert result.status is StageStatus.DONE
    assert "verdict suggested" in caplog.text and "agent step" in caplog.text
    assert f"verdict_run_id={result.verdict_run_id}" in caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text


# --- the review's rules: what a run must have done for its answer to count ------------


def test_story_2_6_a_dropped_decline_beside_a_kept_debit_refers_and_is_not_stored_as_loaded(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
) -> None:
    first, second = case_facts(facts, case_id, True, True)
    rules.default = [TOB_25, DM_DECLINE]
    agent = agent_that(
        LIST,
        search(first.fact_id),
        read(TOB_25),
        # The decline's rule was returned by the search and never read.
        answers=final_answer(
            reason(TOB_25, first.fact_id, debit_pct=25),
            reason(DM_DECLINE, second.fact_id, effect="decline"),
        ),
    )

    suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    assert [item.rule_id for item in run.reasons] == [TOB_25]
    assert (run.verdict, run.loading_pct) == (Verdict.REFER, None)
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]


def test_story_2_5_a_debit_is_kept_only_on_a_rule_the_run_read_and_checked_against_that_text(
    case_id: str,
    ports: RunPorts,
    facts: FakeFacts,
    rules: FakeRules,
    repository: MemoryRepository,
    options: RunOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (only,) = case_facts(facts, case_id)
    rules.default = [DM_50, PD_NONE]
    agent = agent_that(
        LIST,
        search(only.fact_id),
        # Neither rule is read: the search's chunks are all the run saw.
        answers=final_answer(
            reason(DM_50, only.fact_id),
            reason(PD_NONE, only.fact_id, effect="none"),
        ),
    )

    with caplog.at_level(logging.INFO):
        suggest(case_id, with_agent(ports, agent), options, fixed_now)

    run = repository.stored_run()
    # "No debit" needs no reading of the rule; a debit does.
    assert [(item.rule_id, item.effect) for item in run.reasons] == [
        (PD_NONE, ReasonEffect.NONE)
    ]
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]
    assert "rule_not_read:1" in caplog.text


# --- row r6: the run searches, the model composes (story 3.8) --------------------


def test_story_3_8_on_r6_the_run_searches_once_per_statement_and_the_model_composes_with_no_tool(
    case_id: str,
    facts: FakeFacts,
    rules: FakeRules,
    options: RunOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    on_r6 = replace(options, retriever_configs=frozenset(RetrieverConfig))
    glucose, pressure, smoker = (
        fact(case_id, f"SECRET-FACT  {name}\treading") for name in ("a", "b", "c")
    )
    # The first statement stands on a second page too: a fact of its own
    # that states the same thing.
    again = fact(case_id, "SECRET-FACT a reading")
    facts.facts.extend([glucose, pressure, smoker, again])
    # The queries are the contracts' query builder's: whitespace collapsed.
    queries = [f"SECRET-FACT {name} reading" for name in ("a", "b", "c")]
    rules.by_query = dict(zip(queries, [[DM_50, DM_25], [HT_50], []], strict=True))

    def run_with(agent: StubAgent, **changes: Any) -> tuple[Any, MemoryRepository]:
        stored = MemoryRepository()
        ports = RunPorts(repository=stored, facts=facts, rules=rules, agent=agent)
        of = replace(on_r6, **changes)
        try:
            return suggest(
                case_id, ports, of, fixed_now, retriever_config=RetrieverConfig.R6
            ), stored
        except DomainError as error:
            return error, stored

    # The model cites two rules the searches returned, with their debits,
    # and one no search returned.
    agent = StubAgent(
        composes=final_answer(
            reason(DM_50, glucose.fact_id),
            reason(HT_50, pressure.fact_id),
            reason(TOB_25, smoker.fact_id, effect="none"),
        )
    )
    with caplog.at_level(logging.INFO):
        result, repository = run_with(agent)

    # The steps: the facts listed, one search per distinct statement with
    # the query made from it, under the first fact that states it, and no
    # other search and no rule read.
    assert [
        (step.step_no, step.tool, step.fact_id, step.rule_ids, step.outcome)
        for step in repository.steps
    ] == [
        (1, ToolName.LIST_FACTS, None, [], StepOutcome.DONE),
        (2, ToolName.SEARCH_RULES, glucose.fact_id, [DM_50, DM_25], StepOutcome.DONE),
        (3, ToolName.SEARCH_RULES, pressure.fact_id, [HT_50], StepOutcome.DONE),
        (4, ToolName.SEARCH_RULES, smoker.fact_id, [], StepOutcome.DONE),
    ]
    assert [step.arguments.get("query") for step in repository.steps[1:]] == queries
    assert rules.searches == [(query, RetrieverConfig.R6, 5) for query in queries]
    assert rules.reads == []
    # The agent's own loop was never run: the model was asked once, and
    # given the facts and what each search returned.
    assert (agent.runs, len(agent.materials)) == (0, 1)
    material = json.loads(agent.materials[0])
    assert [item["fact_id"] for item in material["facts"]] == [
        glucose.fact_id,
        pressure.fact_id,
        smoker.fact_id,
        again.fact_id,
    ]
    assert [
        (item["fact_id"], [rule["rule_ids"] for rule in item["rules"]])
        for item in material["searches"]
    ] == [
        (glucose.fact_id, [[DM_50], [DM_25]]),
        (pressure.fact_id, [[HT_50]]),
        (smoker.fact_id, []),
        # The fact that repeats a statement has the rules of its one search.
        (again.fact_id, [[DM_50], [DM_25]]),
    ]
    assert material["searches"][0]["rules"][0]["text"] == rule_text(
        DM_50, DIABETES, "a debit of +50 %", HT_50
    )
    # A rule a search returned counts as read, and its debit is checked
    # against that chunk's text: both are kept. The reason on a rule no
    # search returned is dropped, as on every row; it weighed nothing, so
    # the two debits stand.
    run = repository.stored_run()
    assert result.retriever_config is RetrieverConfig.R6
    assert (run.verdict, run.loading_pct, run.system_reasons) == (
        Verdict.LOADED,
        100,
        [],
    )
    assert [(item.rule_id, item.debit_pct) for item in run.reasons] == [
        (DM_50, 50),
        (HT_50, 50),
    ]
    assert "reasons_dropped=1 dropped_by=rule_not_seen:1" in caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text

    # An effect the returned text does not say, and a debit on a rule no
    # search returned: neither is kept, and the case is referred as the
    # rules of story 2.6 say.
    wrong = StubAgent(
        composes=final_answer(
            reason(DM_50, glucose.fact_id, debit_pct=75),
            reason(TOB_25, smoker.fact_id, debit_pct=25),
        )
    )
    referred, repository = run_with(wrong)
    run = repository.stored_run()
    assert (referred.verdict, run.reasons) == (Verdict.REFER, [])
    assert run.system_reasons == [SystemReason.NO_MATCHING_RULE]

    # More distinct statements than the run may search for: referred as
    # at the step limit, as soon as the facts are listed. No search is made
    # and paid for, and the model is not asked.
    stopped = StubAgent(composes=final_answer())
    searched = len(rules.searches)
    limited, repository = run_with(stopped, composed_search_limit=2)
    assert limited.verdict is Verdict.REFER
    assert repository.stored_run().system_reasons == [SystemReason.STEP_LIMIT]
    assert [
        (step.tool, step.outcome, step.error_code) for step in repository.steps
    ] == [
        (ToolName.LIST_FACTS, StepOutcome.DONE, None),
        (ToolName.SEARCH_RULES, StepOutcome.REFUSED, ErrorCode.STEP_LIMIT),
    ]
    assert stopped.materials == [] and len(rules.searches) == searched
    # The model's step limit does not bound these searches: no model makes
    # them. With more facts than that limit has steps, and a search for
    # each distinct statement allowed, the case is searched and composed.
    composed = StubAgent(composes=final_answer())
    through, repository = run_with(composed, step_limit=1, composed_search_limit=3)
    assert (through.status, len(composed.materials)) == (StageStatus.DONE, 1)
    assert repository.stored_run().system_reasons == []
    assert [step.outcome for step in repository.steps] == [StepOutcome.DONE] * 4

    # A search the toolbox refuses (a statement longer than a query may
    # be) was not made: the run fails there, and nothing is composed as if
    # the manual held no rule for that fact.
    facts.facts.append(fact(case_id, "SECRET-FACT " + "long " * 500))
    unmade = StubAgent(composes=final_answer())
    refused, repository = run_with(unmade)
    del facts.facts[-1]
    assert (refused.status, refused.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_FAILED,
    )
    assert (repository.steps[-1].outcome, repository.steps[-1].error_code) == (
        StepOutcome.REFUSED,
        ErrorCode.VALIDATION_FAILED,
    )
    assert unmade.materials == []

    # A search that fails ends the run as it does on the other rows: the
    # step is logged as failed, nothing is composed, and the key row is
    # given up for the command to be sent again.
    rules.fail_search = True
    unasked = StubAgent(composes=final_answer())
    failed, repository = run_with(unasked)
    assert isinstance(failed, DomainError)
    assert failed.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert [
        (step.tool, step.outcome, step.error_code) for step in repository.steps
    ] == [
        (ToolName.LIST_FACTS, StepOutcome.DONE, None),
        (ToolName.SEARCH_RULES, StepOutcome.FAILED, ErrorCode.UPSTREAM_UNAVAILABLE),
    ]
    assert (repository.rows, unasked.materials) == ({}, [])
