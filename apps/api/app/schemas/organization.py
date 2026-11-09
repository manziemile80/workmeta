"""Organization request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import CountryCode, CountryMixin

Slug = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]

RESERVED_SLUGS = {
    "admin",
    "api",
    "app",
    "auth",
    "login",
    "support",
    "static",
    "www",
}


class OrganizationCreate(CountryMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    slug: Slug
    country: CountryCode = "RW"

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Organization name must not be blank")
        return cleaned

    @field_validator("slug")
    @classmethod
    def _slug_allowed(cls, value: str) -> str:
        if value in RESERVED_SLUGS:
            raise ValueError("That slug is reserved")
        return value


class OrganizationUpdate(CountryMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    country: CountryCode | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Organization name must not be blank")
        return cleaned


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    country: str
    status: str
    created_at: datetime
