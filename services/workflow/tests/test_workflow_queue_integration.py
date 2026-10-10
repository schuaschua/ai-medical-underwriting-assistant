"""Story 1.11, against a real PostgreSQL: the queue as the real store reads it.

Run `docker compose up --detach --wait` first. No test here calls Azure.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from workflow_fakes import (
    FakeEngine,
    classification_done,
    redaction_done,
    starting,
)
from workflow_local import connect

from contracts.enums import (
    ClassifierContender,
    PageStatus,
    QueuedBy,
    RetrieverConfig,
    StopAfter,
)
from contracts.ids import new_id
from contracts.models.workflow import DecisionRequest, PageQueue
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.domain.cases import (
    fail_case,
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.queue import read_page_queue
from workflow.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)


def minutes(count: int) -> datetime:
    return NOW + timedelta(minutes=count)


@contextlib.contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


def routed_case(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    routes: list[tuple[Route, int]],
    parameters: StartParameters = PARAMETERS,
    *,
    settled: bool = True,
) -> tuple[str, list[str]]:
    """A started case whose pages the gate routed, each at its own minute; settled unless told not to."""
    case_id = new_id()
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        await record_stage_result(
            redaction_done(case_id, page_ids), store=store, now=lambda: NOW
        )
        for page_id, (route, minute) in zip(page_ids, routes, strict=True):
            done = classification_done(case_id, page_id)
            await record_stage_result(done, store=store, now=lambda: NOW)
            await record_route(
                case_id,
                page_id,
                done.classification_id,
                route,
                0.9,
                store=store,
                now=partial(minutes, minute),
            )
        if settled:
            await settle_case_after_gate(
                case_id, store=store, trace_id=None, now=lambda: minutes(60)
            )

    runner.run(scenario())
    return case_id, page_ids


def decide(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
    minute: int,
) -> None:
    runner.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=FakeEngine(),
            now=partial(minutes, minute),
        )
    )


def triage(store: SqlCaseStore, runner: asyncio.Runner, limit: int = 100) -> PageQueue:
    return runner.run(
        read_page_queue(PageStatus.AWAITING_TRIAGE, store=store, limit=limit)
    )


def listed(queue: PageQueue) -> list[str]:
    return [page.page_id for page in queue.pages]


def test_story_1_11_the_real_store_lists_the_waiting_pages_of_every_case_oldest_first(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        first_case, (unsure, sure_medical, asked, still_asked) = routed_case(
            store,
            runner,
            [
                (Route.TRIAGE, 3),
                (Route.EXTRACTION, 3),
                (Route.CUSTOMER, 3),
                (Route.CUSTOMER, 4),
            ],
        )
        second_case, (older, newest) = routed_case(
            store,
            runner,
            [(Route.TRIAGE, 1), (Route.TRIAGE, 9)],
            replace(
                PARAMETERS, classifier_contender=ClassifierContender.DOC_INTELLIGENCE
            ),
        )
        # The customer keeps a page: it waits in triage from that moment.
        decide(store, runner, first_case, asked, "keep", "customer", minute=5)

        queue = triage(store, runner)
        bounded = triage(store, runner, limit=2)
        exact = triage(store, runner, limit=4)
        customers = runner.run(
            read_page_queue(PageStatus.AWAITING_CUSTOMER, store=store)
        )

        # The underwriter accepts one page and denies another: both leave.
        decide(store, runner, second_case, older, "accept", "underwriter", minute=10)
        decide(store, runner, first_case, asked, "deny", "underwriter", minute=11)
        after = triage(store, runner)

    assert listed(queue) == [older, unsure, asked, newest]
    assert queue.has_more is False
    assert [
        (page.case_id, page.page_number, page.queued_by, page.classifier_contender)
        for page in queue.pages
    ] == [
        (second_case, 1, QueuedBy.GATE, ClassifierContender.DOC_INTELLIGENCE),
        (first_case, 1, QueuedBy.GATE, ClassifierContender.LLM),
        (first_case, 3, QueuedBy.CUSTOMER, ClassifierContender.LLM),
        (second_case, 2, QueuedBy.GATE, ClassifierContender.DOC_INTELLIGENCE),
    ]
    assert {page.page_status for page in queue.pages} == {PageStatus.AWAITING_TRIAGE}
    assert sure_medical not in listed(queue)
    # Bounded by the limit, with a flag that more wait; no flag at the exact size.
    assert (listed(bounded), bounded.has_more) == ([older, unsure], True)
    assert (listed(exact), exact.has_more) == (listed(queue), False)
    # The other queue: the page that still waits for the customer.
    assert listed(customers) == [still_asked]
    assert customers.pages[0].queued_by is None
    assert listed(after) == [unsure, newest]


def test_story_1_11_the_real_store_leaves_out_eval_run_failed_and_completed_cases(
    service_settings: Settings, migrated_database: Settings
) -> None:
    with a_store(service_settings) as (store, runner):
        _, (listed_page,) = routed_case(store, runner, [(Route.TRIAGE, 5)])
        eval_case, _ = routed_case(
            store,
            runner,
            [(Route.TRIAGE, 1)],
            replace(PARAMETERS, eval_run_id=new_id()),
        )
        failed_case, _ = routed_case(store, runner, [(Route.TRIAGE, 2)])
        runner.run(fail_case(failed_case, store=store))
        # Told to stop after the gate: completed there, its page still in triage.
        stopped_case, _ = routed_case(
            store,
            runner,
            [(Route.TRIAGE, 3)],
            replace(PARAMETERS, stop_after=StopAfter.GATE),
        )
        # A case that is completed for any other reason is left out as well.
        completed_case, _ = routed_case(store, runner, [(Route.TRIAGE, 4)])
        with connect(migrated_database, autocommit=True) as connection:
            connection.execute(
                "UPDATE workflow.case_status SET case_status = 'completed' "
                "WHERE case_id = %s",
                (completed_case,),
            )
            statuses = dict(
                connection.execute(
                    "SELECT case_id::text, case_status FROM workflow.case_status"
                ).fetchall()
            )
            waiting = connection.execute(
                "SELECT count(*) FROM workflow.page_status "
                "WHERE page_status = 'awaiting_triage'"
            ).fetchone()

        queue = triage(store, runner)

    # Five pages are in triage; four belong to cases whose pages nobody decides.
    assert waiting == (5,)
    assert statuses[eval_case] == "awaiting_human"
    assert (statuses[failed_case], statuses[stopped_case]) == ("failed", "completed")
    assert listed(queue) == [listed_page]
    assert queue.has_more is False
