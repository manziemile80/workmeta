"""Stage 9: self-service registration.

Revision ID: 20261113_0009
Revises: 20261111_0008
Create Date: 2026-11-13

Registration lets a person create their own account, their first organization
and the Free subscription that organization starts with. The organization row
was previously insertable by platform admins only, so the insert policy is
widened:

* a platform admin may still create any organization;
* an authenticated person may create an organization while they have no
  membership at all, which is exactly the state a freshly registered person is
  in. Once the membership exists the person is a member of their own tenant
  and cannot create another one through this path.

Nothing else changes: the tenant tables keep forced row-level security, the
runtime role keeps its grants, and no existing row is touched, so the
migration is safe to run on a live database.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261113_0009"
down_revision: str | None = "20261111_0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INSERT_POLICY = """
    CREATE POLICY organizations_insert ON public.organizations
    FOR INSERT WITH CHECK (
        app_is_platform_admin()
        OR (
            app_current_user() IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM public.memberships m
                WHERE m.user_id = app_current_user()
            )
        )
    )
"""

_ADMIN_ONLY_POLICY = """
    CREATE POLICY organizations_insert ON public.organizations
    FOR INSERT WITH CHECK (app_is_platform_admin())
"""


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS organizations_insert ON public.organizations")
    op.execute(_INSERT_POLICY)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS organizations_insert ON public.organizations")
    op.execute(_ADMIN_ONLY_POLICY)
