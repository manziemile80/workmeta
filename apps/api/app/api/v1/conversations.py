"""Conversations and grounded chat.

Conversations are private to the user who created them. Administrators cannot
read an employee's chat history, and the platform owner has no content bypass at
all — this is a deliberate product privacy rule, not an omission.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgContext, require_org_member
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.conversation import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
    TokenUsage,
)
from app.services import chat as chat_service
from app.services import quota
from app.services.ai import AIError, AIProvider, count_tokens, get_ai_provider
from app.services.assistants import get_assistant

_ROLE_ORDER = case((Message.role == "user", 0), else_=1)


def _estimated_chat_tokens(assistant, question: str) -> int:
    """Conservative pre-flight reservation; reconciled after the provider."""
    settings = get_settings()
    options = assistant.model_config or {}
    max_output = options.get("max_output_tokens")
    if (
        not isinstance(max_output, int)
        or isinstance(max_output, bool)
        or not 64 <= max_output <= 4096
    ):
        max_output = settings.chat_max_output_tokens
    # Question tokens + answer budget + system prompt/citation overhead.
    return count_tokens(question) + max_output + 256

assistant_router = APIRouter(
    prefix="/organizations/{org_id}/assistants/{assistant_id}/conversations",
    tags=["chat"],
)
router = APIRouter(prefix="/organizations/{org_id}/conversations", tags=["chat"])


async def _own_conversation(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.org_id == org_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise AppError("Conversation not found.", status_code=404, code="not_found")
    return conversation


@assistant_router.post(
    "",
    response_model=ConversationRead,
    status_code=201,
    summary="Start a conversation",
)
async def create_conversation(
    assistant_id: uuid.UUID,
    payload: ConversationCreate,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Conversation:
    assistant = await get_assistant(
        session,
        org_id=context.org_id,
        assistant_id=assistant_id,
        require_active=True,
    )
    conversation = Conversation(
        org_id=context.org_id,
        assistant_id=assistant.id,
        user_id=context.user.id,
        title=payload.title or "New conversation",
    )
    session.add(conversation)
    await session.flush()
    await session.refresh(conversation)
    await session.commit()
    return conversation


@assistant_router.get(
    "",
    response_model=list[ConversationRead],
    summary="List my conversations with an assistant",
)
async def list_conversations(
    assistant_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Conversation]:
    await get_assistant(
        session, org_id=context.org_id, assistant_id=assistant_id
    )
    rows = await session.scalars(
        select(Conversation)
        .where(
            Conversation.org_id == context.org_id,
            Conversation.assistant_id == assistant_id,
            Conversation.user_id == context.user.id,
        )
        .order_by(Conversation.updated_at.desc())
    )
    return list(rows)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Read one conversation",
)
async def read_conversation(
    conversation_id: uuid.UUID,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConversationDetail:
    conversation = await _own_conversation(
        session,
        org_id=context.org_id,
        user_id=context.user.id,
        conversation_id=conversation_id,
    )
    messages = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.asc(), _ROLE_ORDER.asc())
            )
        ).all()
    )
    detail = ConversationDetail.model_validate(conversation)
    detail.messages = [MessageRead.model_validate(message) for message in messages]
    return detail


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatResponse,
    summary="Ask a grounded question",
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: ChatRequest,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> ChatResponse:
    conversation = await _own_conversation(
        session,
        org_id=context.org_id,
        user_id=context.user.id,
        conversation_id=conversation_id,
    )
    assistant = await get_assistant(
        session,
        org_id=context.org_id,
        assistant_id=conversation.assistant_id,
        require_active=True,
    )
    reservation = await quota.reserve_ai_request(
        session,
        org_id=context.org_id,
        estimated_tokens=_estimated_chat_tokens(assistant, payload.question),
        operation="chat",
    )

    try:
        outcome = await chat_service.answer(
            session,
            org_id=context.org_id,
            assistant=assistant,
            conversation=conversation,
            user=context.user,
            question=payload.question,
            ai=ai,
            actor_role=context.role,
            request=request,
            reservation=reservation,
        )
    except AIError as exc:
        raise AppError(
            "The assistant is temporarily unavailable. Please try again.",
            status_code=503,
            code="ai_unavailable",
        ) from exc

    await session.commit()
    return ChatResponse(
        conversation_id=conversation.id,
        grounded=outcome.grounded,
        model=outcome.model,
        usage=TokenUsage(
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
        ),
        user_message=MessageRead.model_validate(outcome.user_message),
        assistant_message=MessageRead.model_validate(outcome.assistant_message),
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Ask a grounded question with a streamed answer",
    response_class=StreamingResponse,
)
async def stream_message(
    conversation_id: uuid.UUID,
    payload: ChatRequest,
    request: Request,
    context: OrgContext = Depends(require_org_member),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ai: AIProvider = Depends(get_ai_provider),  # noqa: B008
) -> StreamingResponse:
    # Authorization and lookups happen before the response starts so a client
    # receives a normal HTTP error (not a broken event stream) for 404/403.
    conversation = await _own_conversation(
        session,
        org_id=context.org_id,
        user_id=context.user.id,
        conversation_id=conversation_id,
    )
    assistant = await get_assistant(
        session,
        org_id=context.org_id,
        assistant_id=conversation.assistant_id,
        require_active=True,
    )
    # Reserve before the response starts so quota failures return a normal 429.
    reservation = await quota.reserve_ai_request(
        session,
        org_id=context.org_id,
        estimated_tokens=_estimated_chat_tokens(assistant, payload.question),
        operation="chat",
    )

    async def event_stream():
        async for event in chat_service.stream_answer(
            session,
            org_id=context.org_id,
            assistant=assistant,
            conversation=conversation,
            user=context.user,
            question=payload.question,
            ai=ai,
            actor_role=context.role,
            request=request,
            reservation=reservation,
        ):
            yield _sse(event.event, event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
