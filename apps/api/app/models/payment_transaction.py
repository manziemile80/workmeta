"""Payment and refund transactions.

Card numbers and provider credentials are never stored. The provider reference,
amount, currency and non-sensitive metadata are enough to reconcile payments and
render history/invoices. The unique provider reference is the duplicate-payment
guard; webhook events have their own idempotency table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import PaymentKind, PaymentStatus


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('payment', 'refund')", name="kind_valid"
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="status_valid",
        ),
        CheckConstraint(
            "billing_interval IS NULL OR billing_interval IN ('monthly', 'annual')",
            name="billing_interval_valid",
        ),
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        # Short name: the naming-convention default exceeds PG's 63-char limit.
        ForeignKeyConstraint(
            ["subscription_id"],
            ["organization_subscriptions.id"],
            name="fk_payments_sub_id_org_subs",
            ondelete="SET NULL",
        ),
        Index("ix_payment_transactions_org_created", "org_id", "created_at"),
        Index("ix_payment_transactions_status_created", "status", "created_at"),
        Index("ix_payment_transactions_plan_id", "plan_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True
    )
    provider_event_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentKind.payment.value,
        server_default=PaymentKind.payment.value,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentStatus.pending.value,
        server_default=PaymentStatus.pending.value,
    )
    amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RWF", server_default="RWF"
    )
    billing_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(
        String(40), nullable=True, unique=True
    )
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
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
