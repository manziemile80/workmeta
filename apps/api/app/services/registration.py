"""Self-service registration.

Registration creates three things in one transaction: the person, their first
organization (they become its ``client_admin``) and the Free subscription every
organization starts with.

The new organization is made the active tenant before it is inserted: the
row-level security policies scope the membership and subscription rows to
``app.current_org``, and PostgreSQL applies the SELECT policy to the rows an
INSERT returns. Only the runtime role is used, so every insert is still checked
by the policies.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.rls import set_rls_context
from app.models.enums import (
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    UserStatus,
)
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.services import audit, billing
from app.services.auth import (
    find_user_by_email,
    hash_password_async,
    normalise_email,
)

MAX_SLUG_LENGTH = 64
MAX_SLUG_ATTEMPTS = 8

_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class Registration:
    """Everything a successful registration produced."""

    user: User
    organization: Organization
    membership: Membership


def organization_slug(name: str) -> str:
    """Turn an organization name into a URL-safe slug."""
    slug = _NON_SLUG.sub("-", name.strip().lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].strip("-")
    return slug or "workspace"


def _slug_candidate(base: str, attempt: int) -> str:
    if attempt == 1:
        return base
    suffix = f"-{attempt}"
    return f"{base[: MAX_SLUG_LENGTH - len(suffix)].rstrip('-')}{suffix}"


async def _create_organization(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    country: str,
) -> Organization:
    """Insert the organization, resolving slug collisions without leaking rows.

    Other tenants' organizations are invisible to the runtime role (that is the
    point of the row-level security policies), so a slug cannot be probed with a
    SELECT. The unique index is the only authority: the insert runs inside a
    savepoint and is retried with the next suffix when it collides.

    The insert is a Core statement on purpose. The ORM flush would add a
    ``RETURNING`` clause, and PostgreSQL applies the SELECT policy to the rows
    an INSERT returns — the organization would have to be visible to itself
    before it exists. The row is read back afterwards instead.
    """
    base = organization_slug(name)
    for attempt in range(1, MAX_SLUG_ATTEMPTS + 1):
        slug = _slug_candidate(base, attempt)
        statement = insert(Organization).values(
            id=org_id,
            name=name,
            slug=slug,
            country=country,
            status=OrganizationStatus.active.value,
        )
        try:
            async with session.begin_nested():
                await session.execute(statement)
        except IntegrityError:
            continue
        organization = await session.scalar(
            select(Organization).where(Organization.id == org_id)
        )
        if organization is None:  # pragma: no cover - the row was just written
            raise AppError(
                "Could not create the organization. Please try again.",
                status_code=500,
                code="organization_not_created",
            )
        return organization
    raise AppError(
        "Could not reserve an organization name. Please try another.",
        status_code=409,
        code="organization_slug_taken",
    )


async def register_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str,
    country: str,
    request: Request,
) -> Registration:
    """Create the account, its first organization and the Free subscription."""
    normalised_email = normalise_email(email)
    if await find_user_by_email(session, normalised_email) is not None:
        raise AppError(
            "An account with this email already exists. Please sign in.",
            status_code=409,
            code="account_exists",
        )

    user = User(
        email=normalised_email,
        password_hash=await hash_password_async(password),
        full_name=full_name,
        is_platform_admin=False,
        status=UserStatus.active.value,
    )
    session.add(user)
    await session.flush()

    # The new organization is made the active tenant before it is inserted.
    # PostgreSQL applies the SELECT policy to the rows an INSERT returns, so the
    # organization has to be visible to the session that creates it. Its id is
    # generated here so the tenant context can be set upfront.
    organization_id = uuid.uuid4()
    await set_rls_context(
        session, org_id=organization_id, user_id=user.id
    )
    organization = await _create_organization(
        session,
        org_id=organization_id,
        name=organization_name,
        country=country,
    )

    # The organization is the active tenant from here on: the membership and
    # subscription policies are scoped to ``app.current_org``.
    membership = Membership(
        org_id=organization.id,
        user_id=user.id,
        role=MembershipRole.client_admin.value,
        status=MembershipStatus.active.value,
    )
    session.add(membership)
    await session.flush()

    # Every organization starts on the Free plan. Billing is resolved from the
    # plan row, never from hard-coded application constants.
    await billing.ensure_subscription(session, organization.id)

    user.last_login_at = datetime.now(UTC)
    await audit.record(
        session,
        action="auth.registered",
        org_id=organization.id,
        actor_user_id=user.id,
        actor_role=membership.role,
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "organization_id": str(organization.id),
            "organization_slug": organization.slug,
        },
        request=request,
    )
    await session.commit()

    return Registration(
        user=user, organization=organization, membership=membership
    )
