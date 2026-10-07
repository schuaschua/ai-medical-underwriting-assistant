"""Stories 2.5, 2.6, 3.2, 3.3 and 3.8: a synthetic case run to a suggested verdict with cited reasons, compared with the rule table.

`workflow`, `intake`, `classification`, `extraction`, `retrieval` and
`verdict` as they really run, against a real PostgreSQL, the Durable Task
Scheduler emulator and the blob emulator (`docker compose up --detach
--wait`), with the stand-ins of this package where Azure AI Language and the
Foundry deployments would be, and a transport where the Dapr sidecars would
be. The verdict agent runs on Microsoft Agent Framework against the model
stand-in, over the manual as the ingestion job indexed it.

The tests are here and not under `services/` because they name the stand-ins'
package and read the answer key, which nothing there may do (spine AD-17).
"""

import json
import time
from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationStatus
from fastapi.testclient import TestClient
from synthdata_stack import (
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    ServicesBehindSidecar,
    audit_rows,
    completed,
    rule_table,
    start_and_wait,
    workflow_service,
)
from workflow_local import wait_for_case_status

from contracts.audit import DECISION_ACTIONS
from contracts.enums import (
    ReasonEffect,
    StageStatus,
    StepOutcome,
    SystemReason,
    ToolName,
    Verdict,
)
from contracts.models.verdict import SUGGESTION_LABEL, VerdictRun
from contracts.models.workflow import AuditTrail, CaseProgress
from contracts.query import build_fact_query
from retrieval.domain.chunker import definition_in
from synthdata.foundry_standin import LOCAL_DEPLOYMENT, FoundryStandIn, Mode
from synthdata.search_standin import SearchStandIn
from synthdata.verdict_standin import composing_of
from workflow.settings import Settings

pytestmark = pytest.mark.integration

VERDICT_ACTOR = f"verdict:{LOCAL_DEPLOYMENT}"
# What each synthetic case should meet, by the rule table (the answer key).
EXPECTED_RULES = {
    "case-001": ["UW-DM-002"],
    "case-002": ["UW-HT-002", "UW-TOB-001"],
    "case-003": [],
}


def sidecar_for(
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    **changes: Any,
) -> ServicesBehindSidecar:
    return ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(**changes),
    )


def only_run(verdict: LocalVerdict, case_id: str) -> VerdictRun:
    listed = verdict.runs(case_id)
    assert not listed.has_more
    (run,) = listed.verdict_runs
    return run


def expected_from_the_rule_table(
    rule_ids: list[str],
) -> tuple[Verdict, int | None, list[tuple[str, ReasonEffect, int | None]]]:
    """The verdict, the loading and the reasons' effects that the rule table gives those rules."""
    table = rule_table()
    reasons: list[tuple[str, ReasonEffect, int | None]] = []
    for rule_id in rule_ids:
        rule = table[rule_id]
        if rule["decline"]:
            reasons.append((rule_id, ReasonEffect.DECLINE, None))
        elif rule["debit_pct"]:
            reasons.append((rule_id, ReasonEffect.DEBIT, rule["debit_pct"]))
        else:
            reasons.append((rule_id, ReasonEffect.NONE, None))
    if any(effect is ReasonEffect.DECLINE for _, effect, _ in reasons):
        return Verdict.DECLINE, None, reasons
    loading = sum(debit or 0 for _, _, debit in reasons)
    if loading:
        return Verdict.LOADED, loading, reasons
    return Verdict.STANDARD, None, reasons


@pytest.mark.parametrize("case_name", sorted(EXPECTED_RULES))
def test_story_2_5_a_synthetic_case_completes_with_the_verdict_the_rule_table_gives_it(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    case_name: str,
) -> None:
    case_id, _ = intake.upload(f"{case_name}.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)
    expected_verdict, expected_loading, expected_reasons = expected_from_the_rule_table(
        EXPECTED_RULES[case_name]
    )

    progress, trail, output = _run_to_the_end(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    assert progress.case_status.value == "completed"
    assert output == {"case_id": case_id, "case_status": "completed"}

    # One run, for the default configuration `r3`: a suggestion, labelled as one.
    run = only_run(verdict, case_id)
    assert run.status is StageStatus.DONE
    assert run.retriever_config.value == "r3"
    assert run.label == SUGGESTION_LABEL == "AI suggestion, not a decision"
    assert (run.verdict, run.loading_pct) == (expected_verdict, expected_loading)
    assert run.system_reasons == []
    assert sorted(
        (reason.rule_id, reason.effect, reason.debit_pct) for reason in run.reasons
    ) == sorted(expected_reasons)

    # Every reason cites facts of the case, and a rule and facts that the
    # run's own steps show it saw.
    case_facts = {fact.fact_id for fact in extraction.facts(case_id).facts}
    steps = verdict.steps(run.verdict_run_id)
    assert not steps.has_more
    assert [step.step_no for step in steps.steps] == list(
        range(1, len(steps.steps) + 1)
    )
    assert steps.steps[0].tool is ToolName.LIST_FACTS
    assert all(step.outcome is StepOutcome.DONE for step in steps.steps)
    seen = {rule_id for step in steps.steps for rule_id in step.rule_ids}
    read = {
        rule_id
        for step in steps.steps
        if step.tool is ToolName.READ_RULE
        for rule_id in step.rule_ids
    }
    for reason in run.reasons:
        assert reason.rule_id in seen and reason.rule_id in read
        assert set(reason.fact_ids) <= case_facts
    # The server fixed the case and the row: every search was made with `r3`,
    # about a fact of this case.
    searches = [step for step in steps.steps if step.tool is ToolName.SEARCH_RULES]
    assert searches and all(step.fact_id in case_facts for step in searches)
    assert ("GET", f"/cases/{case_id}/facts") in verdict.calls("extraction")
    assert ("POST", "/searches") in verdict.calls("retrieval")

    # AD-8: the run is a case-level `verdict.suggested` event, by the service
    # and its model, and the case is completed only after it.
    actions = [event.action.value for event in trail.events]
    assert actions[-2:] == ["verdict.suggested", "case.completed"]
    suggested = trail.events[-2]
    assert (suggested.actor, suggested.page_id, suggested.ref) == (
        VERDICT_ACTOR,
        None,
        run.verdict_run_id,
    )
    # The AI decides nothing: every decision in the trail is a person's.
    assert all(
        event.actor_kind.value == "human"
        for event in trail.events
        if event.action in DECISION_ACTIONS
    )


def _run_to_the_end(
    settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    sidecar: ServicesBehindSidecar,
    case_id: str,
) -> tuple[CaseProgress, AuditTrail, dict[str, Any]]:
    """Run a case whose non-medical pages wait for people: keep, then accept, every waiting page; return what it left.

    The customer keeps each page the gate asks about and the underwriter
    accepts each page in triage, so that every page is extracted and the
    case reaches its verdict run.
    """
    with workflow_service(settings, sidecar) as client:
        assert (
            client.post(
                f"/cases/{case_id}/start", json={"actor": "customer"}
            ).status_code
            == 200
        )
        _decide_until_done(client, case_id)
        state = completed(scheduler_client, case_id)
        output: dict[str, Any] = json.loads(state.serialized_output or "")
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
    return progress, trail, output


def _decide_until_done(client: TestClient, case_id: str) -> None:
    """Decide every page that waits for a person, until none does and the case has ended."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        progress = client.get(f"/cases/{case_id}/progress").json()
        if progress.get("case_status") in ("completed", "failed"):
            return
        for page in progress.get("pages", []):
            if page["page_status"] == "awaiting_customer":
                client.post(
                    f"/cases/{case_id}/pages/{page['page_id']}/decisions",
                    json={"decision": "keep", "actor": "customer"},
                )
            elif page["page_status"] == "awaiting_triage":
                client.post(
                    f"/cases/{case_id}/pages/{page['page_id']}/decisions",
                    json={"decision": "accept", "actor": "underwriter"},
                )
        # Not a wait for time to pass: the worker's threads get their turn.
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} did not end")


def _refer_run(
    settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    sidecar: ServicesBehindSidecar,
    verdict: LocalVerdict,
    case_id: str,
) -> VerdictRun:
    progress, _, _ = start_and_wait(settings, scheduler_client, sidecar, case_id)
    assert progress.case_status.value == "completed"
    return only_run(verdict, case_id)


@pytest.mark.parametrize(
    ("mode", "system_reasons", "kept"),
    [
        # A debit citing a rule the run never saw, and one on a fact never
        # listed, are dropped and never stored. The honest reason stands,
        # and the case is referred: what is left would be a lighter verdict
        # than the agent proposed.
        (Mode.UNSEEN_RULE, [SystemReason.NO_MATCHING_RULE], ["UW-DM-002"]),
        (Mode.LOW_CONFIDENCE, [SystemReason.LOW_CONFIDENCE], ["UW-DM-002"]),
    ],
)
def test_story_2_6_what_the_agent_proposes_is_decided_by_code_for_the_real_service(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    mode: Mode,
    system_reasons: list[SystemReason],
    kept: list[str],
) -> None:
    verdict.model.mode = mode
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    run = _refer_run(
        workflow_service_settings, scheduler_client, sidecar, verdict, case_id
    )

    assert run.system_reasons == system_reasons
    assert [reason.rule_id for reason in run.reasons] == kept
    assert run.verdict is (Verdict.REFER if system_reasons else Verdict.LOADED)
    assert run.loading_pct == (None if system_reasons else 50)
    # Nothing the run did not see is stored, whatever the agent cited.
    steps = verdict.steps(run.verdict_run_id).steps
    seen = {rule_id for step in steps for rule_id in step.rule_ids}
    assert all(reason.rule_id in seen for reason in run.reasons)


def test_story_2_6_an_agent_that_never_answers_is_stopped_at_the_step_limit_and_the_case_referred(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    verdict.model.mode = Mode.ENDLESS_LOOP
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict, step_limit=6)

    run = _refer_run(
        workflow_service_settings, scheduler_client, sidecar, verdict, case_id
    )

    assert (run.verdict, run.system_reasons) == (
        Verdict.REFER,
        [SystemReason.STEP_LIMIT],
    )
    assert (run.confidence, run.reasons) == (None, [])
    # Six calls were made; the seventh was not, and is logged as refused.
    steps = verdict.steps(run.verdict_run_id).steps
    assert [step.outcome.value for step in steps] == [*["done"] * 6, "refused"]


def test_story_2_5_an_invalid_final_answer_fails_the_run_and_the_case(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    verdict.model.mode = Mode.INVALID_ANSWER
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    progress, trail, output = start_and_wait(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    assert progress.case_status.value == "failed"
    assert progress.error_code is not None
    assert progress.error_code.value == "invalid_model_output"
    assert output == {"case_id": case_id, "case_status": "failed"}
    run = only_run(verdict, case_id)
    assert (run.status, run.verdict, run.reasons) == (StageStatus.FAILED, None, [])
    assert run.error_code is not None
    assert run.error_code.value == "invalid_model_output"
    # One case-level `stage.failed` by the verdict stage, and no completion.
    rows = audit_rows(workflow_service_settings, case_id)
    assert rows[-1][0] == "stage.failed"
    assert (rows[-1][1], rows[-1][2], rows[-1][4]) == (
        None,
        "invalid_model_output",
        VERDICT_ACTOR,
    )
    assert "case.completed" not in [event.action.value for event in trail.events]
    # The steps the run made stay in the log.
    assert verdict.steps(run.verdict_run_id).steps


def test_story_2_5_one_more_run_is_refused_while_a_page_waits_and_answered_from_the_store_afterwards(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    verdict_model_stand_in: FoundryStandIn,
) -> None:
    # case-002 has a page the gate asks the customer about.
    case_id, _ = intake.upload("case-002.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)
    asked = {"retriever_config": "r3"}

    with workflow_service(workflow_service_settings, sidecar) as client:
        client.post(f"/cases/{case_id}/start", json={"actor": "customer"})
        wait_for_case_status(client, case_id, "awaiting_human", 90)

        early = client.post(f"/cases/{case_id}/verdict-runs", json=asked)

        assert early.status_code == 409
        assert early.json()["error"]["code"] == "pages_not_terminal"
        assert verdict.runs(case_id).verdict_runs == []

        _decide_until_done(client, case_id)
        wait_for_case_status(client, case_id, "completed", 90)
        first = only_run(verdict, case_id)
        calls_before = verdict_model_stand_in.verdict_calls

        requested = client.post(f"/cases/{case_id}/verdict-runs", json=asked)
        assert requested.status_code == 200
        instance_id = f"{case_id}:verdict:r3"
        state = scheduler_client.wait_for_orchestration_completion(
            instance_id, timeout=90
        )
        again = client.post(f"/cases/{case_id}/verdict-runs", json=asked).json()

    assert state is not None
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    # The stored run: no second run, and no further call of the model.
    assert again == {
        "case_id": case_id,
        "retriever_config": "r3",
        "status": "done",
        "verdict_run_id": first.verdict_run_id,
        "error_code": None,
    }
    assert only_run(verdict, case_id) == first
    assert verdict_model_stand_in.verdict_calls == calls_before
    actions = [row[0] for row in audit_rows(workflow_service_settings, case_id)]
    assert actions.count("verdict.suggested") == 1
    assert actions.count("case.completed") == 1


def test_story_2_5_the_agents_log_is_read_by_case_with_its_filters(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)
    start_and_wait(workflow_service_settings, scheduler_client, sidecar, case_id)
    run = only_run(verdict, case_id)

    every = verdict.case_steps(case_id).steps
    reads = verdict.case_steps(case_id, tool="read_rule").steps
    about_the_rule = verdict.case_steps(case_id, rule_id="UW-DM-002").steps

    assert every == verdict.steps(run.verdict_run_id).steps
    assert reads and all(step.tool is ToolName.READ_RULE for step in reads)
    assert about_the_rule and all(
        "UW-DM-002" in step.rule_ids for step in about_the_rule
    )
    assert {step.tool for step in about_the_rule} == {
        ToolName.SEARCH_RULES,
        ToolName.READ_RULE,
    }


# --- The baseline rows (story 3.2) -----------------------------------------------------


def test_story_3_2_a_case_started_with_several_rows_gets_a_run_for_each_on_the_same_facts(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    # Story 3.3: `r5` is available here, because `retrieval` is given a
    # search service (the stand-in, its index loaded from the stored
    # chunks) and `workflow` and `verdict` are told of the row. Story 3.8:
    # so is `r6`, whose knowledge base the job made over that index.
    rows = ["r1", "r2", "r3", "r5", "r6"]
    search_service = SearchStandIn()
    verdict.retrieval.search = search_service
    verdict.retrieval.load_index()
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(
        intake, classification, extraction, verdict, available_retriever_configs=rows
    )

    progress, trail, _ = start_and_wait(
        workflow_service_settings.model_copy(
            update={"available_retriever_configs": rows}
        ),
        scheduler_client,
        sidecar,
        case_id,
        actor="underwriter",
        retriever_configs=rows,
    )

    # One run per row, each done, and only then the case completes.
    assert progress.case_status.value == "completed"
    listed = verdict.runs(case_id)
    runs = {run.retriever_config.value: run for run in listed.verdict_runs}
    assert sorted(runs) == rows and len(listed.verdict_runs) == 5
    assert all(run.status is StageStatus.DONE for run in runs.values())
    actions = [event.action.value for event in trail.events]
    assert actions[-6:] == [*["verdict.suggested"] * 5, "case.completed"]
    assert {event.ref for event in trail.events[-6:-1]} == {
        run.verdict_run_id for run in runs.values()
    }
    # Story 3.3: the run keyed on the case and `r5` searched the search
    # service, once per search of the agent, and nothing else did. What it
    # then suggests depends on the stand-in's ranker: a verdict, or a referral.
    r5_searches = [
        step
        for step in verdict.steps(runs["r5"].verdict_run_id).steps
        if step.tool is ToolName.SEARCH_RULES
    ]
    assert len(search_service.queries) == len(r5_searches) > 0
    assert runs["r5"].verdict is not None

    # Every row judged the same extracted facts.
    extracted = extraction.facts(case_id).facts
    case_facts = {fact.fact_id for fact in extracted}
    steps = {row: verdict.steps(run.verdict_run_id).steps for row, run in runs.items()}
    for row in rows:
        assert steps[row][0].tool is ToolName.LIST_FACTS
        searched = {
            step.fact_id for step in steps[row] if step.tool is ToolName.SEARCH_RULES
        }
        assert searched and searched <= case_facts
        # The agent picks the facts it searches for, the same on every row
        # it runs on. On `r6` it picks nothing: every fact is searched for.
        assert (
            searched == case_facts
            if row == "r6"
            else searched
            == {
                step.fact_id
                for step in steps["r3"]
                if step.tool is ToolName.SEARCH_RULES
            }
        )

    # Story 3.8, row `r6`: the agent's own search loop is off. The steps
    # are the facts listed and one search per fact, in the facts' order,
    # each with the query the one query builder makes of the statement,
    # and each is one retrieve request to the knowledge base. No rule is
    # read and nothing else is searched.
    assert [
        (step.tool, step.fact_id, step.arguments.get("query"), step.outcome)
        for step in steps["r6"][1:]
    ] == [
        (
            ToolName.SEARCH_RULES,
            fact.fact_id,
            build_fact_query(fact.statement),
            StepOutcome.DONE,
        )
        for fact in extracted
    ]
    assert [
        sent["messages"][0]["content"][0]["text"] for sent in search_service.retrievals
    ] == [build_fact_query(fact.statement) for fact in extracted]
    # Then the model was asked once, with no tool to search or read with,
    # and composed its proposal from the facts and what those searches
    # returned.
    (composing,) = [body for body in verdict.model.requests if composing_of(body)]
    assert not composing.get("tools")
    material = json.loads(composing["messages"][-1]["content"])
    assert [fact["fact_id"] for fact in material["facts"]] == [
        fact.fact_id for fact in extracted
    ]
    returned = {
        rule_id: rule["text"]
        for search in material["searches"]
        for rule in search["rules"]
        for rule_id in rule["rule_ids"]
    }
    assert {rule_id for step in steps["r6"] for rule_id in step.rule_ids} == set(
        returned
    )
    # What is stored of it passed the same checks as on every row: each
    # reason cites a rule a search of the run returned, with the effect
    # the rule table gives that rule. Which rules those are depends on the
    # stand-in's ranking; a verdict, or a referral.
    r6 = runs["r6"]
    assert r6.verdict is not None
    for reason in r6.reasons:
        assert reason.rule_id in returned
        rule = rule_table()[reason.rule_id]
        assert (reason.effect is ReasonEffect.DECLINE) == bool(rule["decline"])
        assert (reason.debit_pct or 0) == (rule["debit_pct"] or 0)

    # `r2` is `r3` with the vector search alone, over the same `smart`
    # chunks: the verdict the rule table gives the case.
    expected_verdict, expected_loading, expected_reasons = expected_from_the_rule_table(
        EXPECTED_RULES["case-001"]
    )
    for row in ("r2", "r3"):
        run = runs[row]
        assert (run.verdict, run.loading_pct) == (expected_verdict, expected_loading)
        assert [
            (reason.rule_id, reason.effect, reason.debit_pct) for reason in run.reasons
        ] == expected_reasons

    # `r1` reads its rules from the `fixed` set: every rule read of that run
    # went to `retrieval` with `r1`, which answers the `fixed` chunk that
    # holds the rule's definition marker.
    asked = [
        target
        for behind in verdict.sidecars
        for app_id, method, target in behind.targets
        if app_id == "retrieval" and method == "GET"
    ]
    read_on_r1 = [
        rule_id
        for step in steps["r1"]
        if step.tool is ToolName.READ_RULE and step.outcome is StepOutcome.DONE
        for rule_id in step.rule_ids
    ]
    table = rule_table()
    # Whether the `fixed` chunk that holds an expected rule's marker also
    # holds its definition whole, threshold and rating, or cuts it off.
    whole_on_r1: dict[str, bool] = {}
    with verdict.retrieval.service() as retrieval:
        for rule_id in read_on_r1:
            target = f"/rules/{rule_id}?retriever_config=r1"
            assert target in asked
            answered = retrieval.get(target).json()
            assert answered["chunk_set"] == "fixed"
            assert f"Rule {rule_id}:" in answered["text"]
        for rule_id in EXPECTED_RULES["case-001"]:
            answered = retrieval.get(f"/rules/{rule_id}?retriever_config=r1").json()
            assert answered["chunk_set"] == "fixed"
            own = definition_in(answered["text"], rule_id) or ""
            whole_on_r1[rule_id] = (
                table[rule_id]["threshold"]["words"] in own and "Probable rating" in own
            )
    # Each row's reads named that row, and no other.
    assert {target.partition("retriever_config=")[2] for target in asked} <= set(rows)
    # A reason is stored only with the effect the rule's own definition
    # names inside its chunk. The rule table says what each definition
    # names.
    r1 = runs["r1"]
    for reason in r1.reasons:
        rule = table[reason.rule_id]
        assert (reason.effect is ReasonEffect.DECLINE) == bool(rule["decline"])
        assert (reason.debit_pct or 0) == (rule["debit_pct"] or 0)
        assert reason.rule_id in read_on_r1 or reason.effect is ReasonEffect.NONE
    cited = {reason.rule_id for reason in r1.reasons}
    for rule_id, whole in whole_on_r1.items():
        # A rule whose definition its chunk holds whole is read and cited,
        # as on the other rows. One the cut falls in (the marker in one
        # chunk, the threshold or the rating in the next) cannot be: nothing
        # shows the agent that the fact meets it, and nothing would bear out
        # its debit. That is the baseline's weakness, which the bake-off
        # measures as verdict accuracy; nothing here works around it.
        assert (rule_id in cited) == whole, (rule_id, whole)
        assert (rule_id in read_on_r1) == whole, (rule_id, whole)
    # The verdict is the one the rule table gives the rules the run could
    # cite: the case's own when every definition was whole, a lighter one
    # when a definition was cut off (today `standard` for this case, a miss
    # of row `r1` that the scoreboard is there to show), or a referral.
    if r1.verdict is not Verdict.REFER:
        assert (r1.verdict, r1.loading_pct) == expected_from_the_rule_table(
            [reason.rule_id for reason in r1.reasons]
        )[:2]
    else:
        assert r1.system_reasons
