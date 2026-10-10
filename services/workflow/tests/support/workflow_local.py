"""Helpers of the integration tests: the local PostgreSQL, seen as one role or another."""

import time
from typing import Any

import psycopg
from fastapi.testclient import TestClient

from workflow.settings import Settings


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    """A connection of the test's own, as the role the settings name."""
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def as_service(settings: Settings) -> Settings:
    """The settings the service itself runs with: signed in as its own role."""
    if settings.database_service_role is None:
        raise ValueError("the settings name no service role")
    return settings.model_copy(
        update={
            "database_user": settings.database_service_role,
            "database_service_role": None,
        }
    )


def wait_for_case_status(
    client: TestClient, case_id: str, wanted: str, timeout_seconds: float = 60.0
) -> dict[str, Any]:
    """Read the case's progress until it has the wanted status; return that progress.

    A case whose page waits for a person keeps its orchestration alive
    (story 1.10), so there is no completion to wait for: what the service
    reports is looked at until the lifecycle, which runs on the worker's
    own threads, has got there.
    """
    deadline = time.monotonic() + timeout_seconds
    progress: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/cases/{case_id}/progress")
        progress = response.json() if response.status_code == 200 else {}
        if progress.get("case_status") == wanted:
            return progress
        # Not a wait for time to pass: the other threads get their turn.
        time.sleep(0.05)
    raise AssertionError(
        f"case {case_id} did not reach {wanted}: {progress.get('case_status')}"
    )


def after_the_start(rows: list[Any]) -> list[Any]:
    """A case's stored events, in the order written, without the start they begin with.

    Every trail begins with its one `case.started` event (story 1.13). The
    tests of the earlier stories are about what follows it: this checks that
    the start is there, first and once, and leaves it out. The action is
    each row's first column. A case with no event at all has no start.
    """
    actions = [row[0] for row in rows]
    if not actions:
        return []
    assert actions[0] == "case.started", actions
    assert actions.count("case.started") == 1, actions
    return list(rows[1:])
