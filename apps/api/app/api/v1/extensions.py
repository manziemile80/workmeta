"""Trained extensions: training, testing, publishing and runtime guidance.

Every route is scoped to one organization. A client can train as many
extensions as their plan allows, each for its own system and version, and can
never read another client's materials, procedures, tests or permissions.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin, require_org_member
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.assistant import Assistant
from app.models.enums import (
    ExtensionActionStatus,
    ExtensionStatus,
    MaterialKind,
    MaterialStatus,
)
from app.models.extension import (
    Extension,
    ExtensionActionPlan,
    ExtensionProcedure,
    ExtensionTest,
    TrainingMaterial,
)
from app.schemas.extension import (
    ActionComplete,
    ActionConfirm,
    ActionDecision,
    ActionPlanCreate,
    ActionPlanRead,
    ExtensionCreate,
    ExtensionRead,
    ExtensionUpdate,
    ExtractionResult,
    GuidanceRequest,
    GuidanceResponse,
    MaterialCreate,
    MaterialRead,
    PackageRead,
    ProcedureRead,
    ProcedureUpdate,
    TestCreate,
    TestRead,
)
from app.services import audit, quota
from app.services import extensions as extension_service
from app.services.ai import AIProvider, get_ai_provider
from app.services.extension_runtime import (
    answer_with_extension,
    procedures_for_extension,
    run_extension_test,
)
from app.services.media import (
    AUDIO_MIME_TYPES,
    VIDEO_MIME_TYPES,
    MediaCapabilityUnavailable,
    build_audio_transcriber,
    build_video_extractor,
)
from app.services.procedure_extraction import analyse_extension
from app.services.storage import StorageProvider, get_storage

router = APIRouter(
    prefix="/organizations/{org_id}/extensions", tags=["extensions"]
)

MAX_MATERIAL_BYTES = 50 * 1024 * 1024


async def _assistant(
    session: AsyncSession, *, org_id: uuid.UUID, assistant_id: uuid.UUID
) -> Assistant:
    assistant = await session.scalar(
        select(Assistant).where(
            Assistant.id == assistant_id, Assistant.org_id == org_id
        )
    )
    if assistant is None:
        raise AppError(
            "Assistant not found.", status_code=404, code="not_found"
        )
    return assistant


async def _owned_extension(
    session: AsyncSession, *, org_id: uuid.UUID, extension_id: uuid.UUID
) -> Extension:
    return await extension_service.get_extension(
        session, org_id=org_id, extension_id=extension_id
    )


async def _owned_material(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    extension_id: uuid.UUID,
    material_id: uuid.UUID,
) -> TrainingMaterial:
    material = await session.scalar(
        select(TrainingMaterial).where(
            TrainingMaterial.id == material_id,
            TrainingMaterial.org_id == org_id,
            TrainingMaterial.extension_id == extension_id,
        )
    )
    if material is None:
        raise AppError(
            "Training material not found.",
            status_code=404,
            code="material_not_found",
        )
    return material


async def _extension_read(
    session: AsyncSession, extension: Extension
) -> ExtensionRead:
    counts = await extension_service.extension_counts(
        session, org_id=extension.org_id, extension_id=extension.id
    )
    read = ExtensionRead.model_validate(extension)
    for key, value in counts.items():
        setattr(read, key, value)
    return read


@router.post(
    "",
    response_model=ExtensionRead,
    status_code=201,
    summary="Create a trained extension",
)
async def create_extension(
    payload: ExtensionCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionRead:
    """Start a new extension for one system at one version."""
    await quota.guard_extension_create(session, context.org_id)
    assistant = await _assistant(
        session, org_id=context.org_id, assistant_id=payload.assistant_id
    )
    extension = await extension_service.create_extension(
        session,
        org_id=context.org_id,
        assistant_id=assistant.id,
        name=payload.name,
        system_code=payload.system_code,
        system_name=payload.system_name,
        system_version=payload.system_version,
        target_kind=payload.target_kind,
        url_patterns=payload.url_patterns,
        app_hints=payload.app_hints,
        instructions=payload.instructions,
        permissions=payload.permissions,
    )
    await audit.record(
        session,
        action="extension.created",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={
            "system_code": extension.system_code,
            "system_version": extension.system_version,
            "target_kind": extension.target_kind,
        },
        request=request,
    )
    await session.commit()
    return await _extension_read(session, extension)


@router.get("", response_model=list[ExtensionRead], summary="List extensions")
async def list_extensions(
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ExtensionRead]:
    rows = await extension_service.list_extensions(
        session, org_id=context.org_id
    )
    return [await _extension_read(session, row) for row in rows]


@router.get(
    "/recognize",
    response_model=list[ExtensionRead],
    summary="Find the extensions that apply to a page or application",
)
async def recognize(
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    url: str | None = None,
    window_title: str | None = None,
    process_name: str | None = None,
) -> list[ExtensionRead]:
    """Which trained extension recognises the page or application in front of the user."""
    hints = {
        "window_title": window_title,
        "process_name": process_name,
    }
    hints = {key: value for key, value in hints.items() if value}
    rows = await extension_service.list_extensions(
        session, org_id=context.org_id
    )
    matched = [
        row
        for row in rows
        if row.status == ExtensionStatus.published.value
        and extension_service.recognizes(row, url=url, app=hints or None)
    ]
    return [await _extension_read(session, row) for row in matched]


@router.get(
    "/{extension_id}",
    response_model=ExtensionRead,
    summary="Read one extension",
)
async def read_extension(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionRead:
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    return await _extension_read(session, extension)


@router.patch(
    "/{extension_id}",
    response_model=ExtensionRead,
    summary="Update an extension",
)
async def update_extension(
    extension_id: uuid.UUID,
    payload: ExtensionUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionRead:
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    changes = payload.model_dump(exclude_unset=True)
    if "permissions" in changes:
        changes["permissions"] = extension_service.normalise_permissions(
            changes["permissions"]
        )
    for field, value in changes.items():
        if value is not None:
            setattr(extension, field, value)
    await session.flush()
    await session.refresh(extension)
    await audit.record(
        session,
        action="extension.updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await session.commit()
    return await _extension_read(session, extension)


# ---------------------------------------------------------------------------
# Training materials
# ---------------------------------------------------------------------------


@router.get(
    "/{extension_id}/materials",
    response_model=list[MaterialRead],
    summary="List training materials",
)
async def list_materials(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[TrainingMaterial]:
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    rows = await session.scalars(
        select(TrainingMaterial)
        .where(
            TrainingMaterial.org_id == context.org_id,
            TrainingMaterial.extension_id == extension_id,
            TrainingMaterial.status != MaterialStatus.archived.value,
        )
        .order_by(TrainingMaterial.created_at.asc())
    )
    return list(rows)


@router.post(
    "/{extension_id}/materials/notes",
    response_model=MaterialRead,
    status_code=201,
    summary="Add a written procedure",
)
async def add_note(
    extension_id: uuid.UUID,
    payload: MaterialCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TrainingMaterial:
    """The client's own written step-by-step procedures."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    material = TrainingMaterial(
        org_id=context.org_id,
        extension_id=extension.id,
        kind=MaterialKind.note.value,
        title=payload.title.strip(),
        note_text=payload.text.strip(),
        size_bytes=len(payload.text.strip().encode("utf-8")),
        status=MaterialStatus.ready.value,
        created_by=context.user.id,
    )
    session.add(material)
    await session.flush()
    await audit.record(
        session,
        action="extension.material_added",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={"material_id": str(material.id), "kind": material.kind},
        request=request,
    )
    await session.commit()
    return material


@router.post(
    "/{extension_id}/materials/upload",
    response_model=MaterialRead,
    status_code=201,
    summary="Upload a screen recording or an audio instruction",
)
async def upload_material(
    extension_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI idiom
    title: str | None = Form(default=None),  # noqa: B008 - FastAPI idiom
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> TrainingMaterial:
    """Store a recording or an audio instruction as training material.

    The file is stored even when this deployment cannot read it yet; the
    material then says exactly what is missing instead of pretending it was
    analysed.
    """
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    mime_type = (file.content_type or "").split(";")[0].strip().lower()
    if mime_type not in VIDEO_MIME_TYPES and mime_type not in AUDIO_MIME_TYPES:
        raise AppError(
            "Upload a screen recording or an audio instruction. "
            "Manuals and screenshots are uploaded as documents.",
            status_code=422,
            code="invalid_material_type",
        )
    raw = await file.read(MAX_MATERIAL_BYTES + 1)
    if len(raw) > MAX_MATERIAL_BYTES:
        raise AppError(
            "The file is larger than the 50 MB limit.",
            status_code=422,
            code="invalid_material",
        )
    if not raw:
        raise AppError(
            "The uploaded file is empty.",
            status_code=422,
            code="invalid_material",
        )

    filename = Path(file.filename or "training-material").name[:255]
    storage_key = f"{context.org_id}/{extension.id}/{uuid.uuid4().hex}"
    await storage.save(storage_key, raw)
    material = TrainingMaterial(
        org_id=context.org_id,
        extension_id=extension.id,
        kind=(
            MaterialKind.video.value
            if mime_type in VIDEO_MIME_TYPES
            else MaterialKind.audio.value
        ),
        title=(title or Path(filename).stem or "Training material").strip()[:200],
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(raw),
        storage_key=storage_key,
        sha256=hashlib.sha256(raw).hexdigest(),
        status=MaterialStatus.stored.value,
        created_by=context.user.id,
    )
    session.add(material)
    await session.flush()
    await audit.record(
        session,
        action="extension.material_added",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={"material_id": str(material.id), "kind": material.kind},
        request=request,
    )
    await session.commit()
    return material


@router.post(
    "/{extension_id}/materials/{material_id}/extract",
    response_model=MaterialRead,
    summary="Read a recording or an audio instruction",
)
async def extract_material(
    extension_id: uuid.UUID,
    material_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage: StorageProvider = Depends(get_storage),  # noqa: B008
) -> TrainingMaterial:
    """Turn a screen recording or an audio instruction into text."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    material = await _owned_material(
        session,
        org_id=context.org_id,
        extension_id=extension.id,
        material_id=material_id,
    )
    if material.kind == MaterialKind.note.value:
        raise AppError(
            "Written procedures do not need to be extracted.",
            status_code=409,
            code="material_not_extractable",
        )
    if not material.storage_key:
        raise AppError(
            "This material has no stored file.",
            status_code=409,
            code="material_not_extractable",
        )

    material.status = MaterialStatus.extracting.value
    material.error_message = None
    await session.flush()

    raw = await storage.read(material.storage_key)
    try:
        if material.kind == MaterialKind.video.value:
            text = await _extract_video_text(raw, mime_type=material.mime_type or "")
        else:
            text = await _extract_audio_text(raw, filename=material.filename or "")
    except MediaCapabilityUnavailable as exc:
        material.status = MaterialStatus.failed.value
        material.error_message = str(exc)[:1000]
        await session.commit()
        raise AppError(
            str(exc),
            status_code=503,
            code="media_capability_unavailable",
        ) from exc

    material.extracted_text = text
    material.extracted_units = len(text)
    material.status = MaterialStatus.ready.value
    await session.flush()
    await session.refresh(material)
    await audit.record(
        session,
        action="extension.material_extracted",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={"material_id": str(material.id), "characters": len(text)},
        request=request,
    )
    await session.commit()
    return material


async def _extract_video_text(raw: bytes, *, mime_type: str) -> str:
    """Read the frames of a screen recording with OCR."""
    extractor = build_video_extractor()
    if extractor is None:
        raise MediaCapabilityUnavailable(
            "Reading screen recordings needs ffmpeg on this deployment. "
            "Upload the manual or the screenshots instead."
        )
    frames = await extractor.frames(raw, mime_type=mime_type)
    if not frames:
        raise MediaCapabilityUnavailable(
            "No frames could be read from the screen recording."
        )
    from app.services.ocr import get_ocr_engine

    ocr = get_ocr_engine()
    if ocr is None:
        raise MediaCapabilityUnavailable(
            "Reading screen recordings needs OCR on this deployment. "
            "Upload the manual or the screenshots instead."
        )
    texts: list[str] = []
    for index, frame in enumerate(frames, start=1):
        text = await ocr.extract_image(frame, mime_type="image/png")
        if text and text.strip():
            texts.append(f"[frame {index}]\n{text.strip()}")
    if not texts:
        raise MediaCapabilityUnavailable(
            "No readable text was found in the screen recording."
        )
    return "\n\n".join(texts)


async def _extract_audio_text(raw: bytes, *, filename: str) -> str:
    transcriber = build_audio_transcriber()
    if transcriber is None:
        raise MediaCapabilityUnavailable(
            "Transcribing audio needs a transcription model on this deployment. "
            "Upload the written instructions instead."
        )
    return await transcriber.transcribe(raw, filename=filename)


@router.delete(
    "/{extension_id}/materials/{material_id}",
    response_model=MaterialRead,
    summary="Archive a training material",
)
async def archive_material(
    extension_id: uuid.UUID,
    material_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TrainingMaterial:
    """Take a material out of the analysis.

    The row is archived rather than deleted: the runtime role has no delete
    grant and the training history stays auditable.
    """
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    material = await _owned_material(
        session,
        org_id=context.org_id,
        extension_id=extension.id,
        material_id=material_id,
    )
    material.status = MaterialStatus.archived.value
    await session.flush()
    await session.refresh(material)
    await audit.record(
        session,
        action="extension.material_archived",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={"material_id": str(material.id)},
        request=request,
    )
    await session.commit()
    return material


# ---------------------------------------------------------------------------
# Training, testing and publishing
# ---------------------------------------------------------------------------


@router.post(
    "/{extension_id}/analyze",
    response_model=ExtractionResult,
    summary="Analyse the training materials",
)
async def analyze_extension(
    extension_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> ExtractionResult:
    """Extract the operating procedures from the training materials."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    if extension.status == ExtensionStatus.published.value:
        raise AppError(
            "Retire the extension before training it again.",
            status_code=409,
            code="extension_published",
        )
    extension.status = ExtensionStatus.training.value
    extension.training_status = "extracting"
    await session.flush()

    procedures, materials, characters = await analyse_extension(
        session,
        org_id=context.org_id,
        extension=extension,
        ai=ai,
        actor_user_id=context.user.id,
        actor_role=context.role,
    )
    await session.commit()
    if not procedures:
        message = (
            "No procedures were found in the training material. Add the manual, "
            "the screenshots or the written steps and analyse again — an "
            "extension is never published with an empty procedure set."
        )
    else:
        message = (
            f"Extracted {len(procedures)} procedures from {materials} materials. "
            "Review and approve them before publishing."
        )
    return ExtractionResult(
        extension_id=extension.id,
        training_status=extension.training_status,
        procedures=[ProcedureRead.model_validate(item) for item in procedures],
        analysed_materials=materials,
        analysed_characters=characters,
        message=message,
    )


@router.get(
    "/{extension_id}/procedures",
    response_model=list[ProcedureRead],
    summary="List the extracted procedures",
)
async def list_procedures(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ExtensionProcedure]:
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    return await procedures_for_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )


@router.patch(
    "/{extension_id}/procedures/{procedure_id}",
    response_model=ProcedureRead,
    summary="Approve, reject or edit a procedure",
)
async def update_procedure(
    extension_id: uuid.UUID,
    procedure_id: uuid.UUID,
    payload: ProcedureUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionProcedure:
    """A procedure only counts once a human approved it."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    procedure = await session.scalar(
        select(ExtensionProcedure).where(
            ExtensionProcedure.id == procedure_id,
            ExtensionProcedure.org_id == context.org_id,
            ExtensionProcedure.extension_id == extension.id,
        )
    )
    if procedure is None:
        raise AppError(
            "Procedure not found.", status_code=404, code="not_found"
        )
    procedure.status = payload.status
    if payload.title:
        procedure.title = payload.title.strip()
    if payload.summary is not None:
        procedure.summary = payload.summary
    if payload.steps is not None:
        procedure.steps = [step.model_dump() for step in payload.steps]
    await session.flush()
    await session.refresh(procedure)
    await audit.record(
        session,
        action="extension.procedure_updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={
            "procedure_id": str(procedure.id),
            "status": procedure.status,
        },
        request=request,
    )
    await session.commit()
    return procedure


@router.post(
    "/{extension_id}/tests",
    response_model=TestRead,
    status_code=201,
    summary="Test that the extension understands the system",
)
async def create_test(
    extension_id: uuid.UUID,
    payload: TestCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> ExtensionTest:
    """Ask the extension a question and check the answer covers the points."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    test = await run_extension_test(
        session,
        org_id=context.org_id,
        extension=extension,
        question=payload.question.strip(),
        expected_points=payload.expected_points,
        ai=ai,
        actor_user_id=context.user.id,
        actor_role=context.role,
    )
    await session.commit()
    return test


@router.get(
    "/{extension_id}/tests",
    response_model=list[TestRead],
    summary="List the tests of an extension",
)
async def list_tests(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ExtensionTest]:
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    rows = await session.scalars(
        select(ExtensionTest)
        .where(
            ExtensionTest.org_id == context.org_id,
            ExtensionTest.extension_id == extension_id,
        )
        .order_by(ExtensionTest.created_at.desc())
    )
    return list(rows)


@router.post(
    "/{extension_id}/publish",
    response_model=PackageRead,
    summary="Publish the trained extension",
)
async def publish_extension(
    extension_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PackageRead:
    """Publish the extension - only when it is trained, approved and tested."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    assistant = await _assistant(
        session, org_id=context.org_id, assistant_id=extension.assistant_id
    )
    await extension_service.publish_extension(
        session,
        org_id=context.org_id,
        extension=extension,
        assistant_name=assistant.name,
    )
    await audit.record(
        session,
        action="extension.published",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={
            "version": extension.version,
            "signed": extension.package_signature is not None,
        },
        request=request,
    )
    await session.commit()
    return await _package(session, extension)


@router.post(
    "/{extension_id}/retire",
    response_model=ExtensionRead,
    summary="Retire a published extension",
)
async def retire_extension(
    extension_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionRead:
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    extension.status = ExtensionStatus.retired.value
    await session.flush()
    await session.refresh(extension)
    await audit.record(
        session,
        action="extension.retired",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        request=request,
    )
    await session.commit()
    return await _extension_read(session, extension)


@router.get(
    "/{extension_id}/package",
    response_model=PackageRead,
    summary="The signed package the client installs",
)
async def read_package(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PackageRead:
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    if extension.status != ExtensionStatus.published.value:
        raise AppError(
            "This extension is not published yet.",
            status_code=409,
            code="extension_not_published",
        )
    return await _package(session, extension)


async def _package(
    session: AsyncSession, extension: Extension
) -> PackageRead:
    from app.services import extension_signing

    return PackageRead(
        extension_id=extension.id,
        slug=extension.slug,
        version=extension.version,
        manifest=extension.package_manifest or {},
        signature=extension.package_signature,
        signing_key_id=extension.signing_key_id,
        public_key=extension_signing.public_key_pem(),
        published_at=extension.published_at,
    )


# ---------------------------------------------------------------------------
# Runtime: guidance and authorised actions
# ---------------------------------------------------------------------------


@router.post(
    "/{extension_id}/guidance",
    response_model=GuidanceResponse,
    summary="Guide the user through an operation",
)
async def guidance(
    extension_id: uuid.UUID,
    payload: GuidanceRequest,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> GuidanceResponse:
    """Step-by-step guidance for the trained system."""
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    if extension.status != ExtensionStatus.published.value:
        raise AppError(
            "This extension is not published yet.",
            status_code=409,
            code="extension_not_published",
        )
    answer = await answer_with_extension(
        session,
        org_id=context.org_id,
        extension=extension,
        question=payload.question,
        ai=ai,
        context=payload.context,
    )
    await session.commit()
    return GuidanceResponse(
        extension_id=extension.id,
        system_name=extension.system_name,
        system_version=extension.system_version,
        answer=answer.answer,
        steps=answer.steps,
        grounded=answer.grounded,
        procedures_used=answer.procedures_used,
        model=answer.model,
        usage={
            "prompt_tokens": answer.prompt_tokens,
            "completion_tokens": answer.completion_tokens,
        },
    )


@router.post(
    "/{extension_id}/actions",
    response_model=ActionDecision,
    status_code=201,
    summary="Ask whether an action may be performed",
)
async def propose_action(
    extension_id: uuid.UUID,
    payload: ActionPlanCreate,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ActionDecision:
    """The agent asks before it acts.

    The answer is the client's own permission policy: what may run on its own,
    what needs the user's confirmation and what is never allowed.
    """
    extension = await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    if extension.status != ExtensionStatus.published.value:
        raise AppError(
            "This extension is not published yet.",
            status_code=409,
            code="extension_not_published",
        )
    steps = [step.model_dump() for step in payload.steps]
    has_sensitive_step = any(bool(step.get("sensitive")) for step in steps)
    plan, allowed, requires_confirmation, reason = (
        await extension_service.record_action(
            session,
            org_id=context.org_id,
            extension=extension,
            action_type=payload.action_type,
            description=payload.description,
            steps=steps,
            has_sensitive_step=has_sensitive_step,
        )
    )
    await audit.record(
        session,
        action="extension.action_proposed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension.id),
        metadata={
            "plan_id": str(plan.id),
            "action_type": plan.action_type,
            "allowed": allowed,
            "requires_confirmation": requires_confirmation,
        },
        request=request,
    )
    await session.commit()
    return ActionDecision(
        plan=ActionPlanRead.model_validate(plan),
        allowed=allowed,
        requires_confirmation=requires_confirmation,
        reason=reason,
    )


async def _owned_plan(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    extension_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> ExtensionActionPlan:
    plan = await session.scalar(
        select(ExtensionActionPlan).where(
            ExtensionActionPlan.id == plan_id,
            ExtensionActionPlan.org_id == org_id,
            ExtensionActionPlan.extension_id == extension_id,
        )
    )
    if plan is None:
        raise AppError(
            "Action plan not found.", status_code=404, code="not_found"
        )
    return plan


@router.post(
    "/{extension_id}/actions/{plan_id}/confirm",
    response_model=ActionPlanRead,
    summary="Record the user's confirmation",
)
async def confirm_action(
    extension_id: uuid.UUID,
    plan_id: uuid.UUID,
    payload: ActionConfirm,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionActionPlan:
    """The user confirms (or refuses) a sensitive operation."""
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        extension_id=extension_id,
        plan_id=plan_id,
    )
    if plan.status == ExtensionActionStatus.executed.value:
        raise AppError(
            "This action has already been performed.",
            status_code=409,
            code="action_already_executed",
        )
    if not payload.confirmed:
        plan.status = ExtensionActionStatus.rejected.value
    else:
        if not plan.requires_confirmation:
            raise AppError(
                "This action does not need a confirmation.",
                status_code=409,
                code="action_confirmation_not_required",
            )
        plan.status = ExtensionActionStatus.confirmed.value
        plan.confirmed_by = context.user.id
        plan.confirmed_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(plan)
    await audit.record(
        session,
        action="extension.action_confirmed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension_id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.commit()
    return plan


@router.post(
    "/{extension_id}/actions/{plan_id}/complete",
    response_model=ActionPlanRead,
    summary="Report that the action ran",
)
async def complete_action(
    extension_id: uuid.UUID,
    plan_id: uuid.UUID,
    payload: ActionComplete,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ExtensionActionPlan:
    """Close the action plan with what happened."""
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    plan = await _owned_plan(
        session,
        org_id=context.org_id,
        extension_id=extension_id,
        plan_id=plan_id,
    )
    if plan.requires_confirmation and plan.status != (
        ExtensionActionStatus.confirmed.value
    ):
        raise AppError(
            "The user has to confirm this action before it runs.",
            status_code=409,
            code="action_confirmation_required",
        )
    plan.status = (
        ExtensionActionStatus.executed.value
        if payload.succeeded
        else ExtensionActionStatus.rejected.value
    )
    plan.result = dict(payload.result)
    await session.flush()
    await session.refresh(plan)
    await audit.record(
        session,
        action="extension.action_completed",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="extension",
        resource_id=str(extension_id),
        metadata={"plan_id": str(plan.id), "status": plan.status},
        request=request,
    )
    await session.commit()
    return plan


@router.get(
    "/{extension_id}/actions",
    response_model=list[ActionPlanRead],
    summary="List the action plans of an extension",
)
async def list_actions(
    extension_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ExtensionActionPlan]:
    await _owned_extension(
        session, org_id=context.org_id, extension_id=extension_id
    )
    rows = await session.scalars(
        select(ExtensionActionPlan)
        .where(
            ExtensionActionPlan.org_id == context.org_id,
            ExtensionActionPlan.extension_id == extension_id,
        )
        .order_by(ExtensionActionPlan.created_at.desc())
    )
    return list(rows)


@router.get(
    "/limits",
    response_model=dict,
    summary="What the plan allows",
)
async def extension_limits(
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """The plan limits for trained extensions."""
    from app.services import billing

    subscription = await billing.ensure_subscription(session, context.org_id)
    settings = get_settings()
    return {
        "plan_code": subscription.plan.code,
        "max_extensions": subscription.plan.max_extensions,
        "video_frames": settings.media_video_max_frames,
        "analysis_max_characters": settings.extension_analysis_max_characters,
        "signing_configured": bool(settings.extension_signing_private_key),
    }
