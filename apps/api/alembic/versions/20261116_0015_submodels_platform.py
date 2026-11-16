"""Stage 15: the submodel platform, owned by the administrator.

Revision ID: 20261116_0015
Revises: 20261115_0014
Create Date: 2026-11-16

Re-creates the five submodel tables of Stage 12 as an **admin-owned**
platform, which changes their shape and their security:

* ``submodels``, ``submodel_materials``, ``submodel_procedures`` and
  ``submodel_tests`` lose ``org_id`` — they hold the platform
  administrator's training material, not any client's data.
* ``submodels`` gains ``training_status`` values for an honest run
  (``pending``/``running``/``extracted``/``failed``) and
  ``training_error`` to say what went wrong; nothing is ever reported as
  trained without a completed run.
* ``submodel_action_plans`` keeps ``org_id``: it is the authority record
  for an action performed inside one tenant, so it stays behind that
  tenant's forced row-level security.

Row-level security on the four platform tables allows:

* everything from a platform administrator (``app_is_platform_admin()``), and
* reading a **published** submodel (and only its approved workflows) by any
  signed-in user — that is what "published" means. Raw training videos,
  draft workflows and test questions never leave the administrator's side.

The action plans keep the tenant policy, extended so the administrator can
audit them across tenants.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261116_0015"
down_revision: str | None = "20261115_0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Written by the administrator; readable as a whole by a platform admin.
PLATFORM_TABLES = (
    "submodels",
    "submodel_materials",
    "submodel_procedures",
    "submodel_tests",
)

#: Belongs to the tenant where the action happens.
TENANT_TABLES = ("submodel_action_plans",)


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def _json_default(value: str) -> sa.text:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # The administrator's submodel: task, lifecycle, honest training state
    # and the package that publishing freezes.
    # ------------------------------------------------------------------
    op.create_table(
        "submodels",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
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
        sa.Column("training_error", sa.Text(), nullable=True),
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
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
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
            ["created_by"],
            ["users.id"],
            name="fk_submodels_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("slug", name="uq_submodels_slug"),
        sa.CheckConstraint(
            "status IN ('draft', 'training', 'trained', 'published', 'retired')",
            name="ck_submodels_status_valid",
        ),
        sa.CheckConstraint(
            "training_status IN ('pending', 'running', 'extracted', 'failed')",
            name="ck_submodels_training_status_valid",
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
    op.create_index("ix_submodels_status", "submodels", ["status"], unique=False)

    # ------------------------------------------------------------------
    # The training material: demonstration videos, audio and written steps.
    # ------------------------------------------------------------------
    op.create_table(
        "submodel_materials",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
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
            ["submodel_id"],
            ["submodels.id"],
            name="fk_submodel_materials_submodel_id_submodels",
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
        "ix_submodel_materials_submodel",
        "submodel_materials",
        ["submodel_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # The workflows: extracted as drafts, approved by a human, published
    # only when the submodel is.
    # ------------------------------------------------------------------
    op.create_table(
        "submodel_procedures",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
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
        "ix_submodel_procedures_submodel",
        "submodel_procedures",
        ["submodel_id", "position"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # The tests: a question, the points the answer must cover, and whether
    # it passed. Publishing requires one passing test.
    # ------------------------------------------------------------------
    op.create_table(
        "submodel_tests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
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
        "ix_submodel_tests_submodel",
        "submodel_tests",
        ["submodel_id", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # The action plans: one row per action a published submodel was asked
    # to perform, inside the tenant where it happens. This is the only
    # submodel table that keeps ``org_id``.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Row-level security: forced on every table, so even the table owner
    # cannot read across the policy.
    # ------------------------------------------------------------------
    for table in PLATFORM_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")

    # The administrator owns the training material outright.
    op.execute(
        """
        CREATE POLICY submodels_platform_admin ON public.submodels
        FOR ALL USING (app_is_platform_admin())
        WITH CHECK (app_is_platform_admin())
        """
    )
    # What "published" means: any signed-in user may read the submodel row
    # of a submodel the administrator published — and nothing else.
    op.execute(
        """
        CREATE POLICY submodels_published_select ON public.submodels
        FOR SELECT USING (
            status = 'published' AND published_at IS NOT NULL
        )
        """
    )
    for table in ("submodel_materials", "submodel_tests"):
        op.execute(
            f"""
            CREATE POLICY {table}_platform_admin ON public.{table}
            FOR ALL USING (app_is_platform_admin())
            WITH CHECK (app_is_platform_admin())
            """
        )
    # Workflows: the administrator reads and writes all of them; a user may
    # read the *approved* workflows of a *published* submodel — that is what
    # guidance answers from. Drafts and rejected steps stay private.
    op.execute(
        """
        CREATE POLICY submodel_procedures_platform_admin
        ON public.submodel_procedures
        FOR ALL USING (app_is_platform_admin())
        WITH CHECK (app_is_platform_admin())
        """
    )
    op.execute(
        """
        CREATE POLICY submodel_procedures_published_select
        ON public.submodel_procedures
        FOR SELECT USING (
            status = 'approved'
            AND EXISTS (
                SELECT 1
                FROM public.submodels s
                WHERE s.id = submodel_procedures.submodel_id
                  AND s.status = 'published'
                  AND s.published_at IS NOT NULL
            )
        )
        """
    )

    # The action plans stay tenant rows: a member sees their own tenant's
    # plans, and the administrator may audit them across tenants.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON public.{table}
            FOR ALL USING (
                org_id = app_current_org() OR app_is_platform_admin()
            )
            WITH CHECK (
                org_id = app_current_org() OR app_is_platform_admin()
            )
            """
        )

    # The application role can read and write, but never delete: submodel
    # rows are retired, not erased, so the audit trail survives.
    role = _app_role()
    for table in PLATFORM_TABLES + TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.{table} TO {role}")
        op.execute(f"REVOKE DELETE ON public.{table} FROM {role}")


def downgrade() -> None:
    for table in reversed(PLATFORM_TABLES + TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS public.{table}")
