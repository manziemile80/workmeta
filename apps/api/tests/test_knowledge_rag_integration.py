"""Stage 2 end-to-end and isolation tests against real PostgreSQL.

Covers upload → worker ingestion → hybrid retrieval → grounded answer with
citations, honest refusal, document/conversation isolation through the API and
through raw SQL as the runtime role, and the platform-owner content boundary.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.rls import set_rls_context
from app.db.session import SessionFactory
from app.main import app
from app.services.ai import (
    AIError,
    ChatCompletion,
    ChatMessage,
    FakeAIProvider,
    get_ai_provider,
)
from app.services.document_text import chunk_pages, extract_pages
from app.services.storage import LocalStorage, get_storage
from app.workers.ingestion import process_next_job
from tests.test_auth_tenancy_integration import _bearer, _tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

RECEIPT_GUIDE = (
    "How to issue an EBM receipt for a walk-in customer:\n"
    "1. Open the sales screen.\n"
    "2. Select Walk-in customer.\n"
    "3. Confirm the invoice and print the receipt.\n"
)


class FlakyAIProvider(FakeAIProvider):
    """Fails after N successful batches, to exercise ingestion rollback."""

    def __init__(self, dimensions: int, *, succeed_batches: int) -> None:
        super().__init__(dimensions)
        self.succeed_batches = succeed_batches
        self.batches = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches += 1
        if self.batches > self.succeed_batches:
            raise RuntimeError("simulated embedding outage")
        return await super().embed(texts)



@asynccontextmanager
async def _engine(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def fake_ai() -> Iterator[FakeAIProvider]:
    provider = FakeAIProvider(get_settings().embedding_dimensions)
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)


@pytest.fixture
def storage(tmp_path) -> Iterator[LocalStorage]:
    provider = LocalStorage(tmp_path)
    app.dependency_overrides[get_storage] = lambda: provider
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_storage, None)


async def _create_assistant(
    client: AsyncClient,
    token: str,
    org_id: str,
    name: str = "EBM Assistant",
    *,
    model_options: dict | None = None,
    retrieval_config: dict | None = None,
) -> dict:
    payload: dict = {"name": name, "product_code": "ebm"}
    if model_options is not None:
        payload["model_options"] = model_options
    if retrieval_config is not None:
        payload["retrieval_config"] = retrieval_config
    response = await client.post(
        f"/api/v1/organizations/{org_id}/assistants",
        headers=_bearer(token),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(
    client: AsyncClient,
    token: str,
    org_id: str,
    assistant_id: str,
    *,
    filename: str = "receipt-guide.txt",
    content: bytes = RECEIPT_GUIDE.encode(),
    mime_type: str = "text/plain",
    title: str = "Receipt Guide",
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/assistants/{assistant_id}/documents",
        headers=_bearer(token),
        files={"file": (filename, content, mime_type)},
        data={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _drain(ai: FakeAIProvider, storage: LocalStorage) -> int:
    processed = 0
    for _ in range(20):
        if not await process_next_job(
            ai=ai, storage=storage, session_factory=SessionFactory
        ):
            break
        processed += 1
    return processed


async def _conversation(
    client: AsyncClient, token: str, org_id: str, assistant_id: str
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/assistants/{assistant_id}/conversations",
        headers=_bearer(token),
        json={},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _ask(
    client: AsyncClient, token: str, org_id: str, conversation_id: str, question: str
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/conversations/{conversation_id}/messages",
        headers=_bearer(token),
        json={"question": question},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _chunk_count(org_id: str, document_id: str) -> int:
    async with _engine(get_settings().database_url) as engine:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.current_org', :org, true)"),
                    {"org": org_id},
                )
                return (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM document_chunks "
                            "WHERE document_id = :doc"
                        ),
                        {"doc": document_id},
                    )
                    or 0
                )


async def _release_ingestion(org_id: str) -> None:
    """Make a requeued job immediately available again."""
    async with SessionFactory() as session:
        await set_rls_context(session, org_id=org_id)
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET available_at = now() "
                "WHERE org_id = :org AND status = 'queued'"
            ),
            {"org": org_id},
        )
        await session.commit()


async def test_grounded_answer_cites_the_uploaded_document(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-flow")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)

    products = await client.get("/api/v1/products", headers=_bearer(tenant.token))
    assert products.status_code == 200
    assert "ebm" in {row["code"] for row in products.json()}

    document = await _upload(
        client, tenant.token, org_id, assistant["id"]
    )
    assert document["status"] == "uploaded"
    assert document["download_url"]
    assert document["sha256"]

    assert await _drain(fake_ai, storage) == 1

    ready = await client.get(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}",
        headers=_bearer(tenant.token),
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"
    assert ready.json()["page_count"] == 1

    download = await client.get(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}/download",
        headers=_bearer(tenant.token),
    )
    assert download.status_code == 200
    assert download.content == RECEIPT_GUIDE.encode()
    assert "attachment" in download.headers["content-disposition"]

    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )
    result = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "How do I issue an EBM receipt for a walk-in customer?",
    )

    assert result["grounded"] is True
    assert result["usage"]["prompt_tokens"] > 0
    assert result["assistant_message"]["citations"]
    citation = result["assistant_message"]["citations"][0]
    assert citation["document_id"] == document["id"]
    assert citation["document_title"] == "Receipt Guide"
    assert citation["page_number"] == 1
    assert citation["used"] is True

    detail = await client.get(
        f"/api/v1/organizations/{org_id}/conversations/{conversation['id']}",
        headers=_bearer(tenant.token),
    )
    assert detail.status_code == 200
    assert [message["role"] for message in detail.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert detail.json()["title"].startswith("How do I issue")


async def test_unknown_question_refuses_without_calling_the_model(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-refuse")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)
    await _upload(client, tenant.token, org_id, assistant["id"])
    await _drain(fake_ai, storage)

    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )
    result = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "Zebra quantum xylophone?",
    )

    assert result["grounded"] is False
    assert result["model"] is None
    assert result["assistant_message"]["citations"] == []
    assert "do not contain an answer" in result["assistant_message"]["content"]


async def test_cross_tenant_documents_and_conversations_are_invisible(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "rag-iso-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "rag-iso-b")
    org_a = tenant_a.organization["id"]
    org_b = tenant_b.organization["id"]

    assistant_a = await _create_assistant(client, tenant_a.token, org_a)
    await _create_assistant(client, tenant_b.token, org_b, "Other Assistant")
    document_a = await _upload(client, tenant_a.token, org_a, assistant_a["id"])
    await _drain(fake_ai, storage)

    conversation_a = await _conversation(
        client, tenant_a.token, org_a, assistant_a["id"]
    )

    # Tenant B cannot reach A's assistant, documents or conversation.
    assert (
        await client.get(
            f"/api/v1/organizations/{org_b}/assistants/{assistant_a['id']}/documents",
            headers=_bearer(tenant_b.token),
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/organizations/{org_b}/assistants/{assistant_a['id']}"
            f"/documents/{document_a['id']}/download",
            headers=_bearer(tenant_b.token),
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/organizations/{org_b}/conversations/{conversation_a['id']}",
            headers=_bearer(tenant_b.token),
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/organizations/{org_b}/conversations/{conversation_a['id']}"
            "/messages",
            headers=_bearer(tenant_b.token),
            json={"question": "Show me the receipt"},
        )
    ).status_code == 404

    own_documents = await client.get(
        f"/api/v1/organizations/{org_b}/assistants/{assistant_a['id']}/documents",
        headers=_bearer(tenant_a.token),
    )
    assert own_documents.status_code == 404

    # Public product catalog is readable by both tenants.
    assert (
        await client.get("/api/v1/products", headers=_bearer(tenant_b.token))
    ).status_code == 200


async def test_platform_owner_has_no_content_bypass(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-owner")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)
    document = await _upload(client, tenant.token, org_id, assistant["id"])

    blocked = await client.get(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}",
        headers=_bearer(owner_token),
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "membership_required"


async def test_raw_sql_and_worker_gate_do_not_expose_tenant_content(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "rag-rls-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "rag-rls-b")
    org_a = tenant_a.organization["id"]
    org_b = tenant_b.organization["id"]

    assistant_a = await _create_assistant(client, tenant_a.token, org_a)
    await _upload(client, tenant_a.token, org_a, assistant_a["id"])
    await _drain(fake_ai, storage)

    settings = get_settings()
    async with _engine(settings.database_url) as engine:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.current_org', :org, true)"),
                    {"org": org_b},
                )
                for table in (
                    "documents",
                    "document_chunks",
                    "conversations",
                    "messages",
                    "assistants",
                ):
                    assert (
                        await connection.scalar(
                            text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),  # noqa: S608
                            {"org": org_a},
                        )
                    ) == 0, table

            # No tenant context: every tenant table is empty.
            async with connection.begin():
                for table in ("documents", "document_chunks", "conversations"):
                    assert (
                        await connection.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                    ) == 0, table

            # The worker gate exposes the queue, never document content.
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.worker', 'true', true)")
                )
                assert (
                    await connection.scalar(text("SELECT count(*) FROM documents"))
                ) == 0
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM document_chunks")
                    )
                ) == 0
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM ingestion_jobs")
                    )
                    >= 1
                )


async def test_uploaded_content_cannot_mislead_the_extractor(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-bad")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)

    mislabelled = await client.post(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}/documents",
        headers=_bearer(tenant.token),
        files={"file": ("guide.txt", b"%PDF-1.7\nnot really", "text/plain")},
    )
    assert mislabelled.status_code == 422
    assert mislabelled.json()["error"]["code"] == "invalid_document"

    unsupported = await client.post(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}/documents",
        headers=_bearer(tenant.token),
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert unsupported.status_code == 422

    # Reprocessing a document that is not failed is a conflict, not a silent
    # re-embed.
    document = await _upload(client, tenant.token, org_id, assistant["id"])
    reprocess = await client.post(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}/reprocess",
        headers=_bearer(tenant.token),
    )
    assert reprocess.status_code == 409
    assert reprocess.json()["error"]["code"] == "document_not_failed"

    # Keep the shared queue clean for later tests.
    assert await _drain(fake_ai, storage) == 1


async def test_invitation_token_cannot_read_knowledge(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    """A valid tenant session is required; an org id alone is not enough."""
    tenant = await _tenant(client, owner_token, test_prefix, "rag-anon")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.get(
            f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}/documents"
        )
    assert response.status_code == 401
    assert uuid.UUID(assistant["id"])


async def test_assistant_updates_bump_version_and_reject_duplicate_slugs(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-config")
    org_id = tenant.organization["id"]
    first = await _create_assistant(client, tenant.token, org_id, "Alpha Guide")
    second = await _create_assistant(client, tenant.token, org_id, "Beta Guide")
    assert first["slug"] != second["slug"]

    updated = await client.patch(
        f"/api/v1/organizations/{org_id}/assistants/{first['id']}",
        headers=_bearer(tenant.token),
        json={
            "name": "Alpha Guide v2",
            "retrieval_config": {"top_k": 3, "min_score": 0.35},
            "model_options": {"temperature": 0.2},
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "Alpha Guide v2"
    assert body["version"] == first["version"] + 1
    assert body["retrieval_config"] == {"top_k": 3, "min_score": 0.35}
    assert body["model_options"] == {"temperature": 0.2}

    conflict = await client.patch(
        f"/api/v1/organizations/{org_id}/assistants/{first['id']}",
        headers=_bearer(tenant.token),
        json={"slug": second["slug"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "slug_taken"

    invalid_product = await client.patch(
        f"/api/v1/organizations/{org_id}/assistants/{first['id']}",
        headers=_bearer(tenant.token),
        json={"product_code": "does-not-exist"},
    )
    assert invalid_product.status_code == 422
    assert invalid_product.json()["error"]["code"] == "product_unavailable"

    other = await _tenant(client, owner_token, test_prefix, "rag-config-b")
    cross_tenant = await client.patch(
        f"/api/v1/organizations/{other.organization['id']}/assistants/{first['id']}",
        headers=_bearer(other.token),
        json={"name": "Hijacked"},
    )
    assert cross_tenant.status_code == 404


async def test_failed_ingestion_rolls_back_chunks_and_retry_is_clean(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    storage: LocalStorage,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "chunk_tokens", 20)
    monkeypatch.setattr(settings, "chunk_overlap_tokens", 5)
    monkeypatch.setattr(settings, "embedding_batch_size", 1)

    pages = extract_pages(
        RECEIPT_GUIDE.encode(), "text/plain", max_pages=settings.max_document_pages
    )
    expected_chunks = len(
        chunk_pages(pages, chunk_tokens=20, overlap_tokens=5)
    )
    assert expected_chunks >= 2

    tenant = await _tenant(client, owner_token, test_prefix, "rag-retry")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)
    document = await _upload(client, tenant.token, org_id, assistant["id"])

    # Fail after the first batch has already been written to the transaction.
    flaky = FlakyAIProvider(settings.embedding_dimensions, succeed_batches=1)
    assert await _drain(flaky, storage) == 1
    assert await _chunk_count(org_id, document["id"]) == 0

    pending = await client.get(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}",
        headers=_bearer(tenant.token),
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "uploaded"

    # A retry succeeds without tripping over leftover chunks or requiring DELETE.
    await _release_ingestion(org_id)
    assert await _drain(FakeAIProvider(settings.embedding_dimensions), storage) == 1
    assert await _chunk_count(org_id, document["id"]) == expected_chunks

    ready = await client.get(
        f"/api/v1/organizations/{org_id}/assistants/{assistant['id']}"
        f"/documents/{document['id']}",
        headers=_bearer(tenant.token),
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"
    assert ready.json()["error_message"] is None


LEAVE_POLICY = (
    "Annual leave policy:\n"
    "Employees request annual leave through the HR portal.\n"
    "Sick leave requires a medical certificate within three days.\n"
)


class RecordingAIProvider(FakeAIProvider):
    """Captures what the chat service sends to the model."""

    def __init__(self, dimensions: int) -> None:
        super().__init__(dimensions)
        self.completions: list[dict] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion:
        self.completions.append(
            {
                "messages": list(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "model": model,
            }
        )
        return await super().complete(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )


async def test_retrieval_ranks_the_relevant_document_first(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-accuracy")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)

    await _upload(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        filename="receipt-guide.txt",
        title="EBM Receipt Guide",
        content=RECEIPT_GUIDE.encode(),
    )
    await _upload(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        filename="leave-policy.txt",
        title="Leave Policy",
        content=LEAVE_POLICY.encode(),
    )
    assert await _drain(fake_ai, storage) == 2

    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )
    receipt = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "How do I issue an EBM receipt for a walk-in customer?",
    )
    assert receipt["grounded"] is True
    assert receipt["assistant_message"]["citations"][0]["document_title"] == (
        "EBM Receipt Guide"
    )
    assert "walk-in customer" in (
        receipt["assistant_message"]["citations"][0]["excerpt"].lower()
    )

    leave = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "How do employees request annual leave?",
    )
    assert leave["grounded"] is True
    assert leave["assistant_message"]["citations"][0]["document_title"] == (
        "Leave Policy"
    )


async def test_retrieval_never_mixes_embedding_models(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
    monkeypatch,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-vector-space")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)
    await _upload(client, tenant.token, org_id, assistant["id"])
    await _drain(fake_ai, storage)

    # The document was embedded with the fake model. Simulate a deployment that
    # switched embedding models: its vectors must not be compared at all.
    monkeypatch.setattr(fake_ai, "embedding_model", "different-embedding-model")

    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )
    result = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "How do I issue an EBM receipt for a walk-in customer?",
    )

    assert result["grounded"] is False
    assert result["model"] is None
    assert result["assistant_message"]["citations"] == []


async def test_retrieval_never_crosses_assistant_boundary(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-assistant-iso")
    org_id = tenant.organization["id"]
    knowledge_assistant = await _create_assistant(
        client, tenant.token, org_id, "Knowledge Assistant"
    )
    empty_assistant = await _create_assistant(
        client, tenant.token, org_id, "Empty Assistant"
    )
    await _upload(
        client, tenant.token, org_id, knowledge_assistant["id"]
    )
    await _drain(fake_ai, storage)

    conversation = await _conversation(
        client, tenant.token, org_id, empty_assistant["id"]
    )
    result = await _ask(
        client,
        tenant.token,
        org_id,
        conversation["id"],
        "How do I issue an EBM receipt for a walk-in customer?",
    )

    assert result["grounded"] is False
    assert result["model"] is None
    assert result["assistant_message"]["citations"] == []


async def test_provider_failure_returns_503_without_persisting_messages(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-provider-down")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(client, tenant.token, org_id)
    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )

    class FailingProvider(FakeAIProvider):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise AIError("provider unavailable", retryable=True)

    app.dependency_overrides[get_ai_provider] = lambda: FailingProvider(
        get_settings().embedding_dimensions
    )
    try:
        response = await client.post(
            f"/api/v1/organizations/{org_id}/conversations/{conversation['id']}"
            "/messages",
            headers=_bearer(tenant.token),
            json={"question": "How do I issue a receipt?"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"

    detail = await client.get(
        f"/api/v1/organizations/{org_id}/conversations/{conversation['id']}",
        headers=_bearer(tenant.token),
    )
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


async def test_chat_uses_model_options_and_conversation_history(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: FakeAIProvider,
    storage: LocalStorage,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "rag-model")
    org_id = tenant.organization["id"]
    assistant = await _create_assistant(
        client,
        tenant.token,
        org_id,
        model_options={
            "model": "tenant-chat-model",
            "temperature": 0.4,
            "max_output_tokens": 256,
        },
    )
    await _upload(client, tenant.token, org_id, assistant["id"])
    await _drain(fake_ai, storage)

    conversation = await _conversation(
        client, tenant.token, org_id, assistant["id"]
    )
    recording = RecordingAIProvider(get_settings().embedding_dimensions)
    app.dependency_overrides[get_ai_provider] = lambda: recording
    try:
        first = await _ask(
            client,
            tenant.token,
            org_id,
            conversation["id"],
            "How do I issue an EBM receipt for a walk-in customer?",
        )
        second = await _ask(
            client,
            tenant.token,
            org_id,
            conversation["id"],
            "Can you repeat the receipt steps?",
        )
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert first["model"] == "tenant-chat-model"
    assert second["model"] == "tenant-chat-model"
    assert len(recording.completions) == 2
    call = recording.completions[1]
    assert call["model"] == "tenant-chat-model"
    assert call["temperature"] == 0.4
    assert call["max_tokens"] == 256
    roles = [message.role for message in call["messages"]]
    contents = [message.content for message in call["messages"]]
    assert roles[:3] == ["system", "user", "assistant"]
    assert any("How do I issue an EBM receipt" in text for text in contents)
    assert any("approved documents" in text for text in contents)
