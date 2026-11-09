"""Tenant assistant configuration.

Assistant configuration is platform metadata (never client knowledge or chat
content), so the platform owner may manage it through the org context. Knowledge
and conversations use the stricter ``require_org_member`` dependency instead.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    OrgContext,
    get_org_context,
    require_org_admin,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.assistant import Assistant
from app.models.document import Document
from app.models.enums import DocumentStatus, IngestionJobStatus
from app.models.ingestion_job import IngestionJob
from app.models.product import Product
from app.schemas.assistant import (
    AssistantAIOptions,
    AssistantCreate,
    AssistantRead,
    AssistantUpdate,
    ReembedResponse,
)
from app.services import audit, quota
from app.services.assistants import get_assistant, slugify
from app.services.ingestion import enqueue_ingestion

router = APIRouter(
    prefix="/organizations/{org_id}/assistants", tags=["assistants"]
)


async def _validate_product(session: AsyncSession, product_code: str | None) -> None:
    if product_code is None:
        return
    product = await session.get(Product, product_code)
    if product is None or not product.is_active:
        raise AppError(
            "That product is not available.",
            status_code=422,
            code="product_unavailable",
        )


def _validate_model_name(model: str | None) -> None:
    if model is None:
        return
    settings = get_settings()
    # The offline fake provider (no key configured) accepts any model name so
    # tests and local development can exercise tenant-specific overrides. The
    # allow-list only constrains real provider deployments.
    if settings.ai_provider == "fake" or not settings.ai_api_key:
        return
    supported = settings.ai_supported_chat_models
    if supported and model not in supported:
        raise AppError(
            "That model is not available on this deployment.",
            status_code=422,
            code="unsupported_model",
            details={"supported_models": supported},
        )


@router.post(
    "",
    response_model=AssistantRead,
    status_code=201,
    summary="Create an assistant",
)
async def create_assistant(
    payload: AssistantCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Assistant:
    await _validate_product(session, payload.product_code)
    _validate_model_name(payload.model_options.model)
    await quota.guard_assistant_create(session, context.org_id)

    slug = payload.slug or slugify(payload.name)
    assistant = Assistant(
        org_id=context.org_id,
        product_code=payload.product_code,
        name=payload.name,
        slug=slug,
        status=payload.status,
        system_prompt=payload.system_prompt,
        model_config=payload.model_options.model_dump(exclude_unset=True),
        retrieval_config=payload.retrieval_config.model_dump(),
        branding=payload.branding,
        version=1,
    )
    session.add(assistant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "An assistant with that slug already exists in this organization.",
            status_code=409,
            code="slug_taken",
        ) from exc

    await audit.record(
        session,
        action="assistant.created",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="assistant",
        resource_id=str(assistant.id),
        metadata={"slug": assistant.slug, "product_code": assistant.product_code},
        request=request,
    )
    await session.flush()
    await session.refresh(assistant)
    await session.commit()
    return assistant


@router.get(
    "",
    response_model=list[AssistantRead],
    summary="List assistants",
)
async def list_assistants(
    context: OrgContext = Depends(get_org_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Assistant]:
    rows = await session.scalars(
        select(Assistant)
        .where(Assistant.org_id == context.org_id)
        .order_by(Assistant.name.asc())
    )
    return list(rows)


@router.get(
    "/{assistant_id}",
    response_model=AssistantRead,
    summary="Read one assistant",
)
async def read_assistant(
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(get_org_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Assistant:
    return await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )


@router.patch(
    "/{assistant_id}",
    response_model=AssistantRead,
    summary="Update an assistant",
)
async def update_assistant(
    assistant_id: uuid.UUID,
    payload: AssistantUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Assistant:
    assistant = await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return assistant

    if "product_code" in changes:
        await _validate_product(session, changes["product_code"])

    if "model_options" in changes:
        value = changes.pop("model_options")
        if value is None:
            assistant.model_config = {}
        else:
            _validate_model_name(payload.model_options.model)
            assistant.model_config = {**(assistant.model_config or {}), **value}
    if "branding" in changes:
        value = changes.pop("branding")
        if value is not None:
            assistant.branding = value
    if "retrieval_config" in changes:
        changes.pop("retrieval_config")
        if payload.retrieval_config is not None:
            assistant.retrieval_config = payload.retrieval_config.model_dump()
    if changes.get("slug") is None:
        changes.pop("slug", None)

    for field, value in changes.items():
        setattr(assistant, field, value)

    assistant.version += 1
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "An assistant with that slug already exists in this organization.",
            status_code=409,
            code="slug_taken",
        ) from exc

    await audit.record(
        session,
        action="assistant.updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="assistant",
        resource_id=str(assistant.id),
        metadata={"fields": sorted(payload.model_dump(exclude_unset=True))},
        request=request,
    )
    await session.flush()
    await session.refresh(assistant)
    await session.commit()
    return assistant


def _resolved_options(assistant: Assistant) -> tuple[str | None, float, int]:
    settings = get_settings()
    options = assistant.model_config or {}
    model = options.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None
    temperature = options.get("temperature")
    if (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not 0.0 <= float(temperature) <= 2.0
    ):
        temperature = settings.ai_temperature
    max_output = options.get("max_output_tokens")
    if (
        not isinstance(max_output, int)
        or isinstance(max_output, bool)
        or not 64 <= max_output <= 4096
    ):
        max_output = settings.chat_max_output_tokens
    return model, float(temperature), max_output


@router.get(
    "/{assistant_id}/ai-options",
    response_model=AssistantAIOptions,
    summary="Read effective AI configuration (never the provider key)",
)
async def read_ai_options(
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(get_org_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssistantAIOptions:
    assistant = await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    settings = get_settings()
    fake = settings.ai_provider == "fake" or not settings.ai_api_key
    provider = "fake" if fake else settings.ai_provider
    default_model = "fake-workmeta-1" if fake else settings.ai_chat_model
    model_override, temperature, max_output = _resolved_options(assistant)
    return AssistantAIOptions(
        provider=provider,
        provider_key_configured=bool(settings.ai_api_key),
        effective_model=model_override or default_model,
        supported_models=list(settings.ai_supported_chat_models),
        model_override=model_override,
        temperature=temperature,
        max_output_tokens=max_output,
        defaults={
            "model": default_model,
            "temperature": settings.ai_temperature,
            "max_output_tokens": settings.chat_max_output_tokens,
            "embedding_model": settings.ai_embedding_model,
            "embedding_dimensions": settings.embedding_dimensions,
        },
    )


@router.post(
    "/{assistant_id}/reembed",
    response_model=ReembedResponse,
    status_code=202,
    summary="Queue every document for safe re-embedding",
)
async def reembed_assistant(
    request: Request,
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ReembedResponse:
    assistant = await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    if assistant.status == "archived":
        raise AppError(
            "An archived assistant cannot be re-embedded.",
            status_code=409,
            code="assistant_archived",
        )

    active_job = await session.scalar(
        select(IngestionJob.id)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(
            IngestionJob.org_id == context.org_id,
            Document.assistant_id == assistant.id,
            IngestionJob.status.in_(
                [IngestionJobStatus.queued.value, IngestionJobStatus.running.value]
            ),
        )
        .limit(1)
    )
    if active_job is not None:
        raise AppError(
            "This assistant already has an ingestion job in progress.",
            status_code=409,
            code="ingestion_in_progress",
        )

    documents = list(
        (
            await session.scalars(
                select(Document).where(
                    Document.org_id == context.org_id,
                    Document.assistant_id == assistant.id,
                )
            )
        ).all()
    )
    for document in documents:
        # Each worker job replaces that document's chunks in one transaction,
        # so a failed re-embed keeps the previous vectors searchable.
        document.status = DocumentStatus.uploaded.value
        document.error_message = None
        await enqueue_ingestion(session, document=document)

    await audit.record(
        session,
        action="assistant.reembed_requested",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="assistant",
        resource_id=str(assistant.id),
        metadata={"documents": len(documents)},
        request=request,
    )
    await session.commit()
    return ReembedResponse(
        assistant_id=assistant.id,
        documents=len(documents),
        queued=len(documents),
    )
