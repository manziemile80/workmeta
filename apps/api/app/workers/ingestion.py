"""Ingestion worker.

Run it as a separate process in development or production::

    python -m app.workers.ingestion            # drain queue, then keep polling
    python -m app.workers.ingestion --once     # process one job and exit
    python -m app.workers.ingestion --drain    # process until the queue is empty

Jobs are claimed with ``FOR UPDATE SKIP LOCKED``. The worker is only allowed to
see the job queue through ``app_is_worker()``; after claiming, it switches to the
job's tenant context before touching any document content.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.rls import set_rls_context
from app.db.session import SessionFactory, dispose_engine
from app.models.document import Document
from app.models.enums import DocumentStatus, IngestionJobStatus
from app.models.ingestion_job import IngestionJob
from app.services import audit
from app.services.ai import AIProvider, build_ai_provider
from app.services.ingestion import ingest_document
from app.services.ocr import OCREngine, build_ocr_engine
from app.services.storage import StorageProvider, build_storage

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ClaimedJob:
    id: uuid.UUID
    org_id: uuid.UUID
    document_id: uuid.UUID
    attempts: int
    max_attempts: int


async def _fail_abandoned(session: AsyncSession) -> None:
    """Mark jobs that exhausted retries after a worker crash."""
    await session.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET status = 'failed',
                error = 'Ingestion was interrupted too many times.',
                locked_at = NULL,
                updated_at = now()
            WHERE status = 'running'
              AND locked_at < now() - interval '15 minutes'
              AND attempts >= max_attempts
            """
        )
    )


async def _claim_job(session: AsyncSession) -> ClaimedJob | None:
    await set_rls_context(session, worker=True)
    await _fail_abandoned(session)
    row = (
        await session.execute(
            text(
                """
                UPDATE ingestion_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_at = now(),
                    error = NULL,
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM ingestion_jobs
                    WHERE attempts < max_attempts
                      AND (
                        (status = 'queued' AND available_at <= now())
                        OR (status = 'running'
                            AND locked_at < now() - interval '15 minutes')
                      )
                    ORDER BY available_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, org_id, document_id, attempts, max_attempts
                """
            )
        )
    ).mappings().first()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        org_id=row["org_id"],
        document_id=row["document_id"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


async def _process(
    job: ClaimedJob,
    ai: AIProvider,
    storage: StorageProvider,
    ocr: OCREngine | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_rls_context(session, org_id=job.org_id, worker=True)
        job_row = await session.get(IngestionJob, job.id)
        document = await session.get(Document, job.document_id)
        if job_row is None or document is None or document.org_id != job.org_id:
            raise RuntimeError("The document for this job no longer exists")

        document.status = DocumentStatus.processing.value
        document.error_message = None
        await session.flush()

        await ingest_document(
            session, document=document, ai=ai, storage=storage, ocr=ocr
        )
        job_row.status = IngestionJobStatus.succeeded.value
        job_row.error = None
        job_row.locked_at = None
        await session.commit()


async def _record_failure(
    job: ClaimedJob,
    error: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_rls_context(session, org_id=job.org_id, worker=True)
        job_row = await session.get(IngestionJob, job.id)
        document = await session.get(Document, job.document_id)
        if job_row is None:
            return

        message = error.strip()[:1000] or "Ingestion failed."
        exhausted = job.attempts >= job.max_attempts
        if exhausted:
            job_row.status = IngestionJobStatus.failed.value
            if document is not None:
                document.status = DocumentStatus.failed.value
                document.error_message = message
            await audit.record(
                session,
                action="document.ingestion_failed",
                org_id=job.org_id,
                resource_type="document",
                resource_id=str(job.document_id),
                metadata={"error": message, "attempts": job.attempts},
            )
        else:
            job_row.status = IngestionJobStatus.queued.value
            job_row.available_at = datetime.now(UTC) + timedelta(seconds=30)

        job_row.error = message
        job_row.locked_at = None
        await session.commit()


async def process_next_job(
    *,
    ai: AIProvider | None = None,
    storage: StorageProvider | None = None,
    ocr: OCREngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> bool:
    """Claim and process one job. Returns False when the queue is empty."""
    ai = ai or build_ai_provider()
    storage = storage or build_storage()
    ocr = ocr if ocr is not None else build_ocr_engine()

    async with session_factory() as session:
        claimed = await _claim_job(session)
        await session.commit()

    if claimed is None:
        return False

    logger.info("ingestion_job_claimed", job_id=str(claimed.id), attempts=claimed.attempts)
    try:
        await _process(claimed, ai, storage, ocr, session_factory)
    except Exception as exc:  # noqa: BLE001 - worker must survive any bad document
        logger.warning(
            "ingestion_job_failed",
            job_id=str(claimed.id),
            error_type=type(exc).__name__,
        )
        await _record_failure(claimed, str(exc), session_factory)
    return True


async def run(*, once: bool = False, drain: bool = False) -> None:
    settings = get_settings()
    ai = build_ai_provider()
    storage = build_storage()
    ocr = build_ocr_engine()
    try:
        if once:
            await process_next_job(ai=ai, storage=storage, ocr=ocr)
            return
        while True:
            processed = await process_next_job(ai=ai, storage=storage, ocr=ocr)
            if drain and not processed:
                return
            if not processed:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkMeta AI ingestion worker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="process at most one job")
    group.add_argument(
        "--drain", action="store_true", help="process jobs until the queue is empty"
    )
    args = parser.parse_args()
    asyncio.run(run(once=args.once, drain=args.drain))


if __name__ == "__main__":
    main()
