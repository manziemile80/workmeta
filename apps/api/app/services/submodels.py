"""Admin-controlled AI submodel lifecycle.

Submodels are platform-global: only the platform administrator creates, trains,
publishes and distributes them. Clients may only read the ones that are
``published``.

Every long-running step (video analysis, training) is tracked in
``submodel_jobs`` with honest progress. Nothing here reports success unless
the work actually completed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.submodel import (
    Submodel,
    SubmodelJob,
    SubmodelJobKind,
    SubmodelJobStatus,
    SubmodelStatus,
    SubmodelVersion,
    SubmodelVideo,
    SubmodelVideoStatus,
    SubmodelWorkflow,
)
from app.models.user import User
from app.schemas.submodel import SubmodelCreate, SubmodelUpdate
from app.services.ai import AIProvider, ChatMessage
from app.services.storage import StorageProvider, build_storage

logger = structlog.get_logger(__name__)


def _slugify(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (slug or "submodel")[:64]


def _now() -> datetime:
    return datetime.now(UTC)


async def _run_subprocess(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def ffprobe_duration(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    code, out, _ = await _run_subprocess(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if code != 0:
        return None
    try:
        return float(out.strip())
    except ValueError:
        return None


async def extract_frames(path: Path, out_dir: Path, *, max_frames: int = 12) -> int:
    """Extract up to ``max_frames`` evenly spaced frames. Returns the count."""
    if shutil.which("ffmpeg") is None:
        raise AppError(
            "Video analysis requires ffmpeg on the server. Install ffmpeg and retry.",
            status_code=503,
            code="ffmpeg_unavailable",
        )
    duration = await ffprobe_duration(path)
    if not duration or duration <= 0:
        duration = 10.0
    # One frame every (duration / max_frames) seconds, at least every 2s.
    interval = max(duration / max_frames, 2.0)
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%04d.jpg")
    code, _, stderr = await _run_subprocess(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval:.3f}",
            "-frames:v",
            str(max_frames),
            pattern,
        ]
    )
    if code != 0:
        raise AppError(
            f"ffmpeg failed to extract frames: {stderr.strip()[:300]}",
            status_code=500,
            code="frame_extraction_failed",
        )
    return await asyncio.to_thread(lambda: len(list(out_dir.glob("frame_*.jpg"))))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_submodel(
    session: AsyncSession, *, payload: SubmodelCreate, user: User
) -> Submodel:
    slug = payload.slug or _slugify(payload.name)
    submodel = Submodel(
        name=payload.name.strip(),
        slug=slug,
        description=payload.description,
        purpose=payload.purpose,
        config=payload.config or {},
        created_by=user.id,
    )
    session.add(submodel)
    try:
        await session.flush()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise AppError(
            "A submodel with that slug already exists.",
            status_code=409,
            code="slug_taken",
        ) from exc
    await session.commit()
    await session.refresh(submodel)
    return submodel


async def list_submodels(session: AsyncSession, *, published_only: bool) -> list[Submodel]:
    stmt = select(Submodel).order_by(Submodel.name.asc())
    if published_only:
        stmt = stmt.where(Submodel.status == SubmodelStatus.published)
    rows = (await session.scalars(stmt)).all()
    return list(rows)


async def get_submodel(session: AsyncSession, submodel_id: uuid.UUID) -> Submodel:
    submodel = await session.get(Submodel, submodel_id)
    if submodel is None:
        raise AppError("Submodel not found.", status_code=404, code="not_found")
    return submodel


async def update_submodel(
    session: AsyncSession, *, submodel: Submodel, payload: SubmodelUpdate
) -> Submodel:
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        submodel.name = changes["name"].strip()
    if "description" in changes:
        submodel.description = changes["description"]
    if "purpose" in changes:
        submodel.purpose = changes["purpose"]
    if "config" in changes and changes["config"] is not None:
        submodel.config = {**(submodel.config or {}), **changes["config"]}
    if "status" in changes and changes["status"] is not None:
        submodel.status = changes["status"]
    await session.flush()
    await session.commit()
    await session.refresh(submodel)
    return submodel


async def delete_submodel(
    session: AsyncSession, *, submodel: Submodel, storage: StorageProvider | None = None
) -> None:
    # The runtime role has no DELETE privilege (by design), so the row is
    # removed through the owner (migration) connection. Storage bytes are
    # cleaned up afterwards.
    storage = storage or build_storage()
    video_keys = [v.storage_key for v in (await session.scalars(
        select(SubmodelVideo).where(SubmodelVideo.submodel_id == submodel.id)
    )).all()]
    version_keys = [v.package_storage_key for v in (await session.scalars(
        select(SubmodelVersion).where(SubmodelVersion.submodel_id == submodel.id)
    )).all() if v.package_storage_key]

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    owner = create_async_engine(settings.database_migration_url, poolclass=NullPool)
    try:
        async with owner.begin() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("DELETE FROM submodels WHERE id = :id"),
                {"id": submodel.id},
            )
    finally:
        await owner.dispose()

    for key in video_keys + version_keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001
            logger.exception("submodel_storage_delete_failed", key=key)


async def publish_submodel(session: AsyncSession, *, submodel: Submodel, user: User) -> Submodel:
    if submodel.status not in (SubmodelStatus.trained, SubmodelStatus.published):
        raise AppError(
            "Only a trained submodel can be published.",
            status_code=409,
            code="not_trained",
        )
    submodel.status = SubmodelStatus.published
    submodel.published_at = _now()
    submodel.published_by = user.id
    await session.flush()
    await session.commit()
    await session.refresh(submodel)
    return submodel


async def unpublish_submodel(session: AsyncSession, *, submodel: Submodel) -> Submodel:
    if submodel.status == SubmodelStatus.published:
        submodel.status = SubmodelStatus.trained
        submodel.published_at = None
        submodel.published_by = None
    await session.flush()
    await session.commit()
    await session.refresh(submodel)
    return submodel


async def counts(session: AsyncSession, submodel_id: uuid.UUID) -> tuple[int, int]:
    videos = (
        await session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(SubmodelVideo).where(
                SubmodelVideo.submodel_id == submodel_id
            )
        )
    ) or 0
    workflows = (
        await session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(SubmodelWorkflow).where(
                SubmodelWorkflow.submodel_id == submodel_id
            )
        )
    ) or 0
    return int(videos), int(workflows)


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


async def upload_video(
    session: AsyncSession,
    *,
    submodel: Submodel,
    title: str,
    filename: str,
    mime_type: str,
    data: bytes,
    user: User,
    storage: StorageProvider,
) -> SubmodelVideo:
    sha = hashlib.sha256(data).hexdigest()
    key = f"submodels/{submodel.id}/videos/{uuid.uuid4().hex}"
    await storage.save(key, data)
    video = SubmodelVideo(
        submodel_id=submodel.id,
        title=title.strip() or filename,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        storage_key=key,
        sha256=sha,
        uploaded_by=user.id,
    )
    session.add(video)
    await session.flush()
    await session.commit()
    await session.refresh(video)
    return video


async def get_video(session: AsyncSession, video_id: uuid.UUID) -> SubmodelVideo:
    video = await session.get(SubmodelVideo, video_id)
    if video is None:
        raise AppError("Video not found.", status_code=404, code="not_found")
    return video


async def delete_video(
    session: AsyncSession, *, video: SubmodelVideo, storage: StorageProvider
) -> None:
    await storage.delete(video.storage_key)
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    owner = create_async_engine(settings.database_migration_url, poolclass=NullPool)
    try:
        async with owner.begin() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("DELETE FROM submodel_videos WHERE id = :id"),
                {"id": video.id},
            )
    finally:
        await owner.dispose()
    await session.commit()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def create_job(
    session: AsyncSession,
    *,
    submodel_id: uuid.UUID,
    kind: str,
    video_id: uuid.UUID | None,
    user: User,
) -> SubmodelJob:
    job = SubmodelJob(
        submodel_id=submodel_id,
        video_id=video_id,
        kind=kind,
        status=SubmodelJobStatus.queued,
        created_by=user.id,
    )
    session.add(job)
    await session.flush()
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> SubmodelJob:
    job = await session.get(SubmodelJob, job_id)
    if job is None:
        raise AppError("Job not found.", status_code=404, code="not_found")
    return job


async def _set_job(
    session: AsyncSession,
    job: SubmodelJob,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    if status is not None:
        job.status = status
        if status == SubmodelJobStatus.running and job.started_at is None:
            job.started_at = _now()
        if status in (SubmodelJobStatus.succeeded, SubmodelJobStatus.failed):
            job.finished_at = _now()
    if progress is not None:
        job.progress = max(0, min(100, progress))
    if message is not None:
        job.message = message
    if error is not None:
        job.error = error
    if result is not None:
        job.result = result
    await session.flush()
    await session.commit()


# ---------------------------------------------------------------------------
# Analysis (real)
# ---------------------------------------------------------------------------


async def analyze_video(
    session: AsyncSession,
    *,
    submodel: Submodel,
    video: SubmodelVideo,
    user: User,
    storage: StorageProvider,
    ai: AIProvider,
) -> SubmodelJob:
    job = await create_job(
        session, submodel_id=submodel.id, kind=SubmodelJobKind.analyze, video_id=video.id, user=user
    )
    # Run synchronously-ish within the request for honesty of status; for very
    # large videos this should be moved to a worker, but the status is real.
    await _set_job(
        session, job, status=SubmodelJobStatus.running, progress=5, message="Preparing video"
    )
    video.status = SubmodelVideoStatus.analyzing
    await session.commit()

    try:
        data = await storage.read(video.storage_key)
        suffix = Path(video.filename).suffix or ".mp4"
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / f"video{suffix}"
            video_path.write_bytes(data)
            frames_dir = Path(tmp) / "frames"

            await _set_job(session, job, progress=15, message="Extracting metadata")
            duration = await ffprobe_duration(video_path)
            await _set_job(session, job, progress=25, message="Extracting frames")
            frame_count = await extract_frames(video_path, frames_dir)

            await _set_job(session, job, progress=45, message="Transcribing audio")
            transcript = await _transcribe(video_path, ai)

            await _set_job(session, job, progress=70, message="Extracting workflow")
            analysis, workflow = await _extract_workflow(
                ai, transcript=transcript, frame_count=frame_count, title=video.title
            )

            video.duration_seconds = duration
            video.frame_count = frame_count
            video.transcript = transcript
            video.analysis = analysis
            video.status = SubmodelVideoStatus.analyzed
            video.error_message = None

            if workflow is not None:
                session.add(
                    SubmodelWorkflow(
                        submodel_id=submodel.id,
                        source_video_id=video.id,
                        name=workflow.get("name", f"Workflow from {video.title}"),
                        description=workflow.get("description"),
                        steps=workflow.get("steps", []),
                    )
                )

            await session.flush()
            await _set_job(
                session,
                job,
                status=SubmodelJobStatus.succeeded,
                progress=100,
                message="Analysis complete",
                result={
                    "duration_seconds": duration,
                    "frame_count": frame_count,
                    "transcript_chars": len(transcript or ""),
                    "workflow_created": workflow is not None,
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("submodel_analyze_failed", submodel_id=str(submodel.id))
        video.status = SubmodelVideoStatus.failed
        video.error_message = str(exc)[:1000]
        await session.commit()
        await _set_job(
            session,
            job,
            status=SubmodelJobStatus.failed,
            progress=job.progress,
            error=str(exc)[:1000],
        )
    return job


async def _transcribe(video_path: Path, ai: AIProvider) -> str:
    settings = get_settings()
    if not settings.ai_api_key:
        raise AppError(
            "Transcription requires a configured AI provider. Set AI_API_KEY "
            "(OpenAI-compatible, with Whisper support) and retry.",
            status_code=503,
            code="transcription_unavailable",
        )
    # Use the OpenAI client directly for audio transcription.
    try:
        import openai

        client = openai.OpenAI(api_key=settings.ai_api_key, base_url=settings.ai_base_url)
        with video_path.open("rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1", file=f
            )
        text = getattr(result, "text", None) or str(result)
        if not text.strip():
            raise AppError(
            "Transcription returned no text.", status_code=502, code="empty_transcript"
        )
        return text.strip()
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            f"Transcription failed: {str(exc)[:300]}",
            status_code=502,
            code="transcription_failed",
        ) from exc


async def _extract_workflow(
    ai: AIProvider, *, transcript: str, frame_count: int, title: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not transcript:
        return {"frames": frame_count, "transcript": ""}, None

    prompt = (
        "You are converting a software demonstration transcript into a structured "
        "workflow. Return ONLY valid JSON with keys: name (string), description "
        "(string), steps (array of objects with keys: order (int), action (string), "
        "target (string), value (string or null), note (string or null)).\n\n"
        f"Video title: {title}\n"
        f"Frames extracted: {frame_count}\n"
        f"Transcript:\n{transcript[:6000]}"
    )
    messages = [ChatMessage(role="user", content=prompt)]
    try:
        completion = await ai.complete(messages, max_tokens=1200, temperature=0.0)
        raw = completion.content.strip()
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            f"Workflow extraction failed: {str(exc)[:300]}",
            status_code=502,
            code="extraction_failed",
        ) from exc

    # Strip code fences if present.
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to a single-step workflow carrying the raw text.
        data = {
            "name": f"Workflow from {title}",
            "description": "Extracted workflow (unstructured).",
            "steps": [
                {
                    "order": 1,
                    "action": "follow",
                    "target": "demonstration",
                    "value": None,
                    "note": raw[:500],
                }
            ],
        }
    if not isinstance(data, dict) or "steps" not in data:
        data = {"name": f"Workflow from {title}", "description": "", "steps": []}
    analysis = {
        "frames": frame_count,
        "transcript": transcript[:2000],
        "raw_extraction": raw[:1000],
    }
    return analysis, data


# ---------------------------------------------------------------------------
# Training (real)
# ---------------------------------------------------------------------------


async def train_submodel(
    session: AsyncSession,
    *,
    submodel: Submodel,
    user: User,
    storage: StorageProvider,
    ai: AIProvider,
) -> SubmodelJob:
    job = await create_job(
        session, submodel_id=submodel.id, kind=SubmodelJobKind.train, video_id=None, user=user
    )
    await _set_job(
        session, job, status=SubmodelJobStatus.running, progress=5, message="Preparing training"
    )
    submodel.status = SubmodelStatus.training
    await session.commit()

    try:
        videos = list(
            (
                await session.scalars(
                    select(SubmodelVideo).where(SubmodelVideo.submodel_id == submodel.id)
                )
            ).all()
        )
        if not videos:
            raise AppError("No training videos uploaded.", status_code=409, code="no_videos")

        # Analyze any videos that are not yet analyzed.
        total = len(videos)
        for idx, video in enumerate(videos):
            if video.status != SubmodelVideoStatus.analyzed:
                await _set_job(
                    session,
                    job,
                    progress=10 + int(60 * idx / total),
                    message=f"Analyzing video {idx + 1} of {total}",
                )
                # Re-run analysis inline.
                data = await storage.read(video.storage_key)
                suffix = Path(video.filename).suffix or ".mp4"
                with tempfile.TemporaryDirectory() as tmp:
                    vpath = Path(tmp) / f"video{suffix}"
                    vpath.write_bytes(data)
                    fdir = Path(tmp) / "frames"
                    dur = await ffprobe_duration(vpath)
                    fc = await extract_frames(vpath, fdir)
                    transcript = await _transcribe(vpath, ai)
                    analysis, workflow = await _extract_workflow(
                        ai, transcript=transcript, frame_count=fc, title=video.title
                    )
                    video.duration_seconds = dur
                    video.frame_count = fc
                    video.transcript = transcript
                    video.analysis = analysis
                    video.status = SubmodelVideoStatus.analyzed
                    video.error_message = None
                    if workflow is not None:
                        session.add(
                            SubmodelWorkflow(
                                submodel_id=submodel.id,
                                source_video_id=video.id,
                                name=workflow.get("name", f"Workflow from {video.title}"),
                                description=workflow.get("description"),
                                steps=workflow.get("steps", []),
                            )
                        )
                    await session.flush()

        await _set_job(session, job, progress=80, message="Finalizing submodel")
        submodel.version += 1
        submodel.status = SubmodelStatus.trained
        # Record a version row.
        version = SubmodelVersion(
            submodel_id=submodel.id,
            version=submodel.version,
            manifest={
                "name": submodel.name,
                "workflows": (await session.scalars(
                    select(SubmodelWorkflow.id).where(SubmodelWorkflow.submodel_id == submodel.id)
                )).all().__len__(),
            },
            created_by=user.id,
        )
        session.add(version)
        await session.flush()
        await session.commit()

        await _set_job(
            session,
            job,
            status=SubmodelJobStatus.succeeded,
            progress=100,
            message="Training complete",
            result={"version": submodel.version},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("submodel_train_failed", submodel_id=str(submodel.id))
        submodel.status = SubmodelStatus.failed
        await session.commit()
        await _set_job(
            session,
            job,
            status=SubmodelJobStatus.failed,
            progress=job.progress,
            error=str(exc)[:1000],
        )
    return job


# ---------------------------------------------------------------------------
# Test (real)
# ---------------------------------------------------------------------------


async def test_submodel(
    session: AsyncSession,
    *,
    submodel: Submodel,
    instruction: str,
    ai: AIProvider,
) -> tuple[str, list[str], str]:
    workflows = list(
        (
            await session.scalars(
                select(SubmodelWorkflow).where(SubmodelWorkflow.submodel_id == submodel.id)
            )
        ).all()
    )
    if not workflows:
        raise AppError(
            "This submodel has no workflows yet. Train it first.",
            status_code=409,
            code="no_workflows",
        )
    context_parts = []
    for w in workflows:
        context_parts.append(f"Workflow: {w.name}\nSteps: {json.dumps(w.steps)}")
    prompt = (
        "You are a specialized assistant. Use the demonstrated workflows below to "
        "answer the user's instruction. Cite which workflow you followed.\n\n"
        + "\n\n".join(context_parts)
        + f"\n\nUser instruction: {instruction}"
    )
    completion = await ai.complete(
        [ChatMessage(role="user", content=prompt)], max_tokens=800, temperature=0.0
    )
    followed = [w.name for w in workflows]
    return completion.content, followed, completion.model


# ---------------------------------------------------------------------------
# Download package (real)
# ---------------------------------------------------------------------------


async def build_download_package(
    session: AsyncSession,
    *,
    submodel: Submodel,
    user: User,
    storage: StorageProvider,
) -> tuple[bytes, str]:
    workflows = list(
        (
            await session.scalars(
                select(SubmodelWorkflow).where(SubmodelWorkflow.submodel_id == submodel.id)
            )
        ).all()
    )
    videos = list(
        (
            await session.scalars(
                select(SubmodelVideo).where(SubmodelVideo.submodel_id == submodel.id)
            )
        ).all()
    )
    manifest = {
        "name": submodel.name,
        "slug": submodel.slug,
        "version": submodel.version,
        "purpose": submodel.purpose,
        "description": submodel.description,
        "workflows": [w.name for w in workflows],
        "video_count": len(videos),
        "exported_at": _now().isoformat(),
        "exported_by": str(user.id),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("config.json", json.dumps(submodel.config or {}, indent=2))
        zf.writestr(
            "workflows.json",
            json.dumps(
                [
                    {"name": w.name, "description": w.description, "steps": w.steps}
                    for w in workflows
                ],
                indent=2,
            ),
        )
        zf.writestr(
            "README.txt",
            f"WorkMeta AI submodel: {submodel.name}\n"
            f"Version: {submodel.version}\n"
            f"Install via the WorkMeta admin console.\n",
        )

    data = buf.getvalue()
    key = f"submodels/{submodel.id}/packages/{submodel.version}-{uuid.uuid4().hex}.zip"
    await storage.save(key, data)

    version = SubmodelVersion(
        submodel_id=submodel.id,
        version=submodel.version,
        manifest=manifest,
        package_storage_key=key,
        created_by=user.id,
    )
    session.add(version)
    await session.commit()

    filename = f"{submodel.slug}-v{submodel.version}.zip"
    return data, filename


# ---------------------------------------------------------------------------
# Install (register an uploaded package)
# ---------------------------------------------------------------------------


async def install_package(
    session: AsyncSession,
    *,
    data: bytes,
    user: User,
) -> Submodel:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            config = json.loads(zf.read("config.json")) if "config.json" in zf.namelist() else {}
            workflows_raw = (
                json.loads(zf.read("workflows.json"))
                if "workflows.json" in zf.namelist()
                else []
            )
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise AppError(
            "Invalid submodel package.", status_code=422, code="invalid_package"
        ) from exc

    name = manifest.get("name") or "Imported submodel"
    slug = _slugify(manifest.get("slug") or name)
    # Ensure unique slug.
    existing = await session.scalar(select(Submodel).where(Submodel.slug == slug))
    if existing is not None:
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    submodel = Submodel(
        name=name,
        slug=slug,
        description=manifest.get("description"),
        purpose=manifest.get("purpose"),
        config=config or {},
        status=SubmodelStatus.trained,
        version=int(manifest.get("version", 1)),
        created_by=user.id,
    )
    session.add(submodel)
    await session.flush()
    for w in workflows_raw:
        session.add(
            SubmodelWorkflow(
                submodel_id=submodel.id,
                name=w.get("name", "Workflow"),
                description=w.get("description"),
                steps=w.get("steps", []),
            )
        )
    await session.flush()
    await session.commit()
    await session.refresh(submodel)
    return submodel
