"""Stage 10: trained extensions for institutional systems.

Revision ID: 20261114_0010
Revises: 20261113_0009
Create Date: 2026-11-14

Adds the per-client extension unit: a trained, signed package for one target
system at one version. Everything a client uploads, everything the AI extracts
from it and everything the agent is allowed to do lives in that client's own
organization.

Five new tables, all carrying ``org_id`` and protected by forced row-level
security with the same tenant policy the knowledge tables use, so one client
can never read another client's training materials, procedures, tests or
permissions:

* ``extensions``             - the extension, its target system and version,
                                recognition rules, permissions and lifecycle.
* ``training_materials``     - manuals, screen recordings, audio instructions
                                and written procedures.
* ``extension_procedures``   - the operating procedures extracted from them.
* ``extension_tests``        - the questions that check the assistant really
                                understands the system.
* ``extension_action_plans`` - the authority record for an action the agent
                                performs, including the user's confirmation.

``subscription_plans`` gains ``max_extensions`` (NULL means unlimited) so
extensions can be metered like assistants and documents.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261114_0010"
down_revision: str | None = "20261113_0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TENANT_TABLES = (
    "extensions",
    "training_materials",
    "extension_procedures",
    "extension_tests",
    "extension_action_plans",
)


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def _json_default(value: str) -> sa.text:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("max_extensions", sa.Integer(), nullable=True),
    )

    op.create_table(
        "extensions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("system_code", sa.String(length=64), nullable=False),
        sa.Column("system_name", sa.String(length=160), nullable=False),
        sa.Column("system_version", sa.String(length=64), nullable=False),
        sa.Column(
            "target_kind",
            sa.String(length=16),
            nullable=False,
            server_default="web",
        ),
        sa.Column(
            "url_patterns",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "app_hints",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "training_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("package_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("package_signature", sa.String(length=128), nullable=True),
        sa.Column("signing_key_id", sa.String(length=64), nullable=True),
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
            name="fk_extensions_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name="fk_extensions_assistant_id_assistants",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "slug", name="uq_extensions_org_slug"),
        sa.CheckConstraint(
            "status IN ('draft', 'training', 'ready', 'published', 'retired')",
            name="ck_extensions_status_valid",
        ),
        sa.CheckConstraint(
            "training_status IN ('pending', 'extracting', 'extracted', 'failed')",
            name="ck_extensions_training_status_valid",
        ),
        sa.CheckConstraint(
            "target_kind IN ('web', 'desktop')",
            name="ck_extensions_target_kind_valid",
        ),
    )
    op.create_index(
        "ix_extensions_org_id_status",
        "extensions",
        ["org_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_extensions_assistant_id",
        "extensions",
        ["assistant_id"],
        unique=False,
    )

    op.create_table(
        "training_materials",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="note",
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column(
            "size_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="stored",
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_units", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            name="fk_training_materials_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extension_id"],
            ["extensions.id"],
            name="fk_training_materials_extension_id_extensions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_training_materials_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_training_materials_created_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind IN ('document', 'video', 'audio', 'note')",
            name="ck_training_materials_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('stored', 'extracting', 'ready', 'failed', 'archived')",
            name="ck_training_materials_status_valid",
        ),
    )
    op.create_index(
        "ix_training_materials_org_extension",
        "training_materials",
        ["org_id", "extension_id"],
        unique=False,
    )
    op.create_index(
        "ix_training_materials_status",
        "training_materials",
        ["org_id", "status"],
        unique=False,
    )

    op.create_table(
        "extension_procedures",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "steps",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "source_material_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
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
            name="fk_extension_procedures_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extension_id"],
            ["extensions.id"],
            name="fk_extension_procedures_extension_id_extensions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "extension_id", "title", name="uq_extension_procedures_title"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_extension_procedures_status_valid",
        ),
    )
    op.create_index(
        "ix_extension_procedures_org_extension",
        "extension_procedures",
        ["org_id", "extension_id"],
        unique=False,
    )

    op.create_table(
        "extension_tests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "expected_points",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "covered_points",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "missed_points",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "score", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "outcome",
            sa.String(length=16),
            nullable=False,
            server_default="failed",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_extension_tests_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extension_id"],
            ["extensions.id"],
            name="fk_extension_tests_extension_id_extensions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_extension_tests_created_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="ck_extension_tests_outcome_valid",
        ),
    )
    op.create_index(
        "ix_extension_tests_org_extension",
        "extension_tests",
        ["org_id", "extension_id"],
        unique=False,
    )

    op.create_table(
        "extension_action_plans",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column(
            "requires_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
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
            name="fk_extension_action_plans_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extension_id"],
            ["extensions.id"],
            name="fk_extension_action_plans_extension_id_extensions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name="fk_extension_action_plans_confirmed_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executed', 'rejected')",
            name="ck_extension_action_plans_status_valid",
        ),
    )
    op.create_index(
        "ix_extension_action_plans_org_extension",
        "extension_action_plans",
        ["org_id", "extension_id"],
        unique=False,
    )

    # Tenant isolation: forced row-level security, same policy as the knowledge
    # tables. The runtime role can read and write its own tenant only.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON public.{table}
            FOR ALL USING (org_id = app_current_org())
            WITH CHECK (org_id = app_current_org())
            """
        )

    role = _app_role()
    for table in TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.{table} TO {role}")
        # The application never deletes extension rows directly.
        op.execute(f"REVOKE DELETE ON public.{table} FROM {role}")

    # The Free plan starts with one trained extension; other plans can raise it.
    op.execute(
        "UPDATE public.subscription_plans SET max_extensions = 1 "
        "WHERE code = 'free' AND max_extensions IS NULL"
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS public.{table}")
    op.drop_column("subscription_plans", "max_extensions")
