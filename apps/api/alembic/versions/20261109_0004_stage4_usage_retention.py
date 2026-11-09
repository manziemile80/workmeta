"""Stage 4: usage dimensions, retention helpers and streaming/analytics support.

Revision ID: 20261109_0004
Revises: 20261109_0003
Create Date: 2026-11-09

The runtime role deliberately has no DELETE privilege on documents, chunks or
jobs. Document deletion and re-ingestion therefore go through narrow
SECURITY DEFINER functions that (a) run as the migration owner, (b) verify the
caller's transaction-local tenant context, and (c) only ever delete rows inside
that tenant. The application role receives EXECUTE on those functions and no
new table privileges.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "20261109_0004"
down_revision: str | None = "20261109_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_PURGE_FUNCTIONS = (
    "app_purge_assistant_chunks(uuid, uuid)",
    "app_purge_document_chunks(uuid, uuid)",
    "app_delete_document(uuid, uuid)",
)


def _app_role() -> str:
    role = make_url(get_settings().database_url).username or "wm_app"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise RuntimeError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "usage_events",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_events_assistant_id_assistants",
        "usage_events",
        "assistants",
        ["assistant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_usage_events_conversation_id_conversations",
        "usage_events",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_usage_events_org_assistant_created",
        "usage_events",
        ["org_id", "assistant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_usage_events_org_kind_created",
        "usage_events",
        ["org_id", "kind", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Retention helpers. These are intentionally the only code paths that
    # remove searchable content, and they always run in the caller's tenant.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_purge_document_chunks(
            p_org uuid, p_document uuid
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
        DECLARE
            deleted integer;
        BEGIN
            IF p_org IS NULL OR p_org IS DISTINCT FROM app_current_org() THEN
                RAISE EXCEPTION 'tenant context mismatch' USING ERRCODE = '42501';
            END IF;
            DELETE FROM public.document_chunks
            WHERE org_id = p_org AND document_id = p_document;
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_purge_assistant_chunks(
            p_org uuid, p_assistant uuid
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
        DECLARE
            deleted integer;
        BEGIN
            IF p_org IS NULL OR p_org IS DISTINCT FROM app_current_org() THEN
                RAISE EXCEPTION 'tenant context mismatch' USING ERRCODE = '42501';
            END IF;
            DELETE FROM public.document_chunks
            WHERE org_id = p_org AND assistant_id = p_assistant;
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_delete_document(
            p_org uuid, p_document uuid
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $fn$
        DECLARE
            deleted integer;
        BEGIN
            IF p_org IS NULL OR p_org IS DISTINCT FROM app_current_org() THEN
                RAISE EXCEPTION 'tenant context mismatch' USING ERRCODE = '42501';
            END IF;
            DELETE FROM public.document_chunks
            WHERE org_id = p_org AND document_id = p_document;
            DELETE FROM public.ingestion_jobs
            WHERE org_id = p_org AND document_id = p_document;
            DELETE FROM public.documents
            WHERE org_id = p_org AND id = p_document;
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $fn$;
        """
    )

    role = _app_role()
    for signature in _PURGE_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")


def downgrade() -> None:
    role = _app_role()
    for signature in _PURGE_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM {role}")
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.drop_index("ix_usage_events_org_kind_created", table_name="usage_events")
    op.drop_index(
        "ix_usage_events_org_assistant_created", table_name="usage_events"
    )
    op.drop_constraint(
        "fk_usage_events_conversation_id_conversations",
        "usage_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_usage_events_assistant_id_assistants",
        "usage_events",
        type_="foreignkey",
    )
    op.drop_column("usage_events", "conversation_id")
    op.drop_column("usage_events", "assistant_id")
