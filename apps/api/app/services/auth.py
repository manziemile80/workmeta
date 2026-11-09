"""Authentication and session services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)
from app.models.enums import MembershipStatus, SessionKind, UserStatus
from app.models.membership import Membership
from app.models.session import UserSession
from app.models.user import User

SESSION_COOKIE = "wm_session"
CSRF_COOKIE = "wm_csrf"


@dataclass(slots=True)
class AuthSource:
    token: str
    kind: str  # "cookie" or "bearer"


def extract_token(request: Request) -> AuthSource | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return AuthSource(token=token, kind="bearer")
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return AuthSource(token=cookie, kind="cookie")
    return None


def normalise_email(email: str) -> str:
    return email.strip().lower()


async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password_hash: str, password: str) -> bool:
    return await run_in_threadpool(verify_password, password_hash, password)


async def reject_dummy_async(password: str) -> None:
    await run_in_threadpool(verify_dummy_password, password)


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalise_email(email)))


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> User | None:
    user = await find_user_by_email(session, email)
    if user is None:
        await reject_dummy_async(password)
        return None
    matched = await verify_password_async(user.password_hash, password)
    if not matched:
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(password)
    return user


def session_expiry(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(days=get_settings().session_ttl_days)


async def create_session(
    session: AsyncSession,
    *,
    user: User,
    org_id: uuid.UUID | None,
    kind: SessionKind,
    request: Request,
) -> tuple[str, UserSession]:
    token = generate_session_token()
    row = UserSession(
        user_id=user.id,
        org_id=org_id,
        token_hash=hash_token(token),
        kind=kind.value,
        expires_at=session_expiry(),
        ip=request.client.host[:45] if request.client else None,
        user_agent=(request.headers.get("user-agent", "")[:512] or None),
    )
    session.add(row)
    await session.flush()
    return token, row


async def load_session(
    session: AsyncSession, token: str
) -> tuple[UserSession, User] | None:
    row = await session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    )
    found = row.one_or_none()
    if found is None:
        return None
    session_row, user = found
    now = datetime.now(UTC)
    if session_row.revoked_at is not None or session_row.expires_at <= now:
        return None
    if user.status != UserStatus.active.value:
        return None
    # Idle timeout: sessions unused for longer than SESSION_IDLE_HOURS die.
    idle_hours = get_settings().session_idle_hours
    if idle_hours > 0:
        raw = session_row.last_seen_at or session_row.created_at
        if raw is not None:
            last_seen = raw.astimezone(UTC)
            if now - last_seen > timedelta(hours=idle_hours):
                session_row.revoked_at = now
                await session.flush()
                return None
    # Record activity for the next idle check (best-effort, same transaction).
    session_row.last_seen_at = now
    return session_row, user


async def revoke_session(session: AsyncSession, session_row: UserSession) -> None:
    session_row.revoked_at = datetime.now(UTC)
    await session.flush()


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)


PASSWORD_RESET_TOKEN_BYTES = 32


def generate_password_reset_token() -> str:
    import secrets as _secrets

    return _secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)


async def request_password_reset(
    session: AsyncSession, *, email: str, request: Request
) -> str | None:
    """Create a reset token if the account exists (always render success).

    Returns the plaintext token for delivery (email in production is out of
    scope; development returns it to the caller for manual delivery, like
    invitation tokens). Only the hash is stored.
    """
    from app.models.password_reset import PasswordReset

    user = await find_user_by_email(session, email)
    if user is None or user.status != UserStatus.active.value:
        # Burn comparable time so timing does not reveal account existence.
        await reject_dummy_async("reset-timing")
        return None
    token = generate_password_reset_token()
    session.add(
        PasswordReset(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=get_settings().password_reset_ttl_minutes),
            requested_ip=(
                request.client.host[:45] if request.client else None
            ),
        )
    )
    await session.flush()
    return token


async def consume_password_reset(
    session: AsyncSession, *, token: str, new_password: str
) -> bool:
    """Consume a reset token once. Revokes every session for the user."""
    from app.models.password_reset import PasswordReset

    row = await session.scalar(
        select(PasswordReset).where(
            PasswordReset.token_hash == hash_token(token)
        )
    )
    now = datetime.now(UTC)
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        return False
    user = await session.get(User, row.user_id)
    if user is None or user.status != UserStatus.active.value:
        return False
    user.password_hash = await hash_password_async(new_password)
    row.consumed_at = now
    await revoke_all_sessions(session, user.id)
    await session.flush()
    return True


async def active_memberships(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Membership]:
    rows = await session.scalars(
        select(Membership)
        .where(
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active.value,
        )
        .order_by(Membership.created_at.asc())
    )
    return list(rows)
