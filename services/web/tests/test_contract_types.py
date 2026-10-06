"""Story 1.3: the schema the SPA's types are generated from matches the contracts package."""

from pathlib import Path

from contracts.export_schema import main

SPA_API = Path(__file__).resolve().parents[1] / "spa" / "src" / "api"


def test_story_1_3_committed_contract_schema_is_current() -> None:
    assert main(["--check", str(SPA_API / "contracts.schema.json")]) == 0


def test_story_1_3_generated_types_are_committed_beside_the_schema() -> None:
    # Whether they match the schema is checked by `npm run contracts:check`.
    assert (SPA_API / "contracts.gen.ts").is_file()
