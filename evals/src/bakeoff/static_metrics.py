"""What the scoreboard prints beside each row and the runner cannot measure (spine AD-17).

`static-metrics.yaml` says what each ladder row is (its store, chunk set and
method) and states its cost and effort, and what a page costs with each
classifier contender, each figure with its source. A figure nobody has
stated yet is left out there and is null on the scoreboard.
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from contracts.base import OneLine
from contracts.enums import ChunkSet, ClassifierContender, RetrieverConfig
from contracts.models.web import StatedFigure


class RowFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: OneLine
    chunk_set: ChunkSet
    method: OneLine
    cost: StatedFigure | None = None
    effort: StatedFigure | None = None


class ClassifierFacts(BaseModel):
    """What is stated of a classifier contender: what classifying one page costs (story 4.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cost_per_page: StatedFigure | None = None


class StaticMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: dict[RetrieverConfig, RowFacts]
    classifiers: dict[ClassifierContender, ClassifierFacts]

    @model_validator(mode="after")
    def _every_row_is_described(self) -> Self:
        missing = sorted(row.value for row in set(RetrieverConfig) - set(self.rows))
        if missing:
            raise ValueError(f"static metrics describe no row {', '.join(missing)}")
        unstated = sorted(
            contender.value
            for contender in set(ClassifierContender) - set(self.classifiers)
        )
        if unstated:
            raise ValueError(
                f"static metrics describe no classifier {', '.join(unstated)}"
            )
        return self


def read_static_metrics(path: Path) -> StaticMetrics:
    """Read and check the stated figures; a file that is no YAML is a `ValueError`."""
    try:
        stated = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"{path.name} is not valid YAML") from error
    return StaticMetrics.model_validate(stated)
