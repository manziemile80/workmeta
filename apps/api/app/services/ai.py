"""AI provider abstraction.

The platform talks to an OpenAI-compatible API, but every call goes through this
small interface. That keeps provider credentials server-side, lets clients with
data-residency requirements point at Azure/OpenRouter/Ollama later, and gives
tests a deterministic offline implementation.

When ``AI_API_KEY`` is empty (development, CI), a deterministic fake provider is
used. It produces stable unit vectors and a canned grounded answer, so no test
ever calls a paid model or depends on the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

import structlog
import tiktoken

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_T = TypeVar("_T")


class AIError(Exception):
    """Raised when the configured provider cannot complete a request.

    ``retryable`` distinguishes a temporary provider problem (rate limit,
    timeout, connection or 5xx) from a permanent one (bad key, invalid model,
    malformed request). Only the former is retried.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(slots=True)
class ChatCompletion:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(slots=True)
class ChatStreamChunk:
    """One streamed piece of an answer.

    ``done`` marks the final chunk. Only the final chunk is guaranteed to carry
    usage counters; intermediate chunks may report zero tokens.
    """

    delta: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    done: bool = False


def count_tokens(text: str) -> int:
    """Count tokens with the same encoding used for prompt accounting."""
    if not text:
        return 0
    return len(_encoding().encode(text))


_ENCODING: tiktoken.Encoding | None = None


def _encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


class AIProvider(Protocol):
    model: str
    embedding_model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]: ...


class FakeAIProvider:
    """Offline, deterministic provider used when no API key is configured."""

    model = "fake-workmeta-1"
    embedding_model = "fake-workmeta-embedding-1"

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            tokens = [text.strip() or "empty"]
        features: list[tuple[str, float]] = []
        for token in tokens:
            features.append((token, 1.0))
        for left, right in zip(tokens, tokens[1:], strict=False):
            features.append((f"{left} {right}", 0.5))
        for feature, weight in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            values[index] += weight
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion:
        prompt_tokens = sum(
            len(self._encoding.encode(message.content)) for message in messages
        )
        content = (
            "Based on the approved documents, follow the cited procedure [1]. "
            "Only the numbered steps in the source are approved guidance."
        )
        return ChatCompletion(
            content=content,
            model=model or self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(self._encoding.encode(content)),
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        prompt_tokens = sum(
            len(self._encoding.encode(message.content)) for message in messages
        )
        resolved_model = model or self.model
        content = (
            "Based on the approved documents, follow the cited procedure [1]. "
            "Only the numbered steps in the source are approved guidance."
        )
        words = content.split(" ")
        for index, word in enumerate(words):
            delta = word if index == 0 else f" {word}"
            # Yield control so a disconnected client can cancel promptly.
            await asyncio.sleep(0)
            yield ChatStreamChunk(delta=delta, model=resolved_model)
        yield ChatStreamChunk(
            delta="",
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(self._encoding.encode(content)),
            done=True,
        )


class OpenAIProvider:
    """OpenAI-compatible embeddings and chat completions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        embedding_model: str,
        dimensions: int,
        timeout_seconds: int,
    ) -> None:
        from openai import AsyncOpenAI  # imported lazily so tests stay light

        self.model = model
        self.embedding_model = embedding_model
        self.dimensions = dimensions
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            # Retries are handled by RetryingAIProvider so every provider has the
            # same explicit retry budget, backoff and logging.
            max_retries=0,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        if isinstance(
            exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
        ):
            return True
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and status >= 500

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            kwargs: dict[str, object] = {
                "model": self.embedding_model,
                "input": list(texts),
            }
            # The dimensions parameter is only accepted by text-embedding-3+.
            if self.embedding_model.startswith("text-embedding-3"):
                kwargs["dimensions"] = self.dimensions
            response = await self._client.embeddings.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider errors become AIError
            retryable = self._is_retryable(exc)
            logger.warning(
                "embedding_request_failed",
                error_type=type(exc).__name__,
                retryable=retryable,
                model=self.embedding_model,
            )
            raise AIError("Embedding request failed", retryable=retryable) from exc

        if len(response.data) != len(texts):
            raise AIError(
                "Embedding provider returned the wrong number of vectors",
                retryable=False,
            )
        vectors = [list(item.embedding) for item in response.data]
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise AIError(
                    "Embedding provider returned an unexpected vector size",
                    retryable=False,
                )
        return vectors

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion:
        requested_model = model or self.model
        try:
            response = await self._client.chat.completions.create(
                model=requested_model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 - provider errors become AIError
            retryable = self._is_retryable(exc)
            logger.warning(
                "chat_request_failed",
                error_type=type(exc).__name__,
                retryable=retryable,
                model=requested_model,
            )
            raise AIError("Chat completion request failed", retryable=retryable) from exc

        choice = response.choices[0] if response.choices else None
        if choice is None or choice.message.content is None:
            raise AIError("Chat provider returned an empty response", retryable=True)
        usage = response.usage
        return ChatCompletion(
            content=choice.message.content.strip(),
            model=response.model or requested_model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        requested_model = model or self.model
        prompt_tokens = sum(count_tokens(message.content) for message in messages)
        completion_tokens = 0
        streamed: list[str] = []
        resolved_model = requested_model
        try:
            response = await self._client.chat.completions.create(
                model=requested_model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in response:
                if chunk.model:
                    resolved_model = chunk.model
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = usage.prompt_tokens
                    completion_tokens = usage.completion_tokens
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta.content if choice.delta is not None else None
                if delta:
                    streamed.append(delta)
                    yield ChatStreamChunk(delta=delta, model=resolved_model)
        except Exception as exc:  # noqa: BLE001 - provider errors become AIError
            retryable = self._is_retryable(exc)
            logger.warning(
                "chat_stream_failed",
                error_type=type(exc).__name__,
                retryable=retryable,
                model=requested_model,
            )
            raise AIError("Chat stream request failed", retryable=retryable) from exc

        if completion_tokens == 0:
            # Some compatible providers omit usage on streams. Token counters
            # are an estimate in that case, never a billing figure.
            completion_tokens = count_tokens("".join(streamed))
        yield ChatStreamChunk(
            delta="",
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            done=True,
        )


class RetryingAIProvider:
    """Retry transient provider errors with bounded exponential backoff.

    Permanent errors (bad credentials, unknown model, invalid request) are
    re-raised immediately so a misconfiguration fails fast instead of burning
    the retry budget. Timeouts apply to every individual HTTP request through
    the OpenAI client; this wrapper only controls how many requests are made.
    """

    def __init__(
        self,
        provider: AIProvider,
        *,
        max_retries: int,
        backoff_seconds: float,
        max_backoff_seconds: float,
    ) -> None:
        self._provider = provider
        self.model = provider.model
        self.embedding_model = provider.embedding_model
        self.dimensions = provider.dimensions
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.max_backoff_seconds = max(0.0, max_backoff_seconds)

    async def _call(
        self, operation: str, request: Callable[[], Awaitable[_T]]
    ) -> _T:
        attempt = 0
        while True:
            try:
                return await request()
            except AIError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    if exc.retryable:
                        logger.error(
                            "ai_request_exhausted",
                            operation=operation,
                            attempts=attempt + 1,
                        )
                    raise

                delay = min(
                    self.backoff_seconds * (2**attempt),
                    self.max_backoff_seconds,
                )
                attempt += 1
                logger.warning(
                    "ai_request_retry",
                    operation=operation,
                    attempt=attempt,
                    attempts_allowed=self.max_retries + 1,
                    delay_seconds=delay,
                    error=str(exc),
                )
                if delay > 0:
                    await asyncio.sleep(delay)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._call("embed", lambda: self._provider.embed(texts))

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion:
        return await self._call(
            "complete",
            lambda: self._provider.complete(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
            ),
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Retry a stream only while no content has been sent to the client.

        Once the first token is yielded the response is no longer replayable:
        retrying would duplicate text, so the error is surfaced instead.
        """
        attempt = 0
        while True:
            emitted = False
            try:
                async for chunk in self._provider.stream(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                ):
                    if chunk.delta:
                        emitted = True
                    yield chunk
                return
            except AIError as exc:
                if emitted or not exc.retryable or attempt >= self.max_retries:
                    if exc.retryable:
                        logger.error(
                            "ai_stream_exhausted",
                            operation="stream",
                            attempts=attempt + 1,
                            emitted=emitted,
                        )
                    raise

                delay = min(
                    self.backoff_seconds * (2**attempt),
                    self.max_backoff_seconds,
                )
                attempt += 1
                logger.warning(
                    "ai_stream_retry",
                    operation="stream",
                    attempt=attempt,
                    attempts_allowed=self.max_retries + 1,
                    delay_seconds=delay,
                    error=str(exc),
                )
                if delay > 0:
                    await asyncio.sleep(delay)


def build_ai_provider(settings: Settings | None = None) -> AIProvider:
    settings = settings or get_settings()
    if settings.ai_provider == "fake" or not settings.ai_api_key:
        logger.warning(
            "ai_provider_fake",
            reason="AI_PROVIDER=fake" if settings.ai_provider == "fake" else "AI_API_KEY missing",
        )
        return FakeAIProvider(settings.embedding_dimensions)
    provider = OpenAIProvider(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_chat_model,
        embedding_model=settings.ai_embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.ai_timeout_seconds,
    )
    return RetryingAIProvider(
        provider,
        max_retries=settings.ai_max_retries,
        backoff_seconds=settings.ai_retry_backoff_seconds,
        max_backoff_seconds=settings.ai_retry_max_backoff_seconds,
    )


def get_ai_provider() -> AIProvider:
    """FastAPI dependency. Tests override this with a fake provider."""
    return build_ai_provider()
