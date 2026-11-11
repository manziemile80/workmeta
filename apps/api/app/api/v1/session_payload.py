"""Shared session payload and cookie helpers."""

from __future__ import annotations

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_csrf_token
from app.db.rls import set_rls_context
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.session import UserSession
from app.models.user import User
from app.schemas.auth import (
    LoginResponse,
    MembershipSummary,
    SessionRead,
    UserRead,
)
from app.schemas.organization import OrganizationRead
from app.services.auth import CSRF_COOKIE, SESSION_COOKIE


def _cookie_max_age() -> int:
    return get_settings().session_ttl_days * 86_400


def set_session_cookies(response: Response, token: str) -> None:
    secure = get_settings().secure_cookies
    max_age = _cookie_max_age()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # Double-submit CSRF token. Readable by the web app, never sent by third
    # parties because of SameSite=Lax and the CORS allow-list.
    response.set_cookie(
        key=CSRF_COOKIE,
        value=generate_csrf_token(),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


async def build_login_response(
    session: AsyncSession,
    *,
    user: User,
    session_row: UserSession,
    access_token: str | None = None,
) -> LoginResponse:
    # A commit ends the previous transaction and clears transaction-local RLS
    # settings, so restore the context before tenant-scoped reads.
    await set_rls_context(
        session, org_id=session_row.org_id, user_id=user.id
    )
    rows = (
        await session.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at.asc())
        )
    ).all()
    active = next(
        (org for _, org in rows if org.id == session_row.org_id), None
    )
    user_read = UserRead.model_validate(user)
    user_read.mfa_enabled = user.mfa_enabled_at is not None
    return LoginResponse(
        user=user_read,
        active_organization=(
            OrganizationRead.model_validate(active) if active else None
        ),
        memberships=[
            MembershipSummary(
                organization=OrganizationRead.model_validate(org),
                role=membership.role,
                status=membership.status,
            )
            for membership, org in rows
        ],
        session=SessionRead(kind=session_row.kind, expires_at=session_row.expires_at),
        access_token=access_token,
    )
