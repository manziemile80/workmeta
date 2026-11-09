"""Invitation contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas.common import PasswordMixin
from app.schemas.membership import MembershipRoleLiteral


class InvitationCreate(BaseModel):
    email: EmailStr
    role: MembershipRoleLiteral = "client_member"


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(InvitationRead):
    token: str
    accept_endpoint: str
    delivery: Literal["manual"] = "manual"


class InvitationAccept(PasswordMixin, BaseModel):
    token: str = Field(min_length=20, max_length=128)
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def _name_clean(self) -> InvitationAccept:
        if self.full_name is not None:
            cleaned = " ".join(self.full_name.split())
            if not cleaned:
                raise ValueError("Full name must not be blank")
            self.full_name = cleaned
        return self
