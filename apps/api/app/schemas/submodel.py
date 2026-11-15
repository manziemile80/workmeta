"""Submodel API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Slug = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]


class SubmodelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    slug: Slug | None = None
    description: str | None = Field(default=None, max_length=4000)
    purpose: str | None = Field(default=None, max_length=4000)
    config: dict[str, Any] = Field(default_factory=dict)


class SubmodelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    purpose: str | None = None
    status: Literal["draft", "archived"] | None = None
    config: dict[str, Any] | None = None


class SubmodelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    purpose: str | None
    status: str
    version: int
    config: dict[str, Any]
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    video_count: int = 0
    workflow_count: int = 0


class SubmodelVideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submodel_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    error_message: str | None
    duration_seconds: float | None
    frame_count: int | None
    transcript: str | None
    analysis: dict[str, Any]
    created_at: datetime


class SubmodelWorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submodel_id: uuid.UUID
    source_video_id: uuid.UUID | None
    name: str
    description: str | None
    steps: list[dict[str, Any]]
    version: int
    created_at: datetime


class SubmodelJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submodel_id: uuid.UUID
    video_id: uuid.UUID | None
    kind: str
    status: str
    progress: int
    message: str | None
    error: str | None
    result: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class SubmodelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submodel_id: uuid.UUID
    version: int
    manifest: dict[str, Any]
    has_package: bool
    created_at: datetime


class SubmodelTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=4000)


class SubmodelTestResponse(BaseModel):
    submodel_id: uuid.UUID
    answer: str
    followed_workflows: list[str]
    model: str


class SubmodelInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
