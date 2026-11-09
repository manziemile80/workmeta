"""Stage 1: identity, tenancy, sessions, invitations, audit and RLS.

Revision ID: 20261108_0001
Revises:
Create Date: 2026-11-08
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261108_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "country", sa.String(length=2), nullable=False, server_default="RW"
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
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
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_organizations_status_valid",
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'disabled')",
            name="ck_users_status_valid",
        ),
    )

    op.create_table(
        "memberships",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default="client_member",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
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
            name="fk_memberships_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
        sa.CheckConstraint(
            "role IN ('client_admin', 'client_member')",
            name="ck_memberships_role_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_memberships_status_valid",
        ),
    )
    op.create_index(
        "ix_memberships_user_id", "memberships", ["user_id"], unique=False
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="web"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_sessions_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint(
            "kind IN ('web', 'extension')", name="ck_sessions_kind_valid"
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_sessions_expires_at", "sessions", ["expires_at"], unique=False
    )

    op.create_table(
        "invitations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_invitations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_invitations_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.CheckConstraint(
            "role IN ('client_admin', 'client_member')",
            name="ck_invitations_role_valid",
        ),
    )
    op.create_index(
        "ix_invitations_org_email",
        "invitations",
        ["org_id", "email"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), primary_key=True
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_audit_logs_org_id_organizations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_audit_logs_org_id_created_at",
        "audit_logs",
        ["org_id", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # RLS helper functions. They read transaction-local settings and return
    # NULL/false when a request has not established a context.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_org() RETURNS uuid
        LANGUAGE sql STABLE AS $fn$
            SELECT NULLIF(current_setting('app.current_org', true), '')::uuid
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_user() RETURNS uuid
        LANGUAGE sql STABLE AS $fn$
            SELECT NULLIF(current_setting('app.current_user', true), '')::uuid
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_invitation_token() RETURNS text
        LANGUAGE sql STABLE AS $fn$
            SELECT NULLIF(current_setting('app.invitation_token', true), '')
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_platform_admin() RETURNS boolean
        LANGUAGE sql STABLE AS $fn$
            SELECT EXISTS (
                SELECT 1
                FROM public.users u
                WHERE u.id = app_current_user()
                  AND u.is_platform_admin
                  AND u.status = 'active'
            )
        $fn$;
        """
    )

    # ------------------------------------------------------------------
    # Row-Level Security. Every tenant table is enabled AND forced, so even a
    # non-superuser table owner cannot silently read across tenants.
    # ------------------------------------------------------------------
    tenant_tables = ("organizations", "memberships", "invitations", "audit_logs")
    for table in tenant_tables:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY organizations_select ON public.organizations
        FOR SELECT USING (
            id = app_current_org()
            OR app_is_platform_admin()
            OR EXISTS (
                SELECT 1 FROM public.memberships m
                WHERE m.org_id = organizations.id
                  AND m.user_id = app_current_user()
                  AND m.status = 'active'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY organizations_insert ON public.organizations
        FOR INSERT WITH CHECK (app_is_platform_admin())
        """
    )
    op.execute(
        """
        CREATE POLICY organizations_update ON public.organizations
        FOR UPDATE
        USING (id = app_current_org() OR app_is_platform_admin())
        WITH CHECK (id = app_current_org() OR app_is_platform_admin())
        """
    )

    op.execute(
        """
        CREATE POLICY memberships_select ON public.memberships
        FOR SELECT USING (
            org_id = app_current_org() OR user_id = app_current_user()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY memberships_insert ON public.memberships
        FOR INSERT WITH CHECK (org_id = app_current_org())
        """
    )
    op.execute(
        """
        CREATE POLICY memberships_update ON public.memberships
        FOR UPDATE
        USING (org_id = app_current_org())
        WITH CHECK (org_id = app_current_org())
        """
    )
    op.execute(
        """
        CREATE POLICY memberships_delete ON public.memberships
        FOR DELETE USING (org_id = app_current_org())
        """
    )

    op.execute(
        """
        CREATE POLICY invitations_all ON public.invitations
        FOR ALL
        USING (
            org_id = app_current_org()
            OR token_hash = app_invitation_token()
        )
        WITH CHECK (
            org_id = app_current_org()
            OR token_hash = app_invitation_token()
        )
        """
    )

    op.execute(
        """
        CREATE POLICY audit_logs_select ON public.audit_logs
        FOR SELECT USING (
            org_id = app_current_org()
            OR (org_id IS NULL AND app_is_platform_admin())
        )
        """
    )
    op.execute(
        """
        CREATE POLICY audit_logs_insert ON public.audit_logs
        FOR INSERT WITH CHECK (
            org_id = app_current_org() OR org_id IS NULL
        )
        """
    )

    # ------------------------------------------------------------------
    # Least-privilege grants. Default privileges keep future migrations from
    # forgetting the runtime role.
    # ------------------------------------------------------------------
    role = _app_role()
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}"
    )
    op.execute(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE ON TABLES TO {role}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {role}"
    )
    # No application code hard-deletes; removing DELETE limits the blast radius
    # of a future query bug or SQL injection.
    op.execute(f"REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM {role}")
    # The audit trail is append-only for the application.
    op.execute(f"REVOKE UPDATE, DELETE ON TABLE public.audit_logs FROM {role}")
    # The runtime must never be able to rewrite migration state.
    op.execute(f"REVOKE ALL ON TABLE public.alembic_version FROM {role}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS organizations_select ON public.organizations")
    op.execute("DROP POLICY IF EXISTS organizations_insert ON public.organizations")
    op.execute("DROP POLICY IF EXISTS organizations_update ON public.organizations")
    op.execute("DROP POLICY IF EXISTS memberships_select ON public.memberships")
    op.execute("DROP POLICY IF EXISTS memberships_insert ON public.memberships")
    op.execute("DROP POLICY IF EXISTS memberships_update ON public.memberships")
    op.execute("DROP POLICY IF EXISTS memberships_delete ON public.memberships")
    op.execute("DROP POLICY IF EXISTS invitations_all ON public.invitations")
    op.execute("DROP POLICY IF EXISTS audit_logs_select ON public.audit_logs")
    op.execute("DROP POLICY IF EXISTS audit_logs_insert ON public.audit_logs")

    op.drop_table("audit_logs")
    op.drop_table("invitations")
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")

    op.execute("DROP FUNCTION IF EXISTS app_is_platform_admin()")
    op.execute("DROP FUNCTION IF EXISTS app_invitation_token()")
    op.execute("DROP FUNCTION IF EXISTS app_current_user()")
    op.execute("DROP FUNCTION IF EXISTS app_current_org()")
