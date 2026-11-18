"""WorkMeta AI API entrypoint.

Your Business. Your AI. Your Way.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodyLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.session import SessionFactory, dispose_engine
from app.workers.embedded import run_embedded_worker

settings = get_settings()
logger = structlog.get_logger(__name__)

# The Alembic revision the running code expects the database to be at. The
# application refuses to start (and therefore refuses to serve traffic) when
# the database is behind this revision, instead of failing later with a
# confusing 500 on the first request that touches a missing column.
EXPECTED_ALEMBIC_REVISION = "20261116_0015"

# How long the shutdown waits for the embedded worker to finish its current
# job before the task is cancelled.
_WORKER_SHUTDOWN_GRACE_SECONDS = 5.0


def _log_worker_exit(task: asyncio.Task[None]) -> None:
    """Report a worker loop that ended on its own (a crash, not a shutdown)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("embedded_worker_failed", error=str(exc))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings)
    if settings.is_production and not settings.session_secret:
        raise RuntimeError("SESSION_SECRET must be set in production")

    logger.info(
        "api_starting",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )

    # Fail fast on schema drift: never serve traffic against a database that
    # is behind the code's expected Alembic revision.
    try:
        async with SessionFactory() as session:
            col = (await session.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='submodels' AND column_name='training_error'"
                )
            )).scalar_one_or_none()
            if col is None:
                raise RuntimeError(
                    "Database schema is missing submodels.training_error. "
                    "Run `alembic upgrade head`."
                )
            logger.info("schema_compatibility_ok")
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("schema_compatibility_check_failed", error=str(exc))
        raise RuntimeError("Could not verify database schema compatibility.") from exc

    stop_worker = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None
    if settings.embedded_worker_enabled:
        # Single-instance deployments (Render Free) have no worker service,
        # so the API drains its own queue. See app/workers/embedded.py.
        worker_task = asyncio.create_task(run_embedded_worker(stop_worker))
        worker_task.add_done_callback(_log_worker_exit)

    try:
        yield
    finally:
        stop_worker.set()
        if worker_task is not None:
            _, pending = await asyncio.wait(
                {worker_task}, timeout=_WORKER_SHUTDOWN_GRACE_SECONDS
            )
            if pending:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
            elif not worker_task.cancelled():
                # The loop stopped on its own; the callback logged the reason.
                worker_task.exception()
        await dispose_engine()
        logger.info("api_stopped")


app = FastAPI(
    title=settings.app_name,
    description=(
        "Secure multi-tenant AI assistant platform. "
        "Your Business. Your AI. Your Way."
    ),
    version=settings.app_version,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)

# Middleware is added inside-out: the last one added runs first.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-CSRF-Token"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)

register_exception_handlers(app)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": settings.app_name,
            "tagline": "Your Business. Your AI. Your Way.",
            "version": settings.app_version,
            "environment": settings.app_env,
            "health": f"{settings.api_v1_prefix}/health",
            "readiness": f"{settings.api_v1_prefix}/ready",
            "docs": "/docs" if settings.docs_enabled else None,
        }
    )
