"""Tenant-scoped usage analytics and monitoring.

Only client administrators can read usage. The report is aggregate-only: it
contains token/cost/latency totals and provider-error metadata, never chat
content or credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin
from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.usage import UsageReportRead
from app.services import usage as usage_service

router = APIRouter(prefix="/organizations/{org_id}/usage", tags=["usage"])
assistant_router = APIRouter(
    prefix="/organizations/{org_id}/assistants/{assistant_id}/usage",
    tags=["usage"],
)


async def _build(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    since: datetime | None,
    until: datetime | None,
    assistant_id: uuid.UUID | None,
) -> UsageReportRead:
    try:
        report = await usage_service.build_report(
            session,
            org_id=org_id,
            since=since,
            until=until,
            assistant_id=assistant_id,
        )
    except ValueError as exc:
        raise AppError(
            "The reporting window is invalid.",
            status_code=422,
            code="invalid_usage_window",
        ) from exc
    return UsageReportRead.model_validate(report)


@router.get("", response_model=UsageReportRead, summary="Organization usage report")
async def organization_usage(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
    assistant_id: uuid.UUID | None = Query(default=None),  # noqa: B008
) -> UsageReportRead:
    return await _build(
        session,
        org_id=context.org_id,
        since=since,
        until=until,
        assistant_id=assistant_id,
    )


@assistant_router.get(
    "", response_model=UsageReportRead, summary="Assistant usage report"
)
async def assistant_usage(
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
) -> UsageReportRead:
    return await _build(
        session,
        org_id=context.org_id,
        since=since,
        until=until,
        assistant_id=assistant_id,
    )
