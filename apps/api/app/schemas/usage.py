"""Usage and analytics API contracts.

Reports expose aggregate counts, token totals, estimated costs and latency.
They never expose conversation content or provider keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UsageTotalsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requests: int
    chat_requests: int
    embedding_requests: int
    provider_errors: int
    tokens_in: int
    tokens_out: int
    cost_micros: int
    cost_usd: float
    avg_latency_ms: float | None


class AssistantUsageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assistant_id: uuid.UUID | None
    assistant_name: str | None
    requests: int
    chat_requests: int
    embedding_requests: int
    provider_errors: int
    tokens_in: int
    tokens_out: int
    cost_micros: int
    cost_usd: float
    avg_latency_ms: float | None


class IngestionStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_documents: int
    uploaded: int
    processing: int
    ready: int
    failed: int
    chunk_count: int


class ProviderErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    assistant_id: uuid.UUID | None
    operation: str | None
    error_type: str | None
    retryable: bool | None
    model: str | None
    latency_ms: int | None


class UsageReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: uuid.UUID
    since: datetime
    until: datetime
    totals: UsageTotalsRead
    by_assistant: list[AssistantUsageRead]
    ingestion: IngestionStatsRead
    recent_provider_errors: list[ProviderErrorRead]
