"""Tenant billing: plans, checkout, subscription, payments, invoices and quotas."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.organization_subscription import OrganizationSubscription
from app.models.payment_transaction import PaymentTransaction
from app.models.subscription_plan import SubscriptionPlan
from app.schemas.billing import (
    BillingOverviewRead,
    CheckoutRequest,
    CheckoutResponse,
    ConfirmationResponse,
    ConfirmCheckoutRequest,
    PaymentRead,
    PlanRead,
    QuotaSnapshotRead,
    SubscriptionRead,
    TrialRequest,
    WebhookResponse,
)
from app.services import billing
from app.services.payments import (
    PaymentProvider,
    WebhookVerificationError,
    get_payment_provider,
)
from app.services.quota import QuotaSnapshot, usage_snapshot

router = APIRouter(prefix="/billing", tags=["billing"])
org_router = APIRouter(
    prefix="/organizations/{org_id}/billing", tags=["billing"]
)


def subscription_read(
    subscription: OrganizationSubscription, plan: SubscriptionPlan
) -> SubscriptionRead:
    return SubscriptionRead(
        id=subscription.id,
        org_id=subscription.org_id,
        plan=PlanRead.model_validate(plan),
        billing_interval=subscription.billing_interval,
        status=subscription.status,
        provider=subscription.provider,
        started_at=subscription.started_at,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        trial_ends_at=subscription.trial_ends_at,
        canceled_at=subscription.canceled_at,
        cancel_at_period_end=subscription.cancel_at_period_end,
        past_due_since=subscription.past_due_since,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


def quota_read(snapshot: QuotaSnapshot) -> QuotaSnapshotRead:
    return QuotaSnapshotRead.model_validate(snapshot)


async def _overview(
    session: AsyncSession, org_id: uuid.UUID
) -> BillingOverviewRead:
    context = await billing.ensure_subscription(session, org_id)
    snapshot = await usage_snapshot(session, org_id)
    return BillingOverviewRead(
        subscription=subscription_read(context.subscription, context.plan),
        usage=quota_read(snapshot),
    )


@router.get(
    "/plans",
    response_model=list[PlanRead],
    summary="List active subscription plans",
)
async def list_plans(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SubscriptionPlan]:
    rows = await session.scalars(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.code.asc())
    )
    return list(rows)


@org_router.get(
    "",
    response_model=BillingOverviewRead,
    summary="Current subscription, plan and quota usage",
)
async def read_billing(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> BillingOverviewRead:
    overview = await _overview(session, context.org_id)
    # Lazy lifecycle advancement and the zero-usage counter are durable reads.
    await session.commit()
    return overview


@org_router.get(
    "/usage",
    response_model=QuotaSnapshotRead,
    summary="Quota and usage snapshot for the current period",
)
async def read_usage(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> QuotaSnapshotRead:
    snapshot = await usage_snapshot(session, context.org_id)
    await session.commit()
    return quota_read(snapshot)


@org_router.post(
    "/checkout",
    response_model=CheckoutResponse,
    summary="Start a plan change or renewal checkout",
)
async def checkout(
    payload: CheckoutRequest,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    provider: PaymentProvider = Depends(get_payment_provider),  # noqa: B008
) -> CheckoutResponse:
    outcome = await billing.start_checkout(
        session,
        org_id=context.org_id,
        plan_code=payload.plan_code,
        interval=payload.interval,
        user_id=context.user.id,
        actor_role=context.role,
        customer_email=context.user.email,
        customer_name=context.user.full_name,
        provider=provider,
        request=request,
    )
    # Build the response before commit so ORM attributes remain accessible.
    if outcome.payment is not None:
        await session.refresh(outcome.payment)
    await session.refresh(outcome.subscription)
    await session.refresh(outcome.plan)
    response = CheckoutResponse(
        immediate=outcome.immediate,
        subscription=subscription_read(outcome.subscription, outcome.plan),
        plan=PlanRead.model_validate(outcome.plan),
        payment=(
            PaymentRead.model_validate(outcome.payment)
            if outcome.payment is not None
            else None
        ),
        checkout_url=outcome.checkout_url,
        provider=outcome.provider,
        amount_minor=outcome.amount_minor,
        currency=outcome.currency,
    )
    await session.commit()
    return response


@org_router.post(
    "/checkout/confirm",
    response_model=ConfirmationResponse,
    summary="Verify a checkout with the provider and apply it",
)
async def confirm_checkout(
    payload: ConfirmCheckoutRequest,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    provider: PaymentProvider = Depends(get_payment_provider),  # noqa: B008
) -> ConfirmationResponse:
    outcome = await billing.confirm_checkout(
        session,
        org_id=context.org_id,
        reference=payload.reference,
        user_id=context.user.id,
        actor_role=context.role,
        provider=provider,
        request=request,
    )
    snapshot = await usage_snapshot(session, context.org_id)
    await session.refresh(outcome.payment)
    await session.refresh(outcome.context.subscription)
    await session.refresh(outcome.context.plan)
    response = ConfirmationResponse(
        activated=outcome.activated,
        payment=PaymentRead.model_validate(outcome.payment),
        subscription=subscription_read(
            outcome.context.subscription, outcome.context.plan
        ),
        usage=quota_read(snapshot),
    )
    payment_id = str(outcome.payment.id)
    await session.commit()
    if not outcome.activated:
        raise AppError(
            "The payment was not successful. No plan change was applied.",
            status_code=402,
            code="payment_failed",
            details={"payment_id": payment_id},
        )
    return response


@org_router.post(
    "/trial",
    response_model=CheckoutResponse,
    summary="Start a trial on a paid plan",
)
async def start_trial(
    payload: TrialRequest,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    provider: PaymentProvider = Depends(get_payment_provider),  # noqa: B008
) -> CheckoutResponse:
    outcome = await billing.start_trial(
        session,
        org_id=context.org_id,
        plan_code=payload.plan_code,
        interval=payload.interval,
        user_id=context.user.id,
        actor_role=context.role,
        customer_email=context.user.email,
        customer_name=context.user.full_name,
        provider=provider,
        request=request,
    )
    await session.refresh(outcome.subscription)
    await session.refresh(outcome.plan)
    response = CheckoutResponse(
        immediate=True,
        subscription=subscription_read(outcome.subscription, outcome.plan),
        plan=PlanRead.model_validate(outcome.plan),
        payment=None,
        checkout_url=None,
        provider=outcome.provider,
        amount_minor=0,
        currency=outcome.currency,
    )
    await session.commit()
    return response


@org_router.post(
    "/cancel",
    response_model=SubscriptionRead,
    summary="Cancel at the end of the current period",
)
async def cancel_subscription(
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubscriptionRead:
    outcome = await billing.cancel_subscription(
        session,
        org_id=context.org_id,
        user_id=context.user.id,
        actor_role=context.role,
        request=request,
    )
    await session.refresh(outcome.subscription)
    await session.refresh(outcome.plan)
    response = subscription_read(outcome.subscription, outcome.plan)
    await session.commit()
    return response


@org_router.post(
    "/renew",
    response_model=CheckoutResponse,
    summary="Renew the current plan",
)
async def renew_subscription(
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    provider: PaymentProvider = Depends(get_payment_provider),  # noqa: B008
) -> CheckoutResponse:
    outcome = await billing.renew_subscription(
        session,
        org_id=context.org_id,
        user_id=context.user.id,
        actor_role=context.role,
        customer_email=context.user.email,
        customer_name=context.user.full_name,
        provider=provider,
        request=request,
    )
    if outcome.payment is not None:
        await session.refresh(outcome.payment)
    await session.refresh(outcome.subscription)
    await session.refresh(outcome.plan)
    response = CheckoutResponse(
        immediate=outcome.immediate,
        subscription=subscription_read(outcome.subscription, outcome.plan),
        plan=PlanRead.model_validate(outcome.plan),
        payment=(
            PaymentRead.model_validate(outcome.payment)
            if outcome.payment is not None
            else None
        ),
        checkout_url=outcome.checkout_url,
        provider=outcome.provider,
        amount_minor=outcome.amount_minor,
        currency=outcome.currency,
    )
    await session.commit()
    return response


@org_router.get(
    "/payments",
    response_model=list[PaymentRead],
    summary="Payment and refund history",
)
async def list_payments(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PaymentTransaction]:
    return await billing.payment_history(session, context.org_id)


@org_router.get(
    "/invoices",
    response_model=list[PaymentRead],
    summary="Invoice history",
)
async def list_invoices(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PaymentTransaction]:
    return await billing.invoice_history(session, context.org_id)


@router.post(
    "/webhooks/{provider}",
    response_model=WebhookResponse,
    summary="Provider webhook (signature verified, idempotent)",
)
async def payment_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WebhookResponse:
    settings = get_settings()
    if provider != settings.billing_provider:
        raise AppError(
            "Unknown payment provider.", status_code=404, code="provider_not_found"
        )

    payment_provider = get_payment_provider()
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    try:
        event = payment_provider.parse_webhook(headers, body)
    except WebhookVerificationError as exc:
        raise AppError(
            "The webhook signature is invalid.",
            status_code=400,
            code="invalid_webhook_signature",
        ) from exc

    first_delivery = await billing.record_webhook_event(
        session, event, provider=provider, body=body
    )
    if not first_delivery:
        await session.commit()
        return WebhookResponse(status="duplicate")

    org_id = await billing.settle_webhook(
        session,
        event=event,
        provider=provider,
        actor_role="payment_provider",
        provider_obj=payment_provider,
    )
    await session.commit()
    return WebhookResponse(status="processed" if org_id is not None else "ignored")
