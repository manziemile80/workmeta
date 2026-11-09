"""Unit tests for storage safety and the AI provider abstraction."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services.ai import (
    AIError,
    ChatMessage,
    FakeAIProvider,
    OpenAIProvider,
    RetryingAIProvider,
    build_ai_provider,
)
from app.services.storage import LocalStorage, generate_storage_key


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


async def test_local_storage_round_trip(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.save("tenant/assistant/file", b"payload")

    assert await storage.exists("tenant/assistant/file")
    assert await storage.read("tenant/assistant/file") == b"payload"

    await storage.delete("tenant/assistant/file")
    assert not await storage.exists("tenant/assistant/file")


async def test_local_storage_rejects_path_traversal(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(AppError) as error:
        await storage.read("../outside")
    assert error.value.code == "storage_key_invalid"


def test_generated_keys_are_tenant_prefixed_and_hide_filenames() -> None:
    org_id = uuid.uuid4()
    assistant_id = uuid.uuid4()
    key = generate_storage_key(org_id=org_id, assistant_id=assistant_id)

    assert key.startswith(f"{org_id}/{assistant_id}/")
    assert "receipt" not in key
    assert generate_storage_key(org_id=org_id, assistant_id=assistant_id) != key


async def test_fake_embeddings_are_deterministic_and_normalised() -> None:
    provider = FakeAIProvider(dimensions=128)
    first = (await provider.embed(["invoice receipt"]))[0]
    second = (await provider.embed(["invoice receipt"]))[0]

    assert len(first) == 128
    assert first == second
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


async def test_fake_embeddings_prefer_related_text() -> None:
    provider = FakeAIProvider(dimensions=512)
    document, related, unrelated = await provider.embed(
        [
            "How to issue an invoice receipt for a walk-in customer",
            "invoice receipt walk-in customer steps",
            "zebra quantum xylophone",
        ]
    )

    assert _cosine(document, related) > _cosine(document, unrelated)


async def test_fake_chat_cites_a_passage_and_counts_tokens() -> None:
    provider = FakeAIProvider(dimensions=64)
    from app.services.ai import ChatMessage

    completion = await provider.complete(
        [ChatMessage(role="user", content="Explain the passage")],
        max_tokens=100,
    )

    assert "[1]" in completion.content
    assert completion.prompt_tokens > 0
    assert completion.completion_tokens > 0


def test_missing_api_key_selects_the_fake_provider() -> None:
    provider = build_ai_provider(Settings(ai_provider="openai", ai_api_key=""))
    assert isinstance(provider, FakeAIProvider)
    assert provider.embedding_model == "fake-workmeta-embedding-1"


def test_fake_provider_is_used_when_explicitly_requested_even_with_a_key() -> None:
    provider = build_ai_provider(
        Settings(ai_provider="fake", ai_api_key="sk-not-used-in-tests")
    )
    assert isinstance(provider, FakeAIProvider)


def test_real_provider_is_wrapped_with_the_configured_retry_budget() -> None:
    provider = build_ai_provider(
        Settings(
            ai_provider="openai",
            ai_api_key="sk-test",
            ai_base_url="http://127.0.0.1:9/v1",
            ai_timeout_seconds=1,
            ai_max_retries=4,
        )
    )
    assert isinstance(provider, RetryingAIProvider)
    assert provider.max_retries == 4
    assert provider.model == "gpt-4.1-mini"


def test_provider_error_classification_only_retries_transient_failures() -> None:
    class StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    assert OpenAIProvider._is_retryable(StatusError(500)) is True
    assert OpenAIProvider._is_retryable(StatusError(503)) is True
    assert OpenAIProvider._is_retryable(StatusError(400)) is False
    assert OpenAIProvider._is_retryable(StatusError(401)) is False


class _FlakyProvider(FakeAIProvider):
    def __init__(
        self,
        *,
        failures: int,
        retryable: bool = True,
        fail_forever: bool = False,
    ) -> None:
        super().__init__(dimensions=32)
        self.failures = failures
        self.retryable = retryable
        self.fail_forever = fail_forever
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_forever or self.calls <= self.failures:
            raise AIError("provider unavailable", retryable=self.retryable)
        return await super().embed(texts)


async def test_retrying_provider_retries_transient_errors() -> None:
    inner = _FlakyProvider(failures=2)
    provider = RetryingAIProvider(
        inner, max_retries=2, backoff_seconds=0, max_backoff_seconds=0
    )

    vectors = await provider.embed(["receipt"])

    assert inner.calls == 3
    assert len(vectors[0]) == 32


async def test_retrying_provider_does_not_retry_permanent_errors() -> None:
    inner = _FlakyProvider(failures=1, retryable=False)
    provider = RetryingAIProvider(
        inner, max_retries=3, backoff_seconds=0, max_backoff_seconds=0
    )

    with pytest.raises(AIError) as error:
        await provider.embed(["receipt"])

    assert error.value.retryable is False
    assert inner.calls == 1


async def test_retrying_provider_stops_after_the_retry_budget() -> None:
    inner = _FlakyProvider(failures=0, fail_forever=True)
    provider = RetryingAIProvider(
        inner, max_retries=2, backoff_seconds=0, max_backoff_seconds=0
    )

    with pytest.raises(AIError):
        await provider.embed(["receipt"])

    assert inner.calls == 3


async def test_assistant_model_override_is_forwarded_to_the_provider() -> None:
    from app.services.ai import ChatCompletion

    class _RecordingProvider(FakeAIProvider):
        def __init__(self) -> None:
            super().__init__(dimensions=32)
            self.last: dict[str, object] = {}

        async def complete(
            self,
            messages: Sequence[ChatMessage],
            *,
            max_tokens: int,
            temperature: float = 0.0,
            model: str | None = None,
        ) -> ChatCompletion:
            self.last = {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "model": model,
            }
            return await super().complete(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
            )

    provider = _RecordingProvider()
    completion = await provider.complete(
        [ChatMessage(role="user", content="hello")],
        max_tokens=256,
        temperature=0.3,
        model="tenant-model",
    )

    assert provider.last == {
        "max_tokens": 256,
        "temperature": 0.3,
        "model": "tenant-model",
    }
    assert completion.model == "tenant-model"
