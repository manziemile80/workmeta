"""Append-only audit recording.

Audit rows are written in the same transaction as the action they describe, so
a committed security event always has its audit trail (and a rolled-back action
leaves no misleading entry).

The insert is expressed as raw SQL on purpose: it must never use ``RETURNING``.
Platform-level events carry a NULL ``org_id`` and are intentionally invisible to
tenant SELECT policies, so asking PostgreSQL to return the inserted row would
violate RLS even though the INSERT itself is allowed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host[:45]


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value)[:64] if value else None


async def record(
    session: AsyncSession,
    *,
    action: str,
    org_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_logs
                (org_id, actor_user_id, actor_role, action, resource_type,
                 resource_id, metadata, ip, request_id)
            VALUES
                (CAST(:org_id AS uuid), CAST(:actor_user_id AS uuid), :actor_role,
                 :action, :resource_type, :resource_id, CAST(:metadata AS jsonb),
                 :ip, :request_id)
            """
        ),
        {
            "org_id": str(org_id) if org_id else None,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "actor_role": actor_role,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "metadata": json.dumps(metadata or {}, separators=(",", ":")),
            "ip": client_ip(request) if request else None,
            "request_id": request_id(request) if request else None,
        },
    )
