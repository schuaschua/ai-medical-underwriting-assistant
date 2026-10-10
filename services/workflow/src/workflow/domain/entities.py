"""The case as `workflow` tracks it: its status and what it was started with (AD-4)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
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


@dataclass(frozen=True, slots=True)
class PageDecision:
    """One human decision about one page, as table `human_decision` holds it (AD-10)."""

    decision_id: str
    case_id: str
    page_id: str
    decision: Decision
    # Always a demo role: no other actor's decision is ever stored.
    actor: DemoRole
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SettledCase:
    """A case as the settle after the gate left it: its status, and each page's, by page id."""

    case_status: CaseStatus
    page_statuses: Mapping[str, PageStatus]
