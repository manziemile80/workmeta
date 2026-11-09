"""Organization membership administration."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin
from app.core.errors import AppError
from app.db.session import get_session
from app.models.membership import Membership
from app.models.user import User
from app.schemas.membership import MembershipRead, MembershipUpdate
from app.services import audit

router = APIRouter(
    prefix="/organizations/{org_id}/members", tags=["memberships"]
)


@router.get("", response_model=list[MembershipRead], summary="List members")
async def list_members(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[MembershipRead]:
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == context.org_id)
        .order_by(User.full_name.asc())
    )
    return [
        MembershipRead(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            status=membership.status,
            created_at=membership.created_at,
        )
        for membership, user in rows.all()
    ]


@router.patch(
    "/{user_id}",
    response_model=MembershipRead,
    summary="Change a member's role or status",
)
async def update_member(
    user_id: uuid.UUID,
    payload: MembershipUpdate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MembershipRead:
    if user_id == context.user.id:
        raise AppError(
            "You cannot change your own membership.",
            status_code=403,
            code="cannot_modify_self",
        )

    membership = await session.scalar(
        select(Membership).where(
            Membership.org_id == context.org_id, Membership.user_id == user_id
        )
    )
    if membership is None:
        raise AppError("Member not found.", status_code=404, code="not_found")

    user = await session.get(User, user_id)
    if user is None:
        raise AppError("Member not found.", status_code=404, code="not_found")

    losing_admin = (
        membership.role == "client_admin"
        and membership.status == "active"
        and (
            (payload.role is not None and payload.role != "client_admin")
            or (payload.status is not None and payload.status != "active")
        )
    )
    if losing_admin:
        other_admins = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.org_id == context.org_id,
                Membership.role == "client_admin",
                Membership.status == "active",
                Membership.user_id != user_id,
            )
        )
        if not other_admins:
            raise AppError(
                "An organization must keep at least one active administrator.",
                status_code=409,
                code="last_admin",
            )

    if payload.role is not None:
        membership.role = payload.role
    if payload.status is not None:
        membership.status = payload.status

    await audit.record(
        session,
        action="membership.updated",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="membership",
        resource_id=str(membership.id),
        metadata={
            "user_id": str(user_id),
            "role": membership.role,
            "status": membership.status,
        },
        request=request,
    )
    await session.commit()

    return MembershipRead(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        status=membership.status,
        created_at=membership.created_at,
    )
