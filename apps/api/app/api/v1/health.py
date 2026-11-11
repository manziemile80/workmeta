"""Operational endpoints: liveness, readiness and Prometheus metrics."""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session, has_pgvector, ping_database
from app.models.enums import IngestionJobStatus
from app.schemas.health import HealthResponse, ReadyResponse

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])

_STARTED_AT = time.monotonic()

# Monotonic counters updated by the request middleware via a tiny module-level
# registry (no Prometheus client dependency).
REQUEST_METRICS: dict[tuple[str, str, str], tuple[int, float]] = {}


def _database_configured(settings) -> bool:
    """True when a real (non-placeholder) database URL is configured."""
    url = (settings.database_url or "").strip()
    return bool(url) and "change-me" not in url


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Return OK when the API process is running (does not touch the database)."""
    settings = get_settings()
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
        database_configured=_database_configured(settings),
        docs_enabled=settings.docs_enabled,
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness probe")
async def ready(
    session: AsyncSession = Depends(get_session),  # noqa: B008 - FastAPI idiom
) -> ReadyResponse:
    """Verify that the API can serve traffic: database reachable, pgvector status."""
    settings = get_settings()
    if not _database_configured(settings):
        # Distinguish "no database configured" from "database unreachable" so
        # operators get an actionable error. No URL or credential is exposed.
        logger.error("database_not_configured")
        raise AppError(
            "The database connection is not configured on this deployment.",
            status_code=503,
            code="database_not_configured",
        )
    try:
        await ping_database(session)
        vector_ready = await has_pgvector(session)
    except Exception as exc:  # noqa: BLE001 - converted into a safe 503 envelope
        logger.exception("readiness_database_error", error_type=type(exc).__name__)
        raise AppError(
            "The database is not reachable.",
            status_code=503,
            code="database_unavailable",
        ) from exc

    if not vector_ready:
        # Informational in Stage 0; Stage 2 (RAG) will require this to be true.
        logger.warning("pgvector_extension_missing")

    return ReadyResponse(
        pgvector=vector_ready,
        version=settings.app_version,
        environment=settings.app_env,
    )


def render_metrics(
    *,
    uptime_seconds: float,
    request_metrics: dict[tuple[str, str, str], tuple[int, float]],
    queue_depths: dict[str, int] | None = None,
    db_size_bytes: int | None = None,
) -> str:
    """Render Prometheus text format. Only aggregates, never tenant content."""
    lines = [
        "# HELP workmeta_uptime_seconds Process uptime.",
        "# TYPE workmeta_uptime_seconds gauge",
        f"workmeta_uptime_seconds {uptime_seconds:.0f}",
        "# HELP workmeta_http_requests_total Completed HTTP requests.",
        "# TYPE workmeta_http_requests_total counter",
    ]
    for (method, path, status), (count, _latency) in sorted(
        request_metrics.items()
    ):
        lines.append(
            f"workmeta_http_requests_total"
            f'{{method="{method}",path="{path}",status="{status}"}} {count}'
        )
    if queue_depths is not None:
        lines.append(
            "# HELP workmeta_ingestion_jobs Gauge by job status."
        )
        lines.append("# TYPE workmeta_ingestion_jobs gauge")
        for status, count in sorted(queue_depths.items()):
            lines.append(
                f'workmeta_ingestion_jobs{{status="{status}"}} {count}'
            )
    if db_size_bytes is not None:
        lines.append("# HELP workmeta_database_size_bytes Database size.")
        lines.append("# TYPE workmeta_database_size_bytes gauge")
        lines.append(f"workmeta_database_size_bytes {db_size_bytes}")
    return "\n".join(lines) + "\n"


@router.get("/metrics", summary="Prometheus metrics (aggregates only)")
async def metrics(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Expose request counters + ingestion queue depth + DB size.

    Metrics are platform-level aggregates (counts and sizes only). No
    document, conversation or billing identifiers are ever exported.
    """
    from app.api.v1.health import REQUEST_METRICS as LOCAL_METRICS

    queue_depths: dict[str, int] = {}
    db_size_bytes: int | None = None
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT status, count(*) FROM ingestion_jobs "
                    "GROUP BY status"
                )
            )
        ).all()
        queue_depths = {row[0]: int(row[1]) for row in rows}
        # Ensure every lifecycle state is present for alerting dashboards.
        for status in (
            IngestionJobStatus.queued.value,
            IngestionJobStatus.running.value,
            IngestionJobStatus.succeeded.value,
            IngestionJobStatus.failed.value,
        ):
            queue_depths.setdefault(status, 0)
    except Exception:  # noqa: BLE001 - metrics must still render counts
        logger.warning("metrics_queue_depth_unavailable")

    try:
        db_size_bytes = int(
            await session.scalar(
                text(
                    "SELECT pg_database_size(current_database())"
                )
            )
            or 0
        )
    except Exception:  # noqa: BLE001 - size is best-effort
        db_size_bytes = None

    body = render_metrics(
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
        request_metrics=LOCAL_METRICS,
        queue_depths=queue_depths,
        db_size_bytes=db_size_bytes,
    )
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
