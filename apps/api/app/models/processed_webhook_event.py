"""Idempotency ledger for payment-provider webhook events.

The row is inserted in the same transaction as the state change it causes. If
processing fails the transaction (and this row) rolls back, so the provider's
retry can process the event again. A duplicate event inserts no row and is
acknowledged without touching billing state.

This table is platform-level, has no tenant RLS and is append-only for the
runtime role.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", name="uq_processed_webhook_events_provider_event"
        ),
        Index("ix_processed_webhook_events_processed_at", "processed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
