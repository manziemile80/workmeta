"""Document retention helpers.

The runtime role has no DELETE grant on documents, chunks or jobs. These thin
wrappers call the narrow SECURITY DEFINER functions created in migration 0004,
which re-check the caller's transaction-local tenant context before deleting.
Keeping the checks in both Python (ownership lookup) and PostgreSQL (function)
means neither a routing bug nor a raw SQL bug can delete another tenant's data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def purge_document_chunks(
    session: AsyncSession, *, org_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    value = await session.scalar(
        text("SELECT app_purge_document_chunks(:org_id, :document_id)"),
        {"org_id": str(org_id), "document_id": str(document_id)},
    )
    return int(value or 0)


async def purge_assistant_chunks(
    session: AsyncSession, *, org_id: uuid.UUID, assistant_id: uuid.UUID
) -> int:
    value = await session.scalar(
        text("SELECT app_purge_assistant_chunks(:org_id, :assistant_id)"),
        {"org_id": str(org_id), "assistant_id": str(assistant_id)},
    )
    return int(value or 0)


async def delete_document_rows(
    session: AsyncSession, *, org_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    """Delete the document and its derived rows in one tenant-checked call."""
    value = await session.scalar(
        text("SELECT app_delete_document(:org_id, :document_id)"),
        {"org_id": str(org_id), "document_id": str(document_id)},
    )
    return int(value or 0)
