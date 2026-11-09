"""Invitation creation, listing, revocation and acceptance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_admin
from app.api.v1.session_payload import (
    build_login_response,
    set_session_cookies,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    generate_invitation_token,
    hash_token,
)
from app.db.rls import set_rls_context
from app.db.session import get_session
from app.models.enums import (
    MembershipStatus,
    SessionKind,
    UserStatus,
)
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import LoginResponse
from app.schemas.invitation import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationRead,
)
from app.services import audit
from app.services.auth import (
    create_session,
    find_user_by_email,
    hash_password_async,
    normalise_email,
    verify_password_async,
)

router = APIRouter(tags=["invitations"])


def _accept_endpoint() -> str:
    settings = get_settings()
    return f"{settings.api_v1_prefix}/invitations/accept"


@router.get(
    "/organizations/{org_id}/invitations",
    response_model=list[InvitationRead],
    summary="List invitations",
)
async def list_invitations(
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Invitation]:
    rows = await session.scalars(
        select(Invitation)
        .where(Invitation.org_id == context.org_id)
        .order_by(Invitation.created_at.desc())
    )
    return list(rows)


@router.post(
    "/organizations/{org_id}/invitations",
    response_model=InvitationCreated,
    status_code=201,
    summary="Invite a client administrator or member",
)
async def create_invitation(
    payload: InvitationCreate,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> InvitationCreated:
    email = normalise_email(str(payload.email))

    existing_user = await find_user_by_email(session, email)
    if existing_user is not None:
        existing_membership = await session.scalar(
            select(Membership).where(
                Membership.org_id == context.org_id,
                Membership.user_id == existing_user.id,
            )
        )
        if existing_membership is not None:
            raise AppError(
                "That person is already a member of this organization.",
                status_code=409,
                code="already_member",
            )

    now = datetime.now(UTC)
    # Only one live invitation per email and organization. The previous link
    # stops working the moment a replacement is issued.
    await session.execute(
        update(Invitation)
        .where(
            Invitation.org_id == context.org_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    token = generate_invitation_token()
    invitation = Invitation(
        org_id=context.org_id,
        email=email,
        role=payload.role,
        token_hash=hash_token(token),
        invited_by=context.user.id,
        expires_at=now + timedelta(days=get_settings().invitation_ttl_days),
    )
    session.add(invitation)
    await session.flush()

    await audit.record(
        session,
        action="invitation.created",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="invitation",
        resource_id=str(invitation.id),
        metadata={"email": email, "role": invitation.role},
        request=request,
    )
    await session.commit()

    return InvitationCreated(
        **InvitationRead.model_validate(invitation).model_dump(),
        token=token,
        accept_endpoint=_accept_endpoint(),
    )


@router.delete(
    "/organizations/{org_id}/invitations/{invitation_id}",
    status_code=204,
    summary="Revoke an invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    context: OrgContext = Depends(require_org_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.org_id != context.org_id:
        raise AppError("Invitation not found.", status_code=404, code="not_found")
    if invitation.accepted_at is not None:
        raise AppError(
            "Accepted invitations cannot be revoked.",
            status_code=409,
            code="invitation_accepted",
        )

    invitation.revoked_at = datetime.now(UTC)
    await audit.record(
        session,
        action="invitation.revoked",
        org_id=context.org_id,
        actor_user_id=context.user.id,
        actor_role=context.role,
        resource_type="invitation",
        resource_id=str(invitation.id),
        metadata={"email": invitation.email},
        request=request,
    )
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/invitations/accept",
    response_model=LoginResponse,
    summary="Accept an invitation",
)
async def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LoginResponse:
    token_hash = hash_token(payload.token)
    await set_rls_context(session, invitation_token_hash=token_hash)

    invitation = await session.scalar(
        select(Invitation)
        .where(Invitation.token_hash == token_hash)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise AppError(
            "This invitation is invalid or has expired.",
            status_code=400,
            code="invalid_invitation",
        )

    await set_rls_context(
        session,
        org_id=invitation.org_id,
        invitation_token_hash=token_hash,
    )
    organization = await session.get(Organization, invitation.org_id)
    if organization is None or organization.status != "active":
        raise AppError(
            "This invitation is invalid or has expired.",
            status_code=400,
            code="invalid_invitation",
        )

    user = await find_user_by_email(session, invitation.email)
    if user is not None:
        if not await verify_password_async(user.password_hash, payload.password):
            raise AppError(
                "An account with this email already exists. Enter its current "
                "password to accept the invitation.",
                status_code=401,
                code="invalid_credentials",
            )
        if user.status != UserStatus.active.value:
            raise AppError(
                "This account is not active. Contact your administrator.",
                status_code=403,
                code="account_inactive",
            )
    else:
        if not payload.full_name:
            raise AppError(
                "Your full name is required to create the account.",
                status_code=400,
                code="full_name_required",
            )
        user = User(
            email=invitation.email,
            password_hash=await hash_password_async(payload.password),
            full_name=payload.full_name,
            is_platform_admin=False,
            status=UserStatus.active.value,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise AppError(
                "An account with this email already exists. Please sign in.",
                status_code=409,
                code="account_exists",
            ) from exc

    await set_rls_context(
        session,
        org_id=invitation.org_id,
        user_id=user.id,
        invitation_token_hash=token_hash,
    )

    existing_membership = await session.scalar(
        select(Membership).where(
            Membership.org_id == invitation.org_id,
            Membership.user_id == user.id,
        )
    )
    invitation.accepted_at = now
    if existing_membership is not None:
        await audit.record(
            session,
            action="invitation.accepted",
            org_id=invitation.org_id,
            actor_user_id=user.id,
            actor_role=existing_membership.role,
            resource_type="invitation",
            resource_id=str(invitation.id),
            metadata={"already_member": True},
            request=request,
        )
        await session.commit()
        raise AppError(
            "You are already a member of this organization. Please sign in.",
            status_code=409,
            code="already_member",
        )

    membership = Membership(
        org_id=invitation.org_id,
        user_id=user.id,
        role=invitation.role,
        status=MembershipStatus.active.value,
    )
    session.add(membership)
    await session.flush()

    user.last_login_at = now
    token, session_row = await create_session(
        session,
        user=user,
        org_id=invitation.org_id,
        kind=SessionKind.web,
        request=request,
    )
    await audit.record(
        session,
        action="invitation.accepted",
        org_id=invitation.org_id,
        actor_user_id=user.id,
        actor_role=membership.role,
        resource_type="invitation",
        resource_id=str(invitation.id),
        metadata={"role": membership.role},
        request=request,
    )
    await session.commit()

    set_session_cookies(response, token)
    return await build_login_response(
        session, user=user, session_row=session_row
    )
