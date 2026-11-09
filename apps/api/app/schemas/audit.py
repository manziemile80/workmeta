"""Audit log contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    id: int
    org_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    metadata: dict[str, Any]
    ip: str | None
    request_id: str | None
    created_at: datetime
