"""The case as `workflow` tracks it: its status and what it was started with (AD-4)."""

from dataclasses import dataclass
from datetime import datetime

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    RetrieverConfig,
    StageStatus,
    StopAfter,
)


@dataclass(frozen=True, slots=True)
class StartParameters:
    """What a case runs with, fixed when it is first started."""

    classifier_contender: ClassifierContender
    retriever_configs: tuple[RetrieverConfig, ...]
    stop_after: StopAfter | None
    eval_run_id: str | None


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One case in the lifecycle. Its documents and pages belong to `intake`."""

    case_id: str
    case_status: CaseStatus
    redaction_status: StageStatus
    parameters: StartParameters
    created_at: datetime
