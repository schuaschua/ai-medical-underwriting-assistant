"""Story 1.10, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
`workflow` runs as it really runs, with a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command and
`classification` the classify command, in the contracts' shapes.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationStatus
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from workflow_fakes import (
    FakeEngine,
    FakeStages,
    SidecarStandIn,
    classification_done,
    classification_failed,
    redaction_done,
)
from workflow_local import connect, wait_for_case_status

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
    StopAfter,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRecorded, DecisionRequest
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.adapters.http.app import create_app
from workflow.adapters.migrations import alembic_config
from workflow.adapters.scheduler import build_client
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.settings import Settings

pytestmark = pytest.mark.integration

FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
# What the classifier reads, by page number: two non-medical pages it is sure
# of (the customer is asked), one page it is not sure of (triage), and a
# medical page it is sure of (extraction).
READINGS = {
    1: ("other", False, 0.96),
    2: ("invoice", False, 1.0),
    3: ("lab_report", True, 0.6),
    4: ("lab_report", True, 0.95),
}
CUSTOMER = {"actor": "customer"}
UNDERWRITER = {"actor": "underwriter"}


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def decision_rows(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT page_id::text, decision, actor, decision_id::text "
        "FROM workflow.human_decision WHERE case_id = %s ORDER BY occurred_at, decision_id",
        case_id,
    )


def human_events(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT action, actor, actor_kind, page_id::text, ref::text, eval_run_id::text "
        "FROM workflow.audit_event WHERE case_id = %s AND actor_kind = 'human' "
        "ORDER BY occurred_at, recorded_at, audit_event_id",
        case_id,
    )


def page_statuses(settings: Settings, case_id: str) -> list[str]:
    return [
        row[0]
        for row in query(
            settings,
            "SELECT page_status FROM workflow.page_status WHERE case_id = %s "
            "ORDER BY page_number",
            case_id,
        )
    ]


def case_status(settings: Settings, case_id: str) -> str:
    return str(
        query(
            settings,
            "SELECT case_status FROM workflow.case_status WHERE case_id = %s",
            case_id,
        )[0][0]
    )


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


@contextlib.contextmanager
def workflow_service(
    settings: Settings, sidecar: httpx.AsyncBaseTransport
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(settings.model_copy(update=FAST_RETRIES), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


def post(
    client: TestClient, case_id: str, page_id: str, decision: str, who: dict[str, str]
) -> Any:
    return client.post(
        f"/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision, **who},
    )


def code_of(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def test_story_1_10_decisions_move_a_started_case_page_by_page_to_its_end(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=4, readings=READINGS))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json={"eval_run_id": eval_run_id})
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        first, second, third, fourth = (page["page_id"] for page in waiting["pages"])
        assert [page["page_status"] for page in waiting["pages"]] == [
            "awaiting_customer",
            "awaiting_customer",
            "awaiting_triage",
            "extracting",
        ]

        # What is not allowed changes nothing.
        refusals = [
            code_of(post(client, case_id, first, "accept", CUSTOMER)),
            code_of(post(client, case_id, first, "keep", UNDERWRITER)),
            code_of(post(client, case_id, first, "keep", {"actor": "workflow"})),
            code_of(post(client, case_id, third, "discard", CUSTOMER)),
            code_of(post(client, case_id, fourth, "accept", UNDERWRITER)),
            code_of(post(client, case_id, new_id(), "keep", CUSTOMER)),
            code_of(post(client, new_id(), first, "keep", CUSTOMER)),
        ]
        assert page_statuses(service_settings, case_id) == [
            page["page_status"] for page in waiting["pages"]
        ]
        assert decision_rows(service_settings, case_id) == []

        # The customer discards one waiting page and keeps the other.
        discarded = post(client, case_id, first, "discard", CUSTOMER)
        kept = post(client, case_id, second, "keep", CUSTOMER)
        after_customer = client.get(f"/cases/{case_id}/progress").json()
        # The same decision again; and the other decision for the same page.
        discarded_again = post(client, case_id, first, "discard", CUSTOMER)
        keep_after_discard = code_of(post(client, case_id, first, "keep", CUSTOMER))
        still_running = scheduler_client.get_orchestration_state(case_id)

        # The underwriter answers for the kept page and the unsure one.
        denied = post(client, case_id, second, "deny", UNDERWRITER)
        accepted = post(client, case_id, third, "accept", UNDERWRITER)
        ended = scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        progress = client.get(f"/cases/{case_id}/progress").json()
        # A decision repeated after the lifecycle has ended is still answered.
        accepted_again = post(client, case_id, third, "accept", UNDERWRITER)

    assert refusals == [
        (403, "role_not_allowed"),
        (403, "role_not_allowed"),
        (403, "actor_not_human"),
        (409, "not_awaiting_decision"),
        (409, "not_awaiting_decision"),
        (404, "not_found"),
        (404, "not_found"),
    ]
    for response in (discarded, kept, denied, accepted):
        assert response.status_code == 200
    assert DecisionRecorded.model_validate(discarded.json()).page_status.value == (
        "discarded"
    )
    assert kept.json()["page_status"] == "awaiting_triage"
    # After the customer: the kept page and the unsure one wait for the
    # underwriter, so the case still waits for a human.
    assert [page["page_status"] for page in after_customer["pages"]] == [
        "discarded",
        "awaiting_triage",
        "awaiting_triage",
        "extracting",
    ]
    assert after_customer["case_status"] == "awaiting_human"
    assert still_running is not None
    assert still_running.runtime_status is OrchestrationStatus.RUNNING
    # The repeat is the stored decision; the other decision is refused.
    assert discarded_again.status_code == 200
    assert discarded_again.json() == discarded.json()
    assert keep_after_discard == (409, "not_awaiting_decision")
    assert accepted_again.json() == accepted.json()

    # In the end: no page waits, two are in work (no extraction before
    # story 2.4), so the case runs and its lifecycle has ended.
    assert [page["page_status"] for page in progress["pages"]] == [
        "discarded",
        "denied",
        "extracting",
        "extracting",
    ]
    assert progress["case_status"] == "running"
    assert ended is not None
    assert ended.runtime_status is OrchestrationStatus.COMPLETED
    assert json.loads(ended.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "running",
    }
    # One row per decision, and one event each: actor kind `human`, the
    # actor the demo role, the reference the decision's id, the case's run.
    rows = decision_rows(service_settings, case_id)
    assert [row[:3] for row in rows] == [
        (first, "discard", "customer"),
        (second, "keep", "customer"),
        (second, "deny", "underwriter"),
        (third, "accept", "underwriter"),
    ]
    assert human_events(service_settings, case_id) == [
        (action, actor, "human", page_id, decision_id, eval_run_id)
        for action, (page_id, _, actor, decision_id) in zip(
            ["page.discarded", "page.kept", "page.denied", "page.accepted"],
            rows,
            strict=True,
        )
    ]


def test_story_1_10_a_case_whose_waiting_pages_are_all_discarded_is_completed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(pages=2, readings={1: READINGS[1], 2: READINGS[2]})
    )
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        for page in waiting["pages"]:
            assert (
                post(client, case_id, page["page_id"], "discard", CUSTOMER).status_code
                == 200
            )
        ended = scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        progress = client.get(f"/cases/{case_id}/progress").json()
        # A completed case takes no decision.
        late = code_of(
            post(client, case_id, waiting["pages"][0]["page_id"], "keep", CUSTOMER)
        )

    assert progress["case_status"] == "completed"
    assert [page["page_status"] for page in progress["pages"]] == ["discarded"] * 2
    assert ended is not None
    assert json.loads(ended.serialized_output or "")["case_status"] == "completed"
    assert late == (409, "not_awaiting_decision")


# --- The real store ---------------------------------------------------------------


@contextlib.contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


def gated_case(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
) -> tuple[str, list[str]]:
    """A started case whose pages the gate has routed, and that is not settled yet."""
    case_id = new_id()
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(new_case(case_id, parameters, NOW))
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        for page_id, route in zip(page_ids, routes, strict=True):
            done = classification_done(case_id, page_id)
            await record_stage_result(done, store=store)
            await record_route(
                case_id, page_id, done.classification_id, route, 0.9, store=store
            )

    runner.run(scenario())
    return case_id, page_ids


def decide(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
) -> DecisionRecorded:
    return runner.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=engine,
        )
    )


def test_story_1_10_the_real_store_keeps_the_case_status_in_step_with_the_pages(
    service_settings: Settings,
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (first, second, third) = gated_case(
            store, runner, [Route.CUSTOMER, Route.TRIAGE, Route.EXTRACTION]
        )
        seen = [case_status(service_settings, case_id)]
        # A decision before the settle: the customer was quick.
        decide(store, runner, engine, case_id, first, "keep", "customer")
        seen.append(case_status(service_settings, case_id))
        settled_early = runner.run(settle_case_after_gate(case_id, store=store))
        seen.append(settled_early.case_status.value)
        decide(store, runner, engine, case_id, first, "accept", "underwriter")
        seen.append(case_status(service_settings, case_id))
        decide(store, runner, engine, case_id, second, "deny", "underwriter")
        seen.append(case_status(service_settings, case_id))
        # Story 1.9's deferred item: the settle, run again late, finds the
        # pages as the decisions left them and undoes nothing.
        late = runner.run(settle_case_after_gate(case_id, store=store))
        # The same decision again: the stored one, and no second row or event.
        first_time = decision_rows(service_settings, case_id)
        again = decide(store, runner, engine, case_id, second, "deny", "underwriter")
        with pytest.raises(DomainError) as other:
            decide(store, runner, engine, case_id, second, "accept", "underwriter")

    assert seen == [
        "running",
        "awaiting_human",
        "awaiting_human",
        "awaiting_human",
        "running",
    ]
    assert late.case_status is CaseStatus.RUNNING
    # The settle reports each page as the transaction that settled it read
    # it: the lifecycle waits for those, not for the routes.
    assert settled_early.page_statuses == {
        first: PageStatus.AWAITING_TRIAGE,
        second: PageStatus.AWAITING_TRIAGE,
        third: PageStatus.EXTRACTING,
    }
    assert late.page_statuses == {
        first: PageStatus.EXTRACTING,
        second: PageStatus.DENIED,
        third: PageStatus.EXTRACTING,
    }
    assert case_status(service_settings, case_id) == "running"
    assert page_statuses(service_settings, case_id) == [
        "extracting",
        "denied",
        "extracting",
    ]
    assert decision_rows(service_settings, case_id) == first_time
    assert again.decision_id == first_time[2][3]
    assert other.value.code is ErrorCode.NOT_AWAITING_DECISION
    assert len(human_events(service_settings, case_id)) == 3
    assert len(engine.told) == 4


def test_story_1_10_the_real_store_completes_a_case_whose_pages_are_all_final(
    service_settings: Settings,
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (first, second) = gated_case(
            store, runner, [Route.CUSTOMER, Route.TRIAGE]
        )
        runner.run(settle_case_after_gate(case_id, store=store))
        decide(store, runner, engine, case_id, first, "discard", "customer")
        between = case_status(service_settings, case_id)
        decide(store, runner, engine, case_id, second, "deny", "underwriter")
        # A completed case takes no decision, and neither does another case's page.
        with pytest.raises(DomainError) as late:
            decide(store, runner, engine, case_id, first, "keep", "customer")
        other_case, (other_page,) = gated_case(store, runner, [Route.CUSTOMER])
        with pytest.raises(DomainError) as foreign:
            decide(store, runner, engine, case_id, other_page, "keep", "customer")

    assert between == "awaiting_human"
    assert case_status(service_settings, case_id) == "completed"
    assert late.value.code is ErrorCode.NOT_AWAITING_DECISION
    assert foreign.value.code is ErrorCode.NOT_FOUND
    assert page_statuses(service_settings, other_case) == ["awaiting_customer"]


def test_story_1_10_the_real_store_answers_a_repeat_before_it_looks_at_the_case(
    service_settings: Settings,
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.CUSTOMER])
        runner.run(settle_case_after_gate(case_id, store=store))
        # The discard completes the case, which then takes no decision.
        first = decide(store, runner, engine, case_id, page_id, "discard", "customer")
        completed = case_status(service_settings, case_id)
        rows = decision_rows(service_settings, case_id)
        events = human_events(service_settings, case_id)
        again = decide(store, runner, engine, case_id, page_id, "discard", "customer")

    assert completed == "completed"
    # The stored decision comes back; no second row and no second event.
    assert again == first
    assert again.decision_id == rows[0][3]
    assert decision_rows(service_settings, case_id) == rows
    assert human_events(service_settings, case_id) == events
    assert (len(rows), len(events)) == (1, 1)
    # And the engine is told again.
    assert engine.told == [engine.told[0]] * 2


def test_story_1_10_two_decisions_at_once_are_settled_by_the_database(
    service_settings: Settings,
) -> None:
    engine = FakeEngine()

    async def both(
        store: SqlCaseStore, case_id: str, page_id: str, decisions: tuple[str, str]
    ) -> list[DecisionRecorded | BaseException]:
        return await asyncio.gather(
            *(
                record_decision(
                    case_id,
                    page_id,
                    DecisionRequest.model_validate(
                        {"decision": decision, "actor": "customer"}
                    ),
                    store=store,
                    engine=engine,
                )
                for decision in decisions
            ),
            return_exceptions=True,
        )

    with a_store(service_settings) as (store, runner):
        case_id, (same, contested, _) = gated_case(
            store, runner, [Route.CUSTOMER, Route.CUSTOMER, Route.EXTRACTION]
        )
        runner.run(settle_case_after_gate(case_id, store=store))
        twice = runner.run(both(store, case_id, same, ("discard", "discard")))
        rivals = runner.run(both(store, case_id, contested, ("keep", "discard")))

    # The same decision twice: one row, one event, both answered with it.
    assert all(isinstance(answer, DecisionRecorded) for answer in twice)
    assert twice[0] == twice[1]
    # Keep against discard: exactly one wins, the other is refused (409).
    won = [answer for answer in rivals if isinstance(answer, DecisionRecorded)]
    lost = [answer for answer in rivals if isinstance(answer, DomainError)]
    assert (len(won), len(lost)) == (1, 1)
    assert lost[0].code is ErrorCode.NOT_AWAITING_DECISION
    assert lost[0].http_status == 409
    rows = decision_rows(service_settings, case_id)
    assert sorted(row[:3] for row in rows) == sorted(
        [
            (same, "discard", "customer"),
            (contested, won[0].decision.value, "customer"),
        ]
    )
    assert len(human_events(service_settings, case_id)) == 2
    assert page_statuses(service_settings, case_id)[:2] == [
        "discarded",
        won[0].page_status.value,
    ]


def test_story_1_10_a_case_status_the_pages_ask_for_but_may_not_follow_is_logged(
    migrated_database: Settings,
    service_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.CUSTOMER])
        decide(store, runner, engine, case_id, page_id, "discard", "customer")
        # Past the service: a page of the completed case is put back in work.
        with connect(migrated_database, autocommit=True) as connection:
            connection.execute(
                "UPDATE workflow.page_status SET page_status = 'extracting' "
                "WHERE page_id = %s",
                (page_id,),
            )
        with caplog.at_level(logging.WARNING, logger="workflow.adapters.db"):
            settled = runner.run(settle_case_after_gate(case_id, store=store))

    # A completed case stays completed, and the log says what was wanted.
    assert settled.case_status is CaseStatus.COMPLETED
    assert (
        f"case status not followed: case_id={case_id} case_status=completed "
        "wanted=running"
    ) in caplog.text


def test_story_1_10_the_real_store_takes_no_decision_for_a_failed_or_stopped_case(
    service_settings: Settings,
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        failed_case, (waiting, failing) = gated_case(
            store, runner, [Route.CUSTOMER, Route.EXTRACTION]
        )
        runner.run(settle_case_after_gate(failed_case, store=store))
        runner.run(
            record_stage_result(
                classification_failed(failed_case, failing), store=store
            )
        )
        stopped_case, (stopped_page,) = gated_case(
            store,
            runner,
            [Route.TRIAGE],
            StartParameters(
                classifier_contender=ClassifierContender.LLM,
                retriever_configs=(RetrieverConfig.R3,),
                stop_after=StopAfter.GATE,
                eval_run_id=new_id(),
            ),
        )
        codes = []
        for case_id, page_id, decision, actor in [
            (failed_case, waiting, "discard", "customer"),
            # Between the gate and the settle the stopped case is `running`.
            (stopped_case, stopped_page, "accept", "underwriter"),
        ]:
            with pytest.raises(DomainError) as raised:
                decide(store, runner, engine, case_id, page_id, decision, actor)
            codes.append(raised.value.code)
        settled = runner.run(settle_case_after_gate(stopped_case, store=store))

    assert codes == [ErrorCode.NOT_AWAITING_DECISION] * 2
    assert settled.case_status is CaseStatus.COMPLETED
    assert case_status(service_settings, failed_case) == "failed"
    assert page_statuses(service_settings, failed_case) == [
        "awaiting_customer",
        "failed",
    ]
    assert page_statuses(service_settings, stopped_case) == ["awaiting_triage"]
    assert decision_rows(service_settings, failed_case) == []
    assert decision_rows(service_settings, stopped_case) == []
    assert engine.told == []


def test_story_1_10_when_the_audit_insert_fails_the_whole_decision_is_rolled_back(
    migrated_database: Settings, service_settings: Settings
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.CUSTOMER])
        runner.run(settle_case_after_gate(case_id, store=store))
        with connect(migrated_database, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION workflow.refuse_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'audit insert refused by the test'; END $$"
            )
            connection.execute(
                "CREATE TRIGGER refuse_insert BEFORE INSERT ON workflow.audit_event "
                "FOR EACH ROW EXECUTE FUNCTION workflow.refuse_insert()"
            )
        try:
            with pytest.raises(DBAPIError):
                decide(store, runner, engine, case_id, page_id, "discard", "customer")
            # Neither the decision's row, nor the page's status, nor the
            # case's, which the discard would have completed.
            during = (
                decision_rows(service_settings, case_id),
                page_statuses(service_settings, case_id),
                case_status(service_settings, case_id),
            )
        finally:
            with connect(migrated_database, autocommit=True) as connection:
                connection.execute("DROP TRIGGER refuse_insert ON workflow.audit_event")
                connection.execute("DROP FUNCTION workflow.refuse_insert()")
        # The retry, once the insert works again.
        decide(store, runner, engine, case_id, page_id, "discard", "customer")

    assert during == ([], ["awaiting_customer"], "awaiting_human")
    assert engine.told == [engine.told[0]]
    assert page_statuses(service_settings, case_id) == ["discarded"]
    assert case_status(service_settings, case_id) == "completed"
    assert len(decision_rows(service_settings, case_id)) == 1


def test_story_1_10_the_service_role_can_add_and_read_decisions_and_nothing_else(
    migrated_database: Settings, service_settings: Settings
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.CUSTOMER])
        decide(store, runner, engine, case_id, page_id, "keep", "customer")

    for statement in (
        "UPDATE workflow.human_decision SET actor = 'underwriter'",
        "DELETE FROM workflow.human_decision",
        "TRUNCATE workflow.human_decision",
    ):
        with (
            connect(service_settings) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(statement)
    # A page takes each decision once, also past the service's own check.
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        connection.execute(
            "INSERT INTO workflow.human_decision "
            "(decision_id, case_id, page_id, decision, actor, occurred_at) "
            "SELECT %s, case_id, page_id, decision, actor, occurred_at "
            "FROM workflow.human_decision",
            (new_id(),),
        )
    assert len(decision_rows(service_settings, case_id)) == 1
    # AD-10: the database itself takes a decision from a demo role only, and
    # only one of the four decisions, whoever writes the row.
    for decision, actor in [("discard", "workflow"), ("approve", "customer")]:
        with (
            connect(migrated_database) as connection,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            connection.execute(
                "INSERT INTO workflow.human_decision "
                "(decision_id, case_id, page_id, decision, actor, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s, now())",
                (new_id(), case_id, page_id, decision, actor),
            )
    assert len(decision_rows(service_settings, case_id)) == 1
    assert query(
        migrated_database,
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'workflow' "
        "AND tablename = 'human_decision' AND indexname LIKE 'ix_%%'",
    ) == [("ix_workflow_human_decision_case_id",)]
    # The table is not dropped with decisions in it.
    with pytest.raises(RuntimeError, match="holds decisions"):
        command.downgrade(alembic_config(migrated_database), "0002")
    assert len(decision_rows(service_settings, case_id)) == 1
