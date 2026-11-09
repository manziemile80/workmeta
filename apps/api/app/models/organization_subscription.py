"""One independently managed subscription per organization.

The subscription row is mutable state (status transitions), not an audit trail.
Every change is written to ``audit_logs`` in the same transaction and every
payment is preserved in ``payment_transactions``. The runtime role can update
this row inside its own tenant, but cannot delete it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import BillingInterval, SubscriptionStatus


class OrganizationSubscription(Base):
    __tablename__ = "organization_subscriptions"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_organization_subscriptions_org_id"),
        CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'canceled', 'expired')",
            name="status_valid",
        ),
        CheckConstraint(
            "billing_interval IN ('monthly', 'annual')",
            name="billing_interval_valid",
        ),
        CheckConstraint(
            "current_period_end > current_period_start",
            name="period_order_valid",
        ),
        Index(
            "ix_organization_subscriptions_status_period_end",
            "status",
            "current_period_end",
        ),
        Index("ix_organization_subscriptions_plan_id", "plan_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    billing_interval: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=BillingInterval.monthly.value,
        server_default=BillingInterval.monthly.value,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubscriptionStatus.active.value,
        server_default=SubscriptionStatus.active.value,
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="mock", server_default="mock"
    )
    provider_customer_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    past_due_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
