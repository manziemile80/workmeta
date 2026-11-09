"""Membership contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

MembershipRoleLiteral = Literal["client_admin", "client_member"]
MembershipStatusLiteral = Literal["active", "suspended"]


class MembershipRead(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime


class MembershipUpdate(BaseModel):
    role: MembershipRoleLiteral | None = None
    status: MembershipStatusLiteral | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> MembershipUpdate:
        if self.role is None and self.status is None:
            raise ValueError("Provide at least one of role or status")
        return self
