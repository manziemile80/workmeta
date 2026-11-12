"""WorkMeta AI Submodels: train a model on your own work, then take it with you.

Every route is scoped to one organization. A client's training material,
workflows, tests and action plans stay in that client's own tenant, and a
submodel can only be exported by the client that trained it.

The lifecycle is: **create → train → test → export**, and on another
machine **install → use**. An installed submodel carries its workflows, so it
works there on its own.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin, require_org_member
from app.core.errors import AppError
from app.db.session import get_session
from app.models.enums import (
    SubmodelActionStatus,
    SubmodelMaterialKind,
    SubmodelStatus,
)
from app.models.submodel import (
    Submodel,
    SubmodelActionPlan,
    SubmodelMaterial,
    SubmodelProcedure,
    SubmodelTest,
)
from app.schemas.submodel import (
    SubmodelActionComplete,
    SubmodelActionConfirm,
    SubmodelActionDecision,
    SubmodelActionPlanCreate,
    SubmodelActionPlanRead,
    SubmodelCreate,
    SubmodelGuidanceRequest,
    SubmodelGuidanceResponse,
    SubmodelMaterialCreate,
    SubmodelMaterialRead,
    SubmodelPackageInfo,
    SubmodelRead,
    SubmodelTestCreate,
    SubmodelTestRead,
    SubmodelTrainingResult,
    SubmodelUpdate,
    SubmodelWorkflowRead,
    SubmodelWorkflowUpdate,
)
from app.services import audit, quota
from app.services.ai import AIProvider, get_ai_provider
from app.services.media import (
    AUDIO_MIME_TYPES,
    VIDEO_MIME_TYPES,
    MediaCapabilityUnavailable,
    build_audio_transcriber,
)
from app.services.storage import StorageProvider, get_storage
from app.services.submodel_packaging import (
    build_package_document,
    decode_package,
    install_submodel,
    verify_package_document,
)
from app.services.submodel_runtime import answer_with_submodel, run_submodel_test
from app.services.submodel_training import train_submodel
from app.services.submodels import (
    approved_procedures,
    build_package,
    create_submodel,
    get_submodel,
    list_submodels,
    record_action,
    submodel_counts,
)
from app.services.video_analysis import read_recording, render_timeline

router = APIRouter(
    prefix="/organizations/{org_id}/submodels", tags=["submodels"]
)

MAX_MATERIAL_BYTES = 500 * 1024 * 1024


async def _owned_submodel(
    session: AsyncSession, *, org_id: uuid.UUID, submodel_id: uuid.UUID
) -> Submodel:
    return await get_submodel(session, org_id=org_id, submodel_id=submodel_id)


async def _owned_material(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
) -> SubmodelMaterial:
    material = await session.scalar(
        select(SubmodelMaterial).where(
            SubmodelMaterial.id == material_id,
            SubmodelMaterial.org_id == org_id,
            SubmodelMaterial.submodel_id == submodel_id,
        )
    )
    if material is None:
        raise AppError(
            "Training material not found.",
            status_code=404,
            code="material_not_found",
        )
    return material


async def _submodel_read(
    session: AsyncSession, submodel: Submodel
) -> SubmodelRead:
    read = SubmodelRead.model_validate(submodel)
    for key, value in (
        await submodel_counts(
            session, org_id=submodel.org_id, submodel_id=submodel.id
        )
    ).items():
        setattr(read, key, value)
    return read


# ---------------------------------------------------------------------------
# Managing submodels
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=SubmodelRead,
    status_code=201,
    summary="Create a submodel for a task",
)
async def create(
    payload: SubmodelCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Start a submodel for one task the client's staff repeat."""
    await quota.guard_submodel_create(session, context.org_id)
    submodel = await create_submodel(
        session,
        org_id=context.org_id,
        name=payload.name,
        task=payload.task,
        system_code=payload.system_code,
        system_name=payload.system_name,
        system_version=payload.system_version,
        description=payload.description,
        target_kind=payload.target_kind,
        permissions=payload.permissions,
        capabilities=payload.capabilities,
        created_by=context.user.id,
    )
    await audit.record(
        session,
        action="submodel.created",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"task": submodel.task, "system": submodel.system_code},
        request=request,
    )
    # The counts are read before the commit: the tenant context is
    # transaction-local, so a read after it would see no rows.
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@router.get("", response_model=list[SubmodelRead], summary="List submodels")
async def index(
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelRead]:
    """Every submodel in this organization."""
    rows = await list_submodels(session, org_id=context.org_id)
    return [await _submodel_read(session, row) for row in rows]


@router.get("/{submodel_id}", response_model=SubmodelRead, summary="Read a submodel")
async def show(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """One submodel."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    return await _submodel_read(session, submodel)


@router.patch(
    "/{submodel_id}",
    response_model=SubmodelRead,
    summary="Change a submodel",
)
async def update(
    submodel_id: uuid.UUID,
    payload: SubmodelUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Rename a submodel or change what it is allowed to do."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None:
            continue
        if key == "permissions":
            from app.services.submodels import normalise_permissions

            value = normalise_permissions(value)
        setattr(submodel, key, value)
    await audit.record(
        session,
        action="submodel.updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"fields": sorted(changes)},
        request=request,
    )
    # The change is written before the refresh: a refresh reloads the stored
    # row, so an unflushed change would be lost.
    await session.flush()
    await session.refresh(submodel)
    await session.commit()
    return await _submodel_read(session, submodel)


@router.delete(
    "/{submodel_id}",
    response_model=SubmodelRead,
    summary="Retire a submodel",
)
async def retire(
    submodel_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Retire a submodel.

    The row is kept: it is part of the audit trail and of what the client
    published. A retired submodel is not listed as usable.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    submodel.status = SubmodelStatus.retired.value
    await audit.record(
        session,
        action="submodel.retired",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        request=request,
    )
    # The change is written before the refresh: a refresh reloads the stored
    # row, so an unflushed change would be lost.
    await session.flush()
    await session.refresh(submodel)
    await session.commit()
    return await _submodel_read(session, submodel)


# ---------------------------------------------------------------------------
# Training material
# ---------------------------------------------------------------------------


@router.get(
    "/{submodel_id}/materials",
    response_model=list[SubmodelMaterialRead],
    summary="List the training material",
)
async def list_materials(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelMaterialRead]:
    """Everything the submodel learns from."""
    await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    rows = await session.scalars(
        select(SubmodelMaterial)
        .where(
            SubmodelMaterial.org_id == context.org_id,
            SubmodelMaterial.submodel_id == submodel_id,
        )
        .order_by(SubmodelMaterial.created_at.asc())
    )
    return [SubmodelMaterialRead.model_validate(row) for row in rows]


@router.post(
    "/{submodel_id}/materials/notes",
    response_model=SubmodelMaterialRead,
    status_code=201,
    summary="Add a written procedure",
)
async def add_note(
    submodel_id: uuid.UUID,
    payload: SubmodelMaterialCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelMaterialRead:
    """The client's own written steps."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    material = SubmodelMaterial(
        org_id=context.org_id,
        submodel_id=submodel.id,
        kind=SubmodelMaterialKind.note.value,
        title=payload.title.strip(),
        note_text=payload.text.strip(),
        size_bytes=len(payload.text.strip().encode("utf-8")),
        status="ready",
        created_by=context.user.id,
    )
    session.add(material)
    await session.flush()
    await audit.record(
        session,
        action="submodel.material_added",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"material_id": str(material.id), "kind": material.kind},
        request=request,
    )
    await session.refresh(material)
    await session.commit()
    return SubmodelMaterialRead.model_validate(material)


@router.post(
    "/{submodel_id}/materials/upload",
    response_model=SubmodelMaterialRead,
    status_code=201,
    summary="Upload a training video or an audio instruction",
)
async def upload_material(
    submodel_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    title: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> SubmodelMaterialRead:
    """Store a recording or an audio instruction as training material.

    The file is stored even when this deployment cannot read it yet; the
    material then says exactly what is missing instead of pretending it was
    analysed.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    mime_type = (file.content_type or "").split(";")[0].strip().lower()
    if mime_type not in VIDEO_MIME_TYPES and mime_type not in AUDIO_MIME_TYPES:
        raise AppError(
            "Upload a training video or an audio instruction. Manuals and "
            "screenshots are uploaded as documents.",
            status_code=422,
            code="invalid_material_type",
        )
    raw = await file.read()
    if not raw:
        raise AppError(
            "The uploaded file is empty.", status_code=422, code="material_empty"
        )
    if len(raw) > MAX_MATERIAL_BYTES:
        raise AppError(
            "That recording is too large to upload.",
            status_code=413,
            code="material_too_large",
        )

    key = _material_key(context.org_id, submodel.id)
    await storage.save(key, raw)
    material = SubmodelMaterial(
        org_id=context.org_id,
        submodel_id=submodel.id,
        kind=(
            SubmodelMaterialKind.video.value
            if mime_type in VIDEO_MIME_TYPES
            else SubmodelMaterialKind.audio.value
        ),
        title=(title or file.filename or "Recording").strip()[:200],
        filename=(file.filename or "")[:255] or None,
        mime_type=mime_type,
        size_bytes=len(raw),
        storage_key=key,
        sha256=hashlib.sha256(raw).hexdigest(),
        status="stored",
        created_by=context.user.id,
    )
    session.add(material)
    await session.flush()
    await audit.record(
        session,
        action="submodel.material_uploaded",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"material_id": str(material.id), "kind": material.kind},
        request=request,
    )
    await session.refresh(material)
    await session.commit()
    return SubmodelMaterialRead.model_validate(material)


def _material_key(org_id: uuid.UUID, submodel_id: uuid.UUID) -> str:
    return f"{org_id}/submodels/{submodel_id}/{uuid.uuid4().hex}"


@router.post(
    "/{submodel_id}/materials/{material_id}/read",
    response_model=SubmodelMaterialRead,
    summary="Read a recording into a sequence of screens",
)
async def read_material(
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> SubmodelMaterialRead:
    """Turn a recording into the screens it shows and what changed between them.

    This is the step that makes a recording teachable: the material becomes
    a **timeline** of screens and changes, not a pile of pictures.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    material = await _owned_material(
        session,
        org_id=context.org_id,
        submodel_id=submodel.id,
        material_id=material_id,
    )
    if material.kind == SubmodelMaterialKind.note.value:
        raise AppError(
            "Written procedures do not need to be read.",
            status_code=409,
            code="material_not_readable",
        )
    if not material.storage_key:
        raise AppError(
            "This material has no stored file.",
            status_code=409,
            code="material_not_readable",
        )

    material.status = "extracting"
    material.error_message = None
    await session.flush()

    raw = await storage.read(material.storage_key)
    try:
        if material.kind == SubmodelMaterialKind.video.value:
            analysis = await read_recording(
                raw, mime_type=material.mime_type or ""
            )
            text = render_timeline(analysis)
            units = len(analysis.states)
        else:
            transcriber = build_audio_transcriber()
            if transcriber is None:
                raise MediaCapabilityUnavailable(
                    "Transcribing audio needs a transcription model on this "
                    "deployment. Upload the written instructions instead."
                )
            text = await transcriber.transcribe(
                raw, filename=material.filename or "instruction.mp3"
            )
            units = len(text.split())
    except MediaCapabilityUnavailable as exc:
        material.status = "failed"
        material.error_message = str(exc)[:1000]
        await session.commit()
        raise AppError(
            str(exc),
            status_code=503,
            code="media_capability_unavailable",
        ) from exc

    material.extracted_text = text
    material.extracted_units = units
    material.status = "ready"
    await session.flush()
    await audit.record(
        session,
        action="submodel.material_read",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"material_id": str(material.id), "units": units},
        request=request,
    )
    await session.refresh(material)
    await session.commit()
    return SubmodelMaterialRead.model_validate(material)


@router.delete(
    "/{submodel_id}/materials/{material_id}",
    response_model=SubmodelMaterialRead,
    summary="Archive a training material",
)
async def archive_material(
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelMaterialRead:
    """Archive a material. The row is kept for the audit trail."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    material = await _owned_material(
        session,
        org_id=context.org_id,
        submodel_id=submodel.id,
        material_id=material_id,
    )
    material.status = "archived"
    await audit.record(
        session,
        action="submodel.material_archived",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"material_id": str(material.id)},
        request=request,
    )
    # The change is written before the refresh: a refresh reloads the stored
    # row, so an unflushed change would be lost.
    await session.flush()
    await session.refresh(material)
    await session.commit()
    return SubmodelMaterialRead.model_validate(material)


# ---------------------------------------------------------------------------
# Training, workflows and tests
# ---------------------------------------------------------------------------


@router.post(
    "/{submodel_id}/train",
    response_model=SubmodelTrainingResult,
    summary="Train the submodel on its material",
)
async def train(
    submodel_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelTrainingResult:
    """Learn the workflows the material shows.

    The result is a draft: a human approves each workflow before the submodel
    may use it.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    submodel.status = SubmodelStatus.training.value
    await session.flush()
    try:
        workflows, materials, characters = await train_submodel(
            session,
            org_id=context.org_id,
            submodel=submodel,
            ai=ai,
            actor_user_id=context.user.id,
            actor_role=context.role,
        )
    except AppError:
        submodel.status = SubmodelStatus.draft.value
        await session.commit()
        raise
    # The workflows are read before the commit: the tenant context is
    # transaction-local, so a read after it would see no rows.
    result = SubmodelTrainingResult(
        submodel_id=submodel.id,
        training_status=submodel.training_status,
        workflows=[SubmodelWorkflowRead.model_validate(item) for item in workflows],
        analysed_materials=materials,
        analysed_characters=characters,
        message=(
            f"The AI read {materials} piece(s) of material and wrote "
            f"{len(workflows)} workflow(s). Approve the ones that are right."
        ),
    )
    await session.commit()
    return result


@router.get(
    "/{submodel_id}/workflows",
    response_model=list[SubmodelWorkflowRead],
    summary="List the extracted workflows",
)
async def list_workflows(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelWorkflowRead]:
    """Every workflow the AI extracted."""
    await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    rows = await session.scalars(
        select(SubmodelProcedure)
        .where(
            SubmodelProcedure.org_id == context.org_id,
            SubmodelProcedure.submodel_id == submodel_id,
        )
        .order_by(SubmodelProcedure.position.asc())
    )
    return [SubmodelWorkflowRead.model_validate(row) for row in rows]


@router.patch(
    "/{submodel_id}/workflows/{workflow_id}",
    response_model=SubmodelWorkflowRead,
    summary="Approve or reject a workflow",
)
async def update_workflow(
    submodel_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: SubmodelWorkflowUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelWorkflowRead:
    """Approve a workflow so the submodel may use it, or reject it."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    workflow = await session.scalar(
        select(SubmodelProcedure).where(
            SubmodelProcedure.id == workflow_id,
            SubmodelProcedure.org_id == context.org_id,
            SubmodelProcedure.submodel_id == submodel.id,
        )
    )
    if workflow is None:
        raise AppError(
            "Workflow not found.", status_code=404, code="workflow_not_found"
        )
    if payload.title is not None:
        workflow.title = payload.title.strip()
    if payload.summary is not None:
        workflow.summary = payload.summary.strip() or None
    if payload.steps is not None:
        workflow.steps = [step.model_dump() for step in payload.steps]
    workflow.status = payload.status
    # The change is written before the refresh: a refresh reloads the stored
    # row, so an unflushed change would be lost.
    await session.flush()
    await audit.record(
        session,
        action="submodel.workflow_updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"workflow_id": str(workflow.id), "status": workflow.status},
        request=request,
    )
    await session.refresh(workflow)
    await session.commit()
    return SubmodelWorkflowRead.model_validate(workflow)


@router.post(
    "/{submodel_id}/tests",
    response_model=SubmodelTestRead,
    status_code=201,
    summary="Test the submodel",
)
async def create_test(
    submodel_id: uuid.UUID,
    payload: SubmodelTestCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelTestRead:
    """Ask the submodel a question and check it knows the answer."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    test = await run_submodel_test(
        session,
        org_id=context.org_id,
        submodel=submodel,
        question=payload.question.strip(),
        expected_points=payload.expected_points,
        ai=ai,
        actor_user_id=context.user.id,
        actor_role=context.role,
    )
    await session.refresh(test)
    await session.commit()
    return SubmodelTestRead.model_validate(test)


@router.get(
    "/{submodel_id}/tests",
    response_model=list[SubmodelTestRead],
    summary="List the test results",
)
async def list_tests(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelTestRead]:
    """Every test this submodel was put through."""
    await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    rows = await session.scalars(
        select(SubmodelTest)
        .where(
            SubmodelTest.org_id == context.org_id,
            SubmodelTest.submodel_id == submodel_id,
        )
        .order_by(SubmodelTest.created_at.desc())
    )
    return [SubmodelTestRead.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Export and install
# ---------------------------------------------------------------------------


@router.get(
    "/{submodel_id}/package",
    response_model=SubmodelPackageInfo,
    summary="The package for this submodel",
)
async def package_info(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelPackageInfo:
    """The signed package, with the workflows it carries."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    workflows = await approved_procedures(
        session, org_id=context.org_id, submodel_id=submodel.id
    )
    payload = build_package(submodel, workflows)
    from app.services.extension_signing import canonical_json

    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return SubmodelPackageInfo(
        filename=f"{submodel.slug}-v{submodel.version}.wmsubmodel.json",
        digest=digest,
        signing_key_id=submodel.signing_key_id or "",
        verified=submodel.package_signature is not None,
        reason=(
            ""
            if submodel.package_signature
            else "This submodel has not been exported yet."
        ),
        workflow_count=len(workflows),
        payload=payload,
    )


@router.get(
    "/{submodel_id}/download",
    summary="Download the submodel package",
    response_class=PlainTextResponse,
)
async def download(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PlainTextResponse:
    """Download the signed package.

    The file carries the workflows, the permission policy and the signature,
    so it can be installed on another machine and used there on its own.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    package = await build_package_document(
        session, org_id=context.org_id, submodel=submodel
    )
    await audit.record(
        session,
        action="submodel.exported",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={
            "digest": package.digest,
            "workflows": len(package.payload.get("workflows") or []),
        },
    )
    await session.commit()
    return PlainTextResponse(
        package.to_json(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{package.filename}"'
        },
    )


@router.post(
    "/import",
    response_model=SubmodelRead,
    status_code=201,
    summary="Install a submodel package",
)
async def install(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    label: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Install a submodel that was trained elsewhere.

    The workflows travel with the package, so the installed copy works here
    on its own: it does not need the training material again.

    ``label`` says where the package came from, so an administrator can see
    the provenance later.
    """
    await quota.guard_submodel_create(session, context.org_id)
    raw = await file.read()
    document = decode_package(raw)
    verified = verify_package_document(document)
    if not verified.verified:
        raise AppError(
            verified.reason
            or "This package could not be verified, so it was not installed.",
            status_code=409,
            code="submodel_package_unverified",
        )
    submodel = await install_submodel(
        session,
        org_id=context.org_id,
        verified=verified,
        created_by=context.user.id,
        source_label=label,
    )
    await audit.record(
        session,
        action="submodel.installed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={
            "digest": verified.digest,
            "signing_key_id": verified.signing_key_id,
            "workflows": len(verified.payload.get("workflows") or []),
        },
        request=request,
    )
    # The counts are read before the commit: the tenant context is
    # transaction-local, so a read after it would see no rows.
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


# ---------------------------------------------------------------------------
# Using a submodel
# ---------------------------------------------------------------------------


@router.post(
    "/{submodel_id}/guidance",
    response_model=SubmodelGuidanceResponse,
    summary="Ask the submodel a question",
)
async def guidance(
    submodel_id: uuid.UUID,
    payload: SubmodelGuidanceRequest,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelGuidanceResponse:
    """Answer from the workflows the submodel learned.

    This works on an installed copy: the workflows travelled with the
    package, so nothing else is needed.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    answer = await answer_with_submodel(
        session,
        org_id=context.org_id,
        submodel=submodel,
        question=payload.question,
        ai=ai,
        context=payload.context,
    )
    await session.commit()
    return SubmodelGuidanceResponse(
        submodel_id=submodel.id,
        task=submodel.task,
        answer=answer.answer,
        steps=answer.steps,
        grounded=answer.grounded,
        workflows_used=answer.workflows_used,
        model=answer.model,
        usage={
            "prompt_tokens": answer.prompt_tokens,
            "completion_tokens": answer.completion_tokens,
        },
    )


@router.post(
    "/{submodel_id}/actions",
    response_model=SubmodelActionDecision,
    status_code=201,
    summary="Ask whether an action may be performed",
)
async def propose_action(
    submodel_id: uuid.UUID,
    payload: SubmodelActionPlanCreate,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelActionDecision:
    """Ask the server whether the agent may do this.

    The submodel's own permission policy decides. A sensitive action always
    needs the user's confirmation, and the plan records who confirmed what.
    """
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    steps = [step.model_dump() for step in payload.steps]
    plan, allowed, requires_confirmation, reason = await record_action(
        session,
        org_id=context.org_id,
        submodel=submodel,
        action_type=payload.action_type,
        description=payload.description,
        steps=steps,
    )
    await audit.record(
        session,
        action="submodel.action_proposed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={
            "plan_id": str(plan.id),
            "action_type": plan.action_type,
            "allowed": allowed,
            "requires_confirmation": requires_confirmation,
        },
        request=request,
    )
    await session.commit()
    return SubmodelActionDecision(
        plan=SubmodelActionPlanRead.model_validate(plan),
        allowed=allowed,
        requires_confirmation=requires_confirmation,
        reason=reason,
    )


@router.post(
    "/{submodel_id}/actions/{plan_id}/confirm",
    response_model=SubmodelActionPlanRead,
    summary="Record the user's confirmation",
)
async def confirm_action(
    submodel_id: uuid.UUID,
    plan_id: uuid.UUID,
    payload: SubmodelActionConfirm,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelActionPlanRead:
    """Record the user's answer about a sensitive action."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        submodel_id=submodel.id,
        plan_id=plan_id,
    )
    if plan.status == SubmodelActionStatus.executed.value:
        raise AppError(
            "This action has already been performed.",
            status_code=409,
            code="action_already_executed",
        )
    if not payload.confirmed:
        plan.status = SubmodelActionStatus.rejected.value
    else:
        if not plan.requires_confirmation:
            raise AppError(
                "This action does not need a confirmation.",
                status_code=409,
                code="action_confirmation_not_required",
            )
        plan.status = SubmodelActionStatus.confirmed.value
        plan.confirmed_by = context.user.id
        plan.confirmed_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        action="submodel.action_confirmed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.refresh(plan)
    await session.commit()
    return SubmodelActionPlanRead.model_validate(plan)


@router.post(
    "/{submodel_id}/actions/{plan_id}/complete",
    response_model=SubmodelActionPlanRead,
    summary="Report that the action ran",
)
async def complete_action(
    submodel_id: uuid.UUID,
    plan_id: uuid.UUID,
    payload: SubmodelActionComplete,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelActionPlanRead:
    """Close the action plan with what happened."""
    submodel = await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        submodel_id=submodel.id,
        plan_id=plan_id,
    )
    if plan.status == SubmodelActionStatus.rejected.value:
        raise AppError(
            "This action was refused, so it was never performed.",
            status_code=409,
            code="action_not_allowed",
        )
    if plan.requires_confirmation and plan.status != (
        SubmodelActionStatus.confirmed.value
    ):
        raise AppError(
            "The user has to confirm this action before it runs.",
            status_code=409,
            code="action_confirmation_required",
        )
    plan.status = (
        SubmodelActionStatus.executed.value
        if payload.succeeded
        else SubmodelActionStatus.rejected.value
    )
    plan.result = dict(payload.result)
    await session.flush()
    await audit.record(
        session,
        action="submodel.action_completed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.refresh(plan)
    await session.commit()
    return SubmodelActionPlanRead.model_validate(plan)


@router.get(
    "/{submodel_id}/actions",
    response_model=list[SubmodelActionPlanRead],
    summary="List the action plans",
)
async def list_actions(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelActionPlanRead]:
    """Every action this submodel was asked to perform."""
    await _owned_submodel(
        session, org_id=context.org_id, submodel_id=submodel_id
    )
    rows = await session.scalars(
        select(SubmodelActionPlan)
        .where(
            SubmodelActionPlan.org_id == context.org_id,
            SubmodelActionPlan.submodel_id == submodel_id,
        )
        .order_by(SubmodelActionPlan.created_at.desc())
    )
    return [SubmodelActionPlanRead.model_validate(row) for row in rows]


async def _owned_plan(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    submodel_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> SubmodelActionPlan:
    plan = await session.scalar(
        select(SubmodelActionPlan).where(
            SubmodelActionPlan.id == plan_id,
            SubmodelActionPlan.org_id == org_id,
            SubmodelActionPlan.submodel_id == submodel_id,
        )
    )
    if plan is None:
        raise AppError(
            "Action plan not found.", status_code=404, code="not_found"
        )
    return plan
