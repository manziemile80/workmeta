"""Admin-controlled AI submodel lifecycle.

All mutating/admin endpoints require a platform administrator. Clients may
only list and read published submodels (and run a chat against one).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, require_platform_admin
from app.core.errors import AppError
from app.db.session import get_session
from app.models.submodel import (
    SubmodelJob,
    SubmodelStatus,
    SubmodelVersion,
    SubmodelVideo,
    SubmodelWorkflow,
)
from app.schemas.submodel import (
    SubmodelCreate,
    SubmodelJobRead,
    SubmodelRead,
    SubmodelTestRequest,
    SubmodelTestResponse,
    SubmodelUpdate,
    SubmodelVersionRead,
    SubmodelVideoRead,
    SubmodelWorkflowRead,
)
from app.services import audit
from app.services.ai import build_ai_provider
from app.services.storage import build_storage, get_storage
from app.services.submodels import (
    analyze_video,
    build_download_package,
    counts,
    create_job,
    create_submodel,
    delete_submodel,
    delete_video,
    get_job,
    get_submodel,
    get_video,
    install_package,
    list_submodels,
    publish_submodel,
    test_submodel,
    train_submodel,
    unpublish_submodel,
    update_submodel,
    upload_video,
)

router = APIRouter(prefix="/submodels", tags=["submodels"])


def _to_read(sm, vcount: int, wcount: int) -> SubmodelRead:
    return SubmodelRead(
        id=sm.id,
        name=sm.name,
        slug=sm.slug,
        description=sm.description,
        purpose=sm.purpose,
        status=sm.status,
        version=sm.version,
        config=sm.config or {},
        published_at=sm.published_at,
        created_at=sm.created_at,
        updated_at=sm.updated_at,
        video_count=vcount,
        workflow_count=wcount,
    )


# ---------------------------------------------------------------------------
# Client (any authenticated user)
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SubmodelRead], summary="List published submodels")
async def list_published(
    auth=Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelRead]:
    items = await list_submodels(session, published_only=True)
    out = []
    for sm in items:
        v, w = await counts(session, sm.id)
        out.append(_to_read(sm, v, w))
    return out


@router.get("/{submodel_id}", response_model=SubmodelRead, summary="Read a published submodel")
async def read_published(
    submodel_id: uuid.UUID,
    auth=Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    sm = await get_submodel(session, submodel_id)
    if sm.status != SubmodelStatus.published:
        raise AppError("Submodel not found.", status_code=404, code="not_found")
    v, w = await counts(session, sm.id)
    return _to_read(sm, v, w)


@router.post(
    "/{submodel_id}/chat",
    response_model=SubmodelTestResponse,
    summary="Use a published submodel",
)
async def use_submodel(
    submodel_id: uuid.UUID,
    payload: SubmodelTestRequest,
    auth=Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelTestResponse:
    sm = await get_submodel(session, submodel_id)
    if sm.status != SubmodelStatus.published:
        raise AppError("Submodel not found.", status_code=404, code="not_found")
    ai = build_ai_provider()
    answer, followed, model = await test_submodel(
        session, submodel=sm, instruction=payload.instruction, ai=ai
    )
    return SubmodelTestResponse(
        submodel_id=sm.id, answer=answer, followed_workflows=followed, model=model
    )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@router.get(
    "/admin/all",
    response_model=list[SubmodelRead],
    summary="List all submodels (admin)",
)
async def admin_list_all(
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelRead]:
    items = await list_submodels(session, published_only=False)
    out = []
    for sm in items:
        v, w = await counts(session, sm.id)
        out.append(_to_read(sm, v, w))
    return out


@router.post(
    "/admin",
    response_model=SubmodelRead,
    status_code=201,
    summary="Create a submodel (admin)",
)
async def admin_create(
    payload: SubmodelCreate,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    sm = await create_submodel(session, payload=payload, user=auth.user)
    v, w = await counts(session, sm.id)
    await audit.record(
        session,
        action="submodel.created",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        metadata={"name": sm.name, "slug": sm.slug},
        request=request,
    )
    await session.commit()
    return _to_read(sm, v, w)


@router.patch(
    "/admin/{submodel_id}",
    response_model=SubmodelRead,
    summary="Update a submodel (admin)",
)
async def admin_update(
    submodel_id: uuid.UUID,
    payload: SubmodelUpdate,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    sm = await get_submodel(session, submodel_id)
    sm = await update_submodel(session, submodel=sm, payload=payload)
    v, w = await counts(session, sm.id)
    await audit.record(
        session,
        action="submodel.updated",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        request=request,
    )
    await session.commit()
    return _to_read(sm, v, w)


@router.delete(
    "/admin/{submodel_id}",
    status_code=204,
    summary="Delete a submodel (admin)",
)
async def admin_delete(
    submodel_id: uuid.UUID,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    sm = await get_submodel(session, submodel_id)
    storage = build_storage()
    await delete_submodel(session, submodel=sm, storage=storage)
    await audit.record(
        session,
        action="submodel.deleted",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(submodel_id),
        request=request,
    )
    await session.commit()


@router.post(
    "/admin/{submodel_id}/publish",
    response_model=SubmodelRead,
    summary="Publish a submodel (admin)",
)
async def admin_publish(
    submodel_id: uuid.UUID,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    sm = await get_submodel(session, submodel_id)
    sm = await publish_submodel(session, submodel=sm, user=auth.user)
    v, w = await counts(session, sm.id)
    await audit.record(
        session,
        action="submodel.published",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        request=request,
    )
    await session.commit()
    return _to_read(sm, v, w)


@router.post(
    "/admin/{submodel_id}/unpublish",
    response_model=SubmodelRead,
    summary="Unpublish a submodel (admin)",
)
async def admin_unpublish(
    submodel_id: uuid.UUID,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    sm = await get_submodel(session, submodel_id)
    sm = await unpublish_submodel(session, submodel=sm)
    v, w = await counts(session, sm.id)
    await audit.record(
        session,
        action="submodel.unpublished",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        request=request,
    )
    await session.commit()
    return _to_read(sm, v, w)


# ---------------------------------------------------------------------------
# Videos (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/admin/{submodel_id}/videos",
    response_model=SubmodelVideoRead,
    status_code=201,
    summary="Upload a training video (admin)",
)
async def admin_upload_video(
    submodel_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008
    title: str | None = Form(default=None),  # noqa: B008
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage=Depends(get_storage),  # noqa: B008
) -> SubmodelVideo:
    sm = await get_submodel(session, submodel_id)
    data = await file.read()
    if not data:
        raise AppError("Empty file.", status_code=400, code="empty_file")
    if len(data) > 100 * 1024 * 1024:
        raise AppError("Video too large (max 100 MB).", status_code=413, code="file_too_large")
    video = await upload_video(
        session,
        submodel=sm,
        title=title or file.filename or "video",
        filename=file.filename or "video.mp4",
        mime_type=file.content_type or "video/mp4",
        data=data,
        user=auth.user,
        storage=storage,
    )
    return video


@router.get(
    "/admin/{submodel_id}/videos",
    response_model=list[SubmodelVideoRead],
    summary="List training videos (admin)",
)
async def admin_list_videos(
    submodel_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelVideo]:
    await get_submodel(session, submodel_id)
    rows = (
        await session.scalars(
            select(SubmodelVideo)
            .where(SubmodelVideo.submodel_id == submodel_id)
            .order_by(SubmodelVideo.created_at.desc())
        )
    ).all()
    return list(rows)


@router.delete(
    "/admin/videos/{video_id}",
    status_code=204,
    summary="Delete a training video (admin)",
)
async def admin_delete_video(
    video_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    storage=Depends(get_storage),  # noqa: B008
) -> None:
    video = await get_video(session, video_id)
    await delete_video(session, video=video, storage=storage)


@router.post(
    "/admin/{submodel_id}/videos/{video_id}/analyze",
    response_model=SubmodelJobRead,
    status_code=202,
    summary="Analyze a training video (admin)",
)
async def admin_analyze_video(
    submodel_id: uuid.UUID,
    video_id: uuid.UUID,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelJob:
    sm = await get_submodel(session, submodel_id)
    video = await get_video(session, video_id)
    if video.submodel_id != sm.id:
        raise AppError("Video not found.", status_code=404, code="not_found")
    storage = build_storage()
    ai = build_ai_provider()
    job = await analyze_video(
        session, submodel=sm, video=video, user=auth.user, storage=storage, ai=ai
    )
    await audit.record(
        session,
        action="submodel.video_analyzed",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel_video",
        resource_id=str(video.id),
        request=request,
    )
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Workflows (admin read)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/{submodel_id}/workflows",
    response_model=list[SubmodelWorkflowRead],
    summary="List workflows (admin)",
)
async def admin_list_workflows(
    submodel_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelWorkflow]:
    await get_submodel(session, submodel_id)
    rows = (
        await session.scalars(
            select(SubmodelWorkflow)
            .where(SubmodelWorkflow.submodel_id == submodel_id)
            .order_by(SubmodelWorkflow.created_at.asc())
        )
    ).all()
    return list(rows)


# ---------------------------------------------------------------------------
# Training (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/admin/{submodel_id}/train",
    response_model=SubmodelJobRead,
    status_code=202,
    summary="Train a submodel (admin)",
)
async def admin_train(
    submodel_id: uuid.UUID,
    request: Request,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelJob:
    sm = await get_submodel(session, submodel_id)
    storage = build_storage()
    ai = build_ai_provider()
    job = await train_submodel(session, submodel=sm, user=auth.user, storage=storage, ai=ai)
    await audit.record(
        session,
        action="submodel.trained",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        request=request,
    )
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Test (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/admin/{submodel_id}/test",
    response_model=SubmodelTestResponse,
    summary="Test a submodel (admin)",
)
async def admin_test(
    submodel_id: uuid.UUID,
    payload: SubmodelTestRequest,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelTestResponse:
    sm = await get_submodel(session, submodel_id)
    ai = build_ai_provider()
    answer, followed, model = await test_submodel(
        session, submodel=sm, instruction=payload.instruction, ai=ai
    )
    await create_job(
        session, submodel_id=sm.id, kind="test", video_id=None, user=auth.user
    )
    await session.commit()
    return SubmodelTestResponse(
        submodel_id=sm.id, answer=answer, followed_workflows=followed, model=model
    )


# ---------------------------------------------------------------------------
# Jobs (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/{submodel_id}/jobs",
    response_model=list[SubmodelJobRead],
    summary="List jobs (admin)",
)
async def admin_list_jobs(
    submodel_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session), # noqa: B008
) -> list[SubmodelJob]:
    await get_submodel(session, submodel_id)
    rows = (
        await session.scalars(
            select(SubmodelJob)
            .where(SubmodelJob.submodel_id == submodel_id)
            .order_by(SubmodelJob.created_at.desc())
        )
    ).all()
    return list(rows)


@router.get(
    "/admin/jobs/{job_id}",
    response_model=SubmodelJobRead,
    summary="Read a job (admin)",
)
async def admin_get_job(
    job_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelJob:
    return await get_job(session, job_id)


# ---------------------------------------------------------------------------
# Download (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/{submodel_id}/download",
    summary="Download a submodel package (admin)",
)
async def admin_download(
    submodel_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    sm = await get_submodel(session, submodel_id)
    storage = build_storage()
    data, filename = await build_download_package(
        session, submodel=sm, user=auth.user, storage=storage
    )
    await audit.record(
        session,
        action="submodel.downloaded",
        actor_user_id=auth.user.id,
        actor_role="platform_owner",
        resource_type="submodel",
        resource_id=str(sm.id),
        request=None,
    )
    await session.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Install (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/admin/install",
    response_model=SubmodelRead,
    status_code=201,
    summary="Install a submodel package (admin)",
)
async def admin_install(
    file: UploadFile = File(...),  # noqa: B008
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubmodelRead:
    data = await file.read()
    if not data:
        raise AppError("Empty file.", status_code=400, code="empty_file")
    sm = await install_package(session, data=data, user=auth.user)
    v, w = await counts(session, sm.id)
    return _to_read(sm, v, w)


# ---------------------------------------------------------------------------
# Versions (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/{submodel_id}/versions",
    response_model=list[SubmodelVersionRead],
    summary="List versions (admin)",
)
async def admin_list_versions(
    submodel_id: uuid.UUID,
    auth=Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubmodelVersion]:
    await get_submodel(session, submodel_id)
    rows = (
        await session.scalars(
            select(SubmodelVersion)
            .where(SubmodelVersion.submodel_id == submodel_id)
            .order_by(SubmodelVersion.created_at.desc())
        )
    ).all()
    return [
        SubmodelVersionRead(
            id=r.id,
            submodel_id=r.submodel_id,
            version=r.version,
            manifest=r.manifest or {},
            has_package=bool(r.package_storage_key),
            created_at=r.created_at,
        )
        for r in rows
    ]
