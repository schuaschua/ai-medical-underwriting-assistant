"""Stories 2.5 and 2.6, against a real PostgreSQL.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The service runs as it really runs, as its own database role: the
agent on the Agent Framework, the gateway, the Dapr clients and the SQL
repository, with a transport where its Dapr sidecar would be (behind it
`extraction` and `retrieval` answer in the contracts' shapes) and a
transport where the chat deployment would be, answering a scripted
conversation. The whole path, with the real services and the model
stand-in, is tested beside the stand-ins (`packages/` tests).
"""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from verdict_fakes import (
    DEPLOYMENT,
    DM_25,
    DM_50,
    DM_DECLINE,
    HT_50,
    TOB_25,
    TRACE_ID,
    TRACEPARENT,
    FakeRules,
    ScriptedModel,
    Turn,
    UpstreamSidecar,
    as_service,
    connect,
    fact,
    final_answer,
    list_facts_call,
    read_call,
    reason,
    search_call,
)

from contracts.enums import (
    ReasonEffect,
    RetrieverConfig,
    StageStatus,
    StepOutcome,
    ToolName,
    Verdict,
)
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact
from contracts.models.verdict import (
    SUGGESTION_LABEL,
    AgentStep,
    AgentStepList,
    Reason,
    VerdictRun,
    VerdictRunList,
    VerdictRunResult,
)
from verdict.adapters.db import (
    SqlRunRepository,
    build_database,
)
from verdict.adapters.http.app import create_app
from verdict.adapters.migrations import (
    STEP_LOG_HAS_ROWS_MESSAGE,
    alembic_config,
    bundled_head,
)
from verdict.domain.entities import RunKey, Suggestion
from verdict.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
R3 = RetrieverConfig.R3


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def run_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT case_id::text, retriever_config, status, verdict, loading_pct, "
        "error_code FROM verdict.verdict_run ORDER BY started_at, verdict_run_id",
    )


def reason_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT position, rule_id, effect, debit_pct FROM verdict.reason "
        "ORDER BY verdict_run_id, position",
    )


def step_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT step_no, tool, outcome, error_code FROM verdict.agent_step "
        "ORDER BY agent_step_seq",
    )


@contextmanager
def service(
    settings: Settings, sidecar: UpstreamSidecar, model: ScriptedModel
) -> Iterator[TestClient]:
    """The service as it really runs, on this test's database."""
    app = create_app(settings, sidecar=sidecar.transport(), model=model.transport())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def command_for(case_id: str, **changes: Any) -> dict[str, Any]:
    return {"case_id": case_id, "retriever_config": "r3", **changes}


def ready(settings: Settings) -> int:
    # A new app each time: what a probe sees after the pipeline has migrated.
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        return int(client.get("/ready").status_code)


# --- Migrations and readiness -----------------------------------------------------------


def test_story_2_5_the_service_role_has_exactly_the_rights_the_migration_gives(
    migrated_database: Settings, service_settings: Settings
) -> None:
    role = service_settings.database_user
    granted = query(
        migrated_database,
        "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = %s GROUP BY table_name ORDER BY table_name",
        role,
    )

    assert granted == [
        # AD-15: added to and read. No UPDATE, no DELETE, no TRUNCATE.
        ("agent_step", "INSERT,SELECT"),
        # Readiness reads the revision; only migrations change it.
        ("alembic_version", "SELECT"),
        # Written with their run's result and never changed.
        ("reason", "INSERT,SELECT"),
        # Inserted, settled once, and removed if given up while running.
        ("verdict_run", "DELETE,INSERT,SELECT,UPDATE"),
    ]
    # Only migrations change the schema or its revision.
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("CREATE TABLE verdict.extra (id int)")
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("UPDATE verdict.alembic_version SET version_num = '0000'")
    assert query(
        migrated_database, "SELECT rolsuper FROM pg_roles WHERE rolname = %s", role
    ) == [(False,)]


def test_story_2_5_a_downgrade_is_refused_while_the_step_log_holds_steps(
    migrated_database: Settings, service_settings: Settings
) -> None:
    config = alembic_config(migrated_database)
    with a_repository(service_settings) as (repository, runner):
        runner.run(repository.append_step(a_step(new_id(), new_id(), 1)))

    with pytest.raises(RuntimeError) as raised:
        command.downgrade(config, "base")

    assert str(raised.value) == STEP_LOG_HAS_ROWS_MESSAGE
    # Neither the guard nor the table went: the step is there and protected.
    assert query(
        migrated_database, "SELECT version_num FROM verdict.alembic_version"
    ) == [(bundled_head(),)]
    assert len(step_rows(migrated_database)) == 1
    with (
        connect(migrated_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation),
    ):
        connection.execute("DELETE FROM verdict.agent_step")


def test_story_2_5_readiness_follows_the_schema_revision(
    empty_database: Settings,
) -> None:
    config = alembic_config(empty_database)
    as_the_service = as_service(empty_database)

    # No migration has run: the schema does not even exist. The service
    # never migrates at start-up: the pipeline does.
    assert ready(as_the_service) == 502
    assert query(
        empty_database,
        "SELECT count(*) FROM information_schema.schemata "
        "WHERE schema_name = 'verdict'",
    ) == [(0,)]

    command.upgrade(config, "head")
    assert ready(as_the_service) == 200

    # A revision this build does not know is not its head either.
    with connect(empty_database, autocommit=True) as connection:
        connection.execute("UPDATE verdict.alembic_version SET version_num = '9999'")
    assert ready(as_the_service) == 502
    with connect(empty_database, autocommit=True) as connection:
        connection.execute(
            "UPDATE verdict.alembic_version SET version_num = %s", (bundled_head(),)
        )

    command.downgrade(config, "base")
    assert ready(as_the_service) == 502

    command.upgrade(config, "head")
    assert ready(as_the_service) == 200


# --- The step log is append-only ------------------------------------------------------------


def a_step(
    verdict_run_id: str,
    case_id: str,
    step_no: int,
    tool: ToolName | None = ToolName.LIST_FACTS,
    **changes: Any,
) -> AgentStep:
    values: dict[str, Any] = {
        "verdict_run_id": verdict_run_id,
        "case_id": case_id,
        "step_no": step_no,
        "tool": tool,
        "arguments": {},
        "fact_id": None,
        "rule_ids": [],
        "outcome": StepOutcome.DONE,
        "error_code": None,
        "latency_ms": 4,
        "occurred_at": NOW,
        **changes,
    }
    return AgentStep.model_validate(values)


@contextmanager
def a_repository(
    settings: Settings,
) -> Iterator[tuple[SqlRunRepository, asyncio.Runner]]:
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlRunRepository(database), runner
        finally:
            runner.run(database.dispose())


CHANGES = [
    "UPDATE verdict.agent_step SET outcome = 'done'",
    "DELETE FROM verdict.agent_step",
    "TRUNCATE verdict.agent_step",
]
CHANGE_IDS = ["update", "delete", "truncate"]


def test_story_2_5_the_service_role_cannot_switch_off_what_guards_a_logged_step(
    service_settings: Settings,
) -> None:
    run_id, case_id = new_id(), new_id()
    with a_repository(service_settings) as (repository, runner):
        runner.run(
            repository.append_step(
                a_step(
                    run_id,
                    case_id,
                    1,
                    ToolName.READ_RULE,
                    outcome=StepOutcome.REFUSED,
                    error_code=ErrorCode.RULE_NOT_SEEN,
                )
            )
        )
    before = step_rows(service_settings)

    # AD-15: the role the service runs as holds no such right. (That it
    # holds SELECT and INSERT only is the test of its rights, above.)
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("ALTER TABLE verdict.agent_step DISABLE TRIGGER ALL")

    assert step_rows(service_settings) == before
    assert before == [(1, "read_rule", "refused", "rule_not_seen")]


@pytest.mark.parametrize("statement", CHANGES, ids=CHANGE_IDS)
def test_story_2_5_not_even_the_tables_owner_can_change_or_remove_a_logged_step(
    migrated_database: Settings, service_settings: Settings, statement: str
) -> None:
    with a_repository(service_settings) as (repository, runner):
        runner.run(repository.append_step(a_step(new_id(), new_id(), 1)))

    # The migration role owns the table (and here is a superuser as well):
    # no grant holds it back, so a trigger does.
    with (
        connect(migrated_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation, match="append-only"),
    ):
        connection.execute(statement)

    assert step_rows(migrated_database) == [(1, "list_facts", "done", None)]


# --- The repository -------------------------------------------------------------------------


def test_story_2_5_begins_that_arrive_together_insert_one_row(
    service_settings: Settings,
) -> None:
    key = RunKey(new_id(), R3)

    def begin(_: int) -> bool:
        with a_repository(service_settings) as (repository, runner):
            return runner.run(repository.begin(new_id(), key, NOW)) is None

    with ThreadPoolExecutor(max_workers=6) as pool:
        inserted = list(pool.map(begin, range(6)))

    assert inserted.count(True) == 1
    assert len(run_rows(service_settings)) == 1


def loaded(*reasons: Reason, loading_pct: int | None = 75) -> Suggestion:
    return Suggestion(
        verdict=Verdict.LOADED,
        loading_pct=loading_pct,
        confidence=0.85,
        reasons=reasons,
        system_reasons=(),
    )


def a_reason(
    rule_id: str, *fact_ids: str, effect: str = "debit", debit_pct: int | None = 50
) -> Reason:
    return Reason(
        rule_id=rule_id,
        fact_ids=list(fact_ids) or [new_id()],
        effect=ReasonEffect(effect),
        debit_pct=debit_pct,
    )


def test_story_2_6_a_result_and_its_reasons_are_stored_together_once_and_the_first_stands(
    service_settings: Settings,
) -> None:
    key = RunKey(new_id(), R3)
    run_id = new_id()
    first_fact, second_fact = new_id(), new_id()
    reasons = (
        a_reason(DM_50, first_fact, second_fact),
        a_reason(TOB_25, second_fact, debit_pct=25),
        a_reason(HT_50, first_fact, effect="none", debit_pct=None),
    )

    with a_repository(service_settings) as (repository, runner):
        runner.run(repository.begin(run_id, key, NOW))
        first = runner.run(
            repository.finish(run_id, '{"n": 1}', loaded(*reasons), None)
        )
        # A late finish, done or failed, changes nothing and adds no reason.
        late = runner.run(
            repository.finish(
                run_id, '{"n": 2}', loaded(a_reason(DM_25, debit_pct=25)), None
            )
        )
        failed_late = runner.run(
            repository.finish(run_id, '{"n": 3}', None, ErrorCode.STAGE_TIMEOUT)
        )
        row = runner.run(repository.find(key))
        (stored,) = runner.run(repository.runs_of_case(key.case_id, 10))

    assert (first, late, failed_late) == ('{"n": 1}', '{"n": 1}', '{"n": 1}')
    assert row is not None and (row.result_json, row.running) == ('{"n": 1}', False)
    # Read back as the contracts' run, reasons in the order they were kept.
    assert stored == VerdictRun(
        verdict_run_id=run_id,
        case_id=key.case_id,
        retriever_config=R3,
        status=StageStatus.DONE,
        verdict=Verdict.LOADED,
        loading_pct=75,
        confidence=0.85,
        reasons=list(reasons),
        system_reasons=[],
        error_code=None,
    )
    assert stored.label == SUGGESTION_LABEL
    assert run_rows(service_settings) == [
        (key.case_id, "r3", "done", "loaded", 75, None)
    ]
    assert reason_rows(service_settings) == [
        (0, DM_50, "debit", 50),
        (1, TOB_25, "debit", 25),
        (2, HT_50, "none", None),
    ]
    assert query(
        service_settings, "SELECT finished_at IS NOT NULL FROM verdict.verdict_run"
    ) == [(True,)]


def test_story_2_6_the_database_takes_a_debit_only_with_its_percentage_and_a_loading_only_when_loaded(
    service_settings: Settings,
) -> None:
    key = RunKey(new_id(), R3)
    run_id = new_id()
    with a_repository(service_settings) as (repository, runner):
        runner.run(repository.begin(run_id, key, NOW))

    insert = (
        "INSERT INTO verdict.reason (verdict_run_id, position, rule_id, fact_ids, "
        "effect, debit_pct) VALUES (%s, %s, 'UW-DM-002', %s, %s, %s)"
    )
    for position, (effect, debit_pct) in enumerate(
        [("debit", None), ("decline", 50), ("none", 0)]
    ):
        with (
            connect(service_settings) as connection,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            connection.execute(
                insert, (run_id, position, [new_id()], effect, debit_pct)
            )
    for verdict, loading_pct in (("standard", 25), ("loaded", None)):
        with (
            connect(service_settings) as connection,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            connection.execute(
                "UPDATE verdict.verdict_run SET verdict = %s, loading_pct = %s",
                (verdict, loading_pct),
            )
    # A reason belongs to a stored run.
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        connection.execute(insert, (new_id(), 0, [new_id()], "none", None))


def test_story_2_5_steps_are_appended_and_read_by_run_and_by_case_with_the_filters(
    service_settings: Settings,
) -> None:
    case_id, other_case = new_id(), new_id()
    first_run, second_run, other_run = new_id(), new_id(), new_id()
    fact_id = new_id()
    searched = a_step(
        first_run,
        case_id,
        2,
        ToolName.SEARCH_RULES,
        arguments={"query": "SECRET-QUERY", "fact_id": fact_id, "n": 3, "x": None},
        fact_id=fact_id,
        rule_ids=[DM_50, DM_25],
        latency_ms=17,
    )
    refused = a_step(
        first_run,
        case_id,
        3,
        ToolName.READ_RULE,
        arguments={"rule_id": HT_50},
        outcome=StepOutcome.REFUSED,
        error_code=ErrorCode.RULE_NOT_SEEN,
    )
    # Owner, 2026-10-08: the model asked for a tool that does not exist.
    unknown = a_step(
        first_run,
        case_id,
        4,
        None,
        asked_tool="delete_case",
        outcome=StepOutcome.REFUSED,
        error_code=ErrorCode.NOT_FOUND,
    )
    steps = [
        # Logged out of step order within the run, and runs interleaved.
        searched,
        a_step(second_run, case_id, 1),
        a_step(first_run, case_id, 1),
        a_step(other_run, other_case, 1, ToolName.READ_RULE, rule_ids=[DM_50]),
        refused,
        a_step(second_run, case_id, 2, ToolName.READ_RULE, rule_ids=[DM_50]),
        a_step(
            second_run,
            case_id,
            3,
            ToolName.SEARCH_RULES,
            outcome=StepOutcome.FAILED,
            error_code=ErrorCode.UPSTREAM_UNAVAILABLE,
        ),
        unknown,
    ]

    with a_repository(service_settings) as (repository, runner):
        for step in steps:
            runner.run(repository.append_step(step))

        def of_case(
            tool: ToolName | None = None,
            rule_id: str | None = None,
            limit: int = 50,
            after: tuple[str, int] | None = None,
        ) -> list[tuple[str, int]]:
            listed = runner.run(
                repository.steps_of_case(case_id, tool, rule_id, after, limit)
            )
            return [(step.verdict_run_id, step.step_no) for step in listed]

        of_run = runner.run(repository.steps_of_run(first_run, None, None, None, 50))
        first_two = runner.run(repository.steps_of_run(first_run, None, None, None, 2))
        of_no_run = runner.run(repository.steps_of_run(new_id(), None, None, None, 50))
        # Story 2.8: the cursor and the filters of the read by run.
        after_two = runner.run(repository.steps_of_run(first_run, None, None, 2, 50))
        run_searches = runner.run(
            repository.steps_of_run(first_run, ToolName.SEARCH_RULES, DM_50, None, 50)
        )
        after_third = of_case(after=(second_run, 1))
        searches_after_third = of_case(ToolName.SEARCH_RULES, after=(first_run, 2))
        after_no_step = of_case(after=(other_run, 1))
        everything = of_case()
        searches = of_case(ToolName.SEARCH_RULES)
        with_rule = of_case(rule_id=DM_50)
        reads_of_rule = of_case(ToolName.READ_RULE, DM_50)
        second_rule = of_case(rule_id=DM_25)
        unseen = of_case(rule_id=TOB_25)
        bounded = of_case(limit=3)

    # By run: by step number, whatever the order they were logged in.
    assert [step.step_no for step in of_run] == [1, 2, 3, 4]
    assert [step.step_no for step in first_two] == [1, 2]
    assert of_no_run == []
    # A step is read back as it was appended, its time in UTC.
    assert of_run[1] == searched
    assert of_run[2] == refused
    # A call to a tool that does not exist is listed when no tool is named,
    # with the name asked for, and under none of the three tools.
    assert of_run[3] == unknown
    assert of_run[1].occurred_at.tzinfo is UTC
    # By case: in the order logged, across the runs, and no other case's.
    assert everything == [
        (first_run, 2),
        (second_run, 1),
        (first_run, 1),
        (first_run, 3),
        (second_run, 2),
        (second_run, 3),
        (first_run, 4),
    ]
    assert searches == [(first_run, 2), (second_run, 3)]
    # The calls that returned or read the rule.
    assert with_rule == [(first_run, 2), (second_run, 2)]
    assert reads_of_rule == [(second_run, 2)]
    assert second_rule == [(first_run, 2)]
    assert unseen == []
    assert bounded == everything[:3]
    # Story 2.8: the rest is read from the last step seen on. By run that is
    # a step number; by case the step's place in the log, and a step the
    # case does not have gives nothing.
    assert [step.step_no for step in after_two] == [3, 4]
    assert [step.step_no for step in run_searches] == [2]
    assert after_third == everything[2:]
    assert searches_after_third == [(second_run, 3)]
    assert after_no_step == []


# --- The real service ---------------------------------------------------------------------


def a_loaded_case(case_id: str) -> tuple[UpstreamSidecar, list[Turn], Fact, Fact]:
    """A case whose two facts meet rules with debits of +50 and +25, and the conversation about it."""
    glucose = fact(case_id, "SECRET-FACT HbA1c 7.4 %")
    smoking = fact(case_id, "SECRET-FACT 20 cigarettes a day", verified=True)
    sidecar = UpstreamSidecar(
        facts=[glucose, smoking, fact(new_id(), "SECRET-FACT of another case")],
        rules=FakeRules(by_query={"glucose": [DM_50, DM_25], "smoking": [TOB_25]}),
    )
    turns: list[Turn] = [
        [list_facts_call()],
        [
            search_call(glucose.fact_id, "glucose"),
            search_call(smoking.fact_id, "smoking"),
        ],
        [read_call(DM_50), read_call(TOB_25), read_call(DM_DECLINE)],
        final_answer(
            reason(DM_50, glucose.fact_id),
            reason(TOB_25, smoking.fact_id, debit_pct=25),
            # Never seen in the run: not stored. It claims no debit and no
            # decline, so what is kept is no lighter than what was proposed.
            reason(DM_DECLINE, glucose.fact_id, effect="none"),
            verdict="decline",
            confidence=0.83,
        ),
    ]
    return sidecar, turns, glucose, smoking


def test_story_2_5_a_command_runs_the_agent_and_stores_a_loaded_run_with_reasons_and_steps(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    sidecar, turns, glucose, smoking = a_loaded_case(case_id)
    model = ScriptedModel(turns=turns)

    with service(service_settings, sidecar, model) as client:
        posted = client.post(
            "/verdict-runs",
            json=command_for(case_id),
            headers={"traceparent": TRACEPARENT},
        )
        runs = client.get(f"/cases/{case_id}/verdict-runs")
        result = VerdictRunResult.model_validate(posted.json())
        by_run = client.get(f"/verdict-runs/{result.verdict_run_id}/steps")
        by_case = client.get(f"/cases/{case_id}/agent-steps")
        reads_of_rule = client.get(
            f"/cases/{case_id}/agent-steps?tool=read_rule&rule_id={TOB_25}"
        )
        ready_status = client.get("/ready").status_code

    assert (posted.status_code, runs.status_code, ready_status) == (200, 200, 200)
    assert (by_run.status_code, by_case.status_code) == (200, 200)
    # The result: done, `loaded`, and the audit record of a suggestion.
    assert (result.status, result.verdict) == (StageStatus.DONE, Verdict.LOADED)
    assert (result.audit.action.value, result.audit.actor) == (
        "verdict.suggested",
        f"verdict:{DEPLOYMENT}",
    )
    # AD-15: the record names the retriever row, for `workflow` to match.
    assert result.audit.model_dump(mode="json")["detail"] == {"retriever_config": "r3"}
    assert (result.audit.ref, result.audit.trace_id, result.audit.page_id) == (
        result.verdict_run_id,
        TRACE_ID,
        None,
    )
    # The run as it is read: the label, the loading and the kept reasons.
    listed = VerdictRunList.model_validate(runs.json())
    (run,) = listed.verdict_runs
    assert runs.json()["verdict_runs"][0]["label"] == "AI suggestion, not a decision"
    assert (run.verdict, run.loading_pct, run.confidence, run.system_reasons) == (
        Verdict.LOADED,
        75,
        0.83,
        [],
    )
    assert [
        (item.rule_id, item.fact_ids, item.effect.value, item.debit_pct)
        for item in run.reasons
    ] == [
        (DM_50, [glucose.fact_id], "debit", 50),
        (TOB_25, [smoking.fact_id], "debit", 25),
    ]
    # The steps list every tool call in order, the refused one included.
    steps = AgentStepList.model_validate(by_run.json()).steps
    assert [
        (step.step_no, step.tool, step.outcome.value, step.rule_ids) for step in steps
    ] == [
        (1, "list_facts", "done", []),
        (2, "search_rules", "done", [DM_50, DM_25]),
        (3, "search_rules", "done", [TOB_25]),
        (4, "read_rule", "done", [DM_50]),
        (5, "read_rule", "done", [TOB_25]),
        (6, "read_rule", "refused", []),
    ]
    assert steps[5].error_code is ErrorCode.RULE_NOT_SEEN
    assert AgentStepList.model_validate(by_case.json()).steps == steps
    assert [
        step.step_no
        for step in AgentStepList.model_validate(reads_of_rule.json()).steps
    ] == [5]
    # AD-15: every stored reason's rule is in a step of its run, and each of
    # its facts was listed in the run (the case's own facts, and no other's).
    seen = {rule_id for step in steps for rule_id in step.rule_ids}
    assert {item.rule_id for item in run.reasons} <= seen
    assert {fact_id for item in run.reasons for fact_id in item.fact_ids} <= {
        glucose.fact_id,
        smoking.fact_id,
    }
    # One chat completion per turn, on the deployment the settings name.
    assert model.calls == 4
    assert {body["model"] for body in model.bodies()} == {DEPLOYMENT}
    # The facts once, the two searches with the server's row, the two reads
    # that were allowed; the refused read never reached `retrieval`.
    assert sidecar.paths() == [
        ("GET", f"/v1.0/invoke/extraction/method/cases/{case_id}/facts"),
        ("POST", "/v1.0/invoke/retrieval/method/searches"),
        ("POST", "/v1.0/invoke/retrieval/method/searches"),
        ("GET", f"/v1.0/invoke/retrieval/method/rules/{DM_50}"),
        ("GET", f"/v1.0/invoke/retrieval/method/rules/{TOB_25}"),
    ]
    assert {
        request.url.params["retriever_config"] for request in sidecar.requests[3:]
    } == {"r3"}
    # The caller's trace goes on to the other services.
    assert all(
        request.headers["traceparent"].split("-")[1] == TRACE_ID
        for request in sidecar.requests
    )
    # What the database holds.
    assert run_rows(service_settings) == [(case_id, "r3", "done", "loaded", 75, None)]
    assert reason_rows(service_settings) == [
        (0, DM_50, "debit", 50),
        (1, TOB_25, "debit", 25),
    ]
    assert len(step_rows(service_settings)) == 6
    # No column and no payload holds the agent's own verdict word.
    assert "decline" not in runs.text


def test_story_2_5_a_repeat_is_answered_from_the_database_without_a_model_call(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    sidecar, turns, _, _ = a_loaded_case(case_id)
    model = ScriptedModel(turns=turns)

    with service(service_settings, sidecar, model) as client:
        first = client.post("/verdict-runs", json=command_for(case_id))
        calls, reads = model.calls, len(sidecar.requests)
    # Another process, as after a restart.
    with service(service_settings, sidecar, model) as client:
        again = client.post("/verdict-runs", json=command_for(case_id))
        runs = client.get(f"/cases/{case_id}/verdict-runs").json()["verdict_runs"]

    assert again.status_code == 200
    assert again.json() == first.json()
    # No model call, no call of another service, no second run and no new step.
    assert (model.calls, len(sidecar.requests)) == (calls, reads)
    assert len(runs) == 1
    assert len(run_rows(service_settings)) == 1
    assert len(step_rows(service_settings)) == 6


def test_story_2_6_a_case_with_no_facts_is_referred_with_no_model_call(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    # Its pages were all discarded or denied: `extraction` holds nothing for it.
    sidecar = UpstreamSidecar(facts=[fact(new_id())])
    model = ScriptedModel(turns=[final_answer()])

    with service(service_settings, sidecar, model) as client:
        posted = client.post("/verdict-runs", json=command_for(case_id))
        (run,) = client.get(f"/cases/{case_id}/verdict-runs").json()["verdict_runs"]
        steps = client.get(f"/verdict-runs/{run['verdict_run_id']}/steps").json()

    result = VerdictRunResult.model_validate(posted.json())
    assert (result.status, result.verdict) == (StageStatus.DONE, Verdict.REFER)
    assert result.audit.action.value == "verdict.suggested"
    assert (run["verdict"], run["system_reasons"]) == ("refer", ["no_matching_rule"])
    assert (run["confidence"], run["reasons"], run["loading_pct"]) == (None, [], None)
    assert steps == {"steps": [], "has_more": False}
    assert model.calls == 0
    assert len(sidecar.requests) == 1


def test_story_2_5_a_run_that_fails_is_stored_as_failed_with_no_reason_and_its_steps_kept(
    service_settings: Settings,
) -> None:
    final, error_code, model_calls = (
        "prose, not the object that was asked for",
        "invalid_model_output",
        2,
    )
    case_id = new_id()
    held = fact(case_id)
    sidecar = UpstreamSidecar(facts=[held], rules=FakeRules(default=[DM_50]))
    model = ScriptedModel(turns=[[list_facts_call(), search_call(held.fact_id)], final])
    settings = service_settings.model_copy(
        update={"model_max_retries": 1, "model_retry_seconds": 0.001}
    )

    with service(settings, sidecar, model) as client:
        posted = client.post("/verdict-runs", json=command_for(case_id))
        (run,) = VerdictRunList.model_validate(
            client.get(f"/cases/{case_id}/verdict-runs").json()
        ).verdict_runs
        steps = client.get(f"/verdict-runs/{run.verdict_run_id}/steps").json()["steps"]
        again = client.post("/verdict-runs", json=command_for(case_id))

    # 200: the stored result, which `workflow` records and fails the case with.
    assert posted.status_code == 200
    result = VerdictRunResult.model_validate(posted.json())
    assert (result.status.value, result.error_code, result.verdict) == (
        "failed",
        error_code,
        None,
    )
    assert result.audit.action.value == "stage.failed"
    assert (run.status.value, run.error_code, run.reasons, run.verdict) == (
        "failed",
        error_code,
        [],
        None,
    )
    assert model.calls == model_calls
    assert run_rows(service_settings) == [
        (case_id, "r3", "failed", None, None, error_code)
    ]
    assert reason_rows(service_settings) == []
    # What the agent did before it failed is in the log, and stays there.
    assert [(step["step_no"], step["tool"]) for step in steps] == [
        (1, "list_facts"),
        (2, "search_rules"),
    ]
    # The failure is the run's result: a repeat does not run it again.
    assert again.json() == posted.json()
    assert model.calls == model_calls


def test_story_2_5_a_fault_that_passes_fails_no_case_the_key_row_is_released_and_the_command_sent_again_runs(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    held = fact(case_id)
    sidecar = UpstreamSidecar(facts=[held], rules=FakeRules(default=[DM_50]))
    opening: Turn = [list_facts_call(), search_call(held.fact_id)]
    # The model is throttled through every retry of the gateway.
    down = ScriptedModel(turns=[opening, 429])
    settings = service_settings.model_copy(
        update={
            "model_max_retries": 1,
            "model_retry_seconds": 0.001,
            "upstream_retry_seconds": 0.001,
        }
    )

    with service(settings, sidecar, down) as client:
        posted = client.post("/verdict-runs", json=command_for(case_id))
        runs = client.get(f"/cases/{case_id}/verdict-runs").json()["verdict_runs"]
    logged = step_rows(service_settings)
    # The fault has passed, and `workflow`'s stage retry sends the command again.
    recovered = ScriptedModel(
        turns=[opening, [read_call(DM_50)], final_answer(reason(DM_50, held.fact_id))]
    )
    with service(settings, sidecar, recovered) as client:
        again = client.post("/verdict-runs", json=command_for(case_id))

    # Not a stored failure, which would fail the case at its last step: the
    # error is answered, and nothing holds the case and row.
    assert posted.status_code == 503
    assert ErrorBody.model_validate(posted.json()).error.code.value == (
        "model_unavailable"
    )
    assert runs == []
    # What the released run did is in the log, and stays there.
    assert [row[:2] for row in logged][:1] == [(1, "list_facts")]
    assert (again.status_code, again.json()["status"], again.json()["verdict"]) == (
        200,
        "done",
        "loaded",
    )
    assert run_rows(service_settings) == [(case_id, "r3", "done", "loaded", 50, None)]


def test_story_2_5_the_step_limit_of_the_settings_stops_the_real_agent_and_refers_the_case(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    sidecar = UpstreamSidecar(facts=[fact(case_id)])
    # The model never answers: every turn is one more tool call.
    model = ScriptedModel(turns=[[list_facts_call()]])
    settings = service_settings.model_copy(update={"step_limit": 4})

    with service(settings, sidecar, model) as client:
        posted = client.post("/verdict-runs", json=command_for(case_id))
        (run,) = client.get(f"/cases/{case_id}/verdict-runs").json()["verdict_runs"]

    assert (posted.status_code, posted.json()["status"]) == (200, "done")
    assert (run["verdict"], run["system_reasons"], run["confidence"]) == (
        "refer",
        ["step_limit"],
        None,
    )
    # The fifth call was not made, and the log says where the run stopped.
    assert [row[0] for row in step_rows(service_settings)] == [1, 2, 3, 4, 5]
    assert step_rows(service_settings)[-1][2:] == ("refused", "step_limit")
    assert model.calls == 5


def test_story_2_5_a_run_under_way_is_in_progress_and_a_stale_one_is_settled_as_failed(
    service_settings: Settings,
) -> None:
    under_way, stale = new_id(), new_id()
    under_way_run, stale_run = new_id(), new_id()
    sidecar = UpstreamSidecar(facts=[fact(under_way), fact(stale)])
    model = ScriptedModel(turns=[final_answer()])
    with a_repository(service_settings) as (repository, runner):
        runner.run(
            repository.begin(under_way_run, RunKey(under_way, R3), datetime.now(UTC))
        )
        # Left `running` by a process that died an hour ago, with a step logged.
        runner.run(
            repository.begin(
                stale_run, RunKey(stale, R3), datetime.now(UTC) - timedelta(hours=1)
            )
        )
        runner.run(repository.append_step(a_step(stale_run, stale, 1)))

    with service(service_settings, sidecar, model) as client:
        busy = client.post("/verdict-runs", json=command_for(under_way))
        settled = client.post("/verdict-runs", json=command_for(stale))
        steps = client.get(f"/verdict-runs/{stale_run}/steps").json()["steps"]

    assert busy.status_code == 409
    assert ErrorBody.model_validate(busy.json()).error.code is ErrorCode.IN_PROGRESS
    # Nobody works on the stale one any more: it fails under its own id, and
    # the work is not taken over. Its steps stay in the log.
    result = VerdictRunResult.model_validate(settled.json())
    assert (result.status.value, result.error_code, result.verdict_run_id) == (
        "failed",
        "stage_timeout",
        stale_run,
    )
    assert [step["step_no"] for step in steps] == [1]
    assert (model.calls, sidecar.requests) == (0, [])
