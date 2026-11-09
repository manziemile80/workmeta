"""Document ingestion pipeline: extract → chunk → embed → store chunks.

The whole pipeline runs in the job's tenant transaction. A failure rolls back
every chunk, so a retry never has to delete partially written data. When a
successful ingestion is repeated (re-ingestion after an update) the previous
chunks are removed through the narrow tenant-checked SECURITY DEFINER function
first, in the same transaction, so a failure leaves the old chunks intact and a
success never produces duplicate ``(document_id, chunk_index)`` rows.

OCR is optional and injected: without an engine, image and scanned-PDF uploads
fail with a clear message instead of silently storing an empty document.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.enums import DocumentStatus, IngestionJobStatus, UsageKind
from app.models.ingestion_job import IngestionJob
from app.models.usage_event import UsageEvent
from app.services import audit, quota
from app.services.ai import AIProvider
from app.services.document_lifecycle import purge_document_chunks
from app.services.document_text import chunk_pages, extract_pages_async
from app.services.ocr import OCREngine
from app.services.storage import StorageProvider
from app.services.usage import embedding_cost_micros

logger = structlog.get_logger(__name__)


async def enqueue_ingestion(session: AsyncSession, *, document: Document) -> IngestionJob:
    settings = get_settings()
    job = IngestionJob(
        org_id=document.org_id,
        document_id=document.id,
        status=IngestionJobStatus.queued.value,
        max_attempts=settings.ingestion_max_attempts,
    )
    session.add(job)
    await session.flush()
    return job


@dataclass(slots=True)
class IngestionResult:
    chunk_count: int
    page_count: int
    embedding_tokens: int


async def ingest_document(
    session: AsyncSession,
    *,
    document: Document,
    ai: AIProvider,
    storage: StorageProvider,
    ocr: OCREngine | None = None,
) -> IngestionResult:
    settings = get_settings()
    data = await storage.read(document.storage_key)

    pages = await extract_pages_async(
        data,
        document.mime_type,
        max_pages=settings.max_document_pages,
        ocr=ocr,
    )
    drafts = chunk_pages(
        pages,
        chunk_tokens=settings.chunk_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )

    # Re-ingestion safety: remove only this tenant's chunks for this document
    # through the SECURITY DEFINER helper. The old chunks and new chunks share
    # the same transaction, so a provider failure rolls back to the previous
    # searchable state.
    purged = await purge_document_chunks(
        session, org_id=document.org_id, document_id=document.id
    )
    if purged:
        logger.info(
            "document_chunks_purged",
            document_id=str(document.id),
            chunks=purged,
        )

    batch_size = max(1, settings.embedding_batch_size)
    # Embedding consumes AI tokens too, so it reserves against the same
    # monthly token/request allowance as chat. The reservation lives in the
    # worker transaction and rolls back if ingestion fails.
    embedding_tokens = sum(draft.token_count for draft in drafts)
    reservation = await quota.reserve_ai_request(
        session,
        org_id=document.org_id,
        estimated_tokens=embedding_tokens,
        operation="embedding",
    )
    for start in range(0, len(drafts), batch_size):
        batch = drafts[start : start + batch_size]
        embeddings = await ai.embed([draft.content for draft in batch])
        if len(embeddings) != len(batch):
            raise RuntimeError("Embedding provider returned the wrong batch size")
        for draft, embedding in zip(batch, embeddings, strict=True):
            session.add(
                DocumentChunk(
                    org_id=document.org_id,
                    assistant_id=document.assistant_id,
                    document_id=document.id,
                    chunk_index=draft.index,
                    content=draft.content,
                    token_count=draft.token_count,
                    page_number=draft.page_number,
                    embedding=embedding,
                    meta={
                        "page_number": draft.page_number,
                        "chunk_index": draft.index,
                    },
                )
            )
        await session.flush()

    document.status = DocumentStatus.ready.value
    document.page_count = len(pages)
    document.embedding_model = ai.embedding_model
    document.embedding_dimensions = ai.dimensions
    document.error_message = None

    session.add(
        UsageEvent(
            org_id=document.org_id,
            assistant_id=document.assistant_id,
            kind=UsageKind.embedding.value,
            tokens_in=embedding_tokens,
            cost_micros=embedding_cost_micros(embedding_tokens),
            meta={
                "document_id": str(document.id),
                "chunks": len(drafts),
                "model": ai.embedding_model,
                "ocr": ocr.name if ocr is not None else None,
            },
        )
    )
    await audit.record(
        session,
        action="document.ingested",
        org_id=document.org_id,
        resource_type="document",
        resource_id=str(document.id),
        metadata={
            "chunks": len(drafts),
            "pages": len(pages),
            "embedding_tokens": embedding_tokens,
            "replaced_chunks": purged,
            "ocr": ocr.name if ocr is not None else None,
        },
    )
    # Reconcile the estimate with the exact embedding token count.
    await quota.reconcile_ai_tokens(session, reservation, embedding_tokens)

    logger.info(
        "document_ingested",
        document_id=str(document.id),
        chunks=len(drafts),
        pages=len(pages),
    )
    return IngestionResult(
        chunk_count=len(drafts),
        page_count=len(pages),
        embedding_tokens=embedding_tokens,
    )
