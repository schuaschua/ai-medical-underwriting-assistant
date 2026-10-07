"""Story 1.3: the contract is exported as JSON Schema, with a check that fails on drift."""

import json
from pathlib import Path

import pytest

from contracts.enums import DemoRole
from contracts.errors import ErrorCode
from contracts.export_schema import build_schema, main, render_schema
from contracts.operations import OPERATIONS


def test_story_1_3_schema_holds_every_operation_model_the_enums_and_the_error_shape() -> (
    None
):
    definitions = build_schema()["$defs"]

    for operation in OPERATIONS:
        for model in (
            operation.request_model,
            operation.response_model,
            operation.query_model,
        ):
            if model is not None:
                assert model.__name__ in definitions
    assert definitions["DemoRole"]["enum"] == [role.value for role in DemoRole]
    assert definitions["ErrorCode"]["enum"] == [code.value for code in ErrorCode]
    assert set(definitions["ErrorDetail"]["required"]) == {
        "code",
        "message",
        "trace_id",
    }
    # Shared bases are no payload; a field with a default is optional to send.
    assert "ContractModel" not in definitions
    assert "StageCommand" not in definitions
    assert "required" not in definitions["StartCaseRequest"]
    assert render_schema() == render_schema()


def test_story_1_3_export_writes_the_file_and_check_accepts_it(tmp_path: Path) -> None:
    path = tmp_path / "contracts.schema.json"

    assert main([str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Contracts"
    assert main(["--check", str(path)]) == 0


def test_story_1_3_check_fails_when_a_model_has_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "contracts.schema.json"
    main([str(path)])
    # The file as it would be if the models had since gained or lost a field.
    stale = json.loads(path.read_text(encoding="utf-8"))
    del stale["$defs"]["CaseCreated"]["properties"]["document_id"]
    path.write_text(
        json.dumps(stale, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert main(["--check", str(path)]) == 1
    assert "out of date" in capsys.readouterr().err
    # --check never rewrites the file.
    assert json.loads(path.read_text(encoding="utf-8")) == stale
    assert main(["--check", str(tmp_path / "absent.json")]) == 1
