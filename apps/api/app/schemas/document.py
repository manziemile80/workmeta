"""Document API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    assistant_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: str
    error_message: str | None
    page_count: int | None
    embedding_model: str | None
    embedding_dimensions: int | None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None


class DocumentUploadMeta(BaseModel):
    title: str | None = Field(default=None, max_length=300)
