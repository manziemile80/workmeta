"""Stage 2: products, assistants, knowledge base, conversations and usage.

Revision ID: 20261109_0002
Revises: 20261108_0001
Create Date: 2026-11-09
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261109_0002"
down_revision: str | None = "20261108_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TENANT_TABLES = (
    "assistants",
    "documents",
    "document_chunks",
    "ingestion_jobs",
    "conversations",
    "messages",
    "usage_events",
)


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    dimensions = get_settings().embedding_dimensions

    op.create_table(
        "products",
        sa.Column("code", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "assistants",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_code", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column(
            "model_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "branding",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("{}"),
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
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
            name="fk_assistants_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_code"],
            ["products.code"],
            name="fk_assistants_product_code_products",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("org_id", "slug", name="uq_assistants_org_slug"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_assistants_status_valid",
        ),
    )
    op.create_index(
        "ix_assistants_org_id_status",
        "assistants",
        ["org_id", "status"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            name="fk_documents_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name="fk_documents_assistant_id_assistants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_documents_uploaded_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')",
            name="ck_documents_status_valid",
        ),
    )
    op.create_index(
        "ix_documents_org_assistant_created",
        "documents",
        ["org_id", "assistant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_org_id_status",
        "documents",
        ["org_id", "status"],
        unique=False,
    )

    op.create_table(
        "document_chunks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(dimensions), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_document_chunks_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name="fk_document_chunks_assistant_id_assistants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_chunk"
        ),
    )
    op.create_index(
        "ix_document_chunks_org_assistant",
        "document_chunks",
        ["org_id", "assistant_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_tsv",
        "document_chunks",
        ["tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_ingestion_jobs_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_ingestion_jobs_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ingestion_jobs_status_valid",
        ),
    )
    op.create_index(
        "ix_ingestion_jobs_status_available",
        "ingestion_jobs",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_jobs_org_document",
        "ingestion_jobs",
        ["org_id", "document_id"],
        unique=False,
    )

    op.create_table(
        "conversations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
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
            name="fk_conversations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name="fk_conversations_assistant_id_assistants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_conversations_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_conversations_org_user_updated",
        "conversations",
        ["org_id", "user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_default("[]"),
        ),
        sa.Column("grounded", sa.Boolean(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_messages_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_messages_role_valid"
        ),
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "usage_events",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), primary_key=True
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_usage_events_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usage_events_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_usage_events_org_created",
        "usage_events",
        ["org_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO products (code, name, description, is_active)
        VALUES
            ('ebm', 'EBM Assistant', 'Electronic billing machine guidance for Rwanda', true),
            ('accounting', 'Accounting Assistant', 'Bookkeeping and reporting guidance', true),
            ('stock', 'Stock Assistant', 'Inventory and stock control guidance', true),
            ('hr', 'HR Assistant', 'Employee and payroll policy guidance', true)
        ON CONFLICT (code) DO NOTHING
        """
    )

    # ------------------------------------------------------------------
    # Worker gate. Only the ingestion job queue uses this, so a worker can
    # discover work across tenants without gaining access to client content.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_worker() RETURNS boolean
        LANGUAGE sql STABLE AS $fn$
            SELECT COALESCE(current_setting('app.worker', true) = 'true', false)
        $fn$;
        """
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON public.{table}
            FOR ALL
            USING (org_id = app_current_org())
            WITH CHECK (org_id = app_current_org())
            """
        )

    op.execute(
        """
        CREATE POLICY ingestion_jobs_worker ON public.ingestion_jobs
        FOR ALL
        USING (app_is_worker())
        WITH CHECK (app_is_worker())
        """
    )

    role = _app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}")
    # Metering is append-only for the application, like the audit trail.
    op.execute(f"REVOKE UPDATE, DELETE ON TABLE public.usage_events FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.document_chunks FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.documents FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.assistants FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.conversations FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.messages FROM {role}")
    op.execute(f"REVOKE DELETE ON TABLE public.ingestion_jobs FROM {role}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS ingestion_jobs_worker ON public.ingestion_jobs")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON public.{table}")

    op.drop_table("usage_events")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("ingestion_jobs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("assistants")
    op.drop_table("products")

    op.execute("DROP FUNCTION IF EXISTS app_is_worker()")
