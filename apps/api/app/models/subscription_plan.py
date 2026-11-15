"""Configurable subscription plans.

Plan limits live in the database, never as constants spread through the
application. The runtime role may read active plans; content is platform
metadata, not tenant knowledge. Platform administrators can edit plans through
the API, and the RLS policy allows writes only in a platform-admin context.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        CheckConstraint(
            "monthly_price_minor >= 0 AND annual_price_minor >= 0",
            name="price_non_negative",
        ),
        CheckConstraint(
            "price_exponent BETWEEN 0 AND 3", name="price_exponent_valid"
        ),
        CheckConstraint(
            "(max_assistants IS NULL OR max_assistants >= 0) AND "
            "(max_documents IS NULL OR max_documents >= 0) AND "
            "(max_storage_bytes IS NULL OR max_storage_bytes >= 0) AND "
            "(monthly_token_allowance IS NULL OR monthly_token_allowance >= 0) AND "
            "(monthly_request_allowance IS NULL OR monthly_request_allowance >= 0)",
            name="limits_non_negative",
        ),
        Index("ix_subscription_plans_active_sort", "is_active", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    monthly_price_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    annual_price_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RWF", server_default="RWF"
    )
    price_exponent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    # NULL means unlimited. Limits are copied from this row by the entitlement
    # service, so changing a plan immediately changes what is enforced.
    max_assistants: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_allowance: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    monthly_request_allowance: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    features: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
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
