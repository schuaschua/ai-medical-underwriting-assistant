"""The ingestion of the manual: one run, the same result every time (spine AD-12).

A run is for one chunk set. It reads what is stored and then the manual.
When the manual, the recipe and the deployments are those of the last
successful run it ends there. Otherwise it has the manual parsed, cuts it
into chunks, and brings the stored chunk set to what it found. A chunk whose
text is as it was stored costs no model call; a changed one gets a new
vector (and, in the `smart` set, a new context line); a chunk the manual no
longer has is removed. Everything is written in one transaction at the end,
so a run that fails leaves the index as it was.

The `fixed` set (AD-11, row `r1`) is the plain baseline: the same parsed
manual and the same embedding deployment, no chat model and no context
line. `ingest_chunk_sets` runs several sets over one read of the manual and
one parse, under one deadline; each set is a run of its own, and one that
fails leaves the others as they were. `built_from_different_manuals` says
afterwards whether the stored sets still stand on one manual.
"""

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Awaitable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from contracts.enums import ChunkSet
from contracts.errors import DomainError, ErrorCode
from retrieval.domain.chunker import ManualInvalid, cut_chunks, cut_fixed_chunks
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    ChunkRecord,
    IngestPlan,
    IngestReport,
    IngestRun,
    ParsedLayout,
    StoredChunk,
)
from retrieval.domain.ports import (
    ChunkModel,
    ChunkRepository,
    IndexChanged,
    LayoutFailed,
    LayoutParser,
    ManualMissing,
    ManualStore,
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelUnavailable,
)

logger = logging.getLogger(__name__)

# Every character Unicode treats as a line break: a context line holds none.
_LINE_BREAKS = frozenset("\r\n\x0b\x0c\x85  ")
# The one field of the answer the prompt asks for.
CONTEXT_FIELD = "context_line"
# What the one embedding call before the context lines is made with: a few
# words of no meaning, to see that the deployment answers with vectors of the
# right size before a hundred chat calls are spent.
EMBEDDING_PROBE = "underwriting manual"

MANUAL_MISSING_MESSAGE = "The manual is not in its container."
LAYOUT_FAILED_MESSAGE = "The manual could not be parsed."
MANUAL_INVALID_MESSAGE = "The manual could not be cut into one chunk per rule."
MODEL_UNAVAILABLE_MESSAGE = "The model is not available."
MODEL_REFUSED_MESSAGE = "The model refused the call."
INVALID_OUTPUT_MESSAGE = "The model's answer was not what was asked for."
TOO_MANY_REMOVED_MESSAGE = "The run would remove too many of the stored chunks."
INDEX_CHANGED_MESSAGE = "Another run changed the index while this one worked."
DEADLINE_MESSAGE = "The ingestion took too long."


class IngestError(DomainError):
    """A run that ended without touching the index.

    `code` is from the error catalogue; `reason` says more exactly what went
    wrong, as a short code for the log, and `where` names a rule id or a
    page number when there is one. None of them holds text of the manual or
    of a model's answer.
    """

    def __init__(
        self, code: ErrorCode, message: str, reason: str, where: str = ""
    ) -> None:
        super().__init__(code, message)
        self.reason = reason
        self.where = where


@dataclass(frozen=True, slots=True)
class IngestPorts:
    manual: ManualStore
    layout: LayoutParser
    model: ChunkModel
    repository: ChunkRepository


@dataclass(frozen=True, slots=True)
class IngestOptions:
    """What a run works with, as the settings say."""

    # AD-16: the deployment names, stored with every chunk they made.
    chat_deployment: str
    embedding_deployment: str
    # A digest of the prompt the context line is asked for with: a changed
    # prompt writes every context line again.
    prompt_digest: str
    # The chunk set the run writes.
    chunk_set: ChunkSet = ChunkSet.SMART
    # AD-11, row `r1`: how many words a `fixed` chunk holds, and how many of
    # them it shares with the chunk before it. Words, not model tokens: the
    # baseline only has to be fixed and stated.
    fixed_chunk_words: int = 350
    fixed_overlap_words: int = 35
    context_line_max_chars: int = 300
    embedding_batch_size: int = 16
    # A run after which more than this share of the rules the stored chunks
    # define would be defined by none is refused: a manual that was read
    # badly looks just like one that lost its rules. `allow_large_removal`
    # lets such a run of this chunk set through, for a manual that really
    # did lose them.
    max_removed_share: float = 0.1
    allow_large_removal: bool = False
    # Everything before the one transaction that stores the result ends
    # after this long; None is no limit.
    deadline_seconds: float | None = None


def rule_in_its_place(chunk: Chunk) -> str:
    """What the chat model is shown of one chunk: where the rule is printed, and its text."""
    lines = [f"Section: {chunk.section_id.partition('.')[0]} {chunk.impairment}"]
    if chunk.section_title:
        lines.append(f"Part: {chunk.section_id} {chunk.section_title}")
    lines += ["Rule text:", chunk.text]
    return "\n".join(lines)


def embedding_text(context_line: str, text: str) -> str:
    """AD-12: what is embedded for a chunk: its context line, then its text."""
    return f"{context_line}\n{text}"


def recipe_digest(options: IngestOptions) -> str:
    """A digest of how the run's chunk set is made, besides the manual and the deployments.

    For the `smart` set the prompt's digest; for the `fixed` set the chunk
    size and the overlap. It is part of the run record, so a change of it
    is a run with work to do.
    """
    if options.chunk_set is ChunkSet.SMART:
        return options.prompt_digest
    made_with = [
        options.chunk_set.value,
        options.fixed_chunk_words,
        options.fixed_overlap_words,
    ]
    return hashlib.sha256(json.dumps(made_with).encode()).hexdigest()


def chat_deployment_of(options: IngestOptions) -> str:
    """The chat deployment the run's chunks are made with: none for the `fixed` set."""
    return options.chat_deployment if options.chunk_set is ChunkSet.SMART else ""


def fingerprint(chunk: Chunk, options: IngestOptions) -> str:
    """A hash of everything a chunk's context line and vector are made from.

    For a `smart` chunk: the chunk as the chat model is shown it, the
    prompt, and the two deployment names. For a `fixed` chunk: its text,
    where it starts, and the embedding deployment. The manual page is not
    part of it: a chunk that only moved to another page keeps its vector.
    """
    if chunk.chunk_set is ChunkSet.FIXED:
        made_from: list[object] = [
            chunk.chunk_set.value,
            chunk.section_id,
            chunk.section_title,
            chunk.impairment,
            chunk.text,
            options.embedding_deployment,
            EMBEDDING_DIMENSIONS,
        ]
        return hashlib.sha256(json.dumps(made_from).encode()).hexdigest()
    made_from = [
        rule_in_its_place(chunk),
        options.prompt_digest,
        options.chat_deployment,
        options.embedding_deployment,
        EMBEDDING_DIMENSIONS,
    ]
    return hashlib.sha256(json.dumps(made_from).encode()).hexdigest()


def plan_ingestion(
    chunks: Sequence[Chunk],
    stored: Mapping[str, StoredChunk],
    options: IngestOptions,
) -> IngestPlan:
    """What a run has to do, given the chunks of the manual and what is stored."""
    write: list[Chunk] = []
    move: list[Chunk] = []
    unchanged = 0
    for chunk in chunks:
        before = stored.get(chunk.chunk_id)
        if before is None or before.content_hash != fingerprint(chunk, options):
            write.append(chunk)
        elif before.manual_page != chunk.manual_page:
            move.append(chunk)
        else:
            unchanged += 1
    found = {chunk.chunk_id for chunk in chunks}
    remove = sorted(chunk_id for chunk_id in stored if chunk_id not in found)
    return IngestPlan(tuple(write), tuple(move), tuple(remove), unchanged)


def parse_context_line(answer: str, max_chars: int) -> str:
    """The context line in the chat model's answer; `IngestError` if it is not one.

    The answer is one JSON object with the one field. The line is one line of
    text, not empty and not longer than `max_chars`: a line that is too long
    is refused, never cut.
    """
    try:
        parsed: Any = json.loads(answer)
    except ValueError:
        raise _invalid_output("context_line_not_json") from None
    if not isinstance(parsed, dict) or set(parsed) != {CONTEXT_FIELD}:
        raise _invalid_output("context_line_not_the_object")
    line = parsed[CONTEXT_FIELD]
    if not isinstance(line, str):
        raise _invalid_output("context_line_not_text")
    line = line.strip()
    if not line:
        raise _invalid_output("context_line_empty")
    if _LINE_BREAKS.intersection(line):
        raise _invalid_output("context_line_multi_line")
    if len(line) > max_chars:
        raise _invalid_output("context_line_too_long")
    return line


def checked_vector(vector: Sequence[Any]) -> tuple[float, ...]:
    """The vector as the model gave it, if it is one of the right size."""
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise _invalid_output("embedding_wrong_size")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in vector
    ):
        raise _invalid_output("embedding_not_numbers")
    return tuple(float(value) for value in vector)


def _invalid_output(reason: str) -> IngestError:
    return IngestError(ErrorCode.INVALID_MODEL_OUTPUT, INVALID_OUTPUT_MESSAGE, reason)


async def _from_model[T](call: Awaitable[T]) -> T:
    """Await a call to a model; its failures become the run's."""
    try:
        return await call
    except ModelUnavailable:
        raise IngestError(
            ErrorCode.MODEL_UNAVAILABLE, MODEL_UNAVAILABLE_MESSAGE, "model_unavailable"
        ) from None
    except ModelCallFailed as error:
        raise IngestError(
            ErrorCode.STAGE_FAILED, MODEL_REFUSED_MESSAGE, error.reason
        ) from None
    except ModelAnswerInvalid as error:
        raise _invalid_output(error.reason) from None


async def _all_or_none[T](calls: Sequence[Awaitable[T]]) -> list[T]:
    """Run the calls together; when one fails, stop the others and raise its error."""
    tasks = [asyncio.ensure_future(call) for call in calls]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _context_line(chunk: Chunk, model: ChunkModel, options: IngestOptions) -> str:
    answer = await _from_model(model.context_line(rule_in_its_place(chunk)))
    return parse_context_line(answer, options.context_line_max_chars)


async def _embed(
    texts: Sequence[str], model: ChunkModel, options: IngestOptions
) -> list[tuple[float, ...]]:
    size = options.embedding_batch_size
    batches = [texts[start : start + size] for start in range(0, len(texts), size)]
    answers = await _all_or_none([_from_model(model.embed(batch)) for batch in batches])
    vectors: list[tuple[float, ...]] = []
    for batch, answer in zip(batches, answers, strict=True):
        if len(answer) != len(batch):
            raise _invalid_output("embedding_count_differs")
        vectors.extend(checked_vector(vector) for vector in answer)
    return vectors


async def write_records(
    chunks: Sequence[Chunk], model: ChunkModel, options: IngestOptions
) -> list[ChunkRecord]:
    """A vector for each chunk, and a context line for each `smart` one: the records to store."""
    if not chunks:
        return []
    # One small call first: a deployment that is not there, or whose vectors
    # have another size, is found before any context line is paid for.
    await _embed([EMBEDDING_PROBE], model, options)
    if options.chunk_set is ChunkSet.FIXED:
        # The plain baseline: no model writes about the chunk, and what is
        # embedded is its own text.
        lines = ["" for _ in chunks]
        texts = [chunk.text for chunk in chunks]
    else:
        lines = await _all_or_none(
            [_context_line(chunk, model, options) for chunk in chunks]
        )
        texts = [
            embedding_text(line, chunk.text)
            for chunk, line in zip(chunks, lines, strict=True)
        ]
    vectors = await _embed(texts, model, options)
    return [
        ChunkRecord(
            chunk=chunk,
            context_line=line,
            embedding=vector,
            content_hash=fingerprint(chunk, options),
            chat_deployment=chat_deployment_of(options),
            embedding_deployment=options.embedding_deployment,
        )
        for chunk, line, vector in zip(chunks, lines, vectors, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A run's result before it is stored."""

    pages: int
    chunks: int
    plan: IngestPlan
    records: list[ChunkRecord]
    stored: Mapping[str, StoredChunk]
    run: IngestRun


def check_removal(
    plan: IngestPlan,
    chunks: Sequence[Chunk],
    stored: Mapping[str, StoredChunk],
    options: IngestOptions,
) -> None:
    """Say which chunks a run removes, and refuse a run that loses too many rules.

    What is counted is the rules the stored chunks define and the new
    chunks no longer do, not the chunks: a `fixed` set cut to another size
    has another number of chunks and still defines every rule, whatever
    else changed, while a manual that was read badly loses rules in either
    set. In the `smart` set a chunk is a rule, so the two counts are one.
    """
    if plan.remove:
        # Ids only: a chunk id is its chunk set and its rule id or its position.
        logger.warning(
            "chunks to remove: count=%d of=%d chunk_ids=%s",
            len(plan.remove),
            len(stored),
            ",".join(plan.remove),
        )
    had = {rule_id for chunk in stored.values() for rule_id in chunk.rule_ids}
    lost = sorted(had - {rule_id for chunk in chunks for rule_id in chunk.rule_ids})
    if not lost:
        return
    logger.warning(
        "rules no longer defined: count=%d of=%d chunk_set=%s rule_ids=%s",
        len(lost),
        len(had),
        options.chunk_set.value,
        ",".join(lost),
    )
    if len(lost) > len(had) * options.max_removed_share and (
        not options.allow_large_removal
    ):
        raise IngestError(
            ErrorCode.STAGE_FAILED,
            TOO_MANY_REMOVED_MESSAGE,
            "too_many_chunks_removed",
            str(len(lost)),
        )


async def _prepare(
    ports: IngestPorts, options: IngestOptions
) -> _Prepared | IngestReport:
    chunk_set = options.chunk_set
    # The database first: a store that cannot be read is found before the
    # layout analysis and the models are spent on a run that could not end.
    stored = await ports.repository.stored(chunk_set)
    last = await ports.repository.last_run(chunk_set)
    try:
        pdf = await ports.manual.read()
    except ManualMissing:
        raise IngestError(
            ErrorCode.NOT_FOUND, MANUAL_MISSING_MESSAGE, "manual_missing"
        ) from None
    run = IngestRun(
        manual_sha256=hashlib.sha256(pdf).hexdigest(),
        prompt_digest=recipe_digest(options),
        chat_deployment=chat_deployment_of(options),
        embedding_deployment=options.embedding_deployment,
        chunk_count=len(stored),
    )
    if stored and last == run:
        # The same manual, recipe and deployments as the run that left these
        # chunks: nothing is parsed and no model is asked.
        logger.info(
            "manual unchanged since the last run: manual_sha256=%s chunks=%d "
            "chunk_set=%s",
            run.manual_sha256,
            len(stored),
            chunk_set.value,
        )
        return IngestReport(
            pages=0,
            chunks=len(stored),
            written=0,
            moved=0,
            removed=0,
            unchanged=len(stored),
            skipped=True,
        )
    try:
        layout = await ports.layout.parse(pdf)
    except LayoutFailed as error:
        raise IngestError(
            ErrorCode.UPSTREAM_UNAVAILABLE, LAYOUT_FAILED_MESSAGE, error.reason
        ) from None
    try:
        chunks = _cut(layout, options)
    except ManualInvalid as error:
        raise IngestError(
            ErrorCode.STAGE_FAILED, MANUAL_INVALID_MESSAGE, error.reason, error.where
        ) from None
    # Ids and counts only (security rule 31): never text of the manual.
    logger.info(
        "manual cut: manual_sha256=%s pages=%d chunks=%d chunk_set=%s",
        run.manual_sha256,
        len(layout.pages),
        len(chunks),
        chunk_set.value,
    )
    plan = plan_ingestion(chunks, stored, options)
    check_removal(plan, chunks, stored, options)
    records = await write_records(plan.write, ports.model, options)
    return _Prepared(
        pages=len(layout.pages),
        chunks=len(chunks),
        plan=plan,
        records=records,
        stored=stored,
        run=replace(run, chunk_count=len(chunks)),
    )


def _cut(layout: ParsedLayout, options: IngestOptions) -> list[Chunk]:
    """The chunks of the run's chunk set, cut from the parsed manual."""
    if options.chunk_set is ChunkSet.FIXED:
        return cut_fixed_chunks(
            layout, options.fixed_chunk_words, options.fixed_overlap_words
        )
    return cut_chunks(layout)


async def ingest_manual(ports: IngestPorts, options: IngestOptions) -> IngestReport:
    """Run the ingestion of one chunk set once; `IngestError` when it could not be done.

    Nothing is written before every chunk has its context line and vector.
    The deadline covers everything up to there. The one transaction that
    stores the result is outside it: a deadline that fired while it
    committed would report a failure for a run that was stored.
    """
    try:
        async with asyncio.timeout(options.deadline_seconds) as deadline:
            prepared = await _prepare(ports, options)
    except TimeoutError:
        # Only the job's own deadline: a time-out of something a port
        # called is that port's failure, and is raised as it is.
        if not deadline.expired():
            raise
        raise IngestError(
            ErrorCode.STAGE_TIMEOUT, DEADLINE_MESSAGE, "ingest_deadline"
        ) from None
    if isinstance(prepared, IngestReport):
        return prepared
    plan = prepared.plan
    try:
        await ports.repository.apply(
            options.chunk_set,
            planned_from=prepared.stored,
            write=prepared.records,
            move={chunk.chunk_id: chunk.manual_page for chunk in plan.move},
            remove=plan.remove,
            run=prepared.run,
        )
    except IndexChanged:
        raise IngestError(
            ErrorCode.IN_PROGRESS,
            INDEX_CHANGED_MESSAGE,
            "index_changed_by_another_run",
        ) from None
    return IngestReport(
        pages=prepared.pages,
        chunks=prepared.chunks,
        written=len(prepared.records),
        moved=len(plan.move),
        removed=len(plan.remove),
        unchanged=plan.unchanged,
    )


class _ReadOnce:
    """The manual and its parsed layout, fetched once for every chunk set of a job.

    A failure is kept as well: a manual that could not be read or parsed for
    one set is not asked for again for the next. So is a parse that was
    stopped half way, by the deadline or by anything else.
    """

    def __init__(self, manual: ManualStore, layout: LayoutParser) -> None:
        self._manual = manual
        self._layout = layout
        self._pdf: bytes | ManualMissing | None = None
        self._parsed: dict[bytes, ParsedLayout | LayoutFailed] = {}

    async def read(self) -> bytes:
        if self._pdf is None:
            try:
                self._pdf = await self._manual.read()
            except ManualMissing as error:
                self._pdf = error
        if isinstance(self._pdf, ManualMissing):
            raise self._pdf
        return self._pdf

    async def parse(self, pdf: bytes) -> ParsedLayout:
        if pdf not in self._parsed:
            try:
                self._parsed[pdf] = await self._layout.parse(pdf)
            except LayoutFailed as error:
                self._parsed[pdf] = error
            except BaseException:
                # Stopped before it ended: the next set does not send the
                # manual to the layout model a second time.
                self._parsed[pdf] = LayoutFailed("layout_not_finished")
                raise
        parsed = self._parsed[pdf]
        if isinstance(parsed, LayoutFailed):
            raise parsed
        return parsed


async def ingest_chunk_sets(
    ports: IngestPorts,
    options: IngestOptions,
    chunk_sets: Sequence[ChunkSet],
    *,
    allow_large_removal: Collection[ChunkSet] = (),
) -> dict[ChunkSet, IngestReport | Exception]:
    """Run the ingestion of each chunk set named, over one read and one parse of the manual.

    Each set is a run of its own, with its own checks, removal guard,
    transaction and run record; the removal guard is open only for the sets
    in `allow_large_removal`. `options.deadline_seconds` is one deadline
    over all of them: a set gets what the sets before it left. A set whose
    run could not be done has its error here in place of a report (an
    `IngestError`, or whatever else was raised), and the other sets are run
    all the same: each is left as its own run left it.
    """
    once = _ReadOnce(ports.manual, ports.layout)
    shared = replace(ports, manual=once, layout=once)
    clock = asyncio.get_running_loop().time
    ends_at = (
        None if options.deadline_seconds is None else clock() + options.deadline_seconds
    )
    outcomes: dict[ChunkSet, IngestReport | Exception] = {}
    for chunk_set in dict.fromkeys(chunk_sets):
        of_set = replace(
            options,
            chunk_set=chunk_set,
            allow_large_removal=chunk_set in allow_large_removal,
            deadline_seconds=None if ends_at is None else max(0.0, ends_at - clock()),
        )
        try:
            outcomes[chunk_set] = await ingest_manual(shared, of_set)
        except Exception as error:  # noqa: BLE001 - whatever one set raised, the others are still run and reported
            outcomes[chunk_set] = error
    return outcomes


async def built_from_different_manuals(
    repository: ChunkRepository,
) -> dict[ChunkSet, str]:
    """The manual each stored chunk set was last built from, when they are not all the same one; else empty.

    Rows of the ladder are compared with each other: sets cut from two
    editions of the manual would be compared as if they were one.
    """
    manuals: dict[ChunkSet, str] = {}
    for chunk_set in ChunkSet:
        last = await repository.last_run(chunk_set)
        if last is not None:
            manuals[chunk_set] = last.manual_sha256
    return manuals if len(set(manuals.values())) > 1 else {}
