"""WorkMeta AI Submodels: the administrator trains them, clients use them.

The platform is split in two routers, and the split *is* the permission
model:

``/platform/submodels`` — **platform administrator only**
    Create, upload demonstration videos, analyse, extract workflows, train,
    test, publish, unpublish, retire, download and install. Every route
    depends on ``require_platform_admin``, so a client admin or a member who
    guesses an id gets a ``403`` from the backend regardless of what the
    frontend shows.

``/submodels`` — **signed-in members**
    List and read *published* submodels and ask one for guidance, plus the
    propose → confirm → complete action flow that records who authorised a
    sensitive action on their machine.

A submodel that the administrator has not published does not exist on the
second router: ``get_submodel(..., published=True)`` refuses it, so an
unpublished model, its training videos, its draft workflows and its test
questions never leave the administrator's side.

The lifecycle is: **create → upload → analyse → train → test → publish**, and
on another machine **install → use**. Publishing requires an approved
workflow and a test that really passed, and training reports ``running`` →
``extracted`` or ``failed`` with the reason; nothing is ever shown as trained
before a run has completed.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Request,
    UploadFile,
)
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    OrgContext,
    require_org_member,
    require_platform_admin,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.rls import set_rls_context
from app.db.session import SessionFactory, get_session
from app.models.enums import (
    PLATFORM_OWNER_ROLE,
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
from app.services import audit
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
    count_submodels,
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

#: The administrator's router. Everything that creates, trains, exports or
#: publishes a submodel hangs off this prefix and requires a platform admin.
admin_router = APIRouter(
    prefix="/platform/submodels", tags=["submodels-admin"]
)

#: The client's router: published submodels only, and only to use them.
router = APIRouter(prefix="/submodels", tags=["submodels"])

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _admin_material(
    session: AsyncSession,
    *,
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
) -> SubmodelMaterial:
    material = await session.scalar(
        select(SubmodelMaterial).where(
            SubmodelMaterial.id == material_id,
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
        await submodel_counts(session, submodel_id=submodel.id)
    ).items():
        setattr(read, key, value)
    return read


async def _admin_action(
    session: AsyncSession,
    *,
    auth: AuthContext,
    request: Request,
    submodel: Submodel,
    action: str,
    metadata: dict[str, object],
) -> None:
    """Record an administrator's action as a platform-level audit event."""
    await audit.record(
        session,
        action=action,
        # Platform-level: the submodel belongs to the administrator, not to
        # a tenant, so the row carries no organization.
        org_id=None,
        actor_user_id=auth.user.id,
        actor_role=PLATFORM_OWNER_ROLE,
        resource_type="submodel",
        resource_id=str(submodel.id),
        metadata=metadata,
        request=request,
    )


# ===========================================================================
# Platform administration — administrator only
# ===========================================================================


@admin_router.get(
    "/capabilities",
    response_model=dict,
    summary="What this deployment can do with training media",
)
async def capabilities(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """The training limits of this deployment, and whether it can sign.

    Registered before ``/{submodel_id}`` on purpose: a literal word in a path
    must never be read as an identifier.
    """
    settings = get_settings()
    return {
        "video_frames": settings.media_video_max_frames,
        "frame_interval_seconds": settings.media_video_frame_interval_seconds,
        "analysis_max_characters": settings.analysis_max_characters,
        "max_media_upload_bytes": settings.max_media_upload_bytes,
        "signing_configured": bool(settings.extension_signing_private_key),
        "transcription_configured": bool(settings.ai_transcription_model),
        "submodels_count": await count_submodels(session),
    }


@admin_router.post(
    "",
    response_model=SubmodelRead,
    status_code=201,
    summary="Create a submodel (administrator only)",
)
async def create(
    payload: SubmodelCreate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Start a submodel for one task, such as issuing an EBM receipt."""
    submodel = await create_submodel(
        session,
        name=payload.name,
        task=payload.task,
        system_code=payload.system_code,
        system_name=payload.system_name,
        system_version=payload.system_version,
        description=payload.description,
        target_kind=payload.target_kind,
        permissions=payload.permissions,
        capabilities=payload.capabilities,
        created_by=auth.user.id,
    )
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.created",
        metadata={"task": submodel.task, "system": submodel.system_code},
    )
    # The counts are read before the commit: the tenant context is
    # transaction-local, so a read after it would see no rows.
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@admin_router.get(
    "", response_model=list[SubmodelRead], summary="List every submodel"
)
async def index(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelRead]:
    """Every submodel on the platform, published or not."""
    rows = await list_submodels(session)
    return [await _submodel_read(session, row) for row in rows]


@admin_router.get(
    "/{submodel_id}", response_model=SubmodelRead, summary="Read a submodel"
)
async def show(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """One submodel, with its training status and its error if it failed."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    return await _submodel_read(session, submodel)


@admin_router.patch(
    "/{submodel_id}",
    response_model=SubmodelRead,
    summary="Change a submodel (administrator only)",
)
async def update(
    submodel_id: uuid.UUID,
    payload: SubmodelUpdate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Rename a submodel or change what it is allowed to do."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None:
            continue
        if key == "permissions":
            from app.services.submodels import normalise_permissions

            value = normalise_permissions(value)
        setattr(submodel, key, value)
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.updated",
        metadata={"fields": sorted(changes)},
    )
    # The change is written before the refresh: a refresh reloads the stored
    # row, so an unflushed change would be lost.
    await session.flush()
    await session.refresh(submodel)
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@admin_router.post(
    "/import",
    response_model=SubmodelRead,
    status_code=201,
    summary="Install a submodel package (administrator only)",
)
async def install(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    label: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Install a submodel that was trained on another computer.

    The workflows travel with the package, so the installed copy works here
    on its own: it does not need the training material again. ``label`` says
    where the package came from, so the provenance is visible later.
    """
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
        verified=verified,
        created_by=auth.user.id,
        source_label=label,
    )
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.installed",
        metadata={
            "digest": verified.digest,
            "signing_key_id": verified.signing_key_id,
            "workflows": len(verified.payload.get("workflows") or []),
        },
    )
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@admin_router.delete(
    "/{submodel_id}",
    response_model=SubmodelRead,
    summary="Retire a submodel (administrator only)",
)
async def retire(
    submodel_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Retire a submodel.

    The row is kept: it is part of the audit trail. A retired submodel stops
    being listed and stops answering, immediately.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    submodel.status = SubmodelStatus.retired.value
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.retired",
        metadata={"was_published": submodel.published_at is not None},
    )
    await session.flush()
    await session.refresh(submodel)
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@admin_router.post(
    "/{submodel_id}/publish",
    response_model=SubmodelRead,
    summary="Publish a submodel to clients (administrator only)",
)
async def publish(
    submodel_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Make a trained submodel available to authorized clients.

    Publishing is an approval, so it is refused unless the submodel has been
    trained, has at least one **approved** workflow and has **passed** a
    test. Publishing again after changes raises the version, so a package
    that was downloaded earlier is distinguishable from the new one.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    if submodel.status == SubmodelStatus.retired.value:
        raise AppError(
            "A retired submodel cannot be published. Restore it first.",
            status_code=409,
            code="submodel_retired",
        )
    if submodel.training_status == "running" or (
        submodel.status == SubmodelStatus.training.value
    ):
        raise AppError(
            "Training is still running. Wait for it to finish, then publish.",
            status_code=409,
            code="submodel_training_running",
        )
    if submodel.origin == "trained" and submodel.training_status != "extracted":
        raise AppError(
            "This submodel has not finished training, so it cannot be "
            "published. Train it first.",
            status_code=409,
            code="submodel_not_trained",
        )
    workflows = await approved_procedures(session, submodel_id=submodel.id)
    if not workflows:
        raise AppError(
            "Approve at least one workflow before publishing this submodel.",
            status_code=409,
            code="submodel_no_workflows",
        )
    passed = int(
        await session.scalar(
            select(func.count(SubmodelTest.id)).where(
                SubmodelTest.submodel_id == submodel.id,
                SubmodelTest.outcome == "passed",
            )
        )
        or 0
    )
    if passed == 0:
        raise AppError(
            "This submodel has not passed a test yet, so it cannot be "
            "published. Test it first.",
            status_code=409,
            code="submodel_needs_a_passing_test",
        )

    republished = submodel.published_at is not None
    if republished:
        submodel.version = int(submodel.version or 1) + 1
    submodel.status = SubmodelStatus.published.value
    submodel.published_at = datetime.now(UTC)

    # Freeze what was approved: the package the administrator publishes is
    # stored with the submodel, so "published" has a concrete content.
    package = await build_package_document(session, submodel=submodel)
    submodel.package_manifest = package.payload
    submodel.package_signature = package.signature or None
    submodel.signing_key_id = package.signing_key_id or None

    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.published",
        metadata={
            "version": submodel.version,
            "workflows": len(package.payload.get("workflows") or []),
            "digest": package.digest,
        },
    )
    await session.flush()
    await session.refresh(submodel)
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


@admin_router.post(
    "/{submodel_id}/unpublish",
    response_model=SubmodelRead,
    summary="Unpublish a submodel (administrator only)",
)
async def unpublish(
    submodel_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """Take a submodel away from clients.

    Clients stop seeing it the moment this returns: the list and every use
    of it check the published state.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    if submodel.status != SubmodelStatus.published.value:
        raise AppError(
            "This submodel is not published.",
            status_code=409,
            code="submodel_not_published",
        )
    submodel.status = SubmodelStatus.trained.value
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.unpublished",
        metadata={"version": submodel.version},
    )
    await session.flush()
    await session.refresh(submodel)
    read = await _submodel_read(session, submodel)
    await session.commit()
    return read


# ---------------------------------------------------------------------------
# Training material — administrator only
# ---------------------------------------------------------------------------


@admin_router.get(
    "/{submodel_id}/materials",
    response_model=list[SubmodelMaterialRead],
    summary="List the training material (administrator only)",
)
async def list_materials(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelMaterialRead]:
    """Everything the submodel learns from, including its analysis state."""
    await get_submodel(session, submodel_id=submodel_id)
    rows = await session.scalars(
        select(SubmodelMaterial)
        .where(SubmodelMaterial.submodel_id == submodel_id)
        .order_by(SubmodelMaterial.created_at.asc())
    )
    return [SubmodelMaterialRead.model_validate(row) for row in rows]


@admin_router.get(
    "/{submodel_id}/materials/{material_id}",
    response_model=SubmodelMaterialRead,
    summary="One piece of training material (administrator only)",
)
async def get_material(
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelMaterialRead:
    """Follow a recording while it is analysed (status ``extracting``)."""
    await get_submodel(session, submodel_id=submodel_id)
    material = await _admin_material(
        session, submodel_id=submodel_id, material_id=material_id
    )
    return SubmodelMaterialRead.model_validate(material)


@admin_router.post(
    "/{submodel_id}/materials/notes",
    response_model=SubmodelMaterialRead,
    status_code=201,
    summary="Add a written procedure (administrator only)",
)
async def add_note(
    submodel_id: uuid.UUID,
    payload: SubmodelMaterialCreate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelMaterialRead:
    """The written steps of the task, typed in directly."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    material = SubmodelMaterial(
        submodel_id=submodel.id,
        kind=SubmodelMaterialKind.note.value,
        title=payload.title.strip(),
        note_text=payload.text.strip(),
        size_bytes=len(payload.text.strip().encode("utf-8")),
        status="ready",
        created_by=auth.user.id,
    )
    session.add(material)
    await session.flush()
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.material_added",
        metadata={"material_id": str(material.id), "kind": material.kind},
    )
    await session.refresh(material)
    read = SubmodelMaterialRead.model_validate(material)
    await session.commit()
    return read

@admin_router.post(
    "/{submodel_id}/materials/upload",
    response_model=SubmodelMaterialRead,
    status_code=201,
    summary="Upload a demonstration video (administrator only)",
)
async def upload_material(
    submodel_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    title: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> SubmodelMaterialRead:
    """Upload a demonstration video or an audio instruction.

    The recording is analysed right after the response is sent: the frames
    are decoded and the text on them is read, and the material becomes the
    timeline of screens and changes the training reads. The analysis runs in
    the background — the response is the material still being analysed, and
    the list of materials follows it until it says ``ready``.

    A deployment that cannot read the recording says so on the material and
    the administrator can try again; the file is never lost.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    mime_type = (file.content_type or "").split(";")[0].strip().lower()
    if mime_type not in VIDEO_MIME_TYPES and mime_type not in AUDIO_MIME_TYPES:
        raise AppError(
            "Upload a demonstration video or an audio instruction.",
            status_code=422,
            code="invalid_material_type",
        )
    limit = get_settings().max_media_upload_bytes
    raw = await file.read(limit + 1)
    if not raw:
        raise AppError(
            "The uploaded file is empty.", status_code=422, code="material_empty"
        )
    if len(raw) > limit:
        raise AppError(
            f"That recording is larger than the {limit // (1024 * 1024)} MB limit.",
            status_code=413,
            code="material_too_large",
        )

    key = _material_key(submodel.id)
    await storage.save(key, raw)
    material = SubmodelMaterial(
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
        status="extracting",
        created_by=auth.user.id,
    )
    session.add(material)
    await session.flush()
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.material_uploaded",
        metadata={"material_id": str(material.id), "kind": material.kind},
    )

    # The analysis runs after the response: it decodes frames and reads text
    # on each, which takes a while on a small machine. A failure is reported
    # on the material, not as a lost upload.
    await session.refresh(material)
    read = SubmodelMaterialRead.model_validate(material)
    await session.commit()
    background_tasks.add_task(
        analyze_material_in_background,
        material_id=material.id,
        actor_user_id=auth.user.id,
        storage=storage,
    )
    return read


def _material_key(submodel_id: uuid.UUID) -> str:
    # Platform-owned material: no tenant prefix, an unpredictable key.
    return f"platform/submodels/{submodel_id}/{uuid.uuid4().hex}"


async def _analyze_into_timeline(
    storage: StorageProvider,
    material: SubmodelMaterial,
) -> tuple[str, int]:
    """Read a recording into the screens it shows and what changed between them.

    Returns the text and how many screens it found. A deployment that cannot
    read the recording raises ``MediaCapabilityUnavailable``, which the
    caller turns into a material that says exactly what is missing.
    """
    if not material.storage_key:
        raise MediaCapabilityUnavailable("This material has no stored file.")
    raw = await storage.read(material.storage_key)
    if material.kind == SubmodelMaterialKind.video.value:
        analysis = await read_recording(raw, mime_type=material.mime_type or "")
        return render_timeline(analysis), len(analysis.states)
    transcriber = build_audio_transcriber()
    if transcriber is None:
        raise MediaCapabilityUnavailable(
            "Transcribing audio needs a transcription model on this "
            "deployment. Add the written instructions instead."
        )
    text = await transcriber.transcribe(
        raw, filename=material.filename or "instruction.mp3"
    )
    return text, len(text.split())


async def analyze_material_in_background(
    *,
    material_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    storage: StorageProvider,
) -> None:
    """Analyse an uploaded recording after the response has been sent.

    This owns its database session: the request's session is closed by the
    time a background task runs. The administrator's user id travels with the
    task so the row-level security context of the new session still resolves
    to a platform administrator. The result — the timeline, or the reason the
    analysis failed — is written on the material either way.
    """
    session = SessionFactory()
    try:
        await set_rls_context(session, org_id=None, user_id=actor_user_id)
        material = await session.get(SubmodelMaterial, material_id)
        if material is None or material.status == "archived":
            return
        try:
            text, units = await _analyze_into_timeline(storage, material)
        except MediaCapabilityUnavailable as exc:
            material.status = "failed"
            material.error_message = str(exc)[:1000]
        except Exception as exc:  # noqa: BLE001 - the material says what broke
            material.status = "failed"
            material.error_message = (
                f"The recording could not be analysed: {exc}"
            )[:1000]
            logger.warning(
                "material_analysis_failed",
                material_id=str(material_id),
                error=str(exc)[:400],
            )
        else:
            material.extracted_text = text
            material.extracted_units = units
            material.status = "ready"
        await audit.record(
            session,
            action="submodel.material_analysed",
            org_id=None,
            actor_user_id=actor_user_id,
            actor_role=PLATFORM_OWNER_ROLE,
            resource_type="submodel",
            resource_id=str(material.submodel_id),
            metadata={
                "material_id": str(material_id),
                "units": material.extracted_units or 0,
                "status": material.status,
            },
        )
        await session.commit()
    except Exception:
        logger.error("material_analysis_crashed", material_id=str(material_id))
        await session.rollback()
    finally:
        await session.close()


@admin_router.post(
    "/{submodel_id}/materials/{material_id}/analyze",
    response_model=SubmodelMaterialRead,
    status_code=202,
    summary="Analyse a recording into screens and changes (administrator only)",
)
async def analyze_material(
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> SubmodelMaterialRead:
    """Turn a recording into the screens it shows and what changed between them.

    This is the step that makes a demonstration teachable: the material
    becomes a **timeline** of screens and changes — the buttons, forms and
    menus it shows — rather than a pile of pictures. The analysis runs in the
    background; the answer is the material marked ``extracting``, and the
    console follows it until it is ready or says why it failed.
    """
    await get_submodel(session, submodel_id=submodel_id)
    material = await _admin_material(
        session, submodel_id=submodel_id, material_id=material_id
    )
    if material.kind == SubmodelMaterialKind.note.value:
        raise AppError(
            "Written procedures do not need to be analysed.",
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
    await session.refresh(material)
    read = SubmodelMaterialRead.model_validate(material)
    await session.commit()
    background_tasks.add_task(
        analyze_material_in_background,
        material_id=material.id,
        actor_user_id=auth.user.id,
        storage=storage,
    )
    return read


@admin_router.delete(
    "/{submodel_id}/materials/{material_id}",
    response_model=SubmodelMaterialRead,
    summary="Archive a training material (administrator only)",
)
async def archive_material(
    submodel_id: uuid.UUID,
    material_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelMaterialRead:
    """Archive a material. The row is kept for the audit trail."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    material = await _admin_material(
        session, submodel_id=submodel_id, material_id=material_id
    )
    material.status = "archived"
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.material_archived",
        metadata={"material_id": str(material.id)},
    )
    await session.flush()
    await session.refresh(material)
    read = SubmodelMaterialRead.model_validate(material)
    await session.commit()
    return read

# ---------------------------------------------------------------------------
# Training, workflows and tests — administrator only
# ---------------------------------------------------------------------------


@admin_router.post(
    "/{submodel_id}/train",
    response_model=SubmodelTrainingResult,
    summary="Train the submodel on its material (administrator only)",
)
async def train(
    submodel_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelTrainingResult:
    """Learn the workflows the material shows.

    The run is real: the material is read, the model is called, and the
    workflows it writes are stored as drafts. A human approves each workflow
    before the submodel may use it. While the model works the submodel says
    ``training_status: running``; a failure says ``failed`` and keeps the
    reason, so a submodel is never shown as trained when it is not.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    if submodel.training_status == "running":
        raise AppError(
            "Training is already running for this submodel.",
            status_code=409,
            code="submodel_training_running",
        )
    reading = await session.scalar(
        select(SubmodelMaterial.id).where(
            SubmodelMaterial.submodel_id == submodel_id,
            SubmodelMaterial.status.in_(("stored", "extracting")),
        )
    )
    if reading is not None:
        raise AppError(
            "A recording is still being analysed. Give it a few seconds, "
            "then train again.",
            status_code=409,
            code="submodel_material_reading",
        )
    submodel.status = SubmodelStatus.training.value
    await session.flush()
    try:
        workflows, materials, characters, summary = await train_submodel(
            session,
            submodel=submodel,
            ai=ai,
            actor_user_id=auth.user.id,
            actor_role=PLATFORM_OWNER_ROLE,
        )
    except AppError:
        # The training state (``failed`` + reason) was written by the
        # service; the lifecycle goes back so the submodel is not left
        # claiming to be mid-training.
        if submodel.status == SubmodelStatus.training.value:
            submodel.status = SubmodelStatus.draft.value
        await session.commit()
        raise
    # The workflows are read before the commit: the tenant context is
    # transaction-local, so a read after it would see no rows.
    if workflows:
        message = (
            f"The AI read {materials} piece(s) of material and wrote "
            f"{len(workflows)} workflow(s). Approve the ones that are right."
        )
        if summary["videos"]:
            message = (
                f"The AI read {summary['videos']} video demo(s) "
                f"({summary['screens']} screens) and wrote {len(workflows)} "
                "workflow(s). Approve the ones that are right."
            )
    else:
        # Nothing was found. That is the honest outcome of a run whose
        # material does not support a workflow, and it is said plainly
        # rather than reported like a normal result.
        if summary["videos"]:
            message = (
                f"No workflows were found in {summary['videos']} video "
                "demo(s). Check that the recording shows the task and that "
                "the screens are readable, then train again — a submodel is "
                "never used with an empty workflow set."
            )
        else:
            message = (
                "No workflows were found in the training material. Add a "
                "video demo of the task, or the written steps, and train "
                "again — a submodel is never used with an empty workflow set."
            )
    result = SubmodelTrainingResult(
        submodel_id=submodel.id,
        training_status=submodel.training_status,
        workflows=[SubmodelWorkflowRead.model_validate(item) for item in workflows],
        analysed_materials=materials,
        analysed_characters=characters,
        videos=summary["videos"],
        screens=summary["screens"],
        notes=summary["notes"],
        message=message,
    )
    await session.commit()
    return result


@admin_router.get(
    "/{submodel_id}/workflows",
    response_model=list[SubmodelWorkflowRead],
    summary="List the extracted workflows (administrator only)",
)
async def list_workflows(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelWorkflowRead]:
    """Every workflow the AI extracted, approved or not."""
    await get_submodel(session, submodel_id=submodel_id)
    rows = await session.scalars(
        select(SubmodelProcedure)
        .where(SubmodelProcedure.submodel_id == submodel_id)
        .order_by(SubmodelProcedure.position.asc())
    )
    return [SubmodelWorkflowRead.model_validate(row) for row in rows]


@admin_router.patch(
    "/{submodel_id}/workflows/{workflow_id}",
    response_model=SubmodelWorkflowRead,
    summary="Approve or reject a workflow (administrator only)",
)
async def update_workflow(
    submodel_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: SubmodelWorkflowUpdate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelWorkflowRead:
    """Approve a workflow so the submodel may use it, or reject it."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    workflow = await session.scalar(
        select(SubmodelProcedure).where(
            SubmodelProcedure.id == workflow_id,
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
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.workflow_updated",
        metadata={"workflow_id": str(workflow.id), "status": workflow.status},
    )
    await session.refresh(workflow)
    read = SubmodelWorkflowRead.model_validate(workflow)
    await session.commit()
    return read


@admin_router.post(
    "/{submodel_id}/tests",
    response_model=SubmodelTestRead,
    status_code=201,
    summary="Test the submodel (administrator only)",
)
async def create_test(
    submodel_id: uuid.UUID,
    payload: SubmodelTestCreate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelTestRead:
    """Ask the submodel a question and check whether it covers the points."""
    submodel = await get_submodel(session, submodel_id=submodel_id)
    test = await run_submodel_test(
        session,
        submodel=submodel,
        question=payload.question.strip(),
        expected_points=payload.expected_points,
        ai=ai,
        actor_user_id=auth.user.id,
        actor_role=PLATFORM_OWNER_ROLE,
    )
    await session.refresh(test)
    read = SubmodelTestRead.model_validate(test)
    await session.commit()
    return read


@admin_router.get(
    "/{submodel_id}/tests",
    response_model=list[SubmodelTestRead],
    summary="List the test results (administrator only)",
)
async def list_tests(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelTestRead]:
    """Every test this submodel was put through."""
    await get_submodel(session, submodel_id=submodel_id)
    rows = await session.scalars(
        select(SubmodelTest)
        .where(SubmodelTest.submodel_id == submodel_id)
        .order_by(SubmodelTest.created_at.desc())
    )
    return [SubmodelTestRead.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Export and install — administrator only
# ---------------------------------------------------------------------------


@admin_router.get(
    "/{submodel_id}/package",
    response_model=SubmodelPackageInfo,
    summary="The package for this submodel (administrator only)",
)
async def package_info(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelPackageInfo:
    """The package, whether it matches what was published, and why not."""
    from app.services.extension_signing import canonical_json

    submodel = await get_submodel(session, submodel_id=submodel_id)
    workflows = await approved_procedures(session, submodel_id=submodel.id)
    payload = build_package(submodel, workflows)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()

    stored = submodel.package_manifest
    verified = bool(submodel.package_signature) and stored is not None
    reason = ""
    if not verified:
        if not workflows:
            reason = "Approve at least one workflow to build the package."
        elif stored is None:
            reason = "This submodel has not been published yet."
        else:
            reason = "This submodel has not been exported yet."
    elif hashlib.sha256(canonical_json(stored)).hexdigest() != digest:
        verified = False
        reason = (
            "The workflows changed since this submodel was published. "
            "Publish it again to refresh the package."
        )
    return SubmodelPackageInfo(
        filename=f"{submodel.slug}-v{submodel.version}.wmsubmodel.json",
        digest=digest,
        signing_key_id=submodel.signing_key_id or "",
        verified=verified,
        reason=reason,
        workflow_count=len(workflows),
        payload=payload,
    )


@admin_router.get(
    "/{submodel_id}/download",
    summary="Download the submodel package (administrator only)",
    response_class=PlainTextResponse,
)
async def download(
    submodel_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PlainTextResponse:
    """Download the signed package.

    The file carries the workflows, the permission policy and the signature,
    so it can be installed on another computer and used there on its own.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    package = await build_package_document(session, submodel=submodel)
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.exported",
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


@admin_router.get(
    "/{submodel_id}/actions",
    response_model=list[SubmodelActionPlanRead],
    summary="List the action plans (administrator only)",
)
async def admin_list_actions(
    submodel_id: uuid.UUID,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelActionPlanRead]:
    """Every action this submodel was asked to perform, across all tenants."""
    await get_submodel(session, submodel_id=submodel_id)
    rows = await session.scalars(
        select(SubmodelActionPlan)
        .where(SubmodelActionPlan.submodel_id == submodel_id)
        .order_by(SubmodelActionPlan.created_at.desc())
    )
    return [SubmodelActionPlanRead.model_validate(row) for row in rows]


@admin_router.post(
    "/{submodel_id}/guidance",
    response_model=SubmodelGuidanceResponse,
    summary="Ask a submodel a question (administrator only)",
)
async def admin_guidance(
    submodel_id: uuid.UUID,
    payload: SubmodelGuidanceRequest,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelGuidanceResponse:
    """Try a submodel from the console, published or not.

    The administrator asks the same question a client would, before
    publishing it. No tenant is involved, so no quota is reserved and no
    client's usage is charged; the question is recorded as a platform audit
    event instead. The workflows themselves stay on the server.
    """
    submodel = await get_submodel(session, submodel_id=submodel_id)
    answer = await answer_with_submodel(
        session,
        org_id=None,
        submodel=submodel,
        question=payload.question,
        ai=ai,
        context=payload.context,
    )
    await _admin_action(
        session,
        auth=auth,
        request=request,
        submodel=submodel,
        action="submodel.guidance",
        metadata={"question": payload.question[:500]},
    )
    response = SubmodelGuidanceResponse(
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
    await session.commit()
    return response


# ===========================================================================
# Using a published submodel — organization members
# ===========================================================================


@router.get(
    "",
    response_model=list[SubmodelRead],
    summary="List the submodels published to clients",
)
async def catalog(
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelRead]:
    """Only what the administrator has published."""
    try:
        rows = await list_submodels(session, published_only=True)
        return [await _submodel_read(session, row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - temporary diagnostic
        from app.core.errors import AppError

        raise AppError(
            f"DEBUG: {type(exc).__name__}: {str(exc)[:400]}",
            status_code=500,
            code="submodels_debug",
        ) from exc


@router.get(
    "/{submodel_id}",
    response_model=SubmodelRead,
    summary="Read a published submodel",
)
async def show_published(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    """One published submodel. Anything else is a 404, not a 403."""
    submodel = await get_submodel(
        session, submodel_id=submodel_id, published=True
    )
    return await _submodel_read(session, submodel)


@router.post(
    "/{submodel_id}/guidance",
    response_model=SubmodelGuidanceResponse,
    summary="Ask a published submodel how to do something",
)
async def guidance(
    submodel_id: uuid.UUID,
    payload: SubmodelGuidanceRequest,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> SubmodelGuidanceResponse:
    """Answer from the workflows the submodel learned.

    The call is metered against the asking tenant's plan. The workflows
    themselves stay on the server: only the answer and the steps come back.
    """
    submodel = await get_submodel(
        session, submodel_id=submodel_id, published=True
    )
    answer = await answer_with_submodel(
        session,
        org_id=context.org_id,
        submodel=submodel,
        question=payload.question,
        ai=ai,
        context=payload.context,
    )
    response = SubmodelGuidanceResponse(
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
    await session.commit()
    return response


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
    """Ask the server whether something may be done on the user's machine.

    The submodel's own permission policy decides. A sensitive action always
    needs the user's confirmation, and the plan records who confirmed what.
    """
    submodel = await get_submodel(
        session, submodel_id=submodel_id, published=True
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
    decision = SubmodelActionDecision(
        plan=SubmodelActionPlanRead.model_validate(plan),
        allowed=allowed,
        requires_confirmation=requires_confirmation,
        reason=reason,
    )
    await session.commit()
    return decision


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
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        submodel_id=submodel_id,
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
        resource_id=str(submodel_id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.refresh(plan)
    read = SubmodelActionPlanRead.model_validate(plan)
    await session.commit()
    return read


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
    """Close the action plan with what happened.

    A sensitive action that nobody confirmed can never reach this point: the
    endpoint refuses it, so it is recorded as never performed.
    """
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        submodel_id=submodel_id,
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
        resource_id=str(submodel_id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.refresh(plan)
    read = SubmodelActionPlanRead.model_validate(plan)
    await session.commit()
    return read


@router.get(
    "/{submodel_id}/actions",
    response_model=list[SubmodelActionPlanRead],
    summary="List this tenant's action plans",
)
async def list_actions(
    submodel_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelActionPlanRead]:
    """Every action this submodel was asked to perform in this organization."""
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
