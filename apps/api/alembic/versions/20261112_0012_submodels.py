"""Stage 12: WorkMeta AI Submodels.

Revision ID: 20261112_0012
Revises: 20261114_0011
Create Date: 2026-11-12

Adds the submodel unit: a trained model for one **task** that a client can
export, install on another machine and use there without training it again.

Five new tables, all carrying ``org_id`` and protected by forced row-level
security with the same tenant policy the knowledge tables use:

* ``submodels``             - the submodel: its task, its lifecycle, its
                               permission policy and the package it publishes.
* ``submodel_materials``    - the recordings, audio, documents and written
                               notes the AI learns from.
* ``submodel_procedures``   - the workflows extracted from them.
* ``submodel_tests``        - the questions that check the submodel learned.
* ``submodel_action_plans`` - the authority record for an action it performs.

``subscription_plans`` gains ``max_submodels`` (NULL means unlimited) so
submodels can be metered like assistants, documents and extensions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261112_0012"
down_revision: str | None = "20261114_0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TENANT_TABLES = (
    "submodels",
    "submodel_materials",
    "submodel_procedures",
    "submodel_tests",
    "submodel_action_plans",
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
        sa.Column("max_submodels", sa.Integer(), nullable=True),
    )

    op.create_table(
        "submodels",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("task", sa.String(length=400), nullable=False),
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
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=False,
            server_default="trained",
        ),
        sa.Column("source_label", sa.String(length=200), nullable=True),
        sa.Column("source_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("package_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("package_signature", sa.String(length=128), nullable=True),
        sa.Column("signing_key_id", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_submodels_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_submodels_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("org_id", "slug", name="uq_submodels_org_slug"),
        sa.CheckConstraint(
            "status IN ('draft', 'training', 'trained', 'published', 'retired')",
            name="ck_submodels_status_valid",
        ),
        sa.CheckConstraint(
            "origin IN ('trained', 'imported')",
            name="ck_submodels_origin_valid",
        ),
        sa.CheckConstraint(
            "target_kind IN ('web', 'desktop')",
            name="ck_submodels_target_kind_valid",
        ),
    )
    op.create_index(
        "ix_submodels_org_id_status",
        "submodels",
        ["org_id", "status"],
        unique=False,
    )

    op.create_table(
        "submodel_materials",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submodel_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            name="fk_submodel_materials_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submodel_id"],
            ["submodels.id"],
            name="fk_submodel_materials_submodel_id_submodels",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_submodel_materials_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_submodel_materials_created_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind IN ('video', 'audio', 'document', 'note')",
            name="ck_submodel_materials_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('stored', 'extracting', 'ready', 'failed', 'archived')",
            name="ck_submodel_materials_status_valid",
        ),
    )
    op.create_index(
        "ix_submodel_materials_org_submodel",
        "submodel_materials",
        ["org_id", "submodel_id"],
        unique=False,
    )

    op.create_table(
        "submodel_procedures",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submodel_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            name="fk_submodel_procedures_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submodel_id"],
            ["submodels.id"],
            name="fk_submodel_procedures_submodel_id_submodels",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_submodel_procedures_status_valid",
        ),
    )
    op.create_index(
        "ix_submodel_procedures_org_submodel",
        "submodel_procedures",
        ["org_id", "submodel_id"],
        unique=False,
    )

    op.create_table(
        "submodel_tests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submodel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.String(length=2000), nullable=False),
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
            sa.String(length=20),
            nullable=False,
            server_default="error",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_submodel_tests_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submodel_id"],
            ["submodels.id"],
            name="fk_submodel_tests_submodel_id_submodels",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="ck_submodel_tests_outcome_valid",
        ),
    )
    op.create_index(
        "ix_submodel_tests_org_submodel",
        "submodel_tests",
        ["org_id", "submodel_id"],
        unique=False,
    )

    op.create_table(
        "submodel_action_plans",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submodel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
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
            server_default=sa.text("false"),
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
            name="fk_submodel_action_plans_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submodel_id"],
            ["submodels.id"],
            name="fk_submodel_action_plans_submodel_id_submodels",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name="fk_submodel_action_plans_confirmed_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executed', 'rejected')",
            name="ck_submodel_action_plans_status_valid",
        ),
    )
    op.create_index(
        "ix_submodel_action_plans_org_submodel",
        "submodel_action_plans",
        ["org_id", "submodel_id"],
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
        # The application never deletes submodel rows directly.
        op.execute(f"REVOKE DELETE ON public.{table} FROM {role}")

    # The Free plan starts with one submodel; other plans can raise it.
    op.execute(
        "UPDATE public.subscription_plans SET max_submodels = 1 "
        "WHERE code = 'free' AND max_submodels IS NULL"
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS public.{table}")
    op.drop_column("subscription_plans", "max_submodels")
