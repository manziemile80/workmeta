"""Stage 5: subscription plans, organization subscriptions, payments and quotas.

Revision ID: 20261110_0005
Revises: 20261109_0004
Create Date: 2026-11-10

Design notes
------------
* Plans are platform metadata. Tenants may read active plans; only a platform
  administrator context may create or change them.
* Subscriptions, payment history and quota counters are tenant data. They keep
  the same forced-RLS tenant isolation as every other tenant table, plus a
  platform-administrator SELECT policy for aggregate billing administration.
* The webhook idempotency ledger is platform-level and append-only. It has no
  tenant RLS because the provider callback arrives without a tenant session.
* ``app_webhook_org`` is a narrow SECURITY DEFINER capability that resolves an
  authenticated provider reference to its organization. The webhook handler
  verifies the provider signature before calling it, then switches to that
  tenant's RLS context to settle the payment.
* Payments never store card data. Only provider references, amounts, currency
  and non-sensitive metadata are persisted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261110_0005"
down_revision: str | None = "20261109_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Stable identifiers make the seed reproducible and let the free-plan backfill
# reference a row without a second lookup.
FREE_PLAN_ID = "00000000-0000-4000-8000-000000000001"
STARTER_PLAN_ID = "00000000-0000-4000-8000-000000000002"
PROFESSIONAL_PLAN_ID = "00000000-0000-4000-8000-000000000003"
ENTERPRISE_PLAN_ID = "00000000-0000-4000-8000-000000000004"

_BILLING_TABLES = (
    "subscription_plans",
    "organization_subscriptions",
    "payment_transactions",
    "usage_counters",
)


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "monthly_price_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "annual_price_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="RWF",
        ),
        sa.Column(
            "price_exponent",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_assistants", sa.Integer(), nullable=True),
        sa.Column("max_documents", sa.Integer(), nullable=True),
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column("monthly_token_allowance", sa.BigInteger(), nullable=True),
        sa.Column("monthly_request_allowance", sa.Integer(), nullable=True),
        sa.Column(
            "features",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("code", name="uq_subscription_plans_code"),
        sa.CheckConstraint(
            "monthly_price_minor >= 0 AND annual_price_minor >= 0",
            name="ck_subscription_plans_price_non_negative",
        ),
        sa.CheckConstraint(
            "price_exponent BETWEEN 0 AND 3",
            name="ck_subscription_plans_price_exponent_valid",
        ),
        sa.CheckConstraint(
            "(max_assistants IS NULL OR max_assistants >= 0) AND "
            "(max_documents IS NULL OR max_documents >= 0) AND "
            "(max_storage_bytes IS NULL OR max_storage_bytes >= 0) AND "
            "(monthly_token_allowance IS NULL OR monthly_token_allowance >= 0) AND "
            "(monthly_request_allowance IS NULL OR monthly_request_allowance >= 0)",
            name="ck_subscription_plans_limits_non_negative",
        ),
    )
    op.create_index(
        "ix_subscription_plans_active_sort",
        "subscription_plans",
        ["is_active", "sort_order"],
        unique=False,
    )

    op.create_table(
        "organization_subscriptions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "billing_interval",
            sa.String(length=16),
            nullable=False,
            server_default="monthly",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="mock",
        ),
        sa.Column("provider_customer_id", sa.String(length=120), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "current_period_start",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "current_period_end", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_organization_subscriptions_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name="fk_organization_subscriptions_plan_id_subscription_plans",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "org_id", name="uq_organization_subscriptions_org_id"
        ),
        sa.CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'canceled', 'expired')",
            name="ck_organization_subscriptions_status_valid",
        ),
        sa.CheckConstraint(
            "billing_interval IN ('monthly', 'annual')",
            name="ck_organization_subscriptions_billing_interval_valid",
        ),
        sa.CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_organization_subscriptions_period_order_valid",
        ),
    )
    op.create_index(
        "ix_organization_subscriptions_status_period_end",
        "organization_subscriptions",
        ["status", "current_period_end"],
        unique=False,
    )
    op.create_index(
        "ix_organization_subscriptions_plan_id",
        "organization_subscriptions",
        ["plan_id"],
        unique=False,
    )

    op.create_table(
        "payment_transactions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "subscription_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=120), nullable=False),
        sa.Column("provider_event_id", sa.String(length=160), nullable=True),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="payment",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "amount_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="RWF"
        ),
        sa.Column("billing_interval", sa.String(length=16), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("invoice_number", sa.String(length=40), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_payment_transactions_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["organization_subscriptions.id"],
            name="fk_payments_sub_id_org_subs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name="fk_payment_transactions_plan_id_subscription_plans",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "provider_reference",
            name="uq_payment_transactions_provider_reference",
        ),
        sa.UniqueConstraint(
            "invoice_number", name="uq_payment_transactions_invoice_number"
        ),
        sa.CheckConstraint(
            "kind IN ('payment', 'refund')",
            name="ck_payment_transactions_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="ck_payment_transactions_status_valid",
        ),
        sa.CheckConstraint(
            "billing_interval IS NULL OR billing_interval IN ('monthly', 'annual')",
            name="ck_payment_transactions_billing_interval_valid",
        ),
        sa.CheckConstraint(
            "amount_minor >= 0",
            name="ck_payment_transactions_amount_non_negative",
        ),
    )
    op.create_index(
        "ix_payment_transactions_org_created",
        "payment_transactions",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_transactions_status_created",
        "payment_transactions",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_transactions_plan_id",
        "payment_transactions",
        ["plan_id"],
        unique=False,
    )

    op.create_table(
        "processed_webhook_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider",
            "event_id",
            name="uq_processed_webhook_events_provider_event",
        ),
    )
    op.create_index(
        "ix_processed_webhook_events_processed_at",
        "processed_webhook_events",
        ["processed_at"],
        unique=False,
    )

    op.create_table(
        "usage_counters",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "period_start", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tokens_used",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "requests_used",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_usage_counters_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "org_id", "period_start", name="uq_usage_counters_org_period"
        ),
        sa.CheckConstraint(
            "tokens_used >= 0 AND requests_used >= 0",
            name="ck_usage_counters_counts_non_negative",
        ),
    )
    op.create_index(
        "ix_usage_counters_org_period",
        "usage_counters",
        ["org_id", "period_start"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Seed the four plans. Limits live here, never as application constants.
    # Free limits are deliberately generous so existing tenants keep working;
    # platform administrators can tune any plan through the API.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT INTO subscription_plans (
            id, code, name, description, monthly_price_minor, annual_price_minor,
            currency, price_exponent, max_assistants, max_documents,
            max_storage_bytes, monthly_token_allowance, monthly_request_allowance,
            features, is_active, sort_order
        ) VALUES
        (
            '{FREE_PLAN_ID}', 'free', 'Free',
            'Start with a single workspace and generous evaluation limits.',
            0, 0, 'RWF', 0, 20, 500, 2147483648, 10000000, 100000,
            '["grounded_chat", "knowledge_base", "usage_reports"]'::jsonb,
            true, 10
        ),
        (
            '{STARTER_PLAN_ID}', 'starter', 'Starter',
            'For small teams getting their first assistants into production.',
            15000, 150000, 'RWF', 0, 10, 250, 5368709120, 5000000, 50000,
            '["grounded_chat", "knowledge_base", "usage_reports", "priority_support"]'::jsonb,
            true, 20
        ),
        (
            '{PROFESSIONAL_PLAN_ID}', 'professional', 'Professional',
            'Growing teams that need more capacity and advanced controls.',
            45000, 450000, 'RWF', 0, 50, 2000, 26843545600, 25000000, 250000,
            '["grounded_chat", "knowledge_base", "usage_reports", '
            '"priority_support", "advanced_analytics", "custom_branding"]'::jsonb,
            true, 30
        ),
        (
            '{ENTERPRISE_PLAN_ID}', 'enterprise', 'Enterprise',
            'Unlimited-scale deployment with dedicated support and onboarding.',
            150000, 1500000, 'RWF', 0, NULL, NULL, NULL, NULL, NULL,
            '["grounded_chat", "knowledge_base", "usage_reports", '
            '"priority_support", "advanced_analytics", "custom_branding", '
            '"sso", "dedicated_support"]'::jsonb,
            true, 40
        )
        ON CONFLICT (code) DO NOTHING
        """
    )

    # Backfill an active free subscription for every existing organization.
    op.execute(
        f"""
        INSERT INTO organization_subscriptions (
            id, org_id, plan_id, billing_interval, status, provider,
            started_at, current_period_start, current_period_end
        )
        SELECT
            gen_random_uuid(), o.id, '{FREE_PLAN_ID}', 'monthly', 'active', 'mock',
            now(), now(), now() + interval '1 month'
        FROM organizations o
        ON CONFLICT (org_id) DO NOTHING
        """
    )

    # ------------------------------------------------------------------
    # Row-Level Security.
    # ------------------------------------------------------------------
    for table in _BILLING_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY subscription_plans_select ON public.subscription_plans
        FOR SELECT USING (
            is_active
            OR app_is_platform_admin()
            OR EXISTS (
                SELECT 1
                FROM public.organization_subscriptions s
                WHERE s.plan_id = subscription_plans.id
                  AND s.org_id = app_current_org()
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY subscription_plans_insert ON public.subscription_plans
        FOR INSERT WITH CHECK (app_is_platform_admin())
        """
    )
    op.execute(
        """
        CREATE POLICY subscription_plans_update ON public.subscription_plans
        FOR UPDATE
        USING (app_is_platform_admin())
        WITH CHECK (app_is_platform_admin())
        """
    )

    op.execute(
        """
        CREATE POLICY organization_subscriptions_select
        ON public.organization_subscriptions
        FOR SELECT USING (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY organization_subscriptions_insert
        ON public.organization_subscriptions
        FOR INSERT WITH CHECK (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY organization_subscriptions_update
        ON public.organization_subscriptions
        FOR UPDATE
        USING (org_id = app_current_org() OR app_is_platform_admin())
        WITH CHECK (org_id = app_current_org() OR app_is_platform_admin())
        """
    )

    op.execute(
        """
        CREATE POLICY payment_transactions_select
        ON public.payment_transactions
        FOR SELECT USING (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY payment_transactions_insert
        ON public.payment_transactions
        FOR INSERT WITH CHECK (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY payment_transactions_update
        ON public.payment_transactions
        FOR UPDATE
        USING (org_id = app_current_org() OR app_is_platform_admin())
        WITH CHECK (org_id = app_current_org() OR app_is_platform_admin())
        """
    )

    op.execute(
        """
        CREATE POLICY usage_counters_select ON public.usage_counters
        FOR SELECT USING (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY usage_counters_insert ON public.usage_counters
        FOR INSERT WITH CHECK (
            org_id = app_current_org() OR app_is_platform_admin()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY usage_counters_update ON public.usage_counters
        FOR UPDATE
        USING (org_id = app_current_org() OR app_is_platform_admin())
        WITH CHECK (org_id = app_current_org() OR app_is_platform_admin())
        """
    )

    # Platform usage statistics read the append-only metering table. Tenant
    # rows stay invisible to a platform admin without this explicit policy.
    op.execute(
        """
        CREATE POLICY usage_events_platform_admin_select ON public.usage_events
        FOR SELECT USING (app_is_platform_admin())
        """
    )

    # ------------------------------------------------------------------
    # Webhook capability. The provider callback has no tenant session; after
    # the signature is verified this function resolves the payment reference
    # to exactly one organization so the handler can switch RLS context.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_webhook_org(
            p_provider text, p_reference text
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
            SELECT org_id
            FROM public.payment_transactions
            WHERE provider = p_provider
              AND provider_reference = p_reference
            LIMIT 1
        $fn$;
        """
    )

    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.subscription_plans TO {role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON public.organization_subscriptions TO {role}"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON public.payment_transactions TO {role}"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.usage_counters TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON public.processed_webhook_events TO {role}")
    op.execute(f"REVOKE DELETE ON public.subscription_plans FROM {role}")
    op.execute(
        f"REVOKE DELETE ON public.organization_subscriptions FROM {role}"
    )
    op.execute(f"REVOKE DELETE ON public.payment_transactions FROM {role}")
    op.execute(f"REVOKE DELETE ON public.usage_counters FROM {role}")
    op.execute(f"REVOKE UPDATE, DELETE ON public.processed_webhook_events FROM {role}")
    op.execute(
        "REVOKE ALL ON FUNCTION app_webhook_org(text, text) FROM PUBLIC"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION app_webhook_org(text, text) TO {role}")


def downgrade() -> None:
    role = _app_role()
    op.execute(f"REVOKE EXECUTE ON FUNCTION app_webhook_org(text, text) FROM {role}")
    op.execute("DROP FUNCTION IF EXISTS app_webhook_org(text, text)")

    op.execute(
        "DROP POLICY IF EXISTS usage_events_platform_admin_select "
        "ON public.usage_events"
    )
    for table in (
        "usage_counters",
        "payment_transactions",
        "organization_subscriptions",
        "subscription_plans",
    ):
        for suffix in ("select", "insert", "update"):
            op.execute(
                f"DROP POLICY IF EXISTS {table}_{suffix} ON public.{table}"
            )

    op.drop_table("usage_counters")
    op.drop_table("processed_webhook_events")
    op.drop_table("payment_transactions")
    op.drop_table("organization_subscriptions")
    op.drop_table("subscription_plans")
