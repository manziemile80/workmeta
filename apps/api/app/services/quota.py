"""Subscription entitlements, concurrency-safe quota reservation and limits.

Token/request limits are enforced through an atomic conditional ``UPDATE`` on
``usage_counters``. Two concurrent requests cannot both consume the last unit:
PostgreSQL serialises the row update and the second statement sees the first
committed value. The counter row is created in the same transaction as the
business operation, so a provider failure or cancelled request releases its
reservation automatically.

Resource limits (assistants, documents, storage) are enforced around the
count-and-insert. A transaction-scoped advisory lock keyed by the organization
serialises concurrent uploads/creates, closing the check-then-insert race.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.assistant import Assistant
from app.models.document import Document
from app.models.enums import SubscriptionStatus
from app.models.subscription_plan import SubscriptionPlan
from app.models.usage_counter import UsageCounter
from app.services import billing

_ACTIVE_STATUSES = {
    SubscriptionStatus.trialing.value,
    SubscriptionStatus.active.value,
    SubscriptionStatus.past_due.value,
}


@dataclass(slots=True)
class QuotaReservation:
    org_id: uuid.UUID
    period_start: datetime
    reserved_tokens: int


@dataclass(slots=True)
class QuotaSnapshot:
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


def _quota_error(
    *,
    code: str,
    message: str,
    status_code: int,
    plan: SubscriptionPlan,
    limit_type: str,
    used: int,
    limit: int | None,
    requested: int = 0,
) -> AppError:
    return AppError(
        message,
        status_code=status_code,
        code=code,
        details={
            "limit_type": limit_type,
            "plan_code": plan.code,
            "plan_name": plan.name,
            "used": used,
            "limit": limit,
            "requested": requested,
        },
    )


def _ensure_mutations_allowed(context: billing.SubscriptionContext) -> None:
    if context.subscription.status == SubscriptionStatus.expired.value:
        raise AppError(
            "This organization's subscription has expired. Renew the plan to "
            "continue using AI features.",
            status_code=403,
            code="subscription_expired",
            details={
                "plan_code": context.plan.code,
                "status": context.subscription.status,
            },
        )
    if context.subscription.status not in _ACTIVE_STATUSES:
        raise AppError(
            "This organization's subscription is not active.",
            status_code=403,
            code="subscription_inactive",
            details={
                "plan_code": context.plan.code,
                "status": context.subscription.status,
            },
        )


async def _ensure_counter(
    session: AsyncSession, context: billing.SubscriptionContext
) -> UsageCounter:
    subscription = context.subscription
    await session.execute(
        text(
            """
            INSERT INTO usage_counters
                (id, org_id, period_start, period_end)
            VALUES
                (gen_random_uuid(), CAST(:org_id AS uuid),
                 CAST(:period_start AS timestamptz),
                 CAST(:period_end AS timestamptz))
            ON CONFLICT (org_id, period_start) DO UPDATE
                SET period_end = EXCLUDED.period_end
            """
        ),
        {
            "org_id": str(subscription.org_id),
            "period_start": subscription.current_period_start,
            "period_end": subscription.current_period_end,
        },
    )
    counter = await session.scalar(
        select(UsageCounter).where(
            UsageCounter.org_id == subscription.org_id,
            UsageCounter.period_start == subscription.current_period_start,
        )
    )
    if counter is None:  # pragma: no cover - defensive
        raise AppError(
            "Usage accounting is temporarily unavailable.",
            status_code=503,
            code="usage_counter_unavailable",
        )
    return counter


async def reserve_ai_request(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    estimated_tokens: int,
    operation: str,
) -> QuotaReservation:
    """Atomically reserve one AI request and an estimated token amount."""
    tokens = max(0, estimated_tokens)
    context = await billing.ensure_subscription(session, org_id)
    _ensure_mutations_allowed(context)
    await _ensure_counter(session, context)

    subscription = context.subscription
    plan = context.plan
    result = await session.execute(
        text(
            """
            UPDATE usage_counters
            SET requests_used = requests_used + 1,
                tokens_used = tokens_used + CAST(:tokens AS bigint),
                updated_at = now()
            WHERE org_id = CAST(:org_id AS uuid)
              AND period_start = CAST(:period_start AS timestamptz)
              AND (CAST(:request_limit AS integer) IS NULL
                   OR requests_used + 1 <= CAST(:request_limit AS integer))
              AND (CAST(:token_limit AS bigint) IS NULL
                   OR tokens_used + CAST(:tokens AS bigint) <= CAST(:token_limit AS bigint))
            RETURNING requests_used, tokens_used
            """
        ),
        {
            "org_id": str(org_id),
            "period_start": subscription.current_period_start,
            "tokens": tokens,
            "request_limit": plan.monthly_request_allowance,
            "token_limit": plan.monthly_token_allowance,
        },
    )
    row = result.first()
    if row is None:
        current = (
            await session.execute(
                text(
                    """
                    SELECT requests_used, tokens_used
                    FROM usage_counters
                    WHERE org_id = CAST(:org_id AS uuid)
                      AND period_start = CAST(:period_start AS timestamptz)
                    """
                ),
                {
                    "org_id": str(org_id),
                    "period_start": subscription.current_period_start,
                },
            )
        ).mappings().one()
        if (
            plan.monthly_request_allowance is not None
            and int(current["requests_used"]) + 1
            > plan.monthly_request_allowance
        ):
            raise _quota_error(
                code="request_quota_exceeded",
                message=(
                    "This organization has reached its monthly AI request "
                    "allowance. Upgrade the plan to continue."
                ),
                status_code=429,
                plan=plan,
                limit_type="requests",
                used=int(current["requests_used"]),
                limit=plan.monthly_request_allowance,
                requested=1,
            )
        raise _quota_error(
            code="token_quota_exceeded",
            message=(
                "This organization has reached its monthly AI token allowance. "
                "Upgrade the plan to continue."
            ),
            status_code=429,
            plan=plan,
            limit_type="tokens",
            used=int(current["tokens_used"]),
            limit=plan.monthly_token_allowance,
            requested=tokens,
        )

    # A failed business transaction rolls this reservation back with it.
    return QuotaReservation(
        org_id=org_id,
        period_start=subscription.current_period_start,
        reserved_tokens=tokens,
    )


async def reconcile_ai_tokens(
    session: AsyncSession, reservation: QuotaReservation, actual_tokens: int
) -> None:
    """Replace an estimate with the provider-reported token count."""
    actual = max(0, actual_tokens)
    await session.execute(
        text(
            """
            UPDATE usage_counters
            SET tokens_used = GREATEST(
                    tokens_used - CAST(:reserved AS bigint) + CAST(:actual AS bigint), 0
                ),
                updated_at = now()
            WHERE org_id = CAST(:org_id AS uuid)
              AND period_start = CAST(:period_start AS timestamptz)
            """
        ),
        {
            "org_id": str(reservation.org_id),
            "period_start": reservation.period_start,
            "reserved": reservation.reserved_tokens,
            "actual": actual,
        },
    )


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def guard_assistant_create(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    context = await billing.ensure_subscription(session, org_id)
    _ensure_mutations_allowed(context)
    limit = context.plan.max_assistants
    if limit is None:
        return
    await _advisory_lock(session, f"assistants:{org_id}")
    used = int(
        await session.scalar(
            select(func.count(Assistant.id)).where(
                Assistant.org_id == org_id,
                Assistant.status != "archived",
            )
        )
        or 0
    )
    if used >= limit:
        raise _quota_error(
            code="assistant_limit_reached",
            message=(
                "This organization has reached its assistant limit. "
                "Upgrade the plan to create more assistants."
            ),
            status_code=403,
            plan=context.plan,
            limit_type="assistants",
            used=used,
            limit=limit,
            requested=1,
        )


async def guard_document_upload(
    session: AsyncSession, org_id: uuid.UUID, *, size_bytes: int
) -> None:
    context = await billing.ensure_subscription(session, org_id)
    _ensure_mutations_allowed(context)
    document_limit = context.plan.max_documents
    storage_limit = context.plan.max_storage_bytes
    if document_limit is None and storage_limit is None:
        return

    await _advisory_lock(session, f"documents:{org_id}")
    used_documents = int(
        await session.scalar(
            select(func.count(Document.id)).where(Document.org_id == org_id)
        )
        or 0
    )
    if document_limit is not None and used_documents >= document_limit:
        raise _quota_error(
            code="document_limit_reached",
            message=(
                "This organization has reached its document limit. "
                "Upgrade the plan or delete unused documents."
            ),
            status_code=403,
            plan=context.plan,
            limit_type="documents",
            used=used_documents,
            limit=document_limit,
            requested=1,
        )

    if storage_limit is not None:
        used_bytes = int(
            await session.scalar(
                select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
                    Document.org_id == org_id
                )
            )
            or 0
        )
        if used_bytes + size_bytes > storage_limit:
            raise _quota_error(
                code="storage_limit_reached",
                message=(
                    "This organization has reached its storage limit. "
                    "Upgrade the plan or delete unused documents."
                ),
                status_code=403,
                plan=context.plan,
                limit_type="storage",
                used=used_bytes,
                limit=storage_limit,
                requested=size_bytes,
            )


async def usage_snapshot(
    session: AsyncSession, org_id: uuid.UUID
) -> QuotaSnapshot:
    context = await billing.ensure_subscription(session, org_id)
    counter = await _ensure_counter(session, context)
    subscription = context.subscription
    plan = context.plan

    assistants_used = int(
        await session.scalar(
            select(func.count(Assistant.id)).where(
                Assistant.org_id == org_id,
                Assistant.status != "archived",
            )
        )
        or 0
    )
    documents_used = int(
        await session.scalar(
            select(func.count(Document.id)).where(Document.org_id == org_id)
        )
        or 0
    )
    storage_used = int(
        await session.scalar(
            select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.org_id == org_id
            )
        )
        or 0
    )
    return QuotaSnapshot(
        org_id=org_id,
        plan_code=plan.code,
        plan_name=plan.name,
        subscription_status=subscription.status,
        billing_interval=subscription.billing_interval,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
        tokens_used=counter.tokens_used,
        token_allowance=plan.monthly_token_allowance,
        requests_used=counter.requests_used,
        request_allowance=plan.monthly_request_allowance,
        assistants_used=assistants_used,
        assistant_allowance=plan.max_assistants,
        documents_used=documents_used,
        document_allowance=plan.max_documents,
        storage_bytes_used=storage_used,
        storage_allowance=plan.max_storage_bytes,
    )
