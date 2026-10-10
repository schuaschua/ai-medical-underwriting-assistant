"""The fixtures of the tests that put the stand-ins behind the real services.

They are defined in `support/synthdata_fixtures.py`, which the bake-off
runner's tests share (story 3.4), and imported here by name so that pytest
finds them for this folder.
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
