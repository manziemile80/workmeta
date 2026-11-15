"""Authentication request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.common import (
    REGISTER_PASSWORD_MIN_LENGTH,
    CountryCode,
    CountryMixin,
    LoginPassword,
    PasswordMixin,
    validate_register_password,
)
from app.schemas.organization import OrganizationRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: LoginPassword
    kind: Literal["web", "extension"] = "web"
    org_id: uuid.UUID | None = None
    # TOTP code when the account has MFA enabled (required then).
    mfa_code: str | None = None


class RegisterRequest(PasswordMixin, CountryMixin, BaseModel):
    """Self-service sign-up.

    Creates the person, their first organization and the Free subscription that
    every organization starts with, then signs them in.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(
        min_length=REGISTER_PASSWORD_MIN_LENGTH, max_length=128
    )
    organization_name: str = Field(min_length=2, max_length=160)
    country: CountryCode = "RW"

    @field_validator("full_name")
    @classmethod
    def _full_name_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Full name must not be blank")
        return cleaned

    @field_validator("organization_name")
    @classmethod
    def _organization_name_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Organization name must not be blank")
        return cleaned

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return validate_register_password(value)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    new_password: LoginPassword


class PasswordResetResponse(BaseModel):
    # Always rendered the same whether or not the account exists (no oracle).
    status: Literal["ok"] = "ok"
    # Development/manual delivery: the token is returned once so it can be
    # delivered out-of-band until email delivery exists. Never logged.
    reset_token: str | None = None


class MFAEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MFAConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class MFADisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    status: str
    last_login_at: datetime | None
    mfa_enabled: bool = False


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    expires_at: datetime


class MembershipSummary(BaseModel):
    organization: OrganizationRead
    role: str
    status: str


class LoginResponse(BaseModel):
    user: UserRead
    active_organization: OrganizationRead | None
    memberships: list[MembershipSummary]
    session: SessionRead
    access_token: str | None = None


class SwitchOrganizationRequest(BaseModel):
    # NULL is allowed only for platform admins returning to platform context.
    org_id: uuid.UUID | None
