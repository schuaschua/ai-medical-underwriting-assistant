"""The case lifecycle's own rules: how a case starts and what its status may become."""

from datetime import datetime

from contracts.enums import CaseStatus, StageStatus
from contracts.models.workflow import CaseStarted, StartCaseRequest
from workflow.domain.entities import CaseRecord, StartParameters


def resolve_start_parameters(
    request: StartCaseRequest | None, defaults: StartParameters
) -> StartParameters:
    """Fill every field the start request left out from the defaults.

    The classifier contender and the retriever configurations have a default
    in the settings; `stop_after` and `eval_run_id` default to "not set".
    """
    if request is None:
        return defaults
    return StartParameters(
        classifier_contender=request.classifier_contender
        or defaults.classifier_contender,
        retriever_configs=tuple(request.retriever_configs)
        if request.retriever_configs is not None
        else defaults.retriever_configs,
        stop_after=request.stop_after
        if request.stop_after is not None
        else defaults.stop_after,
        eval_run_id=request.eval_run_id
        if request.eval_run_id is not None
        else defaults.eval_run_id,
    )


def new_case(case_id: str, parameters: StartParameters, now: datetime) -> CaseRecord:
    """A case as it is when it is started: running, with redaction still to come."""
    return CaseRecord(
        case_id=case_id,
        case_status=CaseStatus.RUNNING,
        # AD-21: redaction is the first stage, so a started case is waiting for it.
        redaction_status=StageStatus.RUNNING,
        parameters=parameters,
        created_at=now,
    )


def case_started(case: CaseRecord) -> CaseStarted:
    """The answer to a start: the case as it was actually started."""
    parameters = case.parameters
    return CaseStarted(
        case_id=case.case_id,
        case_status=case.case_status,
        classifier_contender=parameters.classifier_contender,
        retriever_configs=list(parameters.retriever_configs),
        stop_after=parameters.stop_after,
        eval_run_id=parameters.eval_run_id,
    )
