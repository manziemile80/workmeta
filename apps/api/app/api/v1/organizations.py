"""Client organization administration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    OrgContext,
    get_auth_context,
    get_org_context,
    require_org_admin,
    require_platform_admin,
)
from app.core.errors import AppError
from app.db.rls import set_rls_context
from app.db.session import get_session
from app.models.enums import PLATFORM_OWNER_ROLE, OrganizationStatus
from app.models.membership import Membership
from app.models.organization import Organization
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services import audit, billing

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationRead], summary="List organizations")
async def list_organizations(
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Organization]:
    await set_rls_context(session, org_id=auth.org_id, user_id=auth.user.id)
    statement = select(Organization)
    # Defense in depth: mirror the RLS policy in the ORM query so that even if
    # RLS were ever misconfigured, a non-platform user can only list their own
    # organizations.
    if not auth.user.is_platform_admin:
        membership_orgs = (
            select(Membership.org_id).where(
                Membership.user_id == auth.user.id,
                Membership.status == "active",
            )
        )
        statement = statement.where(Organization.id.in_(membership_orgs))
    rows = await session.scalars(
        statement.order_by(Organization.name.asc())
    )
    return list(rows)


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=201,
    summary="Create a client organization",
)
async def create_organization(
    payload: OrganizationCreate,
    request: Request,
    auth: AuthContext = Depends(require_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Organization:
    existing = await session.scalar(
        select(Organization).where(Organization.slug == payload.slug)
    )
    if existing is not None:
        raise AppError(
            "That organization slug is already in use.",
            status_code=409,
            code="slug_taken",
        )

    organization = Organization(
        name=payload.name,
        slug=payload.slug,
        country=payload.country,
        status=OrganizationStatus.active.value,
    )
    session.add(organization)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "That organization slug is already in use.",
            status_code=409,
            code="slug_taken",
        ) from exc

    # Act inside the new tenant so the audit row is visible to its admins.
    await set_rls_context(
        session, org_id=organization.id, user_id=auth.user.id
    )
    # Every organization starts on the Free plan. Billing is resolved from the
    # plan row, never from hard-coded application constants.
    await billing.ensure_subscription(session, organization.id)
    await audit.record(
        session,
        action="organization.created",
        org_id=organization.id,
        actor_user_id=auth.user.id,
        actor_role=PLATFORM_OWNER_ROLE,
        resource_type="organization",
        resource_id=str(organization.id),
        metadata={"slug": organization.slug, "name": organization.name},
        request=request,
    )
    await session.commit()
    return organization


@router.get(
    "/{org_id}",
    response_model=OrganizationRead,
    summary="Read one organization",
)
async def get_organization(
    context: OrgContext = Depends(get_org_context),  # noqa: B008
) -> Organization:
    return context.organization


@router.patch(
    "/{org_id}",
    response_model=OrganizationRead,
    summary="Update organization profile",
)
async def update_organization(
    payload: OrganizationUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Organization:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return context.organization

    for field, value in changes.items():
        if value is not None:
            setattr(context.organization, field, value)

    await audit.record(
        session,
        action="organization.updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="organization",
        resource_id=str(context.org_id),
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await session.commit()
    return context.organization
