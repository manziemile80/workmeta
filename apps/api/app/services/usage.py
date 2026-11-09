"""Usage metering, cost estimation and reporting.

Usage rows are append-only for the runtime role. Cost values are estimates based
on configurable per-million-token prices; they are never billed amounts. Report
queries select aggregates only, so administrators can see token/cost/latency
totals without reading employee conversation content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.rls import set_rls_context
from app.db.session import SessionFactory
from app.models.enums import UsageKind
from app.models.usage_event import UsageEvent

logger = structlog.get_logger(__name__)


def chat_cost_micros(
    prompt_tokens: int, completion_tokens: int, settings: Settings | None = None
) -> int:
    settings = settings or get_settings()
    return int(
        round(
            prompt_tokens * settings.ai_chat_input_cost_per_million
            + completion_tokens * settings.ai_chat_output_cost_per_million
        )
    )


def embedding_cost_micros(
    tokens: int, settings: Settings | None = None
) -> int:
    settings = settings or get_settings()
    return int(round(tokens * settings.ai_embedding_cost_per_million))


async def record_provider_error(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    assistant_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    operation: str,
    error_type: str,
    retryable: bool,
    model: str | None,
    latency_ms: int | None = None,
) -> None:
    """Persist a provider failure even when the request transaction rolls back."""
    try:
        async with SessionFactory() as session:
            await set_rls_context(session, org_id=org_id, user_id=user_id)
            session.add(
                UsageEvent(
                    org_id=org_id,
                    user_id=user_id,
                    assistant_id=assistant_id,
                    conversation_id=conversation_id,
                    kind=UsageKind.provider_error.value,
                    tokens_in=0,
                    tokens_out=0,
                    cost_micros=0,
                    meta={
                        "operation": operation,
                        "error_type": error_type,
                        "retryable": retryable,
                        "model": model,
                        "latency_ms": latency_ms,
                    },
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - observability must never mask the error
        logger.exception("provider_error_usage_failed", operation=operation)


@dataclass(slots=True)
class UsageTotals:
    requests: int
    chat_requests: int
    embedding_requests: int
    provider_errors: int
    tokens_in: int
    tokens_out: int
    cost_micros: int
    avg_latency_ms: float | None

    @property
    def cost_usd(self) -> float:
        return round(self.cost_micros / 1_000_000, 6)


@dataclass(slots=True)
class AssistantUsage:
    assistant_id: uuid.UUID | None
    assistant_name: str | None
    requests: int
    chat_requests: int
    embedding_requests: int
    provider_errors: int
    tokens_in: int
    tokens_out: int
    cost_micros: int
    avg_latency_ms: float | None

    @property
    def cost_usd(self) -> float:
        return round(self.cost_micros / 1_000_000, 6)


@dataclass(slots=True)
class IngestionStats:
    total_documents: int
    uploaded: int
    processing: int
    ready: int
    failed: int
    chunk_count: int


@dataclass(slots=True)
class ProviderError:
    created_at: datetime
    assistant_id: uuid.UUID | None
    operation: str | None
    error_type: str | None
    retryable: bool | None
    model: str | None
    latency_ms: int | None


@dataclass(slots=True)
class UsageReport:
    org_id: uuid.UUID
    since: datetime
    until: datetime
    totals: UsageTotals
    by_assistant: list[AssistantUsage]
    ingestion: IngestionStats
    recent_provider_errors: list[ProviderError]


def _range(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    end = until or datetime.now(UTC)
    start = since or (end - timedelta(days=30))
    if start > end:
        raise ValueError("since must be before until")
    return start, end


def _totals(row: Any) -> UsageTotals:
    return UsageTotals(
        requests=int(row["requests"] or 0),
        chat_requests=int(row["chat_requests"] or 0),
        embedding_requests=int(row["embedding_requests"] or 0),
        provider_errors=int(row["provider_errors"] or 0),
        tokens_in=int(row["tokens_in"] or 0),
        tokens_out=int(row["tokens_out"] or 0),
        cost_micros=int(row["cost_micros"] or 0),
        avg_latency_ms=(
            round(float(row["avg_latency_ms"]), 1)
            if row["avg_latency_ms"] is not None
            else None
        ),
    )


async def build_report(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    since: datetime | None = None,
    until: datetime | None = None,
    assistant_id: uuid.UUID | None = None,
) -> UsageReport:
    start, end = _range(since, until)
    filters = ["org_id = :org_id", "created_at >= :since", "created_at < :until"]
    params: dict[str, object] = {
        "org_id": str(org_id),
        "since": start,
        "until": end,
    }
    if assistant_id is not None:
        filters.append("assistant_id = :assistant_id")
        params["assistant_id"] = str(assistant_id)
    where = " AND ".join(filters)

    latency = (
        "CASE WHEN jsonb_typeof(metadata->'latency_ms') = 'number' "
        "THEN (metadata->>'latency_ms')::numeric END"
    )
    totals_row = (
        await session.execute(
            text(
                f"""
                SELECT
                    count(*) AS requests,
                    count(*) FILTER (WHERE kind = 'chat') AS chat_requests,
                    count(*) FILTER (WHERE kind = 'embedding') AS embedding_requests,
                    count(*) FILTER (WHERE kind = 'provider_error') AS provider_errors,
                    COALESCE(sum(tokens_in), 0) AS tokens_in,
                    COALESCE(sum(tokens_out), 0) AS tokens_out,
                    COALESCE(sum(cost_micros), 0) AS cost_micros,
                    avg({latency}) AS avg_latency_ms
                FROM usage_events
                WHERE {where}
                """
            ),
            params,
        )
    ).mappings().one()
    totals = _totals(totals_row)

    assistant_filters = [
        "u.org_id = :org_id",
        "u.created_at >= :since",
        "u.created_at < :until",
    ]
    if assistant_id is not None:
        assistant_filters.append("u.assistant_id = :assistant_id")
    assistant_where = " AND ".join(assistant_filters)
    by_assistant_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    u.assistant_id,
                    a.name AS assistant_name,
                    count(*) AS requests,
                    count(*) FILTER (WHERE u.kind = 'chat') AS chat_requests,
                    count(*) FILTER (WHERE u.kind = 'embedding') AS embedding_requests,
                    count(*) FILTER (WHERE u.kind = 'provider_error') AS provider_errors,
                    COALESCE(sum(u.tokens_in), 0) AS tokens_in,
                    COALESCE(sum(u.tokens_out), 0) AS tokens_out,
                    COALESCE(sum(u.cost_micros), 0) AS cost_micros,
                    avg(
                        CASE WHEN jsonb_typeof(u.metadata->'latency_ms') = 'number'
                        THEN (u.metadata->>'latency_ms')::numeric END
                    ) AS avg_latency_ms
                FROM usage_events u
                LEFT JOIN assistants a
                  ON a.id = u.assistant_id AND a.org_id = u.org_id
                WHERE {assistant_where}
                GROUP BY u.assistant_id, a.name
                ORDER BY requests DESC, assistant_name ASC NULLS LAST
                """
            ),
            params,
        )
    ).mappings().all()
    by_assistant = [
        AssistantUsage(
            assistant_id=row["assistant_id"],
            assistant_name=row["assistant_name"],
            requests=int(row["requests"] or 0),
            chat_requests=int(row["chat_requests"] or 0),
            embedding_requests=int(row["embedding_requests"] or 0),
            provider_errors=int(row["provider_errors"] or 0),
            tokens_in=int(row["tokens_in"] or 0),
            tokens_out=int(row["tokens_out"] or 0),
            cost_micros=int(row["cost_micros"] or 0),
            avg_latency_ms=(
                round(float(row["avg_latency_ms"]), 1)
                if row["avg_latency_ms"] is not None
                else None
            ),
        )
        for row in by_assistant_rows
    ]

    doc_filters = ["org_id = :org_id"]
    doc_params: dict[str, object] = {"org_id": str(org_id)}
    if assistant_id is not None:
        doc_filters.append("assistant_id = :assistant_id")
        doc_params["assistant_id"] = str(assistant_id)
    doc_where = " AND ".join(doc_filters)
    status_rows = (
        await session.execute(
            text(
                f"SELECT status, count(*) AS total FROM documents "
                f"WHERE {doc_where} GROUP BY status"
            ),
            doc_params,
        )
    ).mappings().all()
    statuses = {row["status"]: int(row["total"]) for row in status_rows}
    chunk_count = int(
        (
            await session.scalar(
                text(f"SELECT count(*) FROM document_chunks WHERE {doc_where}"),
                doc_params,
            )
        )
        or 0
    )
    ingestion = IngestionStats(
        total_documents=sum(statuses.values()),
        uploaded=statuses.get("uploaded", 0),
        processing=statuses.get("processing", 0),
        ready=statuses.get("ready", 0),
        failed=statuses.get("failed", 0),
        chunk_count=chunk_count,
    )

    error_rows = (
        await session.execute(
            text(
                f"""
                SELECT created_at, assistant_id, metadata
                FROM usage_events
                WHERE {where} AND kind = 'provider_error'
                ORDER BY created_at DESC
                LIMIT 10
                """
            ),
            params,
        )
    ).mappings().all()
    recent_errors = [
        ProviderError(
            created_at=row["created_at"],
            assistant_id=row["assistant_id"],
            operation=(row["metadata"] or {}).get("operation"),
            error_type=(row["metadata"] or {}).get("error_type"),
            retryable=(row["metadata"] or {}).get("retryable"),
            model=(row["metadata"] or {}).get("model"),
            latency_ms=(row["metadata"] or {}).get("latency_ms"),
        )
        for row in error_rows
    ]

    return UsageReport(
        org_id=org_id,
        since=start,
        until=end,
        totals=totals,
        by_assistant=by_assistant,
        ingestion=ingestion,
        recent_provider_errors=recent_errors,
    )
