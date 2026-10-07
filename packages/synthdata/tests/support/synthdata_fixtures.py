"""Fixtures of the tests that put the stand-ins behind the real services (stories 1.7 and 1.8).

These tests live with the stand-in, and not under `services/`: nothing
there may name the generator's package or the answer key (spine AD-17).
They need the containers of compose.yaml: `docker compose up --detach --wait`.
Each test has a database and blob containers of its own.

The fixtures are in this module, on pytest's `pythonpath`, so that two test
folders can use them: this package's (`packages/synthdata/tests/conftest.py`)
and the bake-off runner's (`evals/tests/conftest.py`, story 3.4), whose
whole-path test drives the same system. Each `conftest.py` imports them by name.
"""

import contextlib
import functools
import secrets
import socket
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from psycopg import sql
from pydantic import SecretStr
from synthdata_stack import (
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalRetrieval,
    LocalVerdict,
)
from workflow_local import as_service

from classification.adapters.blob import (
    build_blob_service as build_classification_blobs,
)
from classification.adapters.migrations import (
    alembic_config as classification_alembic_config,
)
from classification.settings import Settings as ClassificationSettings
from extraction.adapters.migrations import alembic_config as extraction_alembic_config
from extraction.settings import Settings as ExtractionSettings
from intake.adapters.blob import build_blob_service, ensure_local_containers
from intake.adapters.migrations import alembic_config as intake_alembic_config
from intake.settings import Settings as IntakeSettings
from retrieval.adapters.blob import build_blob_service as build_retrieval_blobs
from retrieval.adapters.migrations import alembic_config as retrieval_alembic_config
from retrieval.settings import Settings as RetrievalSettings
from synthdata.foundry_standin import DEFAULT_PORT as MODEL_PORT
from synthdata.foundry_standin import (
    LOCAL_DEPLOYMENT,
    LOCAL_EMBEDDING_DEPLOYMENT,
    FoundryStandIn,
)
from synthdata.language_standin import DEFAULT_PORT, EMULATOR, LanguageStandIn
from synthdata.layout_standin import DEFAULT_PORT as LAYOUT_PORT
from synthdata.layout_standin import LayoutStandIn
from verdict.adapters.local_role import (
    ensure_local_service_role as ensure_verdict_service_role,
)
from verdict.adapters.migrations import alembic_config as verdict_alembic_config
from verdict.settings import Settings as VerdictSettings
from workflow.adapters.local_role import ensure_local_service_role
from workflow.adapters.migrations import alembic_config as workflow_alembic_config
from workflow.adapters.scheduler import build_client
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


@pytest.fixture
def scheduler_client(
    workflow_service_settings: WorkflowSettings,
) -> Iterator[DurableTaskSchedulerClient]:
    """A client of the test's own, to look at what `workflow` did in the scheduler."""
    client = build_client(workflow_service_settings)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def intake(migrated_database: IntakeSettings, stand_in: LanguageStandIn) -> LocalIntake:
    """`intake` on this test's database and blob containers, with the stand-in behind it."""
    return LocalIntake(migrated_database, stand_in)


# --- classification and the model stand-in (story 1.8) -------------------------------


@pytest.fixture
def model_stand_in() -> FoundryStandIn:
    """The stand-in for the Foundry chat deployment."""
    return FoundryStandIn()


@pytest.fixture
def classification_settings(
    migrated_database: IntakeSettings,
) -> Iterator[ClassificationSettings]:
    """`classification`'s settings for the same database, migrated, with a training container of the test's own."""
    settings = ClassificationSettings(
        applicationinsights_connection_string=None,
        database_name=migrated_database.database_name,
        # Where the stand-in listens when it runs as a process. Most tests
        # hand the app a transport to it and never use the network.
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        # A throttled call is sent again at once.
        model_retry_seconds=0.01,
        # Story 4.2: where the training job finds its pages. The container
        # is made only by a test that uploads pages into it.
        blob_connection_string=SecretStr(EMULATOR),
        training_container=f"classifier-training-test-{secrets.token_hex(6)}",
    )
    command.upgrade(classification_alembic_config(settings), "head")
    try:
        yield settings
    finally:
        with contextlib.suppress(ResourceNotFoundError):
            build_classification_blobs(settings).delete_container(
                settings.training_container
            )


@pytest.fixture
def classification(
    classification_settings: ClassificationSettings,
    model_stand_in: FoundryStandIn,
    intake: LocalIntake,
) -> LocalClassification:
    """`classification` on this test's database, with the model stand-in behind it."""
    return LocalClassification(classification_settings, model_stand_in, intake)


# --- extraction and the model stand-in (story 2.4) -----------------------------------


@pytest.fixture
def extraction_settings(migrated_database: IntakeSettings) -> ExtractionSettings:
    """`extraction`'s settings for the same database, with its migrations applied."""
    settings = ExtractionSettings(
        applicationinsights_connection_string=None,
        database_name=migrated_database.database_name,
        # Where the stand-in listens when it runs as a process. The tests
        # hand the app a transport to it and never use the network.
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        # A throttled call is sent again at once.
        model_retry_seconds=0.01,
    )
    command.upgrade(extraction_alembic_config(settings), "head")
    return settings


@pytest.fixture
def extraction(
    extraction_settings: ExtractionSettings,
    model_stand_in: FoundryStandIn,
    intake: LocalIntake,
) -> LocalExtraction:
    """`extraction` on this test's database, with the model stand-in behind it."""
    return LocalExtraction(extraction_settings, model_stand_in, intake)


# --- retrieval's ingestion job and the layout stand-in (story 2.2) -------------------


@pytest.fixture
def layout_stand_in() -> LayoutStandIn:
    """The stand-in for Document Intelligence's layout model."""
    return LayoutStandIn()


@pytest.fixture
def retrieval_settings(
    migrated_database: IntakeSettings,
) -> Iterator[RetrievalSettings]:
    """`retrieval`'s settings for the same database, migrated, with a manual container of the test's own."""
    settings = RetrievalSettings(
        applicationinsights_connection_string=None,
        database_name=migrated_database.database_name,
        blob_connection_string=SecretStr(EMULATOR),
        manual_container=f"manual-test-{secrets.token_hex(6)}",
        # Where the stand-ins listen when they run as processes. The tests
        # hand the job a transport to each and never use the network.
        layout_endpoint=f"http://127.0.0.1:{LAYOUT_PORT}",
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        embedding_deployment=LOCAL_EMBEDDING_DEPLOYMENT,
        # A throttled call is sent again at once, and a running analysis
        # looked at again at once.
        model_retry_seconds=0.01,
        layout_poll_seconds=0.01,
    )
    command.upgrade(retrieval_alembic_config(settings), "head")
    try:
        yield settings
    finally:
        with contextlib.suppress(ResourceNotFoundError):
            build_retrieval_blobs(settings).delete_container(settings.manual_container)


@pytest.fixture
def retrieval(
    retrieval_settings: RetrievalSettings,
    layout_stand_in: LayoutStandIn,
    model_stand_in: FoundryStandIn,
) -> LocalRetrieval:
    """The ingestion job on this test's database, with the project's manual uploaded."""
    job = LocalRetrieval(retrieval_settings, layout_stand_in, model_stand_in)
    job.upload()
    return job


@pytest.fixture
def ingested_manual(retrieval: LocalRetrieval) -> LocalRetrieval:
    """The project's manual, ingested: one `smart` chunk per rule, in this test's database."""
    assert retrieval.ingest() == 0
    return retrieval


# --- verdict, over the real extraction and retrieval (stories 2.5 and 2.6) -----------

# The tables the ingestion job fills, in the order they are copied.
_MANUAL_TABLES = ("retrieval.chunk", "retrieval.ingest_run")


def _copy_out(settings: RetrievalSettings, table: str) -> bytes:
    with (
        psycopg.connect(
            host=settings.database_host,
            port=settings.database_port,
            dbname=settings.database_name,
            user=settings.database_user,
        ) as connection,
        connection.cursor() as cursor,
        # A fixed table name of this module, never a value.
        cursor.copy(f"COPY {table} TO STDOUT") as copy,
    ):
        return b"".join(bytes(block) for block in copy)


def _copy_in(settings: RetrievalSettings, table: str, rows: bytes) -> None:
    with (
        psycopg.connect(
            host=settings.database_host,
            port=settings.database_port,
            dbname=settings.database_name,
            user=settings.database_user,
        ) as connection,
        connection.cursor() as cursor,
    ):
        with cursor.copy(f"COPY {table} FROM STDIN") as copy:
            copy.write(rows)
        connection.commit()


@functools.cache
def _ingested_manual() -> dict[str, bytes]:
    """The project's manual, ingested once for the whole run: the rows of the index it made.

    The ingestion job runs as it really runs, into a database of its own,
    with stand-ins of its own. Its rows are kept and the database dropped:
    every test that needs the manual gets a copy of them (`verdict_manual`),
    which is the same index and costs no second ingestion.
    """
    name = f"aiuw_test_{secrets.token_hex(6)}"
    container = f"manual-test-{secrets.token_hex(6)}"
    base = RetrievalSettings(
        applicationinsights_connection_string=None,
        blob_connection_string=SecretStr(EMULATOR),
        manual_container=container,
        layout_endpoint=f"http://127.0.0.1:{LAYOUT_PORT}",
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        embedding_deployment=LOCAL_EMBEDDING_DEPLOYMENT,
        model_retry_seconds=0.01,
        layout_poll_seconds=0.01,
    )
    if not _listening(base.database_host, base.database_port) or not _listening(
        "127.0.0.1", 10000
    ):
        pytest.fail(NEEDS_CONTAINERS, pytrace=False)
    admin = (
        f"host={base.database_host} port={base.database_port} "
        f"dbname={base.database_name} user={base.database_user}"
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    settings = base.model_copy(update={"database_name": name})
    try:
        command.upgrade(retrieval_alembic_config(settings), "head")
        job = LocalRetrieval(settings, LayoutStandIn(), FoundryStandIn())
        job.upload()
        assert job.ingest() == 0
        return {table: _copy_out(settings, table) for table in _MANUAL_TABLES}
    finally:
        with contextlib.suppress(ResourceNotFoundError):
            build_retrieval_blobs(settings).delete_container(container)
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


@pytest.fixture(scope="session")
def manual_index() -> dict[str, bytes]:
    """The rows of the ingested manual.

    Ingested once per test run, also when both folders that use these
    fixtures are collected: each has its own session fixture, and both are
    answered from the one ingestion.
    """
    return _ingested_manual()


@pytest.fixture
def verdict_manual(
    migrated_database: IntakeSettings, manual_index: dict[str, bytes]
) -> LocalRetrieval:
    """`retrieval` on this test's database, holding the ingested manual, with a model stand-in of its own."""
    settings = RetrievalSettings(
        applicationinsights_connection_string=None,
        database_name=migrated_database.database_name,
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        embedding_deployment=LOCAL_EMBEDDING_DEPLOYMENT,
        model_retry_seconds=0.01,
    )
    command.upgrade(retrieval_alembic_config(settings), "head")
    for table in _MANUAL_TABLES:
        _copy_in(settings, table, manual_index[table])
    return LocalRetrieval(settings, LayoutStandIn(), FoundryStandIn())


@pytest.fixture
def verdict_model_stand_in() -> FoundryStandIn:
    """The stand-in the verdict agent talks to: its own, so its calls are counted apart."""
    return FoundryStandIn()


@pytest.fixture
def verdict_settings(migrated_database: IntakeSettings) -> Iterator[VerdictSettings]:
    """`verdict`'s settings for the same database, migrated, signed in as a role of its own."""
    role = f"verdict_test_{secrets.token_hex(6)}"
    admin = VerdictSettings(
        applicationinsights_connection_string=None,
        database_name=migrated_database.database_name,
        database_service_role=role,
        # Where the stand-in listens when it runs as a process. The tests
        # hand the app a transport to it and never use the network.
        model_endpoint=f"http://127.0.0.1:{MODEL_PORT}",
        chat_deployment=LOCAL_DEPLOYMENT,
        # A throttled call, and a tool's call that got no answer, are sent
        # again at once.
        model_retry_seconds=0.01,
        upstream_retry_seconds=0.01,
    )
    ensure_verdict_service_role(admin)
    command.upgrade(verdict_alembic_config(admin), "head")
    try:
        # The service signs in as its own role: the step log is append-only for it.
        yield admin.model_copy(
            update={"database_user": role, "database_service_role": None}
        )
    finally:
        # Its rights go with the database; the role is dropped once that is gone.
        with psycopg.connect(
            host=admin.database_host,
            port=admin.database_port,
            dbname=VerdictSettings().database_name,
            user=admin.database_user,
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(admin.database_name)
                )
            )
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )


@pytest.fixture
def verdict(
    verdict_settings: VerdictSettings,
    verdict_model_stand_in: FoundryStandIn,
    extraction: LocalExtraction,
    verdict_manual: LocalRetrieval,
) -> LocalVerdict:
    """`verdict` on this test's database, with the real `extraction` and `retrieval` behind it."""
    return LocalVerdict(
        verdict_settings, verdict_model_stand_in, extraction, verdict_manual
    )
