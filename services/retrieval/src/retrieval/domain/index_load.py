"""Loading Azure AI Search's index from the stored chunk records (spine AD-11, AD-12; row `r5`).

A step of the ingestion job, after pgvector is written. The index gets
exactly the `smart` chunk records of the chunk table: the same ids, text,
context lines and vectors. Nothing is cut again and no model is asked, so
the store is the one thing in which row `r5` differs from the pgvector rows.

The load is safe to run again: it creates the index if the service has none,
uploads the documents that are new or changed, removes the ones whose chunk
is gone, and then compares what the index holds with what pgvector holds. A
difference fails the run. Whatever happens here, pgvector stays as it was
written.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from contracts.enums import ChunkSet
from contracts.errors import ErrorCode
from retrieval.domain.entities import (
    ChunkRecord,
    IndexDocument,
    IndexHoldings,
    IndexLoadReport,
)
from retrieval.domain.ingest import IngestError
from retrieval.domain.ports import (
    ChunkRepository,
    SearchIndexStore,
    SearchServiceUnavailable,
)

logger = logging.getLogger(__name__)

# AD-11: the chunk set every row on the search service reads.
INDEXED_CHUNK_SET = ChunkSet.SMART
# How many ids a log line names at most, of those two stores disagree on.
_IDS_IN_A_LOG_LINE = 20

LOAD_FAILED_MESSAGE = "The search index could not be loaded."
INDEX_DIFFERS_MESSAGE = "The search index does not hold what the chunk table holds."
LOAD_DEADLINE_MESSAGE = "Loading the search index took too long."


@dataclass(frozen=True, slots=True)
class IndexLoadOptions:
    """What a load works with, as the settings say."""

    # How many documents one upload carries: each holds a 3,072-number vector.
    upload_batch_size: int = 50
    # The whole load ends after this long; None is no limit.
    deadline_seconds: float | None = 300.0
    # The service takes a moment to count what it was just sent: the
    # comparison is made this often, this far apart, before it fails.
    check_attempts: int = 10
    check_wait_seconds: float = 1.0


def document_hash(record: ChunkRecord) -> str:
    """A hash of everything the index holds of a record, the vector by its `content_hash`.

    The record's own hash covers what the context line and the vector were
    made from, not the manual page or the ids: a chunk that only moved to
    another page is a changed document all the same.
    """
    chunk = record.chunk
    made_from = [
        chunk.chunk_id,
        chunk.chunk_set.value,
        list(chunk.rule_ids),
        list(chunk.reference_rule_ids),
        chunk.section_id,
        chunk.section_title,
        chunk.impairment,
        chunk.manual_page,
        chunk.text,
        record.context_line,
        record.content_hash,
        record.embedding_deployment,
    ]
    return hashlib.sha256(json.dumps(made_from).encode()).hexdigest()


def index_document(record: ChunkRecord) -> IndexDocument:
    """The stored record as the index holds it: every field as it is, nothing made anew."""
    chunk = record.chunk
    return IndexDocument(
        chunk_id=chunk.chunk_id,
        chunk_set=chunk.chunk_set,
        rule_ids=chunk.rule_ids,
        reference_rule_ids=chunk.reference_rule_ids,
        section_id=chunk.section_id,
        section_title=chunk.section_title,
        impairment=chunk.impairment,
        manual_page=chunk.manual_page,
        text=chunk.text,
        context_line=record.context_line,
        embedding=record.embedding,
        content_hash=record.content_hash,
        embedding_deployment=record.embedding_deployment,
        document_hash=document_hash(record),
    )


def difference(
    documents: Mapping[str, IndexDocument], held: IndexHoldings
) -> tuple[list[str], list[str], list[str]]:
    """The ids pgvector holds and the index lacks, those only the index holds, and those held changed."""
    hashes = held.document_hashes
    missing = sorted(chunk_id for chunk_id in documents if chunk_id not in hashes)
    extra = sorted(chunk_id for chunk_id in hashes if chunk_id not in documents)
    changed = sorted(
        chunk_id
        for chunk_id, document in documents.items()
        if chunk_id in hashes and hashes[chunk_id] != document.document_hash
    )
    return missing, extra, changed


def _same(documents: Mapping[str, IndexDocument], held: IndexHoldings) -> bool:
    """Whether the index's count and its ids are pgvector's, each document as stored."""
    return held.count == len(documents) and not any(difference(documents, held))


async def _load(
    repository: ChunkRepository,
    index: SearchIndexStore,
    options: IndexLoadOptions,
    sleep: Callable[[float], Awaitable[None]],
) -> IndexLoadReport:
    records = await repository.chunk_records(INDEXED_CHUNK_SET)
    documents = {record.chunk.chunk_id: index_document(record) for record in records}
    created = await index.ensure()
    held = await index.held()
    missing, extra, changed = difference(documents, held)
    upload = [documents[chunk_id] for chunk_id in sorted([*missing, *changed])]
    size = options.upload_batch_size
    for start in range(0, len(upload), size):
        await index.upload(upload[start : start + size])
    if extra:
        # Ids only (security rule 31).
        logger.warning(
            "search index documents to remove: count=%d chunk_ids=%s",
            len(extra),
            ",".join(extra[:_IDS_IN_A_LOG_LINE]),
        )
        await index.remove(extra)
    # The check runs after every load, also one that sent nothing.
    for attempt in range(1, options.check_attempts + 1):
        held = await index.held()
        if _same(documents, held):
            break
        if attempt < options.check_attempts:
            await sleep(options.check_wait_seconds)
    else:
        still_missing, still_extra, still_changed = difference(documents, held)
        logger.error(
            "search index is not what pgvector holds: index=%d pgvector=%d missing=%s "
            "extra=%s changed=%s",
            held.count,
            len(documents),
            ",".join(still_missing[:_IDS_IN_A_LOG_LINE]) or "-",
            ",".join(still_extra[:_IDS_IN_A_LOG_LINE]) or "-",
            ",".join(still_changed[:_IDS_IN_A_LOG_LINE]) or "-",
        )
        raise IngestError(
            ErrorCode.STAGE_FAILED,
            INDEX_DIFFERS_MESSAGE,
            "search_index_differs",
            f"index={held.count},pgvector={len(documents)}",
        )
    return IndexLoadReport(
        documents=len(documents),
        uploaded=len(upload),
        removed=len(extra),
        unchanged=len(documents) - len(upload),
        created=created,
    )


async def load_search_index(
    repository: ChunkRepository,
    index: SearchIndexStore,
    options: IndexLoadOptions,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> IndexLoadReport:
    """Bring the search service's index to the stored `smart` chunk records, and check it.

    `IngestError` when it could not be done, with a reason of its own
    (`search_index_...`): the service gave no usable answer
    (`upstream_unavailable`), the load took too long (`stage_timeout`), or
    the index and pgvector still differ afterwards (`stage_failed`).
    Nothing here writes to pgvector.
    """
    try:
        async with asyncio.timeout(options.deadline_seconds) as deadline:
            return await _load(repository, index, options, sleep)
    except SearchServiceUnavailable as error:
        raise IngestError(
            ErrorCode.UPSTREAM_UNAVAILABLE, LOAD_FAILED_MESSAGE, error.reason
        ) from None
    except TimeoutError:
        # Only the load's own deadline, as for the ingestion's.
        if not deadline.expired():
            raise
        raise IngestError(
            ErrorCode.STAGE_TIMEOUT, LOAD_DEADLINE_MESSAGE, "search_index_load_deadline"
        ) from None
