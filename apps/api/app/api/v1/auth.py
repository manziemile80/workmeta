"""Login, logout, current identity and organization switching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.api.v1.session_payload import (
    build_login_response,
    clear_session_cookies,
    set_session_cookies,
)
from app.core.errors import AppError
from app.core.security import (
    generate_mfa_secret,
    totp_uri,
    verify_totp,
)
from app.db.rls import set_rls_context
from app.db.session import get_session
from app.models.enums import (
    PLATFORM_OWNER_ROLE,
    MembershipStatus,
    SessionKind,
    UserStatus,
)
from app.models.membership import Membership
from app.models.organization import Organization
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MFAConfirmRequest,
    MFADisableRequest,
    MFAEnrollResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordResetResponse,
    SwitchOrganizationRequest,
)
from app.services import audit
from app.services.auth import (
    authenticate,
    consume_password_reset,
    create_session,
    request_password_reset,
    revoke_all_sessions,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _membership_rows(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Membership, Organization]]:
    rows = await session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.asc())
    )
    return [(membership, org) for membership, org in rows.all()]


@router.post("/login", response_model=LoginResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LoginResponse:
    from app.services.auth import find_user_by_email

    user = await authenticate(session, str(payload.email), payload.password)
    if user is None:
        # Track consecutive failures for suspicious-login analytics.
        try:
            failed_user = await find_user_by_email(session, str(payload.email))
            if failed_user is not None:
                failed_user.failed_login_count = (
                    failed_user.failed_login_count or 0
                ) + 1
                await session.flush()
        except Exception:  # noqa: BLE001 - never mask the 401
            await session.rollback()
        try:
            await audit.record(
                session,
                action="auth.login_failed",
                metadata={"email": str(payload.email).lower()},
                request=request,
            )
            await session.commit()
        except Exception:  # noqa: BLE001 - never mask the 401 with a log failure
            await session.rollback()
        raise AppError(
            "Invalid email or password.",
            status_code=401,
            code="invalid_credentials",
        )
    if user.status != UserStatus.active.value:
        await audit.record(
            session,
            action="auth.login_blocked",
            actor_user_id=user.id,
            metadata={"status": user.status},
            request=request,
        )
        await session.commit()
        raise AppError(
            "This account is not active. Contact your administrator.",
            status_code=403,
            code="account_inactive",
        )

    # MFA: enrolled users must present a fresh TOTP code (replay-protected).
    if user.mfa_enabled_at is not None and user.mfa_secret:
        step = None
        if payload.mfa_code:
            step = verify_totp(user.mfa_secret, payload.mfa_code)
        if step is None or (
            user.mfa_last_step is not None and step <= user.mfa_last_step
        ):
            await audit.record(
                session,
                action="auth.mfa_failed",
                org_id=None,
                actor_user_id=user.id,
                metadata={"reason": "missing_invalid_or_replay"},
                request=request,
            )
            await session.commit()
            raise AppError(
                "A valid one-time code is required for this account.",
                status_code=401,
                code="mfa_required",
            )
        user.mfa_last_step = step

    # Suspicious-login detection: new client IP vs the previous login.
    client_ip = request.client.host[:45] if request.client else None
    if (
        user.last_login_ip
        and client_ip
        and user.last_login_ip != client_ip
    ):
        await audit.record(
            session,
            action="auth.suspicious_login",
            org_id=None,
            actor_user_id=user.id,
            metadata={
                "previous_ip": user.last_login_ip,
                "ip": client_ip,
            },
            request=request,
        )
    user.last_login_ip = client_ip
    user.failed_login_count = 0

    # Resolve memberships with the user context in place.
    await set_rls_context(session, user_id=user.id)
    rows = await _membership_rows(session, user.id)
    active_org_ids = {
        org.id
        for membership, org in rows
        if membership.status == MembershipStatus.active.value
        and org.status == "active"
    }

    if payload.org_id is not None:
        if not user.is_platform_admin and payload.org_id not in active_org_ids:
            raise AppError(
                "You do not have access to that organization.",
                status_code=403,
                code="forbidden",
            )
        if user.is_platform_admin:
            org = await session.get(Organization, payload.org_id)
            if org is None or org.status != "active":
                raise AppError(
                    "Organization not found.", status_code=404, code="not_found"
                )

    chosen_org: uuid.UUID | None = payload.org_id
    if chosen_org is None and not user.is_platform_admin:
        chosen_org = next((org_id for org_id in active_org_ids), None)

    if chosen_org is not None:
        await set_rls_context(session, org_id=chosen_org, user_id=user.id)

    token, session_row = await create_session(
        session,
        user=user,
        org_id=chosen_org,
        kind=SessionKind(payload.kind),
        request=request,
    )
    user.last_login_at = datetime.now(UTC)
    await audit.record(
        session,
        action="auth.login",
        org_id=chosen_org,
        actor_user_id=user.id,
        actor_role=(PLATFORM_OWNER_ROLE if user.is_platform_admin else None),
        resource_type="session",
        resource_id=str(session_row.id),
        metadata={"kind": payload.kind},
        request=request,
    )
    await session.commit()

    if payload.kind == SessionKind.web.value:
        set_session_cookies(response, token)

    return await build_login_response(
        session,
        user=user,
        session_row=session_row,
        access_token=token if payload.kind == SessionKind.extension.value else None,
    )


@router.get("/me", response_model=LoginResponse, summary="Current identity")
async def me(
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LoginResponse:
    return await build_login_response(
        session, user=auth.user, session_row=auth.session
    )


@router.post("/logout", summary="Sign out this device")
async def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    await revoke_session(session, auth.session)
    await audit.record(
        session,
        action="auth.logout",
        org_id=auth.org_id,
        actor_user_id=auth.user.id,
        actor_role=auth.role,
        resource_type="session",
        resource_id=str(auth.session.id),
        request=request,
    )
    await session.commit()
    clear_session_cookies(response)
    return {"status": "signed_out"}


@router.post("/logout-all", summary="Sign out every device")
async def logout_all(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, int | str]:
    count = await revoke_all_sessions(session, auth.user.id)
    await audit.record(
        session,
        action="auth.logout_all",
        org_id=auth.org_id,
        actor_user_id=auth.user.id,
        actor_role=auth.role,
        metadata={"revoked": count},
        request=request,
    )
    await session.commit()
    clear_session_cookies(response)
    return {"status": "signed_out", "revoked": count}


@router.post(
    "/switch-organization",
    response_model=LoginResponse,
    summary="Change the active organization",
)
async def switch_organization(
    payload: SwitchOrganizationRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LoginResponse:
    if payload.org_id is None and not auth.user.is_platform_admin:
        raise AppError(
            "Choose an organization.", status_code=400, code="org_required"
        )

    if payload.org_id is not None:
        await set_rls_context(
            session, org_id=payload.org_id, user_id=auth.user.id
        )
        organization = await session.get(Organization, payload.org_id)
        if organization is None or organization.status != "active":
            raise AppError(
                "Organization not found.", status_code=404, code="not_found"
            )
        if not auth.user.is_platform_admin:
            membership = await session.scalar(
                select(Membership).where(
                    Membership.org_id == payload.org_id,
                    Membership.user_id == auth.user.id,
                    Membership.status == MembershipStatus.active.value,
                )
            )
            if membership is None:
                raise AppError(
                    "You do not have access to that organization.",
                    status_code=403,
                    code="forbidden",
                )

    auth.session.org_id = payload.org_id
    await set_rls_context(
        session, org_id=payload.org_id, user_id=auth.user.id
    )
    await audit.record(
        session,
        action="auth.organization_switched",
        org_id=payload.org_id,
        actor_user_id=auth.user.id,
        actor_role=(
            PLATFORM_OWNER_ROLE if auth.user.is_platform_admin else auth.role
        ),
        request=request,
    )
    await session.commit()
    return await build_login_response(
        session, user=auth.user, session_row=auth.session
    )


@router.post(
    "/password-reset/request",
    response_model=PasswordResetResponse,
    summary="Request a password reset",
)
async def request_reset(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PasswordResetResponse:
    token = await request_password_reset(
        session, email=str(payload.email), request=request
    )
    await audit.record(
        session,
        action="auth.password_reset_requested",
        request=request,
    )
    await session.commit()
    # Always rendered the same (no account oracle). The token is returned
    # for manual/out-of-band delivery until email delivery exists.
    return PasswordResetResponse(reset_token=token)


@router.post(
    "/password-reset/confirm", summary="Reset a password with a token"
)
async def confirm_reset(
    payload: PasswordResetConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    ok = await consume_password_reset(
        session, token=payload.token, new_password=payload.new_password
    )
    await audit.record(
        session,
        action="auth.password_reset_consumed",
        metadata={"success": ok},
        request=request,
    )
    await session.commit()
    if not ok:
        raise AppError(
            "That reset token is invalid or expired.",
            status_code=400,
            code="invalid_reset_token",
        )
    return {"status": "password_reset"}


@router.post(
    "/mfa/enroll",
    response_model=MFAEnrollResponse,
    summary="Start MFA enrollment (secret shown once)",
)
async def mfa_enroll(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MFAEnrollResponse:
    if auth.user.mfa_enabled_at is not None:
        raise AppError(
            "MFA is already enabled.", status_code=409, code="mfa_already_enabled"
        )
    secret = generate_mfa_secret()
    auth.user.mfa_secret = secret
    auth.user.mfa_enabled_at = None
    await audit.record(
        session,
        action="auth.mfa_enrollment_started",
        actor_user_id=auth.user.id,
        request=request,
    )
    await session.flush()
    await session.refresh(auth.user)
    uri = totp_uri(secret, auth.user.email)
    await session.commit()
    return MFAEnrollResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/confirm", summary="Confirm MFA enrollment")
async def mfa_confirm(
    payload: MFAConfirmRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    if auth.user.mfa_enabled_at is not None:
        raise AppError(
            "MFA is already enabled.", status_code=409, code="mfa_already_enabled"
        )
    if not auth.user.mfa_secret:
        raise AppError(
            "No pending MFA enrollment.", status_code=409, code="mfa_not_pending"
        )
    step = verify_totp(auth.user.mfa_secret, payload.code)
    if step is None:
        raise AppError(
            "That one-time code is invalid.", status_code=401, code="mfa_invalid_code"
        )
    auth.user.mfa_enabled_at = datetime.now(UTC)
    auth.user.mfa_last_step = step
    await audit.record(
        session,
        action="auth.mfa_enabled",
        actor_user_id=auth.user.id,
        request=request,
    )
    await session.commit()
    return {"status": "mfa_enabled"}


@router.post("/mfa/disable", summary="Disable MFA")
async def mfa_disable(
    payload: MFADisableRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    if auth.user.mfa_enabled_at is None or not auth.user.mfa_secret:
        raise AppError("MFA is not enabled.", status_code=409, code="mfa_not_enabled")
    step = verify_totp(auth.user.mfa_secret, payload.code)
    if step is None or (
        auth.user.mfa_last_step is not None and step <= auth.user.mfa_last_step
    ):
        raise AppError(
            "That one-time code is invalid.", status_code=401, code="mfa_invalid_code"
        )
    auth.user.mfa_secret = None
    auth.user.mfa_enabled_at = None
    auth.user.mfa_last_step = None
    await audit.record(
        session,
        action="auth.mfa_disabled",
        actor_user_id=auth.user.id,
        request=request,
    )
    await session.commit()
    return {"status": "mfa_disabled"}
