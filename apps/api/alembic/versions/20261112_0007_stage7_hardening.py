"""Stage 7: MFA, idle timeout, password resets, suspicious-login tracking.

Revision ID: 20261112_0007
Revises: 20261111_0006
Create Date: 2026-11-12

New columns are additive and nullable/defaulted so existing rows keep
working. ``password_resets`` is a platform-global ledger (no tenant RLS,
like ``users``/``sessions``): the token is looked up by hash before any
tenant context exists, exactly as invitations do. Only hashes are stored;
the plaintext reset token is returned once and never in audit logs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261112_0007"
down_revision: str | None = "20261111_0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    # User columns: MFA (TOTP) + suspicious-login tracking.
    for column, kind in (
        ("mfa_secret", sa.Text()),
        ("mfa_enabled_at", sa.DateTime(timezone=True)),
        ("last_login_ip", sa.String(length=45)),
    ):
        op.add_column("users", sa.Column(column, kind, nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "mfa_last_step",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # Session idle tracking: last activity timestamp.
    op.add_column(
        "sessions",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Password reset ledger (platform-global, hash-only tokens).
    op.create_table(
        "password_resets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_resets_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "token_hash", name="uq_password_resets_token_hash"
        ),
    )
    op.create_index(
        "ix_password_resets_user_id", "password_resets", ["user_id"], unique=False
    )

    # Grants: users/sessions already have SELECT/INSERT/UPDATE for wm_app.
    # password_resets is append-ish: INSERT/SELECT/UPDATE (no DELETE), no RLS
    # (platform-global, like users/sessions — looked up pre-auth by hash).
    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.password_resets TO {role}")
    op.execute(f"REVOKE DELETE ON public.password_resets FROM {role}")


def downgrade() -> None:
    op.drop_index("ix_password_resets_user_id", table_name="password_resets")
    op.drop_table("password_resets")
    op.drop_column("sessions", "last_seen_at")
    op.drop_column("users", "failed_login_count")
    op.drop_column("users", "last_login_ip")
    op.drop_column("users", "mfa_last_step")
    op.drop_column("users", "mfa_enabled_at")
    op.drop_column("users", "mfa_secret")
