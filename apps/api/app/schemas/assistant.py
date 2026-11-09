"""Assistant and product API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Slug = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]


class RetrievalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=6, ge=1, le=20)
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)


class ModelOptions(BaseModel):
    """Per-assistant model overrides.

    The embedding model stays deployment-level: every chunk and query must use
    the same model and vector dimensions. Chat model, temperature and output
    length can be tuned per assistant.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=120)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=64, le=4096)

    @field_validator("model")
    @classmethod
    def _strip_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    is_active: bool


class AssistantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    slug: Slug | None = None
    product_code: str | None = Field(default=None, max_length=32)
    status: Literal["draft", "active"] = "active"
    system_prompt: str | None = Field(default=None, max_length=8000)
    model_options: ModelOptions = Field(default_factory=ModelOptions)
    retrieval_config: RetrievalSettings = Field(default_factory=RetrievalSettings)
    branding: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "system_prompt")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class AssistantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: Slug | None = None
    product_code: str | None = Field(default=None, max_length=32)
    status: Literal["draft", "active", "archived"] | None = None
    system_prompt: str | None = Field(default=None, max_length=8000)
    model_options: ModelOptions | None = None
    retrieval_config: RetrievalSettings | None = None
    branding: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class AssistantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    org_id: uuid.UUID
    product_code: str | None
    name: str
    slug: str
    status: str
    system_prompt: str | None
    model_options: dict[str, Any] = Field(validation_alias="model_config")
    retrieval_config: dict[str, Any]
    branding: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class AssistantAIOptions(BaseModel):
    """Effective, secret-free AI settings for the configuration dashboard."""

    provider: str
    provider_key_configured: bool
    effective_model: str
    supported_models: list[str]
    model_override: str | None
    temperature: float
    max_output_tokens: int
    defaults: dict[str, Any]


class ReembedResponse(BaseModel):
    assistant_id: uuid.UUID
    documents: int
    queued: int
