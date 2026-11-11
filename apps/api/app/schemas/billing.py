"""Billing, plan, payment and quota API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PlanCode = Annotated[
    str, Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
]
Currency = Annotated[str, Field(min_length=3, max_length=3)]


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    monthly_price_minor: int
    annual_price_minor: int
    currency: str
    price_exponent: int
    max_assistants: int | None
    max_documents: int | None
    max_storage_bytes: int | None
    monthly_token_allowance: int | None
    monthly_request_allowance: int | None
    features: list[Any]
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: PlanCode
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    monthly_price_minor: int = Field(default=0, ge=0)
    annual_price_minor: int = Field(default=0, ge=0)
    currency: Currency = "RWF"
    price_exponent: int = Field(default=0, ge=0, le=3)
    max_assistants: int | None = Field(default=None, ge=0)
    max_documents: int | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    monthly_token_allowance: int | None = Field(default=None, ge=0)
    monthly_request_allowance: int | None = Field(default=None, ge=0)
    features: list[str] = Field(default_factory=list)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    monthly_price_minor: int | None = Field(default=None, ge=0)
    annual_price_minor: int | None = Field(default=None, ge=0)
    currency: Currency | None = None
    price_exponent: int | None = Field(default=None, ge=0, le=3)
    max_assistants: int | None = Field(default=None, ge=0)
    max_documents: int | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    max_extensions: int | None = Field(default=None, ge=0)
    monthly_token_allowance: int | None = Field(default=None, ge=0)
    monthly_request_allowance: int | None = Field(default=None, ge=0)
    features: list[str] | None = None
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    plan: PlanRead
    billing_interval: str
    status: str
    provider: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    canceled_at: datetime | None
    cancel_at_period_end: bool
    past_due_since: datetime | None
    created_at: datetime
    updated_at: datetime


class QuotaSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: uuid.UUID
    plan_code: str
    plan_name: str
    subscription_status: str
    billing_interval: str
    period_start: datetime
    period_end: datetime
    tokens_used: int
    token_allowance: int | None
    requests_used: int
    request_allowance: int | None
    assistants_used: int
    assistant_allowance: int | None
    documents_used: int
    document_allowance: int | None
    storage_bytes_used: int
    storage_allowance: int | None


class BillingOverviewRead(BaseModel):
    subscription: SubscriptionRead
    usage: QuotaSnapshotRead


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: PlanCode
    interval: Literal["monthly", "annual"] = "monthly"


class TrialRequest(CheckoutRequest):
    pass


class ConfirmCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=4, max_length=120)


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    subscription_id: uuid.UUID | None
    plan_id: uuid.UUID | None
    provider: str
    provider_reference: str
    provider_event_id: str | None
    kind: str
    status: str
    amount_minor: int
    currency: str
    billing_interval: str | None
    checkout_url: str | None
    invoice_number: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime


class CheckoutResponse(BaseModel):
    immediate: bool
    subscription: SubscriptionRead
    plan: PlanRead
    payment: PaymentRead | None
    checkout_url: str | None
    provider: str
    amount_minor: int
    currency: str


class ConfirmationResponse(BaseModel):
    activated: bool
    payment: PaymentRead
    subscription: SubscriptionRead
    usage: QuotaSnapshotRead


class WebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored"]


class PlatformOrganizationRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    country: str
    status: str
    created_at: datetime
    subscription_id: uuid.UUID | None
    plan_code: str | None
    plan_name: str | None
    subscription_status: str | None
    billing_interval: str | None
    current_period_end: datetime | None


class PlatformSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    organization_name: str
    organization_slug: str
    plan_code: str
    plan_name: str
    billing_interval: str
    status: str
    provider: str
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    past_due_since: datetime | None
    created_at: datetime


class RevenueByPlanRead(BaseModel):
    plan_code: str
    plan_name: str
    gross_minor: int
    payments_count: int


class RevenueSummaryRead(BaseModel):
    currency: str
    gross_minor: int
    refunds_minor: int
    net_minor: int
    payments_count: int
    refunds_count: int
    by_plan: list[RevenueByPlanRead]


class PlatformUsageRead(BaseModel):
    org_id: uuid.UUID
    organization_name: str
    organization_slug: str
    requests: int
    tokens_in: int
    tokens_out: int
    cost_micros: int


class PlatformUsageSummaryRead(BaseModel):
    since: datetime
    until: datetime
    total_requests: int
    total_tokens: int
    total_cost_micros: int
    organizations: list[PlatformUsageRead]
