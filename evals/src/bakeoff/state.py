"""What a bake-off run remembers between starts: which case it uploaded as which case id.

`web` lists no case of an eval run (they are kept out of the case list), so
the runner keeps the ids itself, in a small file per `eval_run_id` in the
scratch folder. A run started again with the same id finds its cases there
and uploads none of them a second time.
"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from contracts.enums import RetrieverConfig
from contracts.ids import CaseId, EvalRunId


class RunRefused(Exception):
    """The run cannot be made as asked."""


class _Stored(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_run_id: EvalRunId
    # What the run's cases were first started against and with. A case is
    # started once, so a resume must be the same run: null until known.
    web_address: str | None = None
    rows: list[RetrieverConfig] | None = None
    # Which bake-off the run is: its cases are started for that one only.
    # Null in the file of a run made before there were two, and of the
    # training pages' tool.
    bake_off: Literal["retrieval", "classification"] | None = None
    # The answer key's case key to the case id `web` answered the upload with.
    cases: dict[str, CaseId] = {}


def write_text_whole(path: Path, text: str) -> None:
    """Write a file so that a reader sees the old text or the new, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    scratch.write_text(text, encoding="utf-8")
    scratch.replace(path)


class RunState:
    """The uploaded cases of one bake-off run, kept on disk as they are made."""

    def __init__(self, folder: Path, eval_run_id: str, web_address: str) -> None:
        self._path = folder / f"{eval_run_id}.json"
        if self._path.is_file():
            self._stored = _Stored.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
            if self._stored.eval_run_id != eval_run_id:
                raise RunRefused(f"{self._path.name} belongs to another eval run")
            if self._stored.web_address not in (None, web_address):
                raise RunRefused(
                    "this eval run was made against another web address; it "
                    "cannot be resumed against this one"
                )
        else:
            self._stored = _Stored(eval_run_id=eval_run_id)
        self._stored.web_address = web_address

    @property
    def eval_run_id(self) -> str:
        return self._stored.eval_run_id

    def case_id(self, case_key: str) -> str | None:
        """The case id a case was uploaded as in this run; None when it was not."""
        return self._stored.cases.get(case_key)

    def begin(self, rows: list[RetrieverConfig]) -> None:
        """Keep the rows the run's cases are started with; refuse a resume with other rows.

        A case already started keeps its rows: started again with others,
        the new rows would have no run and would count as wrong.
        """
        if self._stored.bake_off == "classification":
            raise RunRefused(
                "this eval run is a classification bake-off; it cannot be "
                "resumed as a retrieval one"
            )
        if self._stored.rows is None and self._stored.cases:
            # Cases and no rows: the training pages' tool made them, stopped
            # at the gate. They hold no verdict run to read.
            raise RunRefused(
                "this eval run prepared the classifier's training pages; it "
                "cannot be resumed as a retrieval bake-off"
            )
        if self._stored.rows is None:
            self._stored.rows = list(rows)
            self._stored.bake_off = "retrieval"
            self._write()
        elif self._stored.rows != list(rows):
            first = ", ".join(row.value for row in self._stored.rows)
            raise RunRefused(
                f"this eval run started its cases with rows {first}; a resume "
                "must run with the same rows (start a new run for others)"
            )

    def begin_classification(self) -> None:
        """Mark the run as a classification bake-off; refuse the resume of a run that is none.

        Its cases stop at the gate and are kept under their contender: a
        run that started cases for the retrieval rows, or that prepared
        training pages, holds cases that are not these.
        """
        if self._stored.bake_off == "classification":
            return
        if self._stored.bake_off is not None or self._stored.rows or self._stored.cases:
            raise RunRefused(
                "this eval run is not a classification bake-off; start a new "
                "run for one"
            )
        self._stored.bake_off = "classification"
        self._write()

    def record(self, case_key: str, case_id: str) -> None:
        self._stored.cases[case_key] = case_id
        self._write()

    def _write(self) -> None:
        write_text_whole(
            self._path,
            json.dumps(self._stored.model_dump(mode="json"), indent=2) + "\n",
        )
