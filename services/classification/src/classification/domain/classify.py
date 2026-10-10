"""Classify one page: what it is, how sure the classifier is, and why (AD-13, AD-6).

The stage `workflow` commands for every page once redaction is done. The page
is read from `intake`, so only the redacted reading is ever used. Two
contenders answer the same command with the same result, and neither sees
anything of the other's:

- `llm`: the chat model is run several times on the page's text and
  thumbnail; what the runs agree on is the answer, and how far they agree is
  the confidence.
- `doc-intelligence` (story 4.2): the page is sent as a one-page PDF to the
  custom classifier of Document Intelligence; the document type it names is
  the answer and its own confidence the confidence.

Nothing here routes a page: that is the gate's rule, in `workflow` (AD-7).
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from pydantic import ValidationError

from classification.domain.agreement import agree
from classification.domain.entities import (
    ClassificationKey,
    ClassifierAnswer,
    KeyRow,
    PageContent,
)
from classification.domain.ports import (
    ClassificationRepository,
    ClassifierNotReady,
    ModelCallFailed,
    ModelUnavailable,
    PageClassifier,
    PageModel,
    PageReader,
)
from contracts.audit import AuditAction, AuditRecord
from contracts.enums import ActorKind, ClassifierContender, PageType, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import (
    Classification,
    ClassificationList,
    ClassificationResult,
    ClassifierOutput,
    ClassifyCommand,
)
from contracts.rules import is_medical

logger = logging.getLogger(__name__)

UNKNOWN_PAGE_MESSAGE = "That page could not be found."
IN_PROGRESS_MESSAGE = "The page is still being classified."
CONTENDER_NOT_AVAILABLE_MESSAGE = "That classifier is not available."
NOT_RECORDED_MESSAGE = "The classification could not be recorded. Please try again."
CLASSIFIER_NOT_READY_MESSAGE = "The classifier is not available right now."

# The `reason` of the Document Intelligence contender: a fixed sentence that
# names the classifier and the type it gave, and never anything of the page.
CLASSIFIER_REASON = "The Document Intelligence classifier gave this page the type {}."

_NO_TRACE_CONTEXT: Mapping[str, str] = MappingProxyType({})


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


class InvalidModelOutput(Exception):
    """A run's answer is not a `ClassifierOutput`: it is never passed on."""


class PageNotReadable(Exception):
    """`intake` listed the page and then could not hand it over."""


@dataclass(frozen=True, slots=True)
class ClassifyPorts:
    """What a classification works with; the app factory or a test provides it."""

    repository: ClassificationRepository
    pages: PageReader
    model: PageModel
    # The Document Intelligence classifier; None where the service was given
    # no endpoint or no classifier id, and the contender is then not available.
    classifier: PageClassifier | None = None


def available_contenders(
    ports: ClassifyPorts, options: "ClassifyOptions"
) -> frozenset[ClassifierContender]:
    """The contenders this instance can run: `llm` always, the other where it is configured.

    Configured means the classifier and the actor that names it: a result
    is never stored under another contender's actor.
    """
    if ports.classifier is None or not options.classifier_actor:
        return frozenset({ClassifierContender.LLM})
    return frozenset(ClassifierContender)


@dataclass(frozen=True, slots=True)
class ClassifyOptions:
    """The settings a classification runs with (CLASSIFICATION_*: see the settings)."""

    # AD-8: who classified, as `classification:<chat deployment name>`.
    actor: str
    # The same for the Document Intelligence contender, as
    # `classification:<classifier id>`; None where it is not configured.
    classifier_actor: str | None = None
    runs: int = 5
    max_concurrent_runs: int = 5
    deadline_seconds: float = 180.0
    stale_margin_seconds: float = 60.0


@dataclass(slots=True)
class _Run:
    """One classification under way: what it is for, and since when."""

    classification_id: str
    key: ClassificationKey
    actor: str
    eval_run_id: str | None
    trace_id: str
    started: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class _Reading:
    """What a contender made of the page, whichever contender it was."""

    page_type: PageType
    confidence: float
    reason: str
    # How often the classifier was asked, and how many of the answers named
    # the type: counts for the log.
    runs: int
    agreeing_runs: int


def _actor_of(contender: ClassifierContender, options: ClassifyOptions) -> str:
    """AD-8: the actor of a classification names what classified."""
    if contender is not ClassifierContender.DOC_INTELLIGENCE:
        return options.actor
    if not options.classifier_actor:
        # Never the chat deployment's name on the classifier's result.
        raise DomainError(ErrorCode.VALIDATION_FAILED, CONTENDER_NOT_AVAILABLE_MESSAGE)
    return options.classifier_actor


def _audit(run: _Run, action: AuditAction, occurred_at: datetime) -> AuditRecord:
    return AuditRecord(
        actor_kind=ActorKind.AI,
        actor=run.actor,
        action=action,
        occurred_at=occurred_at,
        case_id=run.key.case_id,
        page_id=run.key.page_id,
        # The owning record of a classification is the classification itself.
        ref=run.classification_id,
        detail=None,
        trace_id=run.trace_id,
        eval_run_id=run.eval_run_id,
    )


def done_result(
    run: _Run, classification: Classification, occurred_at: datetime
) -> ClassificationResult:
    """The result of a finished classification, with its `page.classified` record."""
    return ClassificationResult(
        case_id=run.key.case_id,
        status=StageStatus.DONE,
        error_code=None,
        audit=_audit(run, AuditAction.PAGE_CLASSIFIED, occurred_at),
        classification_id=run.classification_id,
        page_id=run.key.page_id,
        contender=run.key.contender,
        classification=classification,
    )


def failed_result(
    run: _Run, error_code: ErrorCode, occurred_at: datetime
) -> ClassificationResult:
    """The result of a classification that failed: no classification, and the code that says why."""
    return ClassificationResult(
        case_id=run.key.case_id,
        status=StageStatus.FAILED,
        error_code=error_code,
        audit=_audit(run, AuditAction.STAGE_FAILED, occurred_at),
        classification_id=run.classification_id,
        page_id=run.key.page_id,
        contender=run.key.contender,
        classification=None,
    )


async def classify_page(
    command: ClassifyCommand,
    *,
    ports: ClassifyPorts,
    options: ClassifyOptions,
    trace_id: str | None = None,
    trace_context: Mapping[str, str] = _NO_TRACE_CONTEXT,
    now: Callable[[], datetime] = utc_now,
) -> ClassificationResult:
    """Classify one page; idempotent on `case_id` + `page_id` + `contender`.

    The key row is inserted as running before any work. A repeat while it
    runs is `in_progress`; a repeat after the end is answered with the stored
    result and calls neither `intake` nor the model. After
    `options.deadline_seconds`, for all runs together, a failed result is
    stored. A key row left running by a process that died is settled as
    failed, by the first repeat that comes `options.stale_margin_seconds`
    after its deadline. A page `intake` does not hold for the case is
    `not_found`, and a contender this instance cannot run (`doc-intelligence`
    where no classifier is configured) is `validation_failed`: neither
    leaves a key row, and neither is worth sending again.

    What a repeat of the command can mend is not stored as a failure: when
    `intake` cannot be reached for the page, or Document Intelligence holds
    no classifier of the configured id yet or does not let the service in
    (both `upstream_unavailable`), or the request is cancelled because the caller went away or the service is
    stopping, the key row is released, so the command `workflow` sends again
    classifies the page.
    """
    if command.contender not in available_contenders(ports, options):
        raise DomainError(ErrorCode.VALIDATION_FAILED, CONTENDER_NOT_AVAILABLE_MESSAGE)
    key = ClassificationKey(command.case_id, command.page_id, command.contender)
    repository = ports.repository
    earlier = await repository.find(key)
    classification_id = new_id()
    if earlier is None:
        # Asked of `intake` before the key row, so a page that is not the
        # case's leaves nothing behind.
        page_ids = await ports.pages.page_ids_of_case(key.case_id, trace_context)
        if page_ids is None or key.page_id not in page_ids:
            raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
        earlier = await repository.begin(classification_id, key, now())
    if earlier is not None:
        return await _repeat(earlier, command, ports, options, trace_id, now)

    run = _Run(
        classification_id=classification_id,
        key=key,
        actor=_actor_of(key.contender, options),
        eval_run_id=command.eval_run_id,
        trace_id=trace_id or NO_TRACE_ID,
    )
    # One deadline for everything: reading the page, every run of the model
    # and the storing of the result.
    deadline = asyncio.timeout(options.deadline_seconds)
    try:
        async with deadline:
            return await _classify(run, ports, options, trace_context, now)
    except asyncio.CancelledError:
        # The caller went away or the service is stopping, with the page
        # half done. Nothing is wrong with the page: the key row is given
        # up, whatever happens to the request from here on, so that the
        # command sent again runs it.
        await asyncio.shield(_release(run, "cancelled", repository))
        raise
    except Exception as error:
        if (
            isinstance(error, DomainError)
            and error.code is ErrorCode.UPSTREAM_UNAVAILABLE
        ):
            # `intake` could not be reached for the page. That passes: the
            # key row is given up and the caller is told to send the
            # command again.
            await asyncio.shield(_release(run, error.code.value, repository))
            raise
        if isinstance(error, ClassifierNotReady):
            # The classifier is not trained yet, or a role is not honoured
            # yet. That passes too: nothing is stored, and the command sent
            # again classifies the page once it is there.
            await asyncio.shield(_release(run, error.reason, repository))
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, CLASSIFIER_NOT_READY_MESSAGE
            ) from None
        error_code, reason = _failure_of(error, deadline.expired())
    # Settled whatever happens to the request from here on: a request
    # cancelled now must not leave the key row `running`.
    return await asyncio.shield(_fail(run, error_code, reason, repository, now))


def _failure_of(error: Exception, deadline_passed: bool) -> tuple[ErrorCode, str]:
    """The error code a failure is stored under, and a reason for the log.

    security rule 31: the reason is a code or the error's type, never its
    message, which could hold page text or the model's answer.
    """
    if isinstance(error, TimeoutError) and deadline_passed:
        return ErrorCode.STAGE_TIMEOUT, "deadline"
    if isinstance(error, InvalidModelOutput):
        return ErrorCode.INVALID_MODEL_OUTPUT, "invalid_model_output"
    if isinstance(error, ModelUnavailable):
        return ErrorCode.MODEL_UNAVAILABLE, "model_unavailable"
    if isinstance(error, ModelCallFailed):
        return ErrorCode.STAGE_FAILED, error.reason
    if isinstance(error, DomainError):
        return ErrorCode.STAGE_FAILED, error.code.value
    return ErrorCode.STAGE_FAILED, type(error).__qualname__


async def _repeat(
    earlier: KeyRow,
    command: ClassifyCommand,
    ports: ClassifyPorts,
    options: ClassifyOptions,
    trace_id: str | None,
    now: Callable[[], datetime],
) -> ClassificationResult:
    """Answer a command whose key row exists already (AD-6)."""
    key = earlier.key
    if earlier.result_json is not None:
        logger.info(
            "classification repeated: case_id=%s page_id=%s classification_id=%s",
            key.case_id,
            key.page_id,
            earlier.classification_id,
        )
        return ClassificationResult.model_validate_json(earlier.result_json)
    limit = timedelta(seconds=options.deadline_seconds + options.stale_margin_seconds)
    if now() - earlier.started_at <= limit:
        raise DomainError(ErrorCode.IN_PROGRESS, IN_PROGRESS_MESSAGE)
    # Nothing is working on it any more. It fails, as it would have at its
    # deadline, under the id its key row was given.
    run = _Run(
        classification_id=earlier.classification_id,
        key=key,
        actor=_actor_of(key.contender, options),
        eval_run_id=command.eval_run_id,
        trace_id=trace_id or NO_TRACE_ID,
    )
    return await asyncio.shield(
        _fail(run, ErrorCode.STAGE_TIMEOUT, "stale", ports.repository, now)
    )


async def _classify(
    run: _Run,
    ports: ClassifyPorts,
    options: ClassifyOptions,
    trace_context: Mapping[str, str],
    now: Callable[[], datetime],
) -> ClassificationResult:
    key = run.key
    # Each contender has its own way to the same result (AD-13).
    if key.contender is ClassifierContender.DOC_INTELLIGENCE:
        reading = await _ask_classifier(key, ports, trace_context)
    else:
        reading = await _ask_model(key, ports, options, trace_context)
    classification = Classification(
        classification_id=run.classification_id,
        case_id=key.case_id,
        page_id=key.page_id,
        contender=key.contender,
        page_type=reading.page_type,
        # AD-13: from the one mapping, never from the classifier.
        is_medical=is_medical(reading.page_type),
        confidence=reading.confidence,
        reason=reading.reason,
    )
    result = done_result(run, classification, now())
    stored = ClassificationResult.model_validate_json(
        await _store_done(run, result, classification, ports.repository)
    )
    if stored != result:
        # Settled as failed by another call first: that result stands, and
        # this classification was not stored.
        logger.warning(
            "classification superseded: case_id=%s page_id=%s classification_id=%s",
            key.case_id,
            key.page_id,
            run.classification_id,
        )
        return stored
    # Ids, counts and timings: never the page type, the reason or the text.
    logger.info(
        "page classified: case_id=%s page_id=%s classification_id=%s contender=%s "
        "runs=%d agreeing_runs=%d duration_ms=%d",
        key.case_id,
        key.page_id,
        run.classification_id,
        key.contender.value,
        reading.runs,
        reading.agreeing_runs,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def _ask_model(
    key: ClassificationKey,
    ports: ClassifyPorts,
    options: ClassifyOptions,
    trace_context: Mapping[str, str],
) -> _Reading:
    """The `llm` contender: what the runs of the chat model agree on."""
    page = await ports.pages.read_page(key.page_id, trace_context)
    if page is None:
        raise PageNotReadable
    outputs = await _run_model(
        page, ports.model, options.runs, options.max_concurrent_runs
    )
    agreement = agree(outputs)
    return _Reading(
        page_type=agreement.page_type,
        confidence=agreement.confidence,
        reason=agreement.reason,
        runs=len(outputs),
        agreeing_runs=agreement.agreeing_runs,
    )


async def _ask_classifier(
    key: ClassificationKey, ports: ClassifyPorts, trace_context: Mapping[str, str]
) -> _Reading:
    """The `doc-intelligence` contender: the page as a one-page document, asked once."""
    if ports.classifier is None:
        # `classify_page` refused the command before it came this far.
        raise ModelCallFailed("classifier_not_configured")
    pdf = await ports.pages.read_page_file(key.case_id, key.page_id, trace_context)
    if pdf is None:
        raise PageNotReadable
    page_type, confidence = reading_of(await ports.classifier.classify(pdf))
    return _Reading(
        page_type=page_type,
        confidence=confidence,
        reason=CLASSIFIER_REASON.format(page_type.value),
        runs=1,
        agreeing_runs=1,
    )


def reading_of(answer: ClassifierAnswer) -> tuple[PageType, float]:
    """The page type and confidence of the classifier's answer, or `InvalidModelOutput`.

    A document type that is no page type, an answer that names none, and a
    confidence that is no number from 0 to 1 are refused, as a model's
    answer in another shape is: nothing is guessed in their place.
    """
    try:
        page_type = PageType(answer.doc_type or "")
    except ValueError:
        raise InvalidModelOutput from None
    confidence = answer.confidence
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        raise InvalidModelOutput
    return page_type, float(confidence)


async def _run_model(
    page: PageContent, model: PageModel, runs: int, max_concurrent_runs: int
) -> list[ClassifierOutput]:
    """Run the model `runs` times on the page, a bounded number at once.

    Every answer is parsed into a `ClassifierOutput`. One run that fails, or
    whose answer is not valid, fails them all: the runs still under way are
    stopped, and no partial answer is passed on.
    """
    gate = asyncio.Semaphore(max_concurrent_runs)

    async def one_run() -> ClassifierOutput:
        async with gate:
            answer = await model.classify(page)
        try:
            return ClassifierOutput.model_validate_json(answer)
        except ValidationError:
            # Not raised from the validation error: that one holds the answer.
            raise InvalidModelOutput from None

    tasks = [asyncio.create_task(one_run()) for _ in range(runs)]
    try:
        return list(await asyncio.gather(*tasks))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _store_done(
    run: _Run,
    result: ClassificationResult,
    classification: Classification,
    repository: ClassificationRepository,
) -> str:
    """Store a done result, trying once more if the first attempt fails.

    The model's work is done by now, and a store that fails once often
    works the next moment. A second failure is raised: the classification
    then ends as failed, if that at least can be stored.
    """
    try:
        return await repository.finish(
            run.classification_id, result.model_dump_json(), classification
        )
    except Exception as error:  # noqa: BLE001 - whatever failed, it is tried once more
        logger.warning(
            "classification store retried: case_id=%s page_id=%s "
            "classification_id=%s type=%s",
            run.key.case_id,
            run.key.page_id,
            run.classification_id,
            type(error).__qualname__,
        )
    return await repository.finish(
        run.classification_id, result.model_dump_json(), classification
    )


async def _release(
    run: _Run, reason: str, repository: ClassificationRepository
) -> None:
    """Give the key row up, so that the same command sent again does the work."""
    key = run.key
    try:
        await repository.release(run.classification_id)
        released = True
    except Exception as error:  # noqa: BLE001 - the row then stays `running`; a later repeat settles it as stale
        released = False
        reason = f"{reason}:{type(error).__qualname__}"
    logger.warning(
        "classification released: case_id=%s page_id=%s classification_id=%s "
        "reason=%s released=%s duration_ms=%d",
        key.case_id,
        key.page_id,
        run.classification_id,
        reason,
        str(released).lower(),
        int((time.monotonic() - run.started) * 1000),
    )


async def _fail(
    run: _Run,
    error_code: ErrorCode,
    reason: str,
    repository: ClassificationRepository,
    now: Callable[[], datetime],
) -> ClassificationResult:
    """End a classification as failed: store the result that says why."""
    key = run.key
    result = failed_result(run, error_code, now())
    try:
        stored = ClassificationResult.model_validate_json(
            await repository.finish(
                run.classification_id, result.model_dump_json(), None
            )
        )
    except Exception as error:
        # The key row stays `running`; a later repeat settles it.
        logger.error(
            "classification failed: case_id=%s page_id=%s classification_id=%s "
            "error_code=%s reason=%s stored=false type=%s",
            key.case_id,
            key.page_id,
            run.classification_id,
            error_code.value,
            reason,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_RECORDED_MESSAGE
        ) from error
    logger.error(
        "classification failed: case_id=%s page_id=%s classification_id=%s "
        "error_code=%s reason=%s stored=%s duration_ms=%d",
        key.case_id,
        key.page_id,
        run.classification_id,
        error_code.value,
        reason,
        stored.status.value,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def list_classifications(
    case_id: str, *, repository: ClassificationRepository
) -> ClassificationList:
    """The case's stored classifications; an empty list for a case with none."""
    return ClassificationList(
        case_id=case_id, classifications=await repository.of_case(case_id)
    )
