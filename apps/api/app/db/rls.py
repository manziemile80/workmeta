"""PostgreSQL Row-Level Security context helpers.

Every request that touches tenant data sets two transaction-local settings:

    app.current_org   UUID of the active organization
    app.current_user  UUID of the authenticated user

RLS policies read them through the helper functions created in the Stage 1
migration. ``set_config(..., true)`` is transaction-local, so pooled
connections cannot leak one tenant's context into another request.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_rls_context(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    invitation_token_hash: str | None = None,
    worker: bool = False,
) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.current_org', :org, true), "
            "set_config('app.current_user', :usr, true), "
            "set_config('app.invitation_token', :inv, true), "
            "set_config('app.worker', :worker, true)"
        ),
        {
            "org": str(org_id) if org_id else "",
            "usr": str(user_id) if user_id else "",
            "inv": invitation_token_hash or "",
            "worker": "true" if worker else "false",
        },
    )
