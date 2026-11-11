"""Trained extension tests against a real PostgreSQL database.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v

The tests cover the promises the feature makes: a client's extension is
isolated from every other client, a system version is part of its identity,
publishing is gated on training, approval and a passed test, and the agent may
only perform the actions the client's policy allows - with a confirmation for
the sensitive ones.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.main import app
from app.services.ai import ChatCompletion, FakeAIProvider, get_ai_provider
from app.services.extension_signing import (
    canonical_json,
    verify_manifest,
)
from app.services.storage import LocalStorage, get_storage
from tests.test_auth_tenancy_integration import _bearer, _tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

PROCEDURES_JSON = {
    "procedures": [
        {
            "title": "Issue a receipt for a walk-in customer",
            "summary": "Create and print a receipt in the sales screen.",
            "confidence": 0.8,
            "steps": [
                {
                    "order": 1,
                    "instruction": "Open the sales screen",
                    "where": "Menu: Sales",
                    "value": None,
                    "expected_result": "The sales screen opens",
                    "sensitive": False,
                },
                {
                    "order": 2,
                    "instruction": "Select the walk-in customer and confirm",
                    "where": "Button: Walk-in customer",
                    "value": None,
                    "expected_result": "The receipt preview opens",
                    "sensitive": False,
                },
                {
                    "order": 3,
                    "instruction": "Print the receipt",
                    "where": "Button: Print",
                    "value": None,
                    "expected_result": "The receipt is printed",
                    "sensitive": True,
                },
            ],
        }
    ]
}

GUIDANCE_ANSWER = (
    "To issue a receipt for a walk-in customer [1]:\n"
    "1. Open the sales screen.\n"
    "2. Select Walk-in customer.\n"
    "3. Print the receipt."
)


class TrainingAIProvider(FakeAIProvider):
    """Answers the analysis prompt with procedures and the guidance with steps."""

    def __init__(self, dimensions: int) -> None:
        super().__init__(dimensions)

    async def complete(  # type: ignore[override]
        self,
        messages: Sequence[object],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> ChatCompletion:
        system = getattr(messages[0], "content", "")
        if "training analyst" in system:
            content = json.dumps(PROCEDURES_JSON)
        else:
            content = GUIDANCE_ANSWER
        return ChatCompletion(
            content=content,
            model=model or self.model,
            prompt_tokens=500,
            completion_tokens=200,
        )


@pytest.fixture
def fake_ai() -> Iterator[TrainingAIProvider]:
    provider = TrainingAIProvider(get_settings().embedding_dimensions)
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


@asynccontextmanager
async def _engine(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _assistant(client: AsyncClient, token: str, org_id: str) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/assistants",
        headers=_bearer(token),
        json={"name": "EBM assistant", "slug": f"ebm-{uuid.uuid4().hex[:6]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _extension(
    client: AsyncClient,
    token: str,
    org_id: str,
    assistant_id: str,
    *,
    system_code: str = "ebm",
    system_version: str = "3.2",
    url_patterns: list[str] | None = None,
    permissions: dict | None = None,
) -> dict:
    payload = {
        "name": f"{system_code} {system_version}",
        "assistant_id": assistant_id,
        "system_code": system_code,
        "system_name": system_code.upper(),
        "system_version": system_version,
        "target_kind": "web",
        "url_patterns": url_patterns
        if url_patterns is not None
        else ["*://ebm.local/*"],
    }
    if permissions is not None:
        payload["permissions"] = permissions
    response = await client.post(
        f"/api/v1/organizations/{org_id}/extensions",
        headers=_bearer(token),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _train(
    client: AsyncClient,
    token: str,
    org_id: str,
    extension_id: str,
    *,
    approve: bool = True,
) -> list[dict]:
    """Add written material, analyse it and approve the procedures."""
    note = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/materials/notes",
        headers=_bearer(token),
        json={
            "title": "Walk-in receipt notes",
            "text": (
                "Open the sales screen, choose Walk-in customer, confirm the "
                "invoice and print the receipt."
            ),
        },
    )
    assert note.status_code == 201, note.text

    analysis = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/analyze",
        headers=_bearer(token),
    )
    assert analysis.status_code == 200, analysis.text
    procedures = analysis.json()["procedures"]
    assert procedures, analysis.text

    if approve:
        for procedure in procedures:
            approved = await client.patch(
                f"/api/v1/organizations/{org_id}/extensions/{extension_id}"
                f"/procedures/{procedure['id']}",
                headers=_bearer(token),
                json={"status": "approved"},
            )
            assert approved.status_code == 200, approved.text
    return procedures


async def _publish(
    client: AsyncClient, token: str, org_id: str, extension_id: str
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/publish",
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _passing_test(
    client: AsyncClient, token: str, org_id: str, extension_id: str
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/tests",
        headers=_bearer(token),
        json={
            "question": "How do I issue a receipt for a walk-in customer?",
            "expected_points": ["Open the sales screen"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_extension_is_private_to_its_client(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-isolated")
    other = await _tenant(client, owner_token, test_prefix, "ext-other")

    assistant = await _assistant(
        client, tenant.token, tenant.organization["id"]
    )
    extension = await _extension(
        client,
        tenant.token,
        tenant.organization["id"],
        assistant["id"],
    )

    # The other client cannot see it, read it or change it.
    for path in (
        f"/{extension['id']}",
        f"/{extension['id']}/materials",
        f"/{extension['id']}/procedures",
        f"/{extension['id']}/package",
    ):
        response = await client.get(
            f"/api/v1/organizations/{other.organization['id']}/extensions{path}",
            headers=_bearer(other.token),
        )
        assert response.status_code == 404, (path, response.status_code)

    response = await client.get(
        f"/api/v1/organizations/{other.organization['id']}/extensions",
        headers=_bearer(other.token),
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_the_same_system_can_be_trained_for_several_versions(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-versions")
    org_id = tenant.organization["id"]

    # Give the client room for more than one extension.
    plans = await client.get("/api/v1/platform/plans", headers=_bearer(owner_token))
    free = next(
        plan for plan in plans.json() if plan["code"] == "free"
    )
    updated = await client.patch(
        f"/api/v1/platform/plans/{free['id']}",
        headers=_bearer(owner_token),
        json={"max_extensions": 5},
    )
    assert updated.status_code == 200, updated.text

    assistant = await _assistant(client, tenant.token, org_id)
    old = await _extension(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        system_version="3.2",
        url_patterns=["*://ebm.local/v3/*"],
    )
    new = await _extension(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        system_version="4.0",
        url_patterns=["*://ebm.local/v4/*"],
    )
    assert old["id"] != new["id"]
    assert old["slug"] != new["slug"]
    assert old["system_version"] == "3.2"
    assert new["system_version"] == "4.0"

    listed = await client.get(
        f"/api/v1/organizations/{org_id}/extensions", headers=_bearer(tenant.token)
    )
    assert {row["id"] for row in listed.json()} == {old["id"], new["id"]}


async def test_publishing_requires_training_approval_and_a_passed_test(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-publish")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(client, tenant.token, org_id, assistant["id"])
    extension_id = extension["id"]

    # 1. Not trained yet.
    blocked = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/publish",
        headers=_bearer(tenant.token),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "extension_not_trained"

    # 2. Trained, but nothing approved yet.
    await _train(client, tenant.token, org_id, extension_id, approve=False)
    blocked = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/publish",
        headers=_bearer(tenant.token),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "extension_no_procedures"

    # 3. Approved, but not tested.
    await _train(client, tenant.token, org_id, extension_id, approve=True)
    blocked = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/publish",
        headers=_bearer(tenant.token),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "extension_not_tested"

    # 4. A failing test does not unlock publishing either.
    failing = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}/tests",
        headers=_bearer(tenant.token),
        json={
            "question": "How do I issue a receipt?",
            "expected_points": ["open the student registry screen"],
        },
    )
    assert failing.status_code == 201, failing.text
    assert failing.json()["outcome"] == "failed"
    assert failing.json()["missed_points"]

    # 5. A passing test unlocks the publish.
    test = await _passing_test(client, tenant.token, org_id, extension_id)
    assert test["outcome"] == "passed"
    assert test["score"] == 1.0

    package = await _publish(client, tenant.token, org_id, extension_id)
    assert package["manifest"]["system"]["version"] == "3.2"
    assert package["manifest"]["procedures"]
    assert package["manifest"]["procedures"][0]["steps"]
    assert package["published_at"]

    read = await client.get(
        f"/api/v1/organizations/{org_id}/extensions/{extension_id}",
        headers=_bearer(tenant.token),
    )
    assert read.json()["status"] == "published"
    assert read.json()["approved_procedures_count"] == 1
    assert read.json()["passed_tests_count"] == 1


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Configure an Ed25519 signing key for the duration of a test."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("EXTENSION_SIGNING_PRIVATE_KEY", pem)
    monkeypatch.setenv("EXTENSION_SIGNING_KEY_ID", "test-key-1")
    get_settings.cache_clear()
    try:
        yield pem
    finally:
        get_settings.cache_clear()


async def test_the_package_is_signed_when_a_key_is_configured(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
    signing_key: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-signing")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(client, tenant.token, org_id, assistant["id"])
    await _train(client, tenant.token, org_id, extension["id"])
    await _passing_test(client, tenant.token, org_id, extension["id"])
    package = await _publish(client, tenant.token, org_id, extension["id"])

    assert package["signature"]
    assert package["signing_key_id"] == "test-key-1"
    assert package["public_key"]
    assert verify_manifest(package["manifest"], package["signature"])
    # A tampered manifest does not verify.
    tampered = dict(package["manifest"])
    tampered["name"] = "something else"
    assert not verify_manifest(tampered, package["signature"])
    assert canonical_json(package["manifest"])


async def test_sensitive_actions_need_a_confirmation(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-actions")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(client, tenant.token, org_id, assistant["id"])
    await _train(client, tenant.token, org_id, extension["id"])
    await _passing_test(client, tenant.token, org_id, extension["id"])
    await _publish(client, tenant.token, org_id, extension["id"])

    # Reading a page may run on its own.
    harmless = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}/actions",
        headers=_bearer(tenant.token),
        json={
            "action_type": "navigate",
            "description": "Open the sales screen",
            "steps": [
                {
                    "order": 1,
                    "instruction": "Open the sales screen",
                    "where": "Menu: Sales",
                    "value": None,
                    "expected_result": "The sales screen opens",
                    "sensitive": False,
                }
            ],
        },
    )
    assert harmless.status_code == 201, harmless.text
    assert harmless.json()["allowed"] is True
    assert harmless.json()["requires_confirmation"] is False

    # A financial operation always needs the user's confirmation.
    payment = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}/actions",
        headers=_bearer(tenant.token),
        json={
            "action_type": "payment",
            "description": "Collect the invoice payment",
            "steps": [
                {
                    "order": 1,
                    "instruction": "Confirm the payment",
                    "where": "Button: Pay",
                    "value": "1200",
                    "expected_result": "The payment is recorded",
                    "sensitive": True,
                }
            ],
        },
    )
    assert payment.status_code == 201, payment.text
    assert payment.json()["allowed"] is True
    assert payment.json()["requires_confirmation"] is True
    plan_id = payment.json()["plan"]["id"]

    # It cannot run before the user confirmed it.
    early = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}"
        f"/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True},
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "action_confirmation_required"

    confirmed = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}"
        f"/actions/{plan_id}/confirm",
        headers=_bearer(tenant.token),
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    finished = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}"
        f"/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True, "result": {"receipt": "R-1"}},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "executed"


async def test_a_denied_action_is_never_performed(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-denied")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        permissions={"denied_actions": ["delete_record"]},
    )
    await _train(client, tenant.token, org_id, extension["id"])
    await _passing_test(client, tenant.token, org_id, extension["id"])
    await _publish(client, tenant.token, org_id, extension["id"])

    denied = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}/actions",
        headers=_bearer(tenant.token),
        json={
            "action_type": "delete_record",
            "description": "Delete the invoice",
            "steps": [],
        },
    )
    assert denied.status_code == 201, denied.text
    assert denied.json()["allowed"] is False
    assert denied.json()["plan"]["status"] == "rejected"


async def test_the_agent_recognises_the_trained_system(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-recognise")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        url_patterns=["*://ebm.local/v3/*"],
    )
    await _train(client, tenant.token, org_id, extension["id"])
    await _passing_test(client, tenant.token, org_id, extension["id"])
    await _publish(client, tenant.token, org_id, extension["id"])

    matched = await client.get(
        f"/api/v1/organizations/{org_id}/extensions/recognize",
        headers=_bearer(tenant.token),
        params={"url": "https://ebm.local/v3/invoice/12"},
    )
    assert matched.status_code == 200, matched.text
    assert [row["id"] for row in matched.json()] == [extension["id"]]

    other = await client.get(
        f"/api/v1/organizations/{org_id}/extensions/recognize",
        headers=_bearer(tenant.token),
        params={"url": "https://irembo.gov.rw/apply"},
    )
    assert other.json() == []


async def test_guidance_is_grounded_in_the_trained_procedures(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: TrainingAIProvider,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-guidance")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(client, tenant.token, org_id, assistant["id"])
    await _train(client, tenant.token, org_id, extension["id"])
    await _passing_test(client, tenant.token, org_id, extension["id"])
    await _publish(client, tenant.token, org_id, extension["id"])

    response = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{extension['id']}/guidance",
        headers=_bearer(tenant.token),
        json={"question": "How do I issue a receipt for a walk-in customer?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["system_name"] == "EBM"
    assert body["system_version"] == "3.2"
    assert body["grounded"] is True
    assert body["procedures_used"]
    assert body["steps"]
    assert body["steps"][0]["instruction"]

    # Guidance is refused before the extension is published.
    draft = await _extension(
        client,
        tenant.token,
        org_id,
        assistant["id"],
        system_code="irembo",
        system_version="1.0",
    )
    refused = await client.post(
        f"/api/v1/organizations/{org_id}/extensions/{draft['id']}/guidance",
        headers=_bearer(tenant.token),
        json={"question": "How do I apply?"},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "extension_not_published"


async def test_extension_rows_are_protected_by_row_level_security(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "ext-rls")
    org_id = tenant.organization["id"]
    assistant = await _assistant(client, tenant.token, org_id)
    extension = await _extension(client, tenant.token, org_id, assistant["id"])

    settings = get_settings()
    async with _engine(settings.database_url) as engine:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        "SELECT set_config('app.current_org', :org, true), "
                        "set_config('app.current_user', :usr, true)"
                    ),
                    {"org": org_id, "usr": tenant.user_id},
                )
                visible = await connection.scalar(
                    text("SELECT count(*) FROM extensions")
                )
                assert visible == 1
                # The runtime role cannot delete extension rows.
                with pytest.raises(DBAPIError):
                    await connection.execute(
                        text("DELETE FROM extensions WHERE id = :id"),
                        {"id": extension["id"]},
                    )
