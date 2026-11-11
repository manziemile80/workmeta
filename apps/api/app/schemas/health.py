"""Pydantic response models for operational endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    uptime_seconds: float = Field(ge=0)
    # Operational signal only - never contains the URL or credentials.
    database_configured: bool = True
    # Whether the interactive API documentation is served (it is not in
    # production). The web client uses this to hide a dead documentation link.
    docs_enabled: bool = False


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"
    pgvector: bool
    version: str
    environment: str
