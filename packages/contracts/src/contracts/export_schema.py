"""Export every contract model and enum as one JSON Schema document.

The SPA's TypeScript types are generated from this file, so its field names
and enum values cannot drift from the Python models:

    python -m contracts.export_schema PATH           write the schema
    python -m contracts.export_schema --check PATH   fail if PATH is out of date
"""

import argparse
import importlib
import json
import pkgutil
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

import contracts
from contracts.base import ContractModel
from contracts.operations import OPERATIONS

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_TITLE = "Contracts"
_REF_TEMPLATE = "#/$defs/{model}"


def _import_all() -> None:
    for module in pkgutil.walk_packages(contracts.__path__, "contracts."):
        importlib.import_module(module.name)


def _is_exported(candidate: type) -> bool:
    module = candidate.__module__
    # Modules named `_something` hold shared bases, which never go on the wire.
    return module.startswith("contracts.") and not module.rsplit(".", 1)[-1].startswith(
        "_"
    )


def _subclasses[T](base: type[T]) -> list[type[T]]:
    found: set[type[T]] = set()
    pending = [base]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    return sorted(filter(_is_exported, found), key=lambda item: item.__name__)


def build_schema() -> dict[str, Any]:
    """Build the schema document: one `$defs` entry per model and per enum."""
    _import_all()
    # What the SPA sends is described as input (a field with a default may be
    # left out); everything else as output (every field is present).
    sent = {
        model
        for operation in OPERATIONS
        for model in (operation.request_model, operation.query_model)
        if model is not None
    }
    modes: list[tuple[type[ContractModel], Literal["validation", "serialization"]]] = [
        (model, "validation" if model in sent else "serialization")
        for model in _subclasses(ContractModel)
    ]
    _, schema = models_json_schema(modes, ref_template=_REF_TEMPLATE)
    definitions: dict[str, Any] = schema.get("$defs", {})
    for enum in _subclasses(StrEnum):
        # Enums no model refers to are still part of the contract.
        definitions.setdefault(enum.__name__, TypeAdapter(enum).json_schema())
    return {
        "$schema": SCHEMA_DIALECT,
        "title": SCHEMA_TITLE,
        "$defs": dict(sorted(definitions.items())),
    }


def render_schema() -> str:
    """The schema as text, in a stable form so it can be compared byte for byte."""
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="contracts.export_schema", description=__doc__.splitlines()[0]
    )
    parser.add_argument("path", type=Path, help="the schema file")
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if the file differs from the models",
    )
    arguments = parser.parse_args(argv)
    path: Path = arguments.path
    rendered = render_schema()

    if not arguments.check:
        path.write_text(rendered, encoding="utf-8")
        return 0
    if path.is_file() and path.read_text(encoding="utf-8") == rendered:
        return 0
    print(
        f"{path} is out of date with the contracts package. "
        "Regenerate it and the TypeScript types (see README, 'Contract types').",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
