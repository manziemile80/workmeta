"""Subscription lifecycle, checkout, payment application and refunds.

State rules
-----------
* A free plan is effective immediately and never requires a payment.
* A paid plan starts only after the provider confirms the payment. The
  frontend callback is never trusted.
* A successful payment is applied exactly once. The payment row is locked and
  its status transition is the idempotency guard; webhook events have an
  additional platform-level ledger.
* Cancelling keeps the paid plan until the period end, then downgrades to Free.
* A missed renewal enters ``past_due`` for a configurable grace period and then
  ``expired``. Expired subscriptions block AI, assistant and document mutations.
* Past-due/expired state is evaluated lazily whenever billing or quota data is
  read, so the platform does not depend on a separate scheduler to stay honest.
"""

from __future__ import annotations

import calendar
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.rls import set_rls_context
from app.models.enums import (
    BillingInterval,
    PaymentKind,
    PaymentStatus,
    SubscriptionStatus,
)
from app.models.organization_subscription import OrganizationSubscription
from app.models.payment_transaction import PaymentTransaction
from app.models.subscription_plan import SubscriptionPlan
from app.services import audit
from app.services.payments import (
    CheckoutSession,
    PaymentProvider,
    PaymentProviderError,
    PaymentVerification,
    WebhookEvent,
)

logger = structlog.get_logger(__name__)

FREE_PLAN_CODE = "free"


def utcnow() -> datetime:
    return datetime.now(UTC)


def add_interval(start: datetime, interval: str) -> datetime:
    """Add one calendar month/year, clamping the day to the target month."""
    if interval == BillingInterval.annual.value:
        months = 12
    else:
        months = 1
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def is_free_plan(plan: SubscriptionPlan) -> bool:
    return plan.code == FREE_PLAN_CODE or (
        plan.monthly_price_minor == 0 and plan.annual_price_minor == 0
    )


def price_for(plan: SubscriptionPlan, interval: str) -> int:
    if interval == BillingInterval.annual.value:
        return plan.annual_price_minor
    return plan.monthly_price_minor


@dataclass(slots=True)
class SubscriptionContext:
    subscription: OrganizationSubscription
    plan: SubscriptionPlan
    changed: bool = False


@dataclass(slots=True)
class CheckoutOutcome:
    subscription: OrganizationSubscription
    plan: SubscriptionPlan
    payment: PaymentTransaction | None
    checkout_url: str | None
    provider: str
    amount_minor: int
    currency: str
    immediate: bool


@dataclass(slots=True)
class ConfirmationOutcome:
    context: SubscriptionContext
    payment: PaymentTransaction
    activated: bool


@dataclass(slots=True)
class WebhookOutcome:
    duplicate: bool
    handled: bool
    org_id: uuid.UUID | None = None


async def get_plan_by_code(
    session: AsyncSession, code: str
) -> SubscriptionPlan | None:
    return await session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.code == code)
    )


async def get_plan(session: AsyncSession, plan_id: uuid.UUID) -> SubscriptionPlan:
    plan = await session.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise AppError(
            "The subscription plan no longer exists.",
            status_code=409,
            code="plan_missing",
        )
    return plan


async def _free_plan(session: AsyncSession) -> SubscriptionPlan:
    plan = await get_plan_by_code(session, FREE_PLAN_CODE)
    if plan is None:
        raise AppError(
            "Billing is not configured on this deployment.",
            status_code=503,
            code="billing_not_configured",
        )
    return plan


async def _insert_free_subscription(
    session: AsyncSession, org_id: uuid.UUID
) -> OrganizationSubscription:
    plan = await _free_plan(session)
    now = utcnow()
    subscription = OrganizationSubscription(
        id=uuid.uuid4(),
        org_id=org_id,
        plan_id=plan.id,
        billing_interval=BillingInterval.monthly.value,
        status=SubscriptionStatus.active.value,
        provider="mock",
        started_at=now,
        current_period_start=now,
        current_period_end=add_interval(now, BillingInterval.monthly.value),
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def ensure_subscription(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SubscriptionContext:
    """Return the tenant subscription, creating Free or advancing lifecycle state."""
    statement = select(OrganizationSubscription).where(
        OrganizationSubscription.org_id == org_id
    )
    if for_update:
        statement = statement.with_for_update()
    subscription = await session.scalar(statement)
    if subscription is None:
        subscription = await _insert_free_subscription(session, org_id)

    plan = await get_plan(session, subscription.plan_id)
    changed = await _advance_state(session, subscription, plan)
    if changed:
        await session.flush()
        plan = await get_plan(session, subscription.plan_id)
    return SubscriptionContext(
        subscription=subscription, plan=plan, changed=changed
    )


async def _advance_state(
    session: AsyncSession,
    subscription: OrganizationSubscription,
    plan: SubscriptionPlan,
) -> bool:
    now = utcnow()
    interval = subscription.billing_interval or BillingInterval.monthly.value
    changed = False

    if subscription.status == SubscriptionStatus.trialing.value:
        if subscription.trial_ends_at is not None and subscription.trial_ends_at <= now:
            subscription.status = SubscriptionStatus.past_due.value
            subscription.past_due_since = subscription.past_due_since or now
            subscription.trial_ends_at = None
            changed = True
            await _audit_state(session, subscription, "trial_ended")

    renewable_statuses = {
        SubscriptionStatus.active.value,
        SubscriptionStatus.canceled.value,
    }
    if subscription.status in renewable_statuses and subscription.current_period_end <= now:
        if subscription.cancel_at_period_end or is_free_plan(plan):
            await _downgrade_to_free(session, subscription, reason="period_end")
            changed = True
        else:
            subscription.status = SubscriptionStatus.past_due.value
            subscription.past_due_since = subscription.past_due_since or now
            changed = True
            await _audit_state(session, subscription, "renewal_due")

    if (
        subscription.status == SubscriptionStatus.past_due.value
        and subscription.past_due_since is not None
    ):
        grace = timedelta(days=get_settings().billing_past_due_grace_days)
        if subscription.past_due_since + grace <= now:
            subscription.status = SubscriptionStatus.expired.value
            changed = True
            await _audit_state(session, subscription, "grace_expired")

    if (
        subscription.status == SubscriptionStatus.expired.value
        and is_free_plan(plan)
    ):
        # A free subscription is never actually expired; keep the period rolling.
        subscription.status = SubscriptionStatus.active.value
        subscription.current_period_start = now
        subscription.current_period_end = add_interval(now, interval)
        changed = True

    return changed


async def _downgrade_to_free(
    session: AsyncSession,
    subscription: OrganizationSubscription,
    *,
    reason: str,
) -> None:
    free = await _free_plan(session)
    now = utcnow()
    subscription.plan_id = free.id
    subscription.billing_interval = BillingInterval.monthly.value
    subscription.status = SubscriptionStatus.active.value
    subscription.current_period_start = now
    subscription.current_period_end = add_interval(now, BillingInterval.monthly.value)
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.trial_ends_at = None
    subscription.past_due_since = None
    subscription.provider_subscription_id = None
    await _audit_state(
        session,
        subscription,
        "downgraded_to_free",
        metadata={"reason": reason},
    )


async def _audit_state(
    session: AsyncSession,
    subscription: OrganizationSubscription,
    change: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit.record(
        session,
        action="billing.subscription_state_changed",
        org_id=subscription.org_id,
        resource_type="subscription",
        resource_id=str(subscription.id),
        metadata={
            "change": change,
            "status": subscription.status,
            "plan_id": str(subscription.plan_id),
            **(metadata or {}),
        },
    )


def _new_reference() -> str:
    return f"WM-{uuid.uuid4().hex[:20].upper()}"


def _new_invoice_number() -> str:
    return (
        f"INV-{utcnow().strftime('%Y%m%d')}-"
        f"{secrets.token_hex(3).upper()}"
    )


async def start_checkout(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    plan_code: str,
    interval: str,
    user_id: uuid.UUID,
    actor_role: str | None,
    customer_email: str,
    customer_name: str,
    provider: PaymentProvider,
    request: Request | None = None,
    start_trial: bool = False,
) -> CheckoutOutcome:
    if interval not in {
        BillingInterval.monthly.value,
        BillingInterval.annual.value,
    }:
        raise AppError(
            "Choose a monthly or annual billing interval.",
            status_code=422,
            code="invalid_billing_interval",
        )

    plan = await get_plan_by_code(session, plan_code)
    if plan is None or not plan.is_active:
        raise AppError(
            "That plan is not available.",
            status_code=404,
            code="plan_not_found",
        )

    context = await ensure_subscription(session, org_id, for_update=True)
    settings = get_settings()

    if start_trial:
        if is_free_plan(plan):
            raise AppError(
                "The Free plan does not need a trial.",
                status_code=400,
                code="trial_not_available",
            )
        paid_before = await session.scalar(
            select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.org_id == org_id,
                PaymentTransaction.kind == PaymentKind.payment.value,
                PaymentTransaction.status == PaymentStatus.succeeded.value,
            )
        )
        if paid_before or context.subscription.trial_ends_at is not None:
            raise AppError(
                "A trial is only available to a new paying customer.",
                status_code=409,
                code="trial_not_available",
            )
        now = utcnow()
        trial_end = now + timedelta(days=settings.billing_trial_days)
        context.subscription.plan_id = plan.id
        context.subscription.billing_interval = interval
        context.subscription.status = SubscriptionStatus.trialing.value
        context.subscription.trial_ends_at = trial_end
        context.subscription.current_period_start = now
        context.subscription.current_period_end = trial_end
        context.subscription.cancel_at_period_end = False
        context.subscription.canceled_at = None
        context.subscription.past_due_since = None
        context.subscription.provider = provider.name
        await _audit_state(
            session,
            context.subscription,
            "trial_started",
            metadata={"plan_code": plan.code, "interval": interval},
        )
        await session.flush()
        return CheckoutOutcome(
            subscription=context.subscription,
            plan=plan,
            payment=None,
            checkout_url=None,
            provider=provider.name,
            amount_minor=0,
            currency=plan.currency,
            immediate=True,
        )

    if is_free_plan(plan):
        now = utcnow()
        context.subscription.plan_id = plan.id
        context.subscription.billing_interval = BillingInterval.monthly.value
        context.subscription.status = SubscriptionStatus.active.value
        context.subscription.current_period_start = now
        context.subscription.current_period_end = add_interval(
            now, BillingInterval.monthly.value
        )
        context.subscription.cancel_at_period_end = False
        context.subscription.canceled_at = None
        context.subscription.trial_ends_at = None
        context.subscription.past_due_since = None
        await _audit_state(
            session,
            context.subscription,
            "changed_to_free",
            metadata={"plan_code": plan.code},
        )
        await session.flush()
        return CheckoutOutcome(
            subscription=context.subscription,
            plan=plan,
            payment=None,
            checkout_url=None,
            provider="internal",
            amount_minor=0,
            currency=plan.currency,
            immediate=True,
        )

    amount_minor = price_for(plan, interval)
    reference = _new_reference()
    payment = PaymentTransaction(
        id=uuid.uuid4(),
        org_id=org_id,
        subscription_id=context.subscription.id,
        plan_id=plan.id,
        provider=provider.name,
        provider_reference=reference,
        kind=PaymentKind.payment.value,
        status=PaymentStatus.pending.value,
        amount_minor=amount_minor,
        currency=plan.currency,
        billing_interval=interval,
        description=f"{plan.name} plan ({interval})",
        meta={
            "plan_code": plan.code,
            "initiated_by": str(user_id),
        },
    )
    session.add(payment)
    await session.flush()

    try:
        checkout: CheckoutSession = await provider.create_checkout(
            reference=reference,
            amount_minor=amount_minor,
            currency=plan.currency,
            exponent=plan.price_exponent,
            customer_email=customer_email,
            customer_name=customer_name,
            description=f"WorkMeta AI — {plan.name}",
            metadata={
                "org_id": str(org_id),
                "plan_code": plan.code,
                "billing_interval": interval,
            },
        )
    except PaymentProviderError as exc:
        payment.status = PaymentStatus.failed.value
        payment.meta = {**payment.meta, "checkout_error": str(exc)[:300]}
        await session.flush()
        raise AppError(
            "The payment provider could not start checkout. Please try again.",
            status_code=502,
            code="payment_provider_unavailable",
        ) from exc

    payment.checkout_url = checkout.checkout_url
    await audit.record(
        session,
        action="billing.checkout_started",
        org_id=org_id,
        actor_user_id=user_id,
        actor_role=actor_role,
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "plan_code": plan.code,
            "interval": interval,
            "amount_minor": amount_minor,
            "provider": provider.name,
        },
        request=request,
    )
    await session.flush()
    return CheckoutOutcome(
        subscription=context.subscription,
        plan=plan,
        payment=payment,
        checkout_url=checkout.checkout_url,
        provider=provider.name,
        amount_minor=amount_minor,
        currency=plan.currency,
        immediate=False,
    )


async def _payment_by_reference(
    session: AsyncSession, *, org_id: uuid.UUID, reference: str
) -> PaymentTransaction:
    payment = await session.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.org_id == org_id,
            PaymentTransaction.provider_reference == reference,
        )
        .with_for_update()
    )
    if payment is None:
        raise AppError(
            "That payment could not be found.",
            status_code=404,
            code="payment_not_found",
        )
    return payment


async def confirm_checkout(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    reference: str,
    user_id: uuid.UUID,
    actor_role: str | None,
    provider: PaymentProvider,
    request: Request | None = None,
) -> ConfirmationOutcome:
    payment = await _payment_by_reference(
        session, org_id=org_id, reference=reference
    )
    context = await ensure_subscription(session, org_id, for_update=True)

    if payment.status == PaymentStatus.succeeded.value:
        return ConfirmationOutcome(
            context=context, payment=payment, activated=True
        )
    if payment.kind != PaymentKind.payment.value:
        raise AppError(
            "That reference is not a payment.",
            status_code=409,
            code="not_a_payment",
        )

    plan = await get_plan(session, payment.plan_id) if payment.plan_id else context.plan
    try:
        verification = await provider.verify(
            reference=reference,
            expected_amount_minor=payment.amount_minor,
            currency=payment.currency,
            exponent=plan.price_exponent,
        )
    except PaymentProviderError as exc:
        raise AppError(
            "The payment provider could not verify this payment. Please try again.",
            status_code=502,
            code="payment_provider_unavailable",
        ) from exc

    if verification.status != PaymentStatus.succeeded.value:
        payment.status = PaymentStatus.failed.value
        await audit.record(
            session,
            action="billing.payment_failed",
            org_id=org_id,
            actor_user_id=user_id,
            actor_role=actor_role,
            resource_type="payment",
            resource_id=str(payment.id),
            metadata={"provider_status": verification.status},
            request=request,
        )
        await session.flush()
        return ConfirmationOutcome(
            context=context, payment=payment, activated=False
        )

    if (
        verification.amount_minor is not None
        and verification.amount_minor != payment.amount_minor
    ):
        payment.status = PaymentStatus.failed.value
        await audit.record(
            session,
            action="billing.payment_amount_mismatch",
            org_id=org_id,
            actor_user_id=user_id,
            actor_role=actor_role,
            resource_type="payment",
            resource_id=str(payment.id),
            metadata={
                "expected_minor": payment.amount_minor,
                "received_minor": verification.amount_minor,
            },
            request=request,
        )
        await session.flush()
        raise AppError(
            "The confirmed amount does not match this checkout.",
            status_code=409,
            code="payment_amount_mismatch",
        )

    if (
        verification.currency is not None
        and verification.currency.upper() != payment.currency.upper()
    ):
        payment.status = PaymentStatus.failed.value
        await session.flush()
        raise AppError(
            "The confirmed currency does not match this checkout.",
            status_code=409,
            code="payment_currency_mismatch",
        )

    await apply_successful_payment(
        session,
        payment=payment,
        context=context,
        verification=verification,
        actor_user_id=user_id,
        actor_role=actor_role,
        request=request,
    )
    context.plan = await get_plan(session, context.subscription.plan_id)
    return ConfirmationOutcome(
        context=context, payment=payment, activated=True
    )


async def apply_successful_payment(
    session: AsyncSession,
    *,
    payment: PaymentTransaction,
    context: SubscriptionContext,
    verification: PaymentVerification | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    request: Request | None = None,
    event_id: str | None = None,
) -> None:
    """Apply a confirmed payment exactly once to the tenant subscription."""
    if payment.status == PaymentStatus.succeeded.value:
        return
    if payment.kind != PaymentKind.payment.value:
        return
    if payment.plan_id is None:
        raise AppError(
            "The payment has no plan.", status_code=409, code="payment_plan_missing"
        )

    plan = await get_plan(session, payment.plan_id)
    subscription = context.subscription
    now = utcnow()
    interval = payment.billing_interval or BillingInterval.monthly.value

    payment.status = PaymentStatus.succeeded.value
    payment.invoice_number = payment.invoice_number or _new_invoice_number()
    if event_id:
        payment.provider_event_id = event_id
    if verification and verification.provider_transaction_id:
        payment.meta = {
            **payment.meta,
            "provider_transaction_id": verification.provider_transaction_id,
        }

    same_plan = subscription.plan_id == plan.id
    if (
        same_plan
        and subscription.status == SubscriptionStatus.active.value
        and subscription.current_period_end > now
    ):
        # Early renewal extends the current period.
        subscription.current_period_end = add_interval(
            subscription.current_period_end, interval
        )
    else:
        subscription.current_period_start = now
        subscription.current_period_end = add_interval(now, interval)

    subscription.plan_id = plan.id
    subscription.billing_interval = interval
    subscription.status = SubscriptionStatus.active.value
    subscription.provider = payment.provider
    subscription.provider_subscription_id = (
        verification.provider_transaction_id if verification else None
    )
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.trial_ends_at = None
    subscription.past_due_since = None
    payment.subscription_id = subscription.id

    await audit.record(
        session,
        action="billing.payment_succeeded",
        org_id=payment.org_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "plan_code": plan.code,
            "interval": interval,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "invoice_number": payment.invoice_number,
            "event_id": event_id,
        },
        request=request,
    )
    await session.flush()


async def start_trial(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    plan_code: str,
    interval: str,
    user_id: uuid.UUID,
    actor_role: str | None,
    customer_email: str,
    customer_name: str,
    provider: PaymentProvider,
    request: Request | None = None,
) -> CheckoutOutcome:
    return await start_checkout(
        session,
        org_id=org_id,
        plan_code=plan_code,
        interval=interval,
        user_id=user_id,
        actor_role=actor_role,
        customer_email=customer_email,
        customer_name=customer_name,
        provider=provider,
        request=request,
        start_trial=True,
    )


async def cancel_subscription(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_role: str | None,
    request: Request | None = None,
) -> SubscriptionContext:
    context = await ensure_subscription(session, org_id, for_update=True)
    if is_free_plan(context.plan):
        raise AppError(
            "The Free plan does not need cancellation.",
            status_code=409,
            code="free_plan_cannot_cancel",
        )
    context.subscription.cancel_at_period_end = True
    context.subscription.canceled_at = utcnow()
    await _audit_state(
        session,
        context.subscription,
        "cancel_scheduled",
        metadata={"period_end": context.subscription.current_period_end.isoformat()},
    )
    await audit.record(
        session,
        action="billing.subscription_canceled",
        org_id=org_id,
        actor_user_id=user_id,
        actor_role=actor_role,
        resource_type="subscription",
        resource_id=str(context.subscription.id),
        metadata={
            "effective_at": context.subscription.current_period_end.isoformat()
        },
        request=request,
    )
    await session.flush()
    return context


async def renew_subscription(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_role: str | None,
    customer_email: str,
    customer_name: str,
    provider: PaymentProvider,
    request: Request | None = None,
) -> CheckoutOutcome:
    context = await ensure_subscription(session, org_id, for_update=True)
    if is_free_plan(context.plan):
        now = utcnow()
        context.subscription.current_period_start = now
        context.subscription.current_period_end = add_interval(
            now, BillingInterval.monthly.value
        )
        await _audit_state(session, context.subscription, "free_renewed")
        await session.flush()
        return CheckoutOutcome(
            subscription=context.subscription,
            plan=context.plan,
            payment=None,
            checkout_url=None,
            provider="internal",
            amount_minor=0,
            currency=context.plan.currency,
            immediate=True,
        )
    return await start_checkout(
        session,
        org_id=org_id,
        plan_code=context.plan.code,
        interval=context.subscription.billing_interval,
        user_id=user_id,
        actor_role=actor_role,
        customer_email=customer_email,
        customer_name=customer_name,
        provider=provider,
        request=request,
    )


async def record_webhook_event(
    session: AsyncSession, event: WebhookEvent, *, provider: str, body: bytes
) -> bool:
    """Insert the idempotency row. Returns False for a duplicate event."""
    import hashlib

    payload_hash = hashlib.sha256(body).hexdigest()
    row = (
        await session.execute(
            text(
                """
                INSERT INTO processed_webhook_events
                    (id, provider, event_id, event_type, payload_hash)
                VALUES
                    (gen_random_uuid(), :provider, :event_id, :event_type, :hash)
                ON CONFLICT (provider, event_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": provider,
                "event_id": event.event_id[:160],
                "event_type": event.event_type[:80],
                "hash": payload_hash,
            },
        )
    ).first()
    return row is not None


async def settle_webhook(
    session: AsyncSession,
    *,
    event: WebhookEvent,
    provider: str,
    actor_role: str | None = None,
    provider_obj: PaymentProvider | None = None,
) -> uuid.UUID | None:
    """Apply an authenticated provider event to exactly one organization.

    Signature verification happens before this call (in the route). For real
    providers, a succeeded event is additionally re-verified with the provider
    before the plan is activated, so a leaked webhook hash alone can never
    activate a plan without the provider confirming the payment.
    """
    if not event.reference:
        return None
    org_id = await session.scalar(
        text(
            "SELECT app_webhook_org(:provider, :reference)"
        ),
        {"provider": provider, "reference": event.reference},
    )
    if org_id is None:
        logger.warning(
            "billing_webhook_unknown_reference",
            provider=provider,
            reference=event.reference,
        )
        return None

    await set_rls_context(session, org_id=org_id)
    payment = await session.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.org_id == org_id,
            PaymentTransaction.provider == provider,
            PaymentTransaction.provider_reference == event.reference,
        )
        .with_for_update()
    )
    if payment is None:
        return None

    if event.status == PaymentStatus.succeeded.value:
        if payment.status != PaymentStatus.succeeded.value:
            if (
                event.amount_minor is not None
                and event.amount_minor != payment.amount_minor
            ):
                payment.status = PaymentStatus.failed.value
                await audit.record(
                    session,
                    action="billing.webhook_amount_mismatch",
                    org_id=org_id,
                    actor_role=actor_role,
                    resource_type="payment",
                    resource_id=str(payment.id),
                    metadata={
                        "expected_minor": payment.amount_minor,
                        "received_minor": event.amount_minor,
                    },
                )
                await session.flush()
                return org_id
            if (
                provider_obj is not None
                and get_settings().billing_webhook_verify
            ):
                try:
                    plan = await get_plan(session, payment.plan_id) if payment.plan_id else None
                    exponent = plan.price_exponent if plan else 0
                    live = await provider_obj.verify(
                        reference=event.reference,
                        expected_amount_minor=payment.amount_minor,
                        currency=payment.currency,
                        exponent=exponent,
                    )
                except PaymentProviderError:
                    logger.warning(
                        "billing_webhook_verify_failed",
                        provider=provider,
                        reference=event.reference,
                    )
                    return org_id
                if live.status != PaymentStatus.succeeded.value:
                    logger.warning(
                        "billing_webhook_not_live",
                        provider=provider,
                        reference=event.reference,
                        live_status=live.status,
                    )
                    return org_id
                if (
                    live.amount_minor is not None
                    and live.amount_minor != payment.amount_minor
                ):
                    payment.status = PaymentStatus.failed.value
                    await audit.record(
                        session,
                        action="billing.webhook_live_amount_mismatch",
                        org_id=org_id,
                        actor_role=actor_role,
                        resource_type="payment",
                        resource_id=str(payment.id),
                        metadata={
                            "expected_minor": payment.amount_minor,
                            "received_minor": live.amount_minor,
                        },
                    )
                    await session.flush()
                    return org_id
            context = await ensure_subscription(session, org_id, for_update=True)
            verification = PaymentVerification(
                reference=event.reference,
                status=event.status,
                amount_minor=event.amount_minor,
                currency=event.currency,
                provider_transaction_id=event.provider_transaction_id,
                raw=event.raw,
            )
            await apply_successful_payment(
                session,
                payment=payment,
                context=context,
                verification=verification,
                event_id=event.event_id,
            )
    elif event.status == PaymentStatus.failed.value:
        if payment.status == PaymentStatus.pending.value:
            payment.status = PaymentStatus.failed.value
            await audit.record(
                session,
                action="billing.webhook_payment_failed",
                org_id=org_id,
                actor_role=actor_role,
                resource_type="payment",
                resource_id=str(payment.id),
                metadata={"event_type": event.event_type},
            )
    elif event.status == PaymentStatus.refunded.value:
        payment.status = PaymentStatus.refunded.value
        existing = await session.scalar(
            select(PaymentTransaction.id).where(
                PaymentTransaction.org_id == org_id,
                PaymentTransaction.kind == PaymentKind.refund.value,
                PaymentTransaction.provider_event_id == event.event_id,
            )
        )
        if existing is None:
            session.add(
                PaymentTransaction(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    subscription_id=payment.subscription_id,
                    plan_id=payment.plan_id,
                    provider=provider,
                    provider_reference=(
                        f"REFUND-{payment.provider_reference[:40]}-"
                        f"{uuid.uuid4().hex[:8].upper()}"
                    ),
                    provider_event_id=event.event_id,
                    kind=PaymentKind.refund.value,
                    status=PaymentStatus.succeeded.value,
                    amount_minor=payment.amount_minor,
                    currency=payment.currency,
                    billing_interval=payment.billing_interval,
                    description=(
                        f"Refund for {payment.invoice_number or payment.provider_reference}"  # noqa: E501
                    ),
                    meta={"original_payment_id": str(payment.id)},
                )
            )
        await audit.record(
            session,
            action="billing.webhook_refunded",
            org_id=org_id,
            actor_role=actor_role,
            resource_type="payment",
            resource_id=str(payment.id),
            metadata={"event_type": event.event_type},
        )

    await audit.record(
        session,
        action="billing.webhook_processed",
        org_id=org_id,
        actor_role=actor_role,
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "provider": provider,
            "event_type": event.event_type,
            "event_id": event.event_id,
            "status": event.status,
        },
    )
    await session.flush()
    return org_id


async def refund_payment(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payment_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_role: str | None,
    provider: PaymentProvider,
    request: Request | None = None,
) -> PaymentTransaction:
    payment = await session.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.id == payment_id,
            PaymentTransaction.org_id == org_id,
        )
        .with_for_update()
    )
    if payment is None:
        raise AppError(
            "That payment could not be found.",
            status_code=404,
            code="payment_not_found",
        )
    if payment.kind != PaymentKind.payment.value:
        raise AppError(
            "Only a payment can be refunded.",
            status_code=409,
            code="not_a_payment",
        )
    if payment.status != PaymentStatus.succeeded.value:
        raise AppError(
            "Only a successful payment can be refunded.",
            status_code=409,
            code="payment_not_refundable",
        )

    plan = await get_plan(session, payment.plan_id) if payment.plan_id else None
    exponent = plan.price_exponent if plan else 0
    stored_transaction_id = payment.meta.get("provider_transaction_id")
    try:
        result = await provider.refund(
            reference=payment.provider_reference,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            exponent=exponent,
            provider_transaction_id=(
                str(stored_transaction_id) if stored_transaction_id else None
            ),
        )
    except PaymentProviderError as exc:
        raise AppError(
            "The payment provider could not process the refund.",
            status_code=502,
            code="payment_provider_unavailable",
        ) from exc

    if result.status != PaymentStatus.succeeded.value:
        raise AppError(
            "The payment provider did not confirm the refund.",
            status_code=409,
            code="refund_not_confirmed",
        )

    payment.status = PaymentStatus.refunded.value
    refund = PaymentTransaction(
        id=uuid.uuid4(),
        org_id=org_id,
        subscription_id=payment.subscription_id,
        plan_id=payment.plan_id,
        provider=payment.provider,
        provider_reference=(
            f"REFUND-{payment.provider_reference[:40]}-"
            f"{uuid.uuid4().hex[:8].upper()}"
        ),
        provider_event_id=result.provider_refund_id,
        kind=PaymentKind.refund.value,
        status=PaymentStatus.succeeded.value,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        billing_interval=payment.billing_interval,
        description=f"Refund for {payment.invoice_number or payment.provider_reference}",
        meta={"original_payment_id": str(payment.id)},
    )
    session.add(refund)
    await audit.record(
        session,
        action="billing.payment_refunded",
        org_id=org_id,
        actor_user_id=user_id,
        actor_role=actor_role,
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "refund_id": str(refund.id),
            "amount_minor": payment.amount_minor,
            "provider": payment.provider,
        },
        request=request,
    )
    await session.flush()
    return refund


async def payment_history(
    session: AsyncSession, org_id: uuid.UUID
) -> list[PaymentTransaction]:
    rows = await session.scalars(
        select(PaymentTransaction)
        .where(PaymentTransaction.org_id == org_id)
        .order_by(PaymentTransaction.created_at.desc())
    )
    return list(rows)


async def invoice_history(
    session: AsyncSession, org_id: uuid.UUID
) -> list[PaymentTransaction]:
    rows = await session.scalars(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.org_id == org_id,
            PaymentTransaction.kind == PaymentKind.payment.value,
            PaymentTransaction.status.in_(
                [PaymentStatus.succeeded.value, PaymentStatus.refunded.value]
            ),
            PaymentTransaction.invoice_number.is_not(None),
        )
        .order_by(PaymentTransaction.created_at.desc())
    )
    return list(rows)
