"""WorkMeta AI API entrypoint.

Your Business. Your AI. Your Way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

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
from app.db.session import dispose_engine

settings = get_settings()
logger = structlog.get_logger(__name__)


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
    try:
        yield
    finally:
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
