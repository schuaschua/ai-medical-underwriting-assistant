"""Fixtures of the tests that put the Language stand-in behind the real services (story 1.7).

These tests live here, with the stand-in, and not under `services/`: nothing
there may name the generator's package or the answer key (spine AD-17).
They need the containers of compose.yaml: `docker compose up --detach --wait`.
Each test has a database and blob containers of its own.
"""

import contextlib
import secrets
import socket
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient
from psycopg import sql
from pydantic import SecretStr
from workflow_local import as_service

from intake.adapters.blob import build_blob_service, ensure_local_containers
from intake.adapters.migrations import alembic_config as intake_alembic_config
from intake.settings import Settings as IntakeSettings
from synthdata.language_standin import DEFAULT_PORT, EMULATOR, LanguageStandIn
from workflow.adapters.local_role import ensure_local_service_role
from workflow.adapters.migrations import alembic_config as workflow_alembic_config
from workflow.settings import Settings as WorkflowSettings

# The task hub of compose.yaml that only tests use.
TEST_TASK_HUB = "aiuw-test"
NEEDS_CONTAINERS = (
    "Integration tests need PostgreSQL, the blob emulator and the scheduler "
    "emulator: run `docker compose up --detach --wait` first."
)


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def local_stack() -> Iterator[IntakeSettings]:
    """`intake`'s settings for the local containers, with blob containers of the test's own."""
    suffix = secrets.token_hex(6)
    settings = IntakeSettings(
        applicationinsights_connection_string=None,
        blob_connection_string=SecretStr(EMULATOR),
        originals_container=f"originals-test-{suffix}",
        cases_container=f"cases-test-{suffix}",
        # Where the stand-in listens when it runs as a process. Most tests
        # hand the app a transport to it and never use the network.
        language_endpoint=f"http://127.0.0.1:{DEFAULT_PORT}",
        language_poll_seconds=0.02,
    )
    if not _listening(settings.database_host, settings.database_port) or not _listening(
        "127.0.0.1", 10000
    ):
        pytest.fail(NEEDS_CONTAINERS, pytrace=False)
    names = ensure_local_containers(settings)
    try:
        yield settings
    finally:
        blobs = build_blob_service(settings)
        for name in names:
            with contextlib.suppress(ResourceNotFoundError):
                blobs.delete_container(name)


@pytest.fixture
def migrated_database(local_stack: IntakeSettings) -> Iterator[IntakeSettings]:
    """`intake`'s settings for a new database with its migrations applied; dropped afterwards."""
    name = f"aiuw_test_{secrets.token_hex(6)}"
    admin = (
        f"host={local_stack.database_host} port={local_stack.database_port} "
        f"dbname={local_stack.database_name} user={local_stack.database_user}"
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    settings = local_stack.model_copy(update={"database_name": name})
    try:
        command.upgrade(intake_alembic_config(settings), "head")
        yield settings
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


@pytest.fixture
def originals(local_stack: IntakeSettings) -> ContainerClient:
    """This test's originals container, read directly: the service cannot read it."""
    return build_blob_service(local_stack).get_container_client(
        local_stack.originals_container
    )


@pytest.fixture
def cases_container(local_stack: IntakeSettings) -> ContainerClient:
    return build_blob_service(local_stack).get_container_client(
        local_stack.cases_container
    )


@pytest.fixture
def stand_in(local_stack: IntakeSettings) -> LanguageStandIn:
    """The Language stand-in, over this test's blob containers."""
    return LanguageStandIn(build_blob_service(local_stack))


@pytest.fixture
def workflow_admin(
    local_stack: IntakeSettings, migrated_database: IntakeSettings
) -> Iterator[WorkflowSettings]:
    """`workflow`'s migration settings for the same database, migrated, with a role of its own."""
    role = f"workflow_test_{secrets.token_hex(6)}"
    settings = WorkflowSettings(
        applicationinsights_connection_string=None,
        scheduler_task_hub=TEST_TASK_HUB,
        database_name=migrated_database.database_name,
        database_service_role=role,
    )
    endpoint = urlsplit(settings.scheduler_endpoint)
    if not _listening(endpoint.hostname or "", endpoint.port or 0):
        pytest.fail(NEEDS_CONTAINERS, pytrace=False)
    ensure_local_service_role(settings)
    command.upgrade(workflow_alembic_config(settings), "head")
    try:
        yield settings
    finally:
        # Its rights go with the database; it is dropped once that is gone.
        admin = (
            f"host={settings.database_host} port={settings.database_port} "
            f"dbname={local_stack.database_name} "
            f"user={settings.database_user}"
        )
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(settings.database_name)
                )
            )
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )


@pytest.fixture
def workflow_service_settings(workflow_admin: WorkflowSettings) -> WorkflowSettings:
    """What the running `workflow` uses against that database: its own role."""
    return as_service(workflow_admin)
