"""Assistant lookup and slug helpers shared by knowledge routes."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.assistant import Assistant
from app.models.enums import AssistantStatus

_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_CHARS.sub("-", name.strip().lower()).strip("-")
    return (slug or "assistant")[:64]


async def get_assistant(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID,
    require_active: bool = False,
) -> Assistant:
    assistant = await session.scalar(
        select(Assistant).where(
            Assistant.id == assistant_id, Assistant.org_id == org_id
        )
    )
    if assistant is None:
        raise AppError("Assistant not found.", status_code=404, code="not_found")
    if require_active and assistant.status != AssistantStatus.active.value:
        raise AppError(
            "This assistant is not active.",
            status_code=409,
            code="assistant_inactive",
        )
    return assistant
