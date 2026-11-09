"""Stage 6: billing-worker capability and metrics support.

Revision ID: 20261111_0006
Revises: 20261110_0005
Create Date: 2026-11-11

The billing worker needs to discover subscriptions that are due for renewal
without gaining platform-admin login. ``app_billing_orgs`` is a narrow
SECURITY DEFINER listing function (org ids only, never content). Counter
retention is likewise a pinned, audited function rather than a runtime DELETE.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261111_0006"
down_revision: str | None = "20261110_0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_billing_orgs() RETURNS TABLE(
            org_id uuid, status text, current_period_end timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
            SELECT org_id, status, current_period_end
            FROM public.organization_subscriptions
            ORDER BY org_id ASC
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_prune_usage_counters(
            p_retain_months integer
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
        DECLARE
            deleted integer;
        BEGIN
            DELETE FROM public.usage_counters
            WHERE period_end < now() - make_interval(months => p_retain_months);
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $fn$;
        """
    )

    role = _app_role()
    for signature in ("app_billing_orgs()", "app_prune_usage_counters(integer)"):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")


def downgrade() -> None:
    role = _app_role()
    for signature in ("app_billing_orgs()", "app_prune_usage_counters(integer)"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {role}")
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
