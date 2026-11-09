"""Authentication and tenant-context dependencies.

Every dependency that resolves a tenant calls ``set_rls_context`` *before* any
tenant query. A platform admin may act inside any tenant, but still has to do so
with the tenant context set (and mutations are audited).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import constant_time_equals
from app.db.rls import set_rls_context
from app.db.session import get_session
from app.models.enums import PLATFORM_OWNER_ROLE, MembershipStatus
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import (
    CSRF_COOKIE,
    extract_token,
    load_session,
)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(slots=True)
class AuthContext:
    user: User
    session: UserSession
    org_id: uuid.UUID | None
    role: str | None
    source: str


@dataclass(slots=True)
class OrgContext:
    auth: AuthContext
    organization: Organization
    membership: Membership | None

    @property
    def role(self) -> str:
        if self.auth.user.is_platform_admin:
            return PLATFORM_OWNER_ROLE
        return self.membership.role if self.membership else ""

    @property
    def is_platform_admin(self) -> bool:
        return self.auth.user.is_platform_admin

    @property
    def user(self) -> User:
        return self.auth.user

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id


def _check_csrf(request: Request, source: str) -> None:
    if source != "cookie" or request.method in _SAFE_METHODS:
        return
    cookie_value = request.cookies.get(CSRF_COOKIE, "")
    header_value = request.headers.get("x-csrf-token", "")
    if not cookie_value or not header_value or not constant_time_equals(
        cookie_value, header_value
    ):
        raise AppError(
            "The security token is missing or invalid. Refresh and try again.",
            status_code=403,
            code="csrf_failed",
        )


async def _membership(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    return await session.scalar(
        select(Membership).where(
            Membership.org_id == org_id, Membership.user_id == user_id
        )
    )


async def get_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008 - FastAPI idiom
) -> AuthContext:
    source = extract_token(request)
    if source is None:
        raise AppError(
            "Authentication is required.",
            status_code=401,
            code="auth_required",
        )

    found = await load_session(session, source.token)
    if found is None:
        raise AppError(
            "Your session is invalid or has expired. Please sign in again.",
            status_code=401,
            code="session_expired",
        )
    session_row, user = found

    _check_csrf(request, source.kind)

    await set_rls_context(
        session, org_id=session_row.org_id, user_id=user.id
    )

    role: str | None = None
    if session_row.org_id is not None:
        row = await _membership(session, session_row.org_id, user.id)
        if row is not None and row.status == MembershipStatus.active.value:
            role = row.role

    return AuthContext(
        user=user,
        session=session_row,
        org_id=session_row.org_id,
        role=role,
        source=source.kind,
    )


async def require_platform_admin(
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> AuthContext:
    if not auth.user.is_platform_admin:
        raise AppError(
            "Platform administrator access is required.",
            status_code=403,
            code="forbidden",
        )
    return auth


async def get_org_context(
    request: Request,
    org_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OrgContext:
    # Scope the transaction to the tenant *before* reading tenant tables.
    await set_rls_context(session, org_id=org_id, user_id=auth.user.id)

    membership = await _membership(session, org_id, auth.user.id)
    is_platform = auth.user.is_platform_admin

    if not is_platform:
        if membership is None:
            # Do not reveal whether another tenant's organization exists.
            raise AppError("Organization not found.", status_code=404, code="not_found")
        if membership.status != MembershipStatus.active.value:
            raise AppError(
                "Your membership in this organization is suspended.",
                status_code=403,
                code="membership_inactive",
            )

    organization = await session.get(Organization, org_id)
    if organization is None:
        raise AppError("Organization not found.", status_code=404, code="not_found")
    if organization.status != "active":
        raise AppError(
            "This organization is not active.",
            status_code=403,
            code="organization_inactive",
        )

    return OrgContext(auth=auth, organization=organization, membership=membership)


async def require_org_admin(
    context: OrgContext = Depends(get_org_context),  # noqa: B008
) -> OrgContext:
    if context.is_platform_admin:
        return context
    if context.membership is None or context.membership.role != "client_admin":
        raise AppError(
            "Organization administrator access is required.",
            status_code=403,
            code="forbidden",
        )
    return context


async def require_org_member(
    context: OrgContext = Depends(get_org_context),  # noqa: B008
) -> OrgContext:
    """Require an actual membership.

    Knowledge and conversations are client content. The platform owner's privacy
    rule says they are not readable from the platform side, so even a platform
    admin needs a real membership row to pass this dependency.
    """
    if (
        context.membership is None
        or context.membership.status != MembershipStatus.active.value
    ):
        raise AppError(
            "Organization membership is required.",
            status_code=403,
            code="membership_required",
        )
    return context
