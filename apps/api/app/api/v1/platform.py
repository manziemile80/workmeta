"""Platform administration for plans, organizations, subscriptions and revenue.

Every endpoint is aggregate or billing metadata only. Platform administrators do
not receive a content bypass: documents, chunks, messages and conversations are
never selected here, and the tenant-content endpoints still require a real
membership.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_platform_admin
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.rls import set_rls_context
from app.db.session import get_session
from app.models.organization import Organization
from app.models.organization_subscription import OrganizationSubscription
from app.models.payment_transaction import PaymentTransaction
from app.models.subscription_plan import SubscriptionPlan
from app.schemas.billing import (
    PaymentRead,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    PlatformOrganizationRead,
    PlatformSubscriptionRead,
    PlatformUsageRead,
    PlatformUsageSummaryRead,
    RevenueByPlanRead,
    RevenueSummaryRead,
)
from app.services import billing
from app.services.payments import PaymentProvider, get_payment_provider

router = APIRouter(prefix="/platform", tags=["platform-admin"])


@router.get(
    "/organizations",
    response_model=list[PlatformOrganizationRead],
    summary="Organizations with subscription summaries",
)
async def list_organizations(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PlatformOrganizationRead]:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    rows = (
        await session.execute(
            select(Organization, OrganizationSubscription, SubscriptionPlan)
            .outerjoin(
                OrganizationSubscription,
                OrganizationSubscription.org_id == Organization.id,
            )
            .outerjoin(
                SubscriptionPlan,
                SubscriptionPlan.id == OrganizationSubscription.plan_id,
            )
            .order_by(Organization.name.asc())
        )
    ).all()
    return [
        PlatformOrganizationRead(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            country=organization.country,
            status=organization.status,
            created_at=organization.created_at,
            subscription_id=(
                subscription.id if subscription is not None else None
            ),
            plan_code=plan.code if plan is not None else None,
            plan_name=plan.name if plan is not None else None,
            subscription_status=(
                subscription.status if subscription is not None else None
            ),
            billing_interval=(
                subscription.billing_interval if subscription is not None else None
            ),
            current_period_end=(
                subscription.current_period_end
                if subscription is not None
                else None
            ),
        )
        for organization, subscription, plan in rows
    ]


@router.get(
    "/plans",
    response_model=list[PlanRead],
    summary="All subscription plans, including inactive ones",
)
async def list_plans(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubscriptionPlan]:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    rows = await session.scalars(
        select(SubscriptionPlan).order_by(
            SubscriptionPlan.sort_order.asc(), SubscriptionPlan.code.asc()
        )
    )
    return list(rows)


@router.post(
    "/plans",
    response_model=PlanRead,
    status_code=201,
    summary="Create a subscription plan",
)
async def create_plan(
    payload: PlanCreate,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubscriptionPlan:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        code=payload.code,
        name=payload.name,
        description=payload.description,
        monthly_price_minor=payload.monthly_price_minor,
        annual_price_minor=payload.annual_price_minor,
        currency=payload.currency,
        price_exponent=payload.price_exponent,
        max_assistants=payload.max_assistants,
        max_documents=payload.max_documents,
        max_storage_bytes=payload.max_storage_bytes,
        max_extensions=payload.max_extensions,
        max_submodels=payload.max_submodels,
        monthly_token_allowance=payload.monthly_token_allowance,
        monthly_request_allowance=payload.monthly_request_allowance,
        features=list(payload.features),
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    session.add(plan)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "A plan with that code already exists.",
            status_code=409,
            code="plan_code_taken",
        ) from exc
    await session.refresh(plan)
    await session.commit()
    return plan


@router.patch(
    "/plans/{plan_id}",
    response_model=PlanRead,
    summary="Update a subscription plan",
)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdate,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubscriptionPlan:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    plan = await session.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise AppError("Plan not found.", status_code=404, code="not_found")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(plan, field, value)
    await session.flush()
    await session.refresh(plan)
    await session.commit()
    return plan


@router.get(
    "/subscriptions",
    response_model=list[PlatformSubscriptionRead],
    summary="Active and historical organization subscriptions",
)
async def list_subscriptions(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    status_filter: Literal[
        "trialing", "active", "past_due", "canceled", "expired"
    ]
    | None = Query(default=None, alias="status"),  # noqa: B008
) -> list[PlatformSubscriptionRead]:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    statement = (
        select(OrganizationSubscription, Organization, SubscriptionPlan)
        .join(
            Organization, Organization.id == OrganizationSubscription.org_id
        )
        .join(
            SubscriptionPlan,
            SubscriptionPlan.id == OrganizationSubscription.plan_id,
        )
        .order_by(OrganizationSubscription.created_at.desc())
    )
    if status_filter is not None:
        statement = statement.where(
            OrganizationSubscription.status == status_filter
        )
    rows = (await session.execute(statement)).all()
    return [
        PlatformSubscriptionRead(
            id=subscription.id,
            org_id=subscription.org_id,
            organization_name=organization.name,
            organization_slug=organization.slug,
            plan_code=plan.code,
            plan_name=plan.name,
            billing_interval=subscription.billing_interval,
            status=subscription.status,
            provider=subscription.provider,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_ends_at=subscription.trial_ends_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
            past_due_since=subscription.past_due_since,
            created_at=subscription.created_at,
        )
        for subscription, organization, plan in rows
    ]


@router.get(
    "/payments",
    response_model=list[PaymentRead],
    summary="Payment transactions across all organizations",
)
async def list_payments(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    status_filter: Literal["pending", "succeeded", "failed", "refunded"]
    | None = Query(default=None, alias="status"),  # noqa: B008
    limit: int = Query(default=100, ge=1, le=500),  # noqa: B008
) -> list[PaymentTransaction]:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    statement = (
        select(PaymentTransaction)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        statement = statement.where(PaymentTransaction.status == status_filter)
    rows = await session.scalars(statement)
    return list(rows)


@router.post(
    "/payments/{payment_id}/refund",
    response_model=PaymentRead,
    summary="Refund a successful payment",
)
async def refund_payment(
    payment_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    provider: PaymentProvider = Depends(get_payment_provider),  # noqa: B008
) -> PaymentTransaction:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    payment = await session.get(PaymentTransaction, payment_id)
    if payment is None:
        raise AppError("Payment not found.", status_code=404, code="not_found")

    # Switch to the payment's tenant so the audit row belongs to that tenant.
    await set_rls_context(session, org_id=payment.org_id, user_id=auth.user.id)
    refund = await billing.refund_payment(
        session,
        org_id=payment.org_id,
        payment_id=payment_id,
        user_id=auth.user.id,
        actor_role="platform_owner",
        provider=provider,
        request=request,
    )
    await session.refresh(refund)
    await session.commit()
    return refund


@router.get(
    "/revenue",
    response_model=RevenueSummaryRead,
    summary="Revenue summary",
)
async def revenue_summary(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RevenueSummaryRead:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    currency = get_settings().billing_currency
    row = (
        await session.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(amount_minor) FILTER (
                        WHERE kind = 'payment'
                        AND status IN ('succeeded', 'refunded')
                    ), 0) AS gross_minor,
                    COALESCE(SUM(amount_minor) FILTER (
                        WHERE kind = 'refund' AND status = 'succeeded'
                    ), 0) AS refunds_minor,
                    COUNT(*) FILTER (
                        WHERE kind = 'payment'
                        AND status IN ('succeeded', 'refunded')
                    ) AS payments_count,
                    COUNT(*) FILTER (
                        WHERE kind = 'refund' AND status = 'succeeded'
                    ) AS refunds_count
                FROM payment_transactions
                WHERE currency = :currency
                """
            ),
            {"currency": currency},
        )
    ).mappings().one()
    plan_rows = (
        await session.execute(
            text(
                """
                SELECT
                    p.code AS plan_code,
                    p.name AS plan_name,
                    COALESCE(SUM(t.amount_minor), 0) AS gross_minor,
                    COUNT(*) AS payments_count
                FROM payment_transactions t
                JOIN subscription_plans p ON p.id = t.plan_id
                WHERE t.kind = 'payment'
                  AND t.status IN ('succeeded', 'refunded')
                  AND t.currency = :currency
                GROUP BY p.code, p.name
                ORDER BY gross_minor DESC, p.code ASC
                """
            ),
            {"currency": currency},
        )
    ).mappings().all()
    gross = int(row["gross_minor"] or 0)
    refunds = int(row["refunds_minor"] or 0)
    return RevenueSummaryRead(
        currency=currency,
        gross_minor=gross,
        refunds_minor=refunds,
        net_minor=gross - refunds,
        payments_count=int(row["payments_count"] or 0),
        refunds_count=int(row["refunds_count"] or 0),
        by_plan=[
            RevenueByPlanRead(
                plan_code=plan["plan_code"],
                plan_name=plan["plan_name"],
                gross_minor=int(plan["gross_minor"] or 0),
                payments_count=int(plan["payments_count"] or 0),
            )
            for plan in plan_rows
        ],
    )


@router.get(
    "/usage",
    response_model=PlatformUsageSummaryRead,
    summary="Aggregate usage across organizations (no content)",
)
async def platform_usage(
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
) -> PlatformUsageSummaryRead:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    end = until or datetime.now(UTC)
    start = since or (end - timedelta(days=30))
    if start > end:
        raise AppError(
            "The reporting window is invalid.",
            status_code=422,
            code="invalid_usage_window",
        )
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    o.id AS org_id,
                    o.name AS organization_name,
                    o.slug AS organization_slug,
                    count(u.id) AS requests,
                    COALESCE(sum(u.tokens_in), 0) AS tokens_in,
                    COALESCE(sum(u.tokens_out), 0) AS tokens_out,
                    COALESCE(sum(u.cost_micros), 0) AS cost_micros
                FROM usage_events u
                JOIN organizations o ON o.id = u.org_id
                WHERE u.created_at >= :since AND u.created_at < :until
                GROUP BY o.id, o.name, o.slug
                ORDER BY (COALESCE(sum(u.tokens_in), 0) + COALESCE(sum(u.tokens_out), 0)) DESC
                """
            ),
            {"since": start, "until": end},
        )
    ).mappings().all()
    organizations = [
        PlatformUsageRead(
            org_id=row["org_id"],
            organization_name=row["organization_name"],
            organization_slug=row["organization_slug"],
            requests=int(row["requests"] or 0),
            tokens_in=int(row["tokens_in"] or 0),
            tokens_out=int(row["tokens_out"] or 0),
            cost_micros=int(row["cost_micros"] or 0),
        )
        for row in rows
    ]
    return PlatformUsageSummaryRead(
        since=start,
        until=end,
        total_requests=sum(item.requests for item in organizations),
        total_tokens=sum(
            item.tokens_in + item.tokens_out for item in organizations
        ),
        total_cost_micros=sum(item.cost_micros for item in organizations),
        organizations=organizations,
    )
