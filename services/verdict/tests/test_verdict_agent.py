"""Story 2.5: the verdict agent on Microsoft Agent Framework, against a model stub.

Unit tests: the framework runs for real, on the gateway's own client, and a
transport stands in for the chat deployment with a scripted conversation
(coding-style rule 23). No database, no other service and no network: the
tools answer from in-memory stand-ins, and the step log is kept in memory.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from verdict_fakes import (
    DEPLOYMENT,
    DM_25,
    DM_50,
    HT_50,
    FakeRules,
    MemoryRepository,
    ScriptedModel,
    Turn,
    fact,
    final_answer,
    list_facts_call,
    read_call,
    reason,
    search_call,
    tool_call,
)

from contracts.enums import RetrieverConfig, StepOutcome
from contracts.errors import ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact
from verdict.adapters.agent import (
    AGENT_NAME,
    FRAMEWORK_EVENT,
    RESPONSE_FORMAT,
    TASK,
    TOOLS,
    FrameworkVerdictAgent,
)
from verdict.adapters.model import ModelGateway, build_model_client, chat_deployment
from verdict.domain.entities import AgentAnswer
from verdict.domain.ports import AgentFailed, ModelCallFailed
from verdict.domain.toolbox import (
    INVALID_ARGUMENTS_MESSAGE,
    Toolbox,
    ToolFailed,
    ToolPorts,
)
from verdict.prompts import SUGGEST_VERDICT, load_prompt
from verdict.settings import Settings

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SERVICE_DIR = Path(__file__).resolve().parents[1]
# What must never reach a log: the facts, the queries, the rules' text, the
# prompt, the model's answer and the endpoint's own messages.
SECRETS = (
    "SECRET",
    "HbA1c",
    "Probable rating",
    "glucose",
    "underwriting",
    "confidence",
)


@dataclass
class Run:
    """One run of the agent against the model stub, and everything it left behind."""

    model: ScriptedModel
    toolbox: Toolbox
    repository: MemoryRepository
    rules: FakeRules
    facts: list[Fact]
    waits: list[float] = field(default_factory=list)
    answer: AgentAnswer | None = None
    error: BaseException | None = None

    def steps(self) -> list[tuple[int, str, str, str | None]]:
        return [
            (
                step.step_no,
                step.tool.value,
                step.outcome.value,
                step.error_code.value if step.error_code is not None else None,
            )
            for step in self.repository.steps
        ]

    def told(self) -> list[dict[str, Any]]:
        """What the tools answered the model, in order: the tool messages of the last request."""
        messages = self.model.bodies()[-1]["messages"]
        return [
            json.loads(message["content"])
            for message in messages
            if message["role"] == "tool"
        ]


def run_agent(
    settings: Settings,
    *turns: Turn,
    step_limit: int = 30,
    rules: FakeRules | None = None,
    repository: MemoryRepository | None = None,
    fact_count: int = 1,
    headers: dict[str, str] | None = None,
    retriever_config: RetrieverConfig = RetrieverConfig.R3,
    case_id: str | None = None,
    facts: list[Fact] | None = None,
) -> Run:
    """Run the real agent once on a scripted model; errors are kept, not raised."""
    case = case_id or new_id()
    held = (
        facts
        if facts is not None
        else [
            fact(case, f"SECRET-FACT HbA1c 7.{number} %")
            for number in range(fact_count)
        ]
    )
    library = rules or FakeRules(default=[DM_50])
    log = repository or MemoryRepository()
    model = ScriptedModel(turns=list(turns), headers=headers or {})
    toolbox = Toolbox(
        verdict_run_id=new_id(),
        case_id=case,
        retriever_config=retriever_config,
        facts=held,
        ports=ToolPorts(rules=library, repository=log),
        trace_context={},
        now=lambda: NOW,
        step_limit=step_limit,
    )
    run = Run(model, toolbox, log, library, held)

    async def sleep(seconds: float) -> None:
        run.waits.append(seconds)

    async def scenario() -> None:
        gateway = ModelGateway(
            build_model_client(settings, model.transport()),
            deployment=chat_deployment(settings),
            max_retries=settings.model_max_retries,
            max_completion_tokens=settings.model_max_completion_tokens,
            sleep=sleep,
            jitter=lambda: 1.0,
        )
        try:
            agent = FrameworkVerdictAgent(gateway, step_limit=step_limit)
            run.answer = await agent.run(toolbox)
        except Exception as error:  # noqa: BLE001 - the test looks at whatever was raised
            run.error = error
        finally:
            await gateway.aclose()

    asyncio.run(scenario())
    return run


def a_case(count: int = 1) -> tuple[str, list[Fact]]:
    case_id = new_id()
    return case_id, [
        fact(case_id, f"SECRET-FACT HbA1c 7.{number} %") for number in range(count)
    ]


# --- What the model is sent ---------------------------------------------------------------


def test_story_2_5_the_agents_first_request_carries_the_prompt_three_tools_and_the_answer_schema(
    settings: Settings,
) -> None:
    run = run_agent(settings, final_answer())

    assert run.error is None
    (request,) = run.model.requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:5101/openai/v1/chat/completions"
    (body,) = run.model.bodies()
    # AD-16: the deployment name, from the settings, is the model that is asked.
    assert body["model"] == DEPLOYMENT
    system, user = body["messages"]
    # The prompt from the package is the instruction. The task names no
    # case, no run and no retriever row: the server fixes them.
    assert system == {"role": "system", "content": load_prompt(SUGGEST_VERDICT)}
    assert user == {"role": "user", "content": TASK}
    for server_side in (run.toolbox.case_id, run.toolbox.verdict_run_id, "r3"):
        assert server_side not in request.content.decode()
    # AD-15: exactly three tools, and no fourth.
    assert [tool["function"]["name"] for tool in body["tools"]] == [
        "list_facts",
        "search_rules",
        "read_rule",
    ]
    assert {tool["type"] for tool in body["tools"]} == {"function"}
    assert [
        (tool["function"]["name"], tool["function"]["description"])
        for tool in body["tools"]
    ] == [(name.value, description) for name, description, _ in TOOLS]
    assert [tool["function"]["parameters"] for tool in body["tools"]] == [
        schema for _, _, schema in TOOLS
    ]
    # Structured output: the object the contracts model describes.
    assert body["response_format"] == RESPONSE_FORMAT
    assert body["response_format"]["json_schema"]["name"] == "verdict_output"
    # The gateway fixes the room of an answer; the answer is not streamed.
    assert body["max_completion_tokens"] == settings.model_max_completion_tokens
    assert "max_tokens" not in body
    assert not body.get("stream")
    assert AGENT_NAME == "verdict"


# --- A conversation ------------------------------------------------------------------------


def test_story_2_5_a_conversation_logs_one_step_per_tool_call_in_order_and_returns_the_answer(
    settings: Settings,
) -> None:
    case_id, facts = a_case()
    (held,) = facts
    proposal = final_answer(reason(DM_50, held.fact_id))

    run = run_agent(
        settings,
        [list_facts_call()],
        [search_call(held.fact_id)],
        [read_call(DM_50)],
        proposal,
        case_id=case_id,
        facts=facts,
    )

    assert run.error is None
    # The final text as the model gave it: the domain parses it.
    assert run.answer == AgentAnswer(proposal, "stop")
    # One chat completion per turn, each through the gateway.
    assert run.model.calls == 4
    # One step per tool call, in the order the model made them.
    assert run.steps() == [
        (1, "list_facts", "done", None),
        (2, "search_rules", "done", None),
        (3, "read_rule", "done", None),
    ]
    listed, searched, read = run.repository.steps
    assert {step.verdict_run_id for step in run.repository.steps} == {
        run.toolbox.verdict_run_id
    }
    assert {step.case_id for step in run.repository.steps} == {case_id}
    assert (listed.arguments, listed.fact_id, listed.rule_ids) == ({}, None, [])
    assert searched.arguments == {
        "query": "SECRET-QUERY glucose",
        "fact_id": held.fact_id,
    }
    assert (searched.fact_id, searched.rule_ids) == (held.fact_id, [DM_50])
    assert (read.arguments, read.rule_ids) == ({"rule_id": DM_50}, [DM_50])
    # The server fixed the retriever row of the search and of the read.
    assert run.rules.searches == [("SECRET-QUERY glucose", RetrieverConfig.R3, 5)]
    assert run.rules.reads == [(DM_50, RetrieverConfig.R3)]
    # The tools' answers went back to the model as the toolbox gave them.
    facts_told, rules_told, rule_told = run.told()
    assert [item["fact_id"] for item in facts_told["facts"]] == [held.fact_id]
    assert rules_told["rules"][0]["rule_ids"] == [DM_50]
    assert (rule_told["rule_id"], rule_told["reference_rule_ids"]) == (DM_50, [HT_50])
    # The run's memory is what the domain decides from.
    assert run.toolbox.state.steps == 3
    assert run.toolbox.state.saw_rule(DM_50)
    assert set(run.toolbox.state.facts_listed) == {held.fact_id}


@pytest.mark.parametrize(
    ("call", "logged"),
    [
        # The model may not name a case, a run or a retriever row.
        (
            tool_call("read_rule", {"rule_id": DM_50, "retriever_config": "r5"}),
            {"rule_id": DM_50, "retriever_config": "r5"},
        ),
    ],
    ids=["names-a-row"],
)
def test_story_2_5_a_tool_call_with_invalid_arguments_is_a_refused_step_and_the_run_goes_on(
    settings: Settings, call: dict[str, Any], logged: dict[str, Any]
) -> None:
    proposal = final_answer()

    run = run_agent(settings, [call], [list_facts_call()], proposal)

    assert run.error is None
    assert run.answer == AgentAnswer(proposal, "stop")
    refused, listed = run.repository.steps
    assert (refused.step_no, refused.outcome, refused.error_code) == (
        1,
        StepOutcome.REFUSED,
        ErrorCode.VALIDATION_FAILED,
    )
    assert (refused.arguments, refused.rule_ids, refused.fact_id) == (logged, [], None)
    # The run went on: the next call is the next step.
    assert (listed.step_no, listed.outcome) == (2, StepOutcome.DONE)
    # Nothing was asked of `retrieval` for a call that was refused.
    assert (run.rules.searches, run.rules.reads) == ([], [])
    # The model is told why, in plain words, and nothing of the internals.
    assert run.told()[0] == {
        "refused": "validation_failed",
        "message": INVALID_ARGUMENTS_MESSAGE,
    }


def test_story_2_5_a_fourth_tool_does_not_exist_and_is_no_step(
    settings: Settings,
) -> None:
    proposal = final_answer()

    run = run_agent(
        settings,
        [tool_call("delete_case", {"case_id": "SECRET-ARGUMENT"})],
        [list_facts_call()],
        proposal,
    )

    assert run.error is None
    assert run.answer == AgentAnswer(proposal, "stop")
    # The log holds the calls of the three tools, and nothing was run.
    assert run.steps() == [(1, "list_facts", "done", None)]
    messages = run.model.bodies()[-1]["messages"]
    refusal = next(message for message in messages if message["role"] == "tool")
    assert "not found" in refusal["content"]


# --- Logs ------------------------------------------------------------------------------------


def test_story_2_5_nothing_of_prompts_facts_queries_rules_or_answers_reaches_the_logs(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    case_id, facts = a_case()
    (held,) = facts
    proposal = final_answer(reason(DM_50, held.fact_id), confidence=0.42)

    # Everything the service and the framework would say, at their most talkative.
    with (
        caplog.at_level(logging.DEBUG, logger="verdict"),
        caplog.at_level(logging.DEBUG, logger="agent_framework"),
    ):
        run = run_agent(
            settings,
            [list_facts_call()],
            [
                search_call(held.fact_id),
                tool_call("search_rules", {"query": "SECRET-QUERY bad"}),
            ],
            [read_call(DM_50), read_call(DM_25)],
            [tool_call("delete_case", {"case_id": "SECRET-ARGUMENT"})],
            500,
            proposal,
            case_id=case_id,
            facts=facts,
        )
        failed = run_agent(
            settings,
            [list_facts_call()],
            [search_call(held.fact_id)],
            case_id=case_id,
            facts=facts,
            rules=FakeRules(fail_search=True),
        )
        refused = run_agent(settings, 404)
        unreadable = run_agent(settings, {"SECRET-BODY": 1})

    assert run.answer == AgentAnswer(proposal, "stop")
    assert isinstance(failed.error, ToolFailed)
    assert isinstance(refused.error, ModelCallFailed)
    assert isinstance(unreadable.error, AgentFailed)
    # Ids, codes, counts and timings are there ...
    assert f"case_id={case_id}" in caplog.text
    assert f"verdict_run_id={run.toolbox.verdict_run_id}" in caplog.text
    assert (
        "tool=search_rules outcome=refused error_code=validation_failed" in caplog.text
    )
    assert "tool=read_rule outcome=refused error_code=rule_not_seen" in caplog.text
    assert (
        "tool=search_rules outcome=failed error_code=upstream_unavailable"
        in caplog.text
    )
    assert "agent run:" in caplog.text
    # ... the framework's own records are there too, reduced to where they
    # were made ...
    framework_records = [
        record for record in caplog.records if record.name.startswith("agent_framework")
    ]
    assert framework_records
    for record in framework_records:
        assert record.getMessage().startswith(f"{FRAMEWORK_EVENT} at=")
        assert record.exc_info is None
    # ... and nothing that was said by anyone (security rule 31).
    for secret in (
        *SECRETS,
        held.statement,
        held.quote,
        load_prompt(SUGGEST_VERDICT)[:40],
    ):
        assert secret not in caplog.text, secret
