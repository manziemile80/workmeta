"""Knowledge document upload, listing, download and reprocessing.

Uploads are validated by content (magic bytes), stored under a random key and
queued for the worker. No document content is ever exposed to the platform
owner without a real tenant membership.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    OrgContext,
    require_org_admin,
    require_org_member,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.document import Document
from app.models.enums import DocumentStatus, IngestionJobStatus
from app.models.ingestion_job import IngestionJob
from app.schemas.document import DocumentRead
from app.services import audit, quota
from app.services.assistants import get_assistant
from app.services.document_lifecycle import delete_document_rows
from app.services.document_text import DocumentValidationError, validate_upload
from app.services.ingestion import enqueue_ingestion
from app.services.ocr import OCREngine, get_ocr_engine
from app.services.storage import StorageProvider, generate_storage_key, get_storage

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}/assistants/{assistant_id}/documents",
    tags=["documents"],
)

_UNSAFE_FILENAME = re.compile(r'[\r\n"\\\x00-\x1f]')


def _clean_filename(name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", Path(name).name).strip()
    return (cleaned or "document")[:255]


def _safe_header_filename(name: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode("ascii")
    return ascii_name.replace('"', "_") or "document"


def _read_model(document: Document) -> DocumentRead:
    return DocumentRead.model_validate(document).model_copy(
        update={
            "download_url": (
                f"{get_settings().api_v1_prefix}/organizations/{document.org_id}"
                f"/assistants/{document.assistant_id}/documents/{document.id}/download"
            )
        }
    )


async def _owned_document(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Document:
    document = await session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.org_id == org_id,
            Document.assistant_id == assistant_id,
        )
    )
    if document is None:
        raise AppError("Document not found.", status_code=404, code="not_found")
    return document


@router.post(
    "",
    response_model=DocumentRead,
    status_code=201,
    summary="Upload a knowledge document",
)
async def upload_document(
    request: Request,
    assistant_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    title: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
    ocr: OCREngine | None = Depends(get_ocr_engine),  # noqa: B008
) -> DocumentRead:
    assistant = await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    if assistant.status == "archived":
        raise AppError(
            "This assistant is archived and cannot receive documents.",
            status_code=409,
            code="assistant_archived",
        )

    settings = get_settings()
    raw = await file.read(settings.max_upload_bytes + 1)
    filename = _clean_filename(file.filename or "document")
    try:
        mime_type = validate_upload(
            filename=filename,
            declared_mime=file.content_type,
            data=raw,
            max_bytes=settings.max_upload_bytes,
            images_supported=ocr is not None,
        )
    except DocumentValidationError as exc:
        raise AppError(str(exc), status_code=422, code="invalid_document") from exc

    # Reserve document/storage capacity before writing bytes. The transaction
    # advisory lock makes the count-then-insert safe under concurrent uploads.
    await quota.guard_document_upload(
        session, context.org_id, size_bytes=len(raw)
    )

    storage_key = generate_storage_key(
        org_id=context.org_id, assistant_id=assistant_id
    )
    await storage.save(storage_key, raw)

    document = Document(
        org_id=context.org_id,
        assistant_id=assistant_id,
        title=(title or Path(filename).stem or "Document").strip()[:300],
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(raw),
        storage_key=storage_key,
        sha256=hashlib.sha256(raw).hexdigest(),
        status=DocumentStatus.uploaded.value,
        uploaded_by=context.user.id,
    )
    session.add(document)
    await session.flush()
    await enqueue_ingestion(session, document=document)

    await audit.record(
        session,
        action="document.uploaded",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="document",
        resource_id=str(document.id),
        metadata={
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(raw),
            "sha256": document.sha256,
        },
        request=request,
    )
    await session.flush()
    await session.refresh(document)
    await session.commit()
    return _read_model(document)


@router.get(
    "",
    response_model=list[DocumentRead],
    summary="List knowledge documents",
)
async def list_documents(
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[DocumentRead]:
    await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    rows = await session.scalars(
        select(Document)
        .where(
            Document.org_id == context.org_id,
            Document.assistant_id == assistant_id,
        )
        .order_by(Document.created_at.desc())
    )
    return [_read_model(document) for document in rows]


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Read one document",
)
async def read_document(
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DocumentRead:
    document = await _owned_document(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        document_id=document_id,
    )
    return _read_model(document)


@router.get(
    "/{document_id}/download",
    summary="Download the original document",
)
async def download_document(
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> Response:
    document = await _owned_document(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        document_id=document_id,
    )
    data = await storage.read(document.storage_key)
    filename = _safe_header_filename(document.filename)
    return Response(
        content=data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentRead,
    status_code=202,
    summary="Retry ingestion for a failed document",
)
async def reprocess_document(
    request: Request,
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DocumentRead:
    document = await _owned_document(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        document_id=document_id,
    )
    if document.status != DocumentStatus.failed.value:
        raise AppError(
            "Only a failed document can be reprocessed.",
            status_code=409,
            code="document_not_failed",
        )

    document.status = DocumentStatus.uploaded.value
    document.error_message = None
    await enqueue_ingestion(session, document=document)
    await audit.record(
        session,
        action="document.reprocess_requested",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="document",
        resource_id=str(document.id),
        request=request,
    )
    await session.flush()
    await session.refresh(document)
    await session.commit()
    return _read_model(document)


async def _active_job(session: AsyncSession, *, document_id: uuid.UUID) -> bool:
    job_id = await session.scalar(
        select(IngestionJob.id)
        .where(
            IngestionJob.document_id == document_id,
            IngestionJob.status.in_(
                [IngestionJobStatus.queued.value, IngestionJobStatus.running.value]
            ),
        )
        .limit(1)
    )
    return job_id is not None


@router.post(
    "/{document_id}/reingest",
    response_model=DocumentRead,
    status_code=202,
    summary="Re-ingest a document and replace its chunks",
)
async def reingest_document(
    request: Request,
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DocumentRead:
    document = await _owned_document(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        document_id=document_id,
    )
    if await _active_job(session, document_id=document_id):
        raise AppError(
            "This document already has an ingestion job in progress.",
            status_code=409,
            code="ingestion_in_progress",
        )

    # Old chunks stay searchable until the worker replaces them in a single
    # transaction. If the provider fails, the previous chunks remain intact.
    previous_status = document.status
    document.status = DocumentStatus.uploaded.value
    document.error_message = None
    await enqueue_ingestion(session, document=document)
    await audit.record(
        session,
        action="document.reingest_requested",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="document",
        resource_id=str(document.id),
        metadata={"previous_status": previous_status},
        request=request,
    )
    await session.flush()
    await session.refresh(document)
    await session.commit()
    return _read_model(document)


@router.delete(
    "/{document_id}",
    status_code=204,
    summary="Delete a document and its searchable chunks",
)
async def delete_document(
    request: Request,
    assistant_id: uuid.UUID,
    document_id: uuid.UUID,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> Response:
    document = await _owned_document(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        document_id=document_id,
    )
    storage_key = document.storage_key
    filename = document.filename
    sha256 = document.sha256

    # The tenant-checked SECURITY DEFINER function removes chunks, ingestion
    # jobs and the document row atomically. Audit rows and usage rows survive
    # (usage references are set to NULL by the schema).
    deleted = await delete_document_rows(
        session, org_id=context.org_id, document_id=document_id
    )
    if deleted == 0:
        raise AppError("Document not found.", status_code=404, code="not_found")

    await audit.record(
        session,
        action="document.deleted",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="document",
        resource_id=str(document_id),
        metadata={"filename": filename, "sha256": sha256},
        request=request,
    )
    await session.commit()

    # Bytes are removed only after the metadata/chunk deletion committed, so a
    # storage failure can never leave searchable vectors without a document.
    try:
        await storage.delete(storage_key)
    except Exception:  # noqa: BLE001 - DB state is already authoritative
        logger.exception(
            "document_storage_delete_failed",
            document_id=str(document_id),
        )

    return Response(status_code=204)
