"""PostgreSQL adapter: redaction key rows, pages, page text and word boxes (AD-6, AD-14)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from opentelemetry import trace
from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from contracts.enums import StageStatus
from intake.adapters.db import (
    Database,
    case_table,
    document_table,
    page_table,
    page_text_table,
    redaction_table,
    word_box_table,
)
from intake.adapters.telemetry import adapter_span
from intake.domain.entities import Document, NewPage, PageRecord, Redaction, Word
from intake.settings import APP_ID

tracer = trace.get_tracer(APP_ID)


class SqlRedactionRepository:
    """Every statement uses bound parameters (security rule 21)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def document_of_case(self, case_id: str) -> Document | None:
        with adapter_span(tracer, "intake.db.document_of_case"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(document_table)
                    .where(document_table.c.case_id == case_id)
                    # An upload makes one document per case; the first, were
                    # there ever more.
                    .order_by(document_table.c.document_id)
                    .limit(1)
                )
                row = result.first()
        if row is None:
            return None
        return Document(
            document_id=row.document_id,
            case_id=row.case_id,
            original_blob_name=row.original_blob_name,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            created_at=row.created_at,
            idempotency_key=row.idempotency_key,
        )

    async def begin(
        self, case_id: str, document_id: str, started_at: datetime
    ) -> Redaction | None:
        """Insert the key row as running; the primary key settles calls that race."""
        with adapter_span(tracer, "intake.db.begin_redaction"):
            async with self._database.begin() as connection:
                inserted = await connection.execute(
                    pg_insert(redaction_table)
                    .values(
                        case_id=case_id,
                        document_id=document_id,
                        status=StageStatus.RUNNING.value,
                        started_at=started_at,
                    )
                    .on_conflict_do_nothing(index_elements=["case_id"])
                    .returning(redaction_table.c.case_id)
                )
                if inserted.first() is not None:
                    return None
                existing = await connection.execute(
                    select(redaction_table).where(redaction_table.c.case_id == case_id)
                )
                row = existing.one()
        return Redaction(
            case_id=row.case_id,
            document_id=row.document_id,
            started_at=row.started_at,
            result_json=row.result,
            job_id=row.job_id,
        )

    async def note_job(self, case_id: str, job_id: str) -> None:
        with adapter_span(tracer, "intake.db.note_redaction_job"):
            async with self._database.begin() as connection:
                await connection.execute(
                    update(redaction_table)
                    .where(redaction_table.c.case_id == case_id)
                    .values(job_id=job_id)
                )

    async def finish(
        self,
        case_id: str,
        result_json: str,
        pages: Sequence[NewPage],
        redacted_blob_name: str | None,
    ) -> str:
        """Store the result and the pages together, only if the row is still running."""
        status = (
            StageStatus.DONE if redacted_blob_name is not None else StageStatus.FAILED
        )
        with adapter_span(tracer, "intake.db.finish_redaction"):
            async with self._database.begin() as connection:
                settled = await connection.execute(
                    update(redaction_table)
                    .where(
                        redaction_table.c.case_id == case_id,
                        redaction_table.c.status == StageStatus.RUNNING.value,
                    )
                    .values(
                        status=status.value,
                        result=result_json,
                        finished_at=func.now(),
                        redacted_blob_name=redacted_blob_name,
                    )
                    .returning(redaction_table.c.case_id)
                )
                if settled.first() is None:
                    # Settled by another call: that result stands.
                    stored = await connection.execute(
                        select(redaction_table.c.result).where(
                            redaction_table.c.case_id == case_id
                        )
                    )
                    return str(stored.scalar_one())
                for page in pages:
                    await self._insert_page(connection, page)
        return result_json

    @staticmethod
    async def _insert_page(connection: AsyncConnection, page: NewPage) -> None:
        record = page.record
        await connection.execute(
            insert(page_table).values(
                page_id=record.page_id,
                case_id=record.case_id,
                document_id=record.document_id,
                page_number=record.page_number,
                page_width=record.width,
                page_height=record.height,
                thumbnail_blob_name=record.thumbnail_blob_name,
            )
        )
        await connection.execute(
            # PostgreSQL takes no NUL in text. Its replacement is one
            # character too, so the words' offsets still hold.
            insert(page_text_table).values(
                page_id=record.page_id, text=page.text.replace("\x00", "\ufffd")
            )
        )
        if page.words:
            await connection.execute(
                insert(word_box_table),
                [
                    {
                        "page_id": record.page_id,
                        "word_number": number,
                        "char_start": word.char_start,
                        "char_end": word.char_end,
                        "x0": word.x0,
                        "y0": word.y0,
                        "x1": word.x1,
                        "y1": word.y1,
                    }
                    for number, word in enumerate(page.words, start=1)
                ],
            )

    # --- Reads ---------------------------------------------------------------

    async def pages_of_case(self, case_id: str) -> list[PageRecord] | None:
        with adapter_span(tracer, "intake.db.pages_of_case"):
            async with self._database.connect() as connection:
                known = await connection.execute(
                    select(case_table.c.case_id).where(case_table.c.case_id == case_id)
                )
                if known.first() is None:
                    return None
                result = await connection.execute(
                    select(page_table)
                    .where(page_table.c.case_id == case_id)
                    .order_by(page_table.c.page_number)
                )
                return [_page(row) for row in result]

    async def page(self, page_id: str) -> PageRecord | None:
        with adapter_span(tracer, "intake.db.page"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(page_table).where(page_table.c.page_id == page_id)
                )
                row = result.first()
        return _page(row) if row is not None else None

    async def page_text(self, page_id: str) -> str | None:
        with adapter_span(tracer, "intake.db.page_text"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(page_text_table.c.text).where(
                        page_text_table.c.page_id == page_id
                    )
                )
                row = result.first()
        return str(row.text) if row is not None else None

    async def page_words(self, page_id: str) -> list[Word]:
        with adapter_span(tracer, "intake.db.page_words"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(word_box_table)
                    .where(word_box_table.c.page_id == page_id)
                    .order_by(word_box_table.c.word_number)
                )
                return [
                    Word(
                        char_start=row.char_start,
                        char_end=row.char_end,
                        x0=row.x0,
                        y0=row.y0,
                        x1=row.x1,
                        y1=row.y1,
                    )
                    for row in result
                ]

    async def redacted_blob_of(self, document_id: str) -> tuple[bool, str | None]:
        with adapter_span(tracer, "intake.db.redacted_blob_of"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(redaction_table.c.redacted_blob_name)
                    .select_from(
                        document_table.outerjoin(
                            redaction_table,
                            (
                                redaction_table.c.document_id
                                == document_table.c.document_id
                            )
                            & (redaction_table.c.status == StageStatus.DONE.value),
                        )
                    )
                    .where(document_table.c.document_id == document_id)
                )
                row = result.first()
        if row is None:
            return False, None
        return True, row.redacted_blob_name


def _page(row: Row[Any]) -> PageRecord:
    return PageRecord(
        page_id=row.page_id,
        case_id=row.case_id,
        document_id=row.document_id,
        page_number=row.page_number,
        width=row.page_width,
        height=row.page_height,
        thumbnail_blob_name=row.thumbnail_blob_name,
    )
