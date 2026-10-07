"""The system the runner's whole-path test drives: the real services with the stand-ins behind them.

The fixtures are those of the cross-service tests
(`packages/synthdata/tests/support/synthdata_fixtures.py`), imported here by
name so that pytest finds them for this folder. They need the containers of
compose.yaml: `docker compose up --detach --wait`.
"""

from synthdata_fixtures import (
    cases_container,
    classification,
    classification_settings,
    extraction,
    extraction_settings,
    ingested_manual,
    intake,
    layout_stand_in,
    local_stack,
    manual_index,
    migrated_database,
    model_stand_in,
    originals,
    retrieval,
    retrieval_settings,
    scheduler_client,
    stand_in,
    verdict,
    verdict_manual,
    verdict_model_stand_in,
    verdict_settings,
    workflow_admin,
    workflow_service_settings,
)

# Named here so that the imports count as used: pytest finds a fixture by its name
# in this module.
__all__ = [
    "cases_container",
    "classification",
    "classification_settings",
    "extraction",
    "extraction_settings",
    "ingested_manual",
    "intake",
    "layout_stand_in",
    "local_stack",
    "manual_index",
    "migrated_database",
    "model_stand_in",
    "originals",
    "retrieval",
    "retrieval_settings",
    "scheduler_client",
    "stand_in",
    "verdict",
    "verdict_manual",
    "verdict_model_stand_in",
    "verdict_settings",
    "workflow_admin",
    "workflow_service_settings",
]
