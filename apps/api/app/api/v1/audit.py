"""Read-only audit trail for client administrators."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin
from app.db.session import get_session
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogRead

router = APIRouter(
    prefix="/organizations/{org_id}/audit", tags=["audit"]
)


@router.get("", response_model=list[AuditLogRead], summary="List audit events")
async def list_audit_events(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogRead]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == context.org_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        AuditLogRead(
            id=row.id,
            org_id=row.org_id,
            actor_user_id=row.actor_user_id,
            actor_role=row.actor_role,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            metadata=row.meta,
            ip=row.ip,
            request_id=row.request_id,
            created_at=row.created_at,
        )
        for row in rows
    ]
