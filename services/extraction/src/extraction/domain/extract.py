"""Extract the facts of one page, each with a quote checked in code (AD-14, AD-6).

The stage `workflow` commands for every page that reaches `extracting`. The
page is read from `intake`, so only the redacted reading is ever used. The
model proposes facts, each with a verbatim quote; everything else about a
fact is set here: its id, its page and page number, whether its quote is on
the page, and where. The model never decides any of that.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from pydantic import ValidationError

from contracts.audit import AuditAction, AuditRecord
from contracts.enums import ActorKind, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import (
    ExtractedFact,
    ExtractFactsCommand,
    ExtractionOutput,
    Fact,
    FactList,
    FactSetResult,
)
from contracts.text import QuoteFinder, has_mask_token, is_only_mask_tokens
from extraction.domain.entities import FactSetKey, KeyRow, PageReading
from extraction.domain.ports import (
    FactModel,
    FactRepository,
    ModelCallFailed,
    ModelUnavailable,
    PageReader,
)

logger = logging.getLogger(__name__)

UNKNOWN_PAGE_MESSAGE = "That page could not be found."
IN_PROGRESS_MESSAGE = "The page's facts are still being extracted."
NOT_RECORDED_MESSAGE = "The facts could not be recorded. Please try again."

_NO_TRACE_CONTEXT: Mapping[str, str] = MappingProxyType({})


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


class InvalidModelOutput(Exception):
    """The model's answer is not an `ExtractionOutput`: it is never passed on."""


class AnswerCutOff(InvalidModelOutput):
    """The model stopped at its token limit: what it gave is the start of an answer."""


# The model's finish reason for an answer cut off at the token limit.
CUT_OFF = "length"
# The name of the one field of the model's answer, as the contracts have it.
_FACTS_FIELD = "facts"


class PageNotReadable(Exception):
    """`intake` listed the page and then could not hand it over."""


@dataclass(frozen=True, slots=True)
class ExtractPorts:
    """What an extraction works with; the app factory or a test provides it."""

    repository: FactRepository
    pages: PageReader
    model: FactModel


@dataclass(frozen=True, slots=True)
class ExtractOptions:
    """The settings an extraction runs with (EXTRACTION_*: see the settings)."""

    # AD-8: who extracted, as `extraction:<chat deployment name>`.
    actor: str
    deadline_seconds: float = 180.0
    stale_margin_seconds: float = 60.0


@dataclass(slots=True)
class _Run:
    """One extraction under way: what it is for, and since when."""

    fact_set_id: str
    key: FactSetKey
    actor: str
    eval_run_id: str | None
    trace_id: str
    started: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class Proposals:
    """The model's answer for one page, read: what can be a fact, and how much cannot."""

    facts: tuple[ExtractedFact, ...]
    # How many entries of the answer were left out because they break the
    # contract: no quote, no statement, another field, a NUL character.
    not_facts: int = 0

    @property
    def proposed(self) -> int:
        """How many entries the answer held in all."""
        return len(self.facts) + self.not_facts


@dataclass(frozen=True, slots=True)
class CheckedFacts:
    """What the quote check made of the model's proposals for one page."""

    facts: tuple[Fact, ...]
    # How many proposals were left out because a masked value is no fact.
    masked_left_out: int

    @property
    def unverified_count(self) -> int:
        """How many of the facts have a quote that is not on the page."""
        return sum(not fact.quote_verified for fact in self.facts)


def is_masked_value(proposal: ExtractedFact) -> bool:
    """Whether a proposed fact is a masked value, which is never a fact (AD-21).

    So when its statement holds a mask token such as `[Person]`, or its
    quote is nothing but mask tokens and punctuation. A quote that has a
    mask token beside real text is a quote like any other.
    """
    return has_mask_token(proposal.statement) or is_only_mask_tokens(proposal.quote)


def _proposal_of(entry: object) -> ExtractedFact | None:
    """One entry of the model's answer as a proposed fact; None if it cannot be one.

    The statement is put on one line first (a model breaks lines where a
    person would not), and then the contracts judge the entry: exactly a
    statement and a quote, neither empty. A NUL character is refused too:
    no text of the database holds one.
    """
    if not isinstance(entry, dict):
        return None
    statement, quote = entry.get("statement"), entry.get("quote")
    if isinstance(statement, str):
        entry = {**entry, "statement": " ".join(statement.split())}
    if any(isinstance(text, str) and "\x00" in text for text in (statement, quote)):
        return None
    try:
        return ExtractedFact.model_validate(entry)
    except ValidationError:
        return None


def read_answer(answer: str) -> Proposals:
    """Read the model's answer for one page: the proposals that can be facts, and a count of the rest.

    The answer must be the contracts' `ExtractionOutput` in shape: one JSON
    object whose one field is the list of facts. Anything else is
    `InvalidModelOutput`, and no part of it is passed on. Within that list
    a single entry that breaks the contract is left out and counted, as a
    masked value is: one bad proposal does not fail the page, and with it
    the case.
    """
    try:
        given = json.loads(answer)
    except ValueError:
        # Not raised from the parser's error: that one holds the answer.
        raise InvalidModelOutput from None
    if (
        not isinstance(given, dict)
        or set(given) != {_FACTS_FIELD}
        or not isinstance(given[_FACTS_FIELD], list)
    ):
        raise InvalidModelOutput
    read = [_proposal_of(entry) for entry in given[_FACTS_FIELD]]
    facts = ExtractionOutput(facts=[item for item in read if item is not None]).facts
    return Proposals(tuple(facts), not_facts=len(read) - len(facts))


def check_facts(
    proposals: Sequence[ExtractedFact],
    page: PageReading,
    key: FactSetKey,
    new_fact_id: Callable[[], str] = new_id,
) -> CheckedFacts:
    """Turn the model's proposals for one page into facts, each with its quote checked (AD-14).

    Code sets everything but the statement and the quote: the fact's id, its
    page and that page's number, and whether the quote is on the page. A
    fact is `quote_verified` only when the contracts' quote finder finds its
    quote in the page text as `intake` stored it; it then carries the
    offsets of the first place the quote stands whole. A fact whose quote is
    not found is kept, flagged, and given no offsets. A masked value is left
    out and counted. The order of the proposals is kept.
    """
    # The page is normalised and mapped once, for all of its proposals.
    finder = QuoteFinder(page.text)
    facts: list[Fact] = []
    masked_left_out = 0
    for proposal in proposals:
        if is_masked_value(proposal):
            masked_left_out += 1
            continue
        match = finder.find(proposal.quote)
        facts.append(
            Fact(
                fact_id=new_fact_id(),
                case_id=key.case_id,
                page_id=key.page_id,
                page_number=page.page_number,
                statement=proposal.statement,
                quote=proposal.quote,
                quote_verified=match is not None,
                quote_start=match.start if match is not None else None,
                quote_end=match.end if match is not None else None,
            )
        )
    return CheckedFacts(tuple(facts), masked_left_out)


def _audit(run: _Run, action: AuditAction, occurred_at: datetime) -> AuditRecord:
    return AuditRecord(
        actor_kind=ActorKind.AI,
        actor=run.actor,
        action=action,
        occurred_at=occurred_at,
        case_id=run.key.case_id,
        page_id=run.key.page_id,
        # The owning record of an extraction is the page's fact set.
        ref=run.fact_set_id,
        detail=None,
        trace_id=run.trace_id,
        eval_run_id=run.eval_run_id,
    )


def done_result(
    run: _Run, checked: CheckedFacts, occurred_at: datetime
) -> FactSetResult:
    """The result of a finished extraction, with its `facts.extracted` record."""
    return FactSetResult(
        case_id=run.key.case_id,
        status=StageStatus.DONE,
        error_code=None,
        audit=_audit(run, AuditAction.FACTS_EXTRACTED, occurred_at),
        fact_set_id=run.fact_set_id,
        page_id=run.key.page_id,
        fact_ids=[fact.fact_id for fact in checked.facts],
        unverified_count=checked.unverified_count,
    )


def failed_result(
    run: _Run, error_code: ErrorCode, occurred_at: datetime
) -> FactSetResult:
    """The result of an extraction that failed: no facts, and the code that says why."""
    return FactSetResult(
        case_id=run.key.case_id,
        status=StageStatus.FAILED,
        error_code=error_code,
        audit=_audit(run, AuditAction.STAGE_FAILED, occurred_at),
        fact_set_id=run.fact_set_id,
        page_id=run.key.page_id,
        fact_ids=[],
        unverified_count=0,
    )


async def extract_facts(
    command: ExtractFactsCommand,
    *,
    ports: ExtractPorts,
    options: ExtractOptions,
    trace_id: str | None = None,
    trace_context: Mapping[str, str] = _NO_TRACE_CONTEXT,
    now: Callable[[], datetime] = utc_now,
) -> FactSetResult:
    """Extract the facts of one page; idempotent on `case_id` + `page_id`.

    The key row is inserted as running before any work. A repeat while it
    runs is `in_progress`; a repeat after the end is answered with the stored
    result and calls neither `intake` nor the model. After
    `options.deadline_seconds`, for the reading, the model's answer and the
    storing together, a failed result is stored. A key row left running by a
    process that died is taken over, and its page extracted, by the first
    repeat that comes `options.stale_margin_seconds` after its deadline. A page `intake` does
    not hold for the case is `not_found`: it leaves no key row, and is not
    worth sending again.

    What a repeat of the command can mend is not stored as a failure: when
    `intake` cannot be reached for the page (`upstream_unavailable`), or the
    request is cancelled because the caller went away or the service is
    stopping, the key row is released, so the command `workflow` sends again
    reads the page.
    """
    key = FactSetKey(command.case_id, command.page_id)
    repository = ports.repository
    earlier = await repository.find(key)
    run = _Run(
        fact_set_id=new_id(),
        key=key,
        actor=options.actor,
        eval_run_id=command.eval_run_id,
        trace_id=trace_id or NO_TRACE_ID,
    )
    if earlier is None:
        # Asked of `intake` before the key row, so a page that is not the
        # case's leaves nothing behind.
        page_ids = await ports.pages.page_ids_of_case(key.case_id, trace_context)
        if page_ids is None or key.page_id not in page_ids:
            raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
        try:
            earlier = await repository.begin(run.fact_set_id, key, now())
        except asyncio.CancelledError:
            # Cancelled while the key row was being written: it may be
            # there, with nobody to do its work. It is given up, if it is.
            await asyncio.shield(_release(run, "cancelled", repository))
            raise
    if earlier is not None:
        repeated = await _repeat(earlier, run, ports, options, now)
        if isinstance(repeated, FactSetResult):
            return repeated
        run = repeated

    # One deadline for everything: reading the page, the model's answer and
    # the storing of the facts.
    deadline = asyncio.timeout(options.deadline_seconds)
    try:
        async with deadline:
            return await _extract(run, ports, trace_context, now)
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
    if isinstance(error, AnswerCutOff):
        # The catalogue's code for an answer that is not valid; the log says
        # that it was cut off at the token limit, which a setting mends.
        return ErrorCode.INVALID_MODEL_OUTPUT, "answer_cut_off"
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
    run: _Run,
    ports: ExtractPorts,
    options: ExtractOptions,
    now: Callable[[], datetime],
) -> FactSetResult | _Run:
    """Answer a command whose key row exists already (AD-6).

    With the stored result if the extraction has ended; with `in_progress`
    while it runs. A key row still `running` long after its deadline was
    left by a process that died. Extraction does nothing outside its own
    tables before it finishes, so that work is simply done again: this call
    takes the row over, under the id it was given, and the run to do is
    answered.
    """
    key = earlier.key
    if earlier.result_json is not None:
        logger.info(
            "extraction repeated: case_id=%s page_id=%s fact_set_id=%s",
            key.case_id,
            key.page_id,
            earlier.fact_set_id,
        )
        return FactSetResult.model_validate_json(earlier.result_json)
    limit = timedelta(seconds=options.deadline_seconds + options.stale_margin_seconds)
    if now() - earlier.started_at <= limit:
        raise DomainError(ErrorCode.IN_PROGRESS, IN_PROGRESS_MESSAGE)
    taken = _Run(
        fact_set_id=earlier.fact_set_id,
        key=key,
        actor=run.actor,
        eval_run_id=run.eval_run_id,
        trace_id=run.trace_id,
    )
    try:
        won = await ports.repository.take_over(earlier, now())
    except asyncio.CancelledError:
        # As for a new key row: it may be this call's by now, with nobody
        # to do its work.
        await asyncio.shield(_release(taken, "cancelled", ports.repository))
        raise
    if not won:
        # Another repeat took it over first, or its owner finished after all.
        raise DomainError(ErrorCode.IN_PROGRESS, IN_PROGRESS_MESSAGE)
    logger.warning(
        "extraction taken over: case_id=%s page_id=%s fact_set_id=%s reason=stale",
        key.case_id,
        key.page_id,
        earlier.fact_set_id,
    )
    return taken


async def _propose(page: PageReading, model: FactModel) -> Proposals:
    """The model's one answer for the page, read; a page without text is not sent.

    An answer cut off at the token limit is `AnswerCutOff`, whatever it
    holds: the start of a list of facts is not the page's facts.
    """
    if not page.text.strip():
        # A blank page: there is nothing to read, and nothing to quote.
        return Proposals(())
    answer = await model.extract(page.text)
    if answer.finish_reason == CUT_OFF:
        raise AnswerCutOff
    return read_answer(answer.text)


async def _extract(
    run: _Run,
    ports: ExtractPorts,
    trace_context: Mapping[str, str],
    now: Callable[[], datetime],
) -> FactSetResult:
    key = run.key
    page = await ports.pages.read_page(key.page_id, trace_context)
    if page is None:
        raise PageNotReadable
    proposals = await _propose(page, ports.model)
    checked = check_facts(proposals.facts, page, key)
    result = done_result(run, checked, now())
    stored = FactSetResult.model_validate_json(
        await _store_done(run, result, checked.facts, ports.repository)
    )
    if stored != result:
        # Settled as failed by another call first: that result stands, and
        # these facts were not stored.
        logger.warning(
            "extraction superseded: case_id=%s page_id=%s fact_set_id=%s",
            key.case_id,
            key.page_id,
            run.fact_set_id,
        )
        return stored
    # Ids, counts and timings: never a statement, a quote or the page text.
    logger.info(
        "facts extracted: case_id=%s page_id=%s fact_set_id=%s proposed=%d "
        "facts=%d unverified=%d masked_left_out=%d not_facts_left_out=%d "
        "duration_ms=%d",
        key.case_id,
        key.page_id,
        run.fact_set_id,
        proposals.proposed,
        len(checked.facts),
        checked.unverified_count,
        checked.masked_left_out,
        proposals.not_facts,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def _store_done(
    run: _Run,
    result: FactSetResult,
    facts: Sequence[Fact],
    repository: FactRepository,
) -> str:
    """Store a done result, trying once more if the first attempt fails.

    The model's work is done by now, and a store that fails once often
    works the next moment. A second failure is raised: the extraction then
    ends as failed, if that at least can be stored.
    """
    try:
        return await repository.finish(run.fact_set_id, result.model_dump_json(), facts)
    except Exception as error:  # noqa: BLE001 - whatever failed, it is tried once more
        logger.warning(
            "extraction store retried: case_id=%s page_id=%s fact_set_id=%s type=%s",
            run.key.case_id,
            run.key.page_id,
            run.fact_set_id,
            type(error).__qualname__,
        )
    return await repository.finish(run.fact_set_id, result.model_dump_json(), facts)


async def _release(run: _Run, reason: str, repository: FactRepository) -> None:
    """Give the key row up, so that the same command sent again does the work."""
    key = run.key
    try:
        await repository.release(run.fact_set_id)
        released = True
    except Exception as error:  # noqa: BLE001 - the row then stays `running`; a later repeat takes it over
        released = False
        reason = f"{reason}:{type(error).__qualname__}"
    logger.warning(
        "extraction released: case_id=%s page_id=%s fact_set_id=%s "
        "reason=%s released=%s duration_ms=%d",
        key.case_id,
        key.page_id,
        run.fact_set_id,
        reason,
        str(released).lower(),
        int((time.monotonic() - run.started) * 1000),
    )


async def _fail(
    run: _Run,
    error_code: ErrorCode,
    reason: str,
    repository: FactRepository,
    now: Callable[[], datetime],
) -> FactSetResult:
    """End an extraction as failed: store the result that says why, and no fact."""
    key = run.key
    result = failed_result(run, error_code, now())
    try:
        stored = FactSetResult.model_validate_json(
            await repository.finish(run.fact_set_id, result.model_dump_json(), None)
        )
    except Exception as error:
        # The key row stays `running`; a later repeat takes it over.
        logger.error(
            "extraction failed: case_id=%s page_id=%s fact_set_id=%s "
            "error_code=%s reason=%s stored=false type=%s",
            key.case_id,
            key.page_id,
            run.fact_set_id,
            error_code.value,
            reason,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_RECORDED_MESSAGE
        ) from error
    logger.error(
        "extraction failed: case_id=%s page_id=%s fact_set_id=%s "
        "error_code=%s reason=%s stored=%s duration_ms=%d",
        key.case_id,
        key.page_id,
        run.fact_set_id,
        error_code.value,
        reason,
        stored.status.value,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def list_facts(case_id: str, *, repository: FactRepository) -> FactList:
    """The case's stored facts, in page order; an empty list for a case with none."""
    return FactList(case_id=case_id, facts=await repository.of_case(case_id))
