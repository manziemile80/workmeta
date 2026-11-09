"""Stage 5: subscriptions, payments, quotas and platform administration.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.main import app
from app.services.ai import FakeAIProvider, get_ai_provider
from app.services.payments import (
    MockPaymentProvider,
    PaymentProvider,
    PaymentVerification,
    get_payment_provider,
)
from app.services.storage import LocalStorage, get_storage
from app.workers.ingestion import process_next_job
from tests.test_auth_tenancy_integration import _bearer, _tenant
from tests.test_knowledge_rag_integration import (
    _ask,
    _conversation,
    _create_assistant,
    _upload,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

RECEIPT_GUIDE = (
    "How to issue an EBM receipt for a walk-in customer:\n"
    "1. Open the sales screen.\n"
    "2. Select Walk-in customer.\n"
    "3. Confirm the invoice and print the receipt.\n"
)


class FailingPaymentProvider(MockPaymentProvider):
    """Mock provider whose verification always reports ``failed``."""

    async def verify(
        self,
        *,
        reference: str,
        expected_amount_minor: int,
        currency: str,
        exponent: int,
    ) -> PaymentVerification:
        return PaymentVerification(
            reference=reference,
            status="failed",
            amount_minor=expected_amount_minor,
            currency=currency,
            provider_transaction_id=f"mock-fail-{reference}",
            raw={},
        )


@pytest.fixture
def fake_ai():

    provider = FakeAIProvider(get_settings().embedding_dimensions)
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)


@pytest.fixture
def storage(tmp_path):

    provider = LocalStorage(tmp_path)
    app.dependency_overrides[get_storage] = lambda: provider
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_storage, None)


def _mock_signature(body: bytes) -> str:
    secret = get_settings().billing_mock_webhook_secret
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _plans(
    client: AsyncClient, token: str
) -> list[dict]:
    response = await client.get("/api/v1/billing/plans", headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _billing(
    client: AsyncClient, token: str, org_id: str
) -> dict:
    response = await client.get(
        f"/api/v1/organizations/{org_id}/billing", headers=_bearer(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _checkout(
    client: AsyncClient,
    token: str,
    org_id: str,
    plan_code: str = "starter",
    interval: str = "monthly",
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/billing/checkout",
        headers=_bearer(token),
        json={"plan_code": plan_code, "interval": interval},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _confirm(
    client: AsyncClient, token: str, org_id: str, reference: str
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/billing/checkout/confirm",
        headers=_bearer(token),
        json={"reference": reference},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _drain(ai: FakeAIProvider, storage: LocalStorage) -> int:
    processed = 0
    for _ in range(20):
        if not await process_next_job(
            ai=ai, storage=storage, session_factory=SessionFactory
        ):
            break
        processed += 1
    return processed


async def _set_free_limits(
    *,
    max_assistants: int | None = None,
    max_documents: int | None = None,
    request_allowance: int | None = None,
    token_allowance: int | None = None,
) -> dict:
    """Patch the shared Free plan via the superuser connection (test only)."""

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    engine = create_async_engine(
        settings.database_migration_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            before = (
                await connection.execute(
                    text(
                        "SELECT max_assistants, max_documents, "
                        "monthly_request_allowance, monthly_token_allowance "
                        "FROM subscription_plans WHERE code = 'free'"
                    )
                )
            ).mappings().one()
            await connection.execute(
                text(
                    "UPDATE subscription_plans SET "
                    "max_assistants = :a, max_documents = :d, "
                    "monthly_request_allowance = :r, "
                    "monthly_token_allowance = :t "
                    "WHERE code = 'free'"
                ),
                {
                    "a": max_assistants,
                    "d": max_documents,
                    "r": request_allowance,
                    "t": token_allowance,
                },
            )
            return dict(before)
    finally:
        await engine.dispose()


async def _restore_free_limits(before: dict) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    engine = create_async_engine(
        settings.database_migration_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE subscription_plans SET "
                    "max_assistants = :a, max_documents = :d, "
                    "monthly_request_allowance = :r, "
                    "monthly_token_allowance = :t "
                    "WHERE code = 'free'"
                ),
                {
                    "a": before["max_assistants"],
                    "d": before["max_documents"],
                    "r": before["monthly_request_allowance"],
                    "t": before["monthly_token_allowance"],
                },
            )
    finally:
        await engine.dispose()


async def _update_subscription(
    org_id: str, *, values: dict
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    engine = create_async_engine(
        settings.database_migration_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            for column, value in values.items():
                # Fixed statements per column: no dynamic SQL text is built.
                statements = {
                    "trial_ends_at": (
                        "UPDATE organization_subscriptions "
                        "SET trial_ends_at = :v "
                        "WHERE org_id = CAST(:org AS uuid)"
                    ),
                    "past_due_since": (
                        "UPDATE organization_subscriptions "
                        "SET past_due_since = :v "
                        "WHERE org_id = CAST(:org AS uuid)"
                    ),
                    "status": (
                        "UPDATE organization_subscriptions "
                        "SET status = :v "
                        "WHERE org_id = CAST(:org AS uuid)"
                    ),
                    "current_period_end": (
                        "UPDATE organization_subscriptions "
                        "SET current_period_end = :v "
                        "WHERE org_id = CAST(:org AS uuid)"
                    ),
                    "current_period_start": (
                        "UPDATE organization_subscriptions "
                        "SET current_period_start = :v "
                        "WHERE org_id = CAST(:org AS uuid)"
                    ),
                }
                statement = statements.get(column)
                if statement is None:
                    raise AssertionError(f"Unexpected column: {column}")
                await connection.execute(
                    text(statement),
                    {"v": value, "org": org_id},
                )
    finally:
        await engine.dispose()


async def test_new_org_gets_free_plan_and_plans_listed(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-free")
    org_id = tenant.organization["id"]

    plans = await _plans(client, tenant.token)
    codes = {row["code"] for row in plans}
    assert {"free", "starter", "professional", "enterprise"} <= codes

    overview = await _billing(client, tenant.token, org_id)
    assert overview["subscription"]["plan"]["code"] == "free"
    assert overview["subscription"]["status"] == "active"
    assert overview["usage"]["plan_code"] == "free"

    usage = await client.get(
        f"/api/v1/organizations/{org_id}/billing/usage",
        headers=_bearer(tenant.token),
    )
    assert usage.status_code == 200, usage.text
    assert usage.json()["requests_used"] == 0


async def test_paid_checkout_and_confirmation_activates_plan(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-pay")
    org_id = tenant.organization["id"]

    checkout = await _checkout(client, tenant.token, org_id, "starter", "monthly")
    assert checkout["immediate"] is False
    assert checkout["payment"]["status"] == "pending"
    assert checkout["payment"]["amount_minor"] > 0
    reference = checkout["payment"]["provider_reference"]

    # The subscription is NOT yet on the paid plan (frontend not trusted).
    before = await _billing(client, tenant.token, org_id)
    assert before["subscription"]["plan"]["code"] == "free"

    confirmed = await _confirm(client, tenant.token, org_id, reference)
    assert confirmed["activated"] is True
    assert confirmed["payment"]["status"] == "succeeded"
    assert confirmed["payment"]["invoice_number"]
    assert confirmed["subscription"]["plan"]["code"] == "starter"
    assert confirmed["subscription"]["status"] == "active"

    # Re-confirming is idempotent: same payment, no duplicate invoice.
    again = await _confirm(client, tenant.token, org_id, reference)
    assert again["payment"]["id"] == confirmed["payment"]["id"]

    payments = await client.get(
        f"/api/v1/organizations/{org_id}/billing/payments",
        headers=_bearer(tenant.token),
    )
    assert payments.status_code == 200, payments.text
    assert len(payments.json()) == 1

    invoices = await client.get(
        f"/api/v1/organizations/{org_id}/billing/invoices",
        headers=_bearer(tenant.token),
    )
    assert invoices.status_code == 200, invoices.text
    assert len(invoices.json()) == 1
    assert invoices.json()[0]["invoice_number"]


async def test_payment_failure_does_not_activate(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-fail")
    org_id = tenant.organization["id"]

    provider: PaymentProvider = FailingPaymentProvider(get_settings())
    app.dependency_overrides[get_payment_provider] = lambda: provider
    try:
        checkout = await _checkout(
            client, tenant.token, org_id, "starter", "monthly"
        )
        reference = checkout["payment"]["provider_reference"]
        response = await client.post(
            f"/api/v1/organizations/{org_id}/billing/checkout/confirm",
            headers=_bearer(tenant.token),
            json={"reference": reference},
        )
        assert response.status_code == 402, response.text
        assert response.json()["error"]["code"] == "payment_failed"
    finally:
        app.dependency_overrides.pop(get_payment_provider, None)

    overview = await _billing(client, tenant.token, org_id)
    assert overview["subscription"]["plan"]["code"] == "free"
    payments = await client.get(
        f"/api/v1/organizations/{org_id}/billing/payments",
        headers=_bearer(tenant.token),
    )
    assert payments.json()[0]["status"] == "failed"


async def test_downgrade_to_free_is_immediate_and_upgrade_via_checkout(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-change")
    org_id = tenant.organization["id"]

    checkout = await _checkout(
        client, tenant.token, org_id, "starter", "monthly"
    )
    await _confirm(
        client, tenant.token, org_id, checkout["payment"]["provider_reference"]
    )

    # Downgrade to Free via the checkout endpoint is immediate, no payment.
    downgrade = await _checkout(client, tenant.token, org_id, "free", "monthly")
    assert downgrade["immediate"] is True
    assert downgrade["subscription"]["plan"]["code"] == "free"

    # Upgrade again, this time to Professional.
    checkout2 = await _checkout(
        client, tenant.token, org_id, "professional", "monthly"
    )
    confirmed = await _confirm(
        client, tenant.token, org_id, checkout2["payment"]["provider_reference"]
    )
    assert confirmed["subscription"]["plan"]["code"] == "professional"


async def test_cancel_schedules_downgrade_at_period_end(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-cancel")
    org_id = tenant.organization["id"]

    checkout = await _checkout(client, tenant.token, org_id, "starter", "monthly")
    await _confirm(
        client, tenant.token, org_id, checkout["payment"]["provider_reference"]
    )

    response = await client.post(
        f"/api/v1/organizations/{org_id}/billing/cancel",
        headers=_bearer(tenant.token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["cancel_at_period_end"] is True
    assert response.json()["plan"]["code"] == "starter"

    # Simulate the period ending, then read billing (lazy advancement).
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    await _update_subscription(
        org_id,
        values={
            "current_period_start": now - timedelta(days=32),
            "current_period_end": now - timedelta(days=2),
        },
    )
    overview = await _billing(client, tenant.token, org_id)
    assert overview["subscription"]["plan"]["code"] == "free"
    assert overview["subscription"]["status"] == "active"
    assert overview["subscription"]["cancel_at_period_end"] is False


async def test_trial_then_past_due_then_expired_blocks_mutations(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-trial")
    org_id = tenant.organization["id"]

    response = await client.post(
        f"/api/v1/organizations/{org_id}/billing/trial",
        headers=_bearer(tenant.token),
        json={"plan_code": "starter", "interval": "monthly"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["subscription"]["status"] == "trialing"
    assert response.json()["subscription"]["trial_ends_at"]

    # Trial still allows assistant creation.
    assistant = await _create_assistant(client, tenant.token, org_id, "Trial A")
    assert assistant["id"]

    # Expire the trial: past_due grace then expired.
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    await _update_subscription(
        org_id,
        values={
            "trial_ends_at": now - timedelta(days=1),
            "past_due_since": now - timedelta(days=30),
        },
    )
    overview = await _billing(client, tenant.token, org_id)
    assert overview["subscription"]["status"] == "expired"

    blocked = await client.post(
        f"/api/v1/organizations/{org_id}/assistants",
        headers=_bearer(tenant.token),
        json={"name": "Should Fail"},
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "subscription_expired"


async def test_webhook_success_duplicate_and_bad_signature(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-webhook")
    org_id = tenant.organization["id"]

    checkout = await _checkout(client, tenant.token, org_id, "starter", "monthly")
    payment = checkout["payment"]
    event_id = f"evt-{test_prefix}-{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "type": "payment.succeeded",
        "data": {
            "reference": payment["provider_reference"],
            "amount_minor": payment["amount_minor"],
            "currency": payment["currency"],
            "transaction_id": "mock-tx-test-1",
        },
    }
    body = json.dumps(payload).encode()

    # Bad signature is rejected.
    bad = await client.post(
        "/api/v1/billing/webhooks/mock",
        headers={"x-mock-signature": "0" * 64},
        content=body,
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "invalid_webhook_signature"

    good_headers = {"x-mock-signature": _mock_signature(body)}
    first = await client.post(
        "/api/v1/billing/webhooks/mock",
        headers=good_headers,
        content=body,
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "processed"

    overview = await _billing(client, tenant.token, org_id)
    assert overview["subscription"]["plan"]["code"] == "starter"

    # Duplicate delivery is acknowledged without a second application.
    duplicate = await client.post(
        "/api/v1/billing/webhooks/mock",
        headers=good_headers,
        content=body,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] == "duplicate"

    invoices = await client.get(
        f"/api/v1/organizations/{org_id}/billing/invoices",
        headers=_bearer(tenant.token),
    )
    assert len(invoices.json()) == 1


async def test_refund_via_platform_admin_and_revenue(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-refund")
    org_id = tenant.organization["id"]

    checkout = await _checkout(client, tenant.token, org_id, "starter", "monthly")
    confirmed = await _confirm(
        client, tenant.token, org_id, checkout["payment"]["provider_reference"]
    )
    payment_id = confirmed["payment"]["id"]

    # Tenant members cannot refund through the platform endpoint.
    forbidden = await client.post(
        f"/api/v1/platform/payments/{payment_id}/refund",
        headers=_bearer(tenant.token),
    )
    assert forbidden.status_code == 403, forbidden.text

    refunded = await client.post(
        f"/api/v1/platform/payments/{payment_id}/refund",
        headers=_bearer(owner_token),
    )
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["kind"] == "refund"

    payments = await client.get(
        f"/api/v1/organizations/{org_id}/billing/payments",
        headers=_bearer(tenant.token),
    )
    kinds = {row["kind"] for row in payments.json()}
    assert "refund" in kinds

    revenue = await client.get(
        "/api/v1/platform/revenue", headers=_bearer(owner_token)
    )
    assert revenue.status_code == 200, revenue.text
    data = revenue.json()
    assert data["gross_minor"] > 0
    assert data["refunds_minor"] >= confirmed["payment"]["amount_minor"]
    assert data["net_minor"] == data["gross_minor"] - data["refunds_minor"]


async def test_platform_admin_dashboard(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-plat")
    org_id = tenant.organization["id"]

    orgs = await client.get(
        "/api/v1/platform/organizations", headers=_bearer(owner_token)
    )
    assert orgs.status_code == 200, orgs.text
    assert any(row["id"] == org_id for row in orgs.json())

    subs = await client.get(
        "/api/v1/platform/subscriptions", headers=_bearer(owner_token)
    )
    assert subs.status_code == 200, subs.text
    assert any(row["org_id"] == org_id for row in subs.json())

    payments = await client.get(
        "/api/v1/platform/payments", headers=_bearer(owner_token)
    )
    assert payments.status_code == 200, payments.text

    usage = await client.get(
        "/api/v1/platform/usage", headers=_bearer(owner_token)
    )
    assert usage.status_code == 200, usage.text
    assert "organizations" in usage.json()

    # Create and deactivate a plan.
    code = f"test-{uuid.uuid4().hex[:6]}"
    created = await client.post(
        "/api/v1/platform/plans",
        headers=_bearer(owner_token),
        json={"code": code, "name": "Test Plan", "is_active": True},
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/platform/plans/{plan_id}",
        headers=_bearer(owner_token),
        json={"is_active": False},
    )
    assert updated.status_code == 200, updated.text

    # Tenant members cannot create plans.
    forbidden = await client.post(
        "/api/v1/platform/plans",
        headers=_bearer(tenant.token),
        json={"code": f"test2-{uuid.uuid4().hex[:6]}", "name": "Nope"},
    )
    assert forbidden.status_code == 403, forbidden.text


async def test_chat_and_ingestion_quota_enforcement(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-quota")
    org_id = tenant.organization["id"]

    before = await _set_free_limits(
        request_allowance=3, token_allowance=10_000
    )
    try:
        assistant = await _create_assistant(client, tenant.token, org_id)
        await _upload(
            client,
            tenant.token,
            org_id,
            assistant["id"],
            content=RECEIPT_GUIDE.encode(),
        )
        await _drain(fake_ai, storage)
        # Ingestion consumed 1 of 3: two chats remain.

        conversation = await _conversation(
            client, tenant.token, org_id, assistant["id"]
        )
        first = await _ask(
            client,
            tenant.token,
            org_id,
            conversation["id"],
            "How do I issue a receipt?",
        )
        assert first["assistant_message"]["content"]
        await _ask(
            client,
            tenant.token,
            org_id,
            conversation["id"],
            "How do I issue a receipt?",
        )
        response = await client.post(
            f"/api/v1/organizations/{org_id}/conversations/{conversation['id']}/messages",
            headers=_bearer(tenant.token),
            json={"question": "How do I issue a receipt?"},
        )
        assert response.status_code == 429, response.text
        assert response.json()["error"]["code"] in {
            "request_quota_exceeded",
            "token_quota_exceeded",
        }
    finally:
        await _restore_free_limits(before)


async def test_assistant_document_storage_limits(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-res")
    org_id = tenant.organization["id"]

    before = await _set_free_limits(max_assistants=1)
    try:
        await _create_assistant(client, tenant.token, org_id, "First A")
        response = await client.post(
            f"/api/v1/organizations/{org_id}/assistants",
            headers=_bearer(tenant.token),
            json={"name": "Second A"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "assistant_limit_reached"
    finally:
        await _restore_free_limits(before)

    before = await _set_free_limits(max_documents=1)
    try:
        assistant = await _create_assistant(client, tenant.token, org_id, "Doc A")
        await _upload(
            client,
            tenant.token,
            org_id,
            assistant["id"],
            filename="a.txt",
            content=b"hello world",
        )
        response = await client.post(
            f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}/documents",
            headers=_bearer(tenant.token),
            files={"file": ("b.txt", b"more content", "text/plain")},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "document_limit_reached"
    finally:
        await _restore_free_limits(before)


async def test_concurrent_requests_share_quota_safely(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-conc")
    org_id = tenant.organization["id"]

    before = await _set_free_limits(
        request_allowance=2, token_allowance=100_000
    )
    try:
        assistant = await _create_assistant(client, tenant.token, org_id)
        await _upload(
            client,
            tenant.token,
            org_id,
            assistant["id"],
            content=RECEIPT_GUIDE.encode(),
        )
        await _drain(fake_ai, storage)
        conversation = await _conversation(
            client, tenant.token, org_id, assistant["id"]
        )

        async def _one() -> int:
            response = await client.post(
                f"/api/v1/organizations/{org_id}/conversations/{conversation['id']}/messages",
                headers=_bearer(tenant.token),
                json={"question": "How do I issue a receipt?"},
            )
            return response.status_code

        # Ingestion already consumed one request; allowance is 2, so only one
        # concurrent chat may succeed.
        results = await asyncio.gather(_one(), _one(), _one(), _one())
        assert results.count(200) <= 1, results
        assert results.count(429) >= 3, results
    finally:
        await _restore_free_limits(before)


async def test_billing_isolation_and_content_boundary(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "bill-iso-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "bill-iso-b")
    org_b = tenant_b.organization["id"]

    # Tenant A cannot read tenant B's billing (404, not 403: no existence leak).
    response = await client.get(
        f"/api/v1/organizations/{org_b}/billing",
        headers=_bearer(tenant_a.token),
    )
    assert response.status_code == 404, response.text

    # Tenant A cannot checkout for tenant B.
    response = await client.post(
        f"/api/v1/organizations/{org_b}/billing/checkout",
        headers=_bearer(tenant_a.token),
        json={"plan_code": "starter"},
    )
    assert response.status_code == 404, response.text

    # A member (non-admin) cannot mutate billing.
    member = await _tenant(
        client, owner_token, test_prefix, "bill-iso-member", role="client_member"
    )
    org_member = member.organization["id"]
    response = await client.post(
        f"/api/v1/organizations/{org_member}/billing/checkout",
        headers=_bearer(member.token),
        json={"plan_code": "starter"},
    )
    assert response.status_code == 403, response.text
    response = await client.get(
        f"/api/v1/organizations/{org_member}/billing",
        headers=_bearer(member.token),
    )
    assert response.status_code == 403, response.text

    # Platform owner has no content bypass: tenant documents list requires a
    # real membership.
    response = await client.get(
        f"/api/v1/organizations/{org_b}/assistants/some-assistant/documents",
        headers=_bearer(owner_token),
    )
    assert response.status_code in (403, 404), response.text
