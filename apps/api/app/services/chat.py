"""Grounded chat orchestration, including token streaming.

Retrieval happens first. If nothing relevant is found the assistant refuses
without calling the model, so an unknown question can never become an invented
procedure. When passages exist, the answer policy, tenant prompt and numbered
passages are sent to the provider, and citations are resolved back to document
pages before the messages are persisted.

Streaming and non-streaming responses share the same preparation and persistence
code. A streamed turn is persisted only once the provider has finished; a client
disconnect or provider failure rolls the turn back, so a partial answer can never
appear in the transcript.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assistant import Assistant
from app.models.conversation import Conversation
from app.models.enums import MessageRole, UsageKind
from app.models.message import Message
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.prompts import NO_ANSWER_MESSAGE, Passage, build_system_prompt
from app.schemas.conversation import MessageRead
from app.services import audit, quota, usage
from app.services.ai import AIError, AIProvider, ChatMessage
from app.services.retrieval import RetrievedChunk, search

logger = structlog.get_logger(__name__)

_MARKER_RE = re.compile(r"\[(\d+)\]")
_MAX_HISTORY_MESSAGES = 10
_MAX_HISTORY_CHARS = 4000
_ROLE_ORDER = case((Message.role == MessageRole.user.value, 0), else_=1)


@dataclass(slots=True)
class Citation:
    marker: int
    document_id: uuid.UUID
    document_title: str
    page_number: int | None
    chunk_index: int
    excerpt: str
    score: float
    used: bool


@dataclass(slots=True)
class ChatOutcome:
    user_message: Message
    assistant_message: Message
    grounded: bool
    model: str | None
    prompt_tokens: int
    completion_tokens: int


@dataclass(slots=True)
class ChatPreparation:
    chunks: list[RetrievedChunk]
    request_messages: list[ChatMessage]
    model_override: str | None
    temperature: float
    max_output_tokens: int
    refusal: bool


@dataclass(slots=True)
class ChatStreamEvent:
    event: str
    data: dict[str, Any]


def _excerpt(content: str, limit: int = 300) -> str:
    cleaned = " ".join(content.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _history_rows(messages: list[Message]) -> list[ChatMessage]:
    return [
        ChatMessage(
            role=message.role,
            content=message.content[:_MAX_HISTORY_CHARS],
        )
        for message in messages
    ]


def _model_settings(assistant: Assistant) -> tuple[str | None, float, int]:
    """Resolve per-assistant overrides with safe deployment defaults."""
    settings = get_settings()
    options = assistant.model_config or {}

    model = options.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None
    else:
        model = model.strip()

    temperature = options.get("temperature")
    if (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not 0.0 <= float(temperature) <= 2.0
    ):
        temperature = settings.ai_temperature

    max_tokens = options.get("max_output_tokens")
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or not 64 <= max_tokens <= 4096
    ):
        max_tokens = settings.chat_max_output_tokens

    return model, float(temperature), max_tokens


def _citations(chunks: list[RetrievedChunk], answer_text: str) -> list[Citation]:
    used_markers = {int(match) for match in _MARKER_RE.findall(answer_text)}
    return [
        Citation(
            marker=index,
            document_id=chunk.document_id,
            document_title=chunk.document_title,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            excerpt=_excerpt(chunk.content),
            score=round(chunk.score, 4),
            used=index in used_markers,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


async def prepare(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant: Assistant,
    conversation: Conversation,
    question: str,
    ai: AIProvider,
) -> ChatPreparation:
    history = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc(), _ROLE_ORDER.desc())
                .limit(_MAX_HISTORY_MESSAGES)
            )
        ).all()
    )
    history.reverse()

    retrieval_config = assistant.retrieval_config or {}
    top_k = retrieval_config.get("top_k")
    min_score = retrieval_config.get("min_score")
    if not isinstance(top_k, int) or not 1 <= top_k <= 20:
        top_k = None
    if not isinstance(min_score, (int, float)) or not 0.0 <= min_score <= 1.0:
        min_score = None

    chunks: list[RetrievedChunk] = await search(
        session,
        org_id=org_id,
        assistant_id=assistant.id,
        query=question,
        ai=ai,
        top_k=top_k,
        min_score=float(min_score) if min_score is not None else None,
    )
    if not chunks:
        return ChatPreparation(
            chunks=[],
            request_messages=[],
            model_override=None,
            temperature=0.0,
            max_output_tokens=0,
            refusal=True,
        )

    passages = [
        Passage(
            marker=index,
            document_title=chunk.document_title,
            page_number=chunk.page_number,
            content=chunk.content,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]
    system_prompt = build_system_prompt(
        passages, tenant_prompt=assistant.system_prompt
    )
    model_override, temperature, max_output_tokens = _model_settings(assistant)
    return ChatPreparation(
        chunks=chunks,
        request_messages=[
            ChatMessage(role="system", content=system_prompt),
            *_history_rows(history),
            ChatMessage(role="user", content=question),
        ],
        model_override=model_override,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        refusal=False,
    )


async def _persist_turn(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant: Assistant,
    conversation: Conversation,
    user: User,
    question: str,
    answer_text: str,
    chunks: list[RetrievedChunk],
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    started: float,
    actor_role: str | None,
    request: Request | None,
    reservation: quota.QuotaReservation | None = None,
) -> ChatOutcome:
    citations = _citations(chunks, answer_text)
    grounded = any(citation.used for citation in citations)

    user_message = Message(
        org_id=org_id,
        conversation_id=conversation.id,
        role=MessageRole.user.value,
        content=question,
    )
    assistant_message = Message(
        org_id=org_id,
        conversation_id=conversation.id,
        role=MessageRole.assistant.value,
        content=answer_text,
        citations=[
            {
                "marker": citation.marker,
                "document_id": str(citation.document_id),
                "document_title": citation.document_title,
                "page_number": citation.page_number,
                "chunk_index": citation.chunk_index,
                "excerpt": citation.excerpt,
                "score": citation.score,
                "used": citation.used,
            }
            for citation in citations
        ],
        grounded=grounded,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    session.add_all([user_message, assistant_message])

    conversation.updated_at = func.now()
    if conversation.title == "New conversation":
        conversation.title = question[:120]

    session.add(
        UsageEvent(
            org_id=org_id,
            user_id=user.id,
            assistant_id=assistant.id,
            conversation_id=conversation.id,
            kind=UsageKind.chat.value,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
            cost_micros=usage.chat_cost_micros(prompt_tokens, completion_tokens),
            meta={
                "model": model,
                "citations": len(citations),
                "grounded": grounded,
            },
        )
    )

    await audit.record(
        session,
        action="chat.message_sent",
        org_id=org_id,
        actor_user_id=user.id,
        actor_role=actor_role,
        resource_type="conversation",
        resource_id=str(conversation.id),
        metadata={
            "question_chars": len(question),
            "citations": len(citations),
            "grounded": grounded,
            "model": model,
        },
        request=request,
    )

    # Replace the pre-flight token estimate with the provider-reported usage.
    if reservation is not None:
        await quota.reconcile_ai_tokens(
            session, reservation, prompt_tokens + completion_tokens
        )

    await session.flush()
    return ChatOutcome(
        user_message=user_message,
        assistant_message=assistant_message,
        grounded=grounded,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _outcome_payload(outcome: ChatOutcome) -> dict[str, Any]:
    return {
        "conversation_id": str(outcome.assistant_message.conversation_id),
        "grounded": outcome.grounded,
        "model": outcome.model,
        "usage": {
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
        },
        "user_message": MessageRead.model_validate(outcome.user_message).model_dump(
            mode="json"
        ),
        "assistant_message": MessageRead.model_validate(
            outcome.assistant_message
        ).model_dump(mode="json"),
    }


async def answer(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant: Assistant,
    conversation: Conversation,
    user: User,
    question: str,
    ai: AIProvider,
    actor_role: str | None = None,
    request: Request | None = None,
    reservation: quota.QuotaReservation | None = None,
) -> ChatOutcome:
    started = time.perf_counter()
    prep = await prepare(
        session,
        org_id=org_id,
        assistant=assistant,
        conversation=conversation,
        question=question,
        ai=ai,
    )

    if prep.refusal:
        answer_text = NO_ANSWER_MESSAGE
        model: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        chunks: list[RetrievedChunk] = []
    else:
        completion = await ai.complete(
            prep.request_messages,
            max_tokens=prep.max_output_tokens,
            temperature=prep.temperature,
            model=prep.model_override,
        )
        answer_text = completion.content
        model = completion.model
        prompt_tokens = completion.prompt_tokens
        completion_tokens = completion.completion_tokens
        chunks = prep.chunks

    return await _persist_turn(
        session,
        org_id=org_id,
        assistant=assistant,
        conversation=conversation,
        user=user,
        question=question,
        answer_text=answer_text,
        chunks=chunks,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        started=started,
        actor_role=actor_role,
        request=request,
        reservation=reservation,
    )


def _pieces(text: str, size: int = 24) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


async def stream_answer(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant: Assistant,
    conversation: Conversation,
    user: User,
    question: str,
    ai: AIProvider,
    actor_role: str | None = None,
    request: Request | None = None,
    reservation: quota.QuotaReservation | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """Yield token/done/error events for one grounded turn.

    The turn is persisted only after the provider completes. Nothing is written
    before the first token, so an early failure leaves no partial transcript.
    """
    started = time.perf_counter()
    prep = await prepare(
        session,
        org_id=org_id,
        assistant=assistant,
        conversation=conversation,
        question=question,
        ai=ai,
    )

    if prep.refusal:
        for piece in _pieces(NO_ANSWER_MESSAGE):
            yield ChatStreamEvent("token", {"delta": piece})
        outcome = await _persist_turn(
            session,
            org_id=org_id,
            assistant=assistant,
            conversation=conversation,
            user=user,
            question=question,
            answer_text=NO_ANSWER_MESSAGE,
            chunks=[],
            model=None,
            prompt_tokens=0,
            completion_tokens=0,
            started=started,
            actor_role=actor_role,
            request=request,
            reservation=reservation,
        )
        await session.refresh(outcome.user_message)
        await session.refresh(outcome.assistant_message)
        await session.commit()
        yield ChatStreamEvent("done", _outcome_payload(outcome))
        return

    accumulated: list[str] = []
    model: str | None = None
    prompt_tokens = 0
    completion_tokens = 0
    try:
        async for chunk in ai.stream(
            prep.request_messages,
            max_tokens=prep.max_output_tokens,
            temperature=prep.temperature,
            model=prep.model_override,
        ):
            if chunk.delta:
                accumulated.append(chunk.delta)
                yield ChatStreamEvent("token", {"delta": chunk.delta})
            if chunk.done:
                model = chunk.model or model
                prompt_tokens = chunk.prompt_tokens or prompt_tokens
                completion_tokens = chunk.completion_tokens or completion_tokens
    except asyncio.CancelledError:
        logger.info(
            "chat_stream_cancelled",
            conversation_id=str(conversation.id),
            assistant_id=str(assistant.id),
        )
        await session.rollback()
        raise
    except AIError as exc:
        await usage.record_provider_error(
            org_id=org_id,
            user_id=user.id,
            assistant_id=assistant.id,
            conversation_id=conversation.id,
            operation="stream",
            error_type=type(exc).__name__,
            retryable=exc.retryable,
            model=prep.model_override or ai.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        yield ChatStreamEvent(
            "error",
            {
                "code": "ai_unavailable",
                "message": "The assistant is temporarily unavailable. Please try again.",
            },
        )
        return

    answer_text = "".join(accumulated).strip()
    if not answer_text:
        await usage.record_provider_error(
            org_id=org_id,
            user_id=user.id,
            assistant_id=assistant.id,
            conversation_id=conversation.id,
            operation="stream",
            error_type="empty_response",
            retryable=True,
            model=model or ai.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        yield ChatStreamEvent(
            "error",
            {
                "code": "ai_unavailable",
                "message": "The assistant returned an empty answer. Please try again.",
            },
        )
        return

    outcome = await _persist_turn(
        session,
        org_id=org_id,
        assistant=assistant,
        conversation=conversation,
        user=user,
        question=question,
        answer_text=answer_text,
        chunks=prep.chunks,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        started=started,
        actor_role=actor_role,
        request=request,
        reservation=reservation,
    )
    await session.refresh(outcome.user_message)
    await session.refresh(outcome.assistant_message)
    await session.commit()
    yield ChatStreamEvent("done", _outcome_payload(outcome))
