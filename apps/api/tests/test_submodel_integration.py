"""Submodel tests against a real PostgreSQL database.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v

The tests cover the promises the feature makes: a client trains a submodel on
its own material, approves the workflows it found, tests it, exports it as a
signed package, and installs that package in another organization — where it
works on its own, without the training material and without talking to the
machine that trained it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import app
from app.services.ai import ChatCompletion, FakeAIProvider, get_ai_provider
from app.services.storage import LocalStorage, get_storage
from tests.test_auth_tenancy_integration import _bearer, _tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

WORKFLOWS_JSON = {
    "workflows": [
        {
            "title": "Issue a walk-in receipt",
            "summary": "Open the sales screen and print the receipt.",
            "confidence": 0.85,
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
    "To issue a walk-in receipt [1]:\n"
    "1. Open the sales screen.\n"
    "2. Print the receipt."
)


class SubmodelAIProvider(FakeAIProvider):
    """Answers the training prompt with workflows and the guidance with steps."""

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
            content = json.dumps(WORKFLOWS_JSON)
        else:
            content = GUIDANCE_ANSWER
        return ChatCompletion(
            content=content,
            model=model or self.model,
            prompt_tokens=500,
            completion_tokens=200,
        )


@pytest.fixture
def fake_ai() -> Iterator[SubmodelAIProvider]:
    provider = SubmodelAIProvider(get_settings().embedding_dimensions)
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


async def _create_submodel(
    client: AsyncClient, token: str, org_id: str, **overrides
) -> dict:
    payload = {
        "name": "EBM receipts",
        "task": "issue a receipt for a walk-in customer",
        "system_code": "ebm",
        "system_name": "EBM",
        "system_version": "3.2",
    }
    payload.update(overrides)
    response = await client.post(
        f"/api/v1/organizations/{org_id}/submodels",
        headers=_bearer(token),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _train(
    client: AsyncClient, token: str, org_id: str, submodel_id: str
) -> dict:
    """Add material and train."""
    note = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel_id}/materials/notes",
        headers=_bearer(token),
        json={
            "title": "Walk-in receipt notes",
            "text": (
                "Open the sales screen, choose Walk-in customer and print the "
                "receipt."
            ),
        },
    )
    assert note.status_code == 201, note.text

    trained = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel_id}/train",
        headers=_bearer(token),
    )
    assert trained.status_code == 200, trained.text
    return trained.json()


async def _approve(
    client: AsyncClient, token: str, org_id: str, submodel_id: str
) -> list[dict]:
    """Approve every extracted workflow."""
    listed = await client.get(
        f"/api/v1/organizations/{org_id}/submodels/{submodel_id}/workflows",
        headers=_bearer(token),
    )
    assert listed.status_code == 200, listed.text
    approved = []
    for workflow in listed.json():
        response = await client.patch(
            f"/api/v1/organizations/{org_id}/submodels/{submodel_id}"
            f"/workflows/{workflow['id']}",
            headers=_bearer(token),
            json={"status": "approved"},
        )
        assert response.status_code == 200, response.text
        approved.append(response.json())
    return approved


async def test_a_client_trains_tests_and_exports_a_submodel(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-train")
    org_id = tenant.organization["id"]

    # 1. Create the submodel for a task.
    submodel = await _create_submodel(client, tenant.token, org_id)
    assert submodel["status"] == "draft"
    assert submodel["origin"] == "trained"
    assert submodel["task"] == "issue a receipt for a walk-in customer"

    # 2. Train it on the client's own material.
    trained = await _train(client, tenant.token, org_id, submodel["id"])
    assert trained["training_status"] == "extracted"
    assert len(trained["workflows"]) == 1
    workflow = trained["workflows"][0]
    assert workflow["title"] == "Issue a walk-in receipt"
    assert workflow["status"] == "draft"
    assert workflow["steps"][1]["sensitive"] is True

    # 3. The workflows are drafts until a human approves them.
    listed = await client.get(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/workflows",
        headers=_bearer(tenant.token),
    )
    assert listed.status_code == 200
    assert all(item["status"] == "draft" for item in listed.json())

    # 4. Approve them.
    await _approve(client, tenant.token, org_id, submodel["id"])

    # 5. Test the submodel: it must answer from the approved workflow.
    tested = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/tests",
        headers=_bearer(tenant.token),
        json={
            "question": "How do I issue a walk-in receipt?",
            "expected_points": ["Open the sales screen"],
        },
    )
    assert tested.status_code == 201, tested.text
    assert tested.json()["outcome"] == "passed"

    # 6. Export the package. It carries the approved workflows.
    package = await client.get(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/package",
        headers=_bearer(tenant.token),
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["workflow_count"] == 1
    assert body["payload"]["workflows"][0]["title"] == "Issue a walk-in receipt"

    # 7. Download it as a file.
    download = await client.get(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/download",
        headers=_bearer(tenant.token),
    )
    assert download.status_code == 200, download.text
    document = json.loads(download.text)
    assert document["schema"] == "workmeta.submodel-package/1"
    assert document["signature"]
    assert document["public_key"]


async def test_an_installed_submodel_works_on_its_own(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """The whole point: a submodel trained on one machine works on another.

    The second organization installs the package and can ask the submodel
    questions immediately. The training material never reaches it.
    """
    trainer = await _tenant(client, owner_token, test_prefix, "submodel-src")
    trainer_org = trainer.organization["id"]
    submodel = await _create_submodel(client, trainer.token, trainer_org)
    await _train(client, trainer.token, trainer_org, submodel["id"])
    await _approve(client, trainer.token, trainer_org, submodel["id"])

    download = await client.get(
        f"/api/v1/organizations/{trainer_org}/submodels/{submodel['id']}/download",
        headers=_bearer(trainer.token),
    )
    assert download.status_code == 200, download.text
    raw = download.content

    # A different client installs it.
    installer = await _tenant(client, owner_token, test_prefix, "submodel-dst")
    installer_org = installer.organization["id"]

    installed = await client.post(
        f"/api/v1/organizations/{installer_org}/submodels/import",
        headers=_bearer(installer.token),
        files={"file": ("ebm-receipts.wmsubmodel.json", raw, "application/json")},
        data={"label": "Trained by Example Rwanda Ltd"},
    )
    assert installed.status_code == 201, installed.text
    copy = installed.json()
    assert copy["origin"] == "imported"
    assert copy["task"] == "issue a receipt for a walk-in customer"
    assert copy["approved_workflows_count"] == 1
    assert copy["materials_count"] == 0
    # The provenance is kept, so an administrator can see where it came from.
    assert copy["source_label"] == "Trained by Example Rwanda Ltd"
    assert copy["source_digest"]

    # The installed copy answers right away: the workflows came with it.
    answered = await client.post(
        f"/api/v1/organizations/{installer_org}/submodels/{copy['id']}/guidance",
        headers=_bearer(installer.token),
        json={"question": "How do I issue a walk-in receipt?"},
    )
    assert answered.status_code == 200, answered.text
    assert "Open the sales screen" in answered.json()["answer"]
    assert answered.json()["workflows_used"] == ["Issue a walk-in receipt"]

    # And it can be tested on the new machine as well.
    tested = await client.post(
        f"/api/v1/organizations/{installer_org}/submodels/{copy['id']}/tests",
        headers=_bearer(installer.token),
        json={
            "question": "How do I issue a walk-in receipt?",
            "expected_points": ["Open the sales screen"],
        },
    )
    assert tested.status_code == 201, tested.text
    assert tested.json()["outcome"] == "passed"


async def test_a_tampered_package_is_not_installed(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """A package that was changed after it was signed must be refused."""
    trainer = await _tenant(client, owner_token, test_prefix, "submodel-tamper")
    trainer_org = trainer.organization["id"]
    submodel = await _create_submodel(client, trainer.token, trainer_org)
    await _train(client, trainer.token, trainer_org, submodel["id"])
    await _approve(client, trainer.token, trainer_org, submodel["id"])

    download = await client.get(
        f"/api/v1/organizations/{trainer_org}/submodels/{submodel['id']}/download",
        headers=_bearer(trainer.token),
    )
    document = json.loads(download.text)
    document["package"]["task"] = "something else entirely"
    raw = json.dumps(document).encode("utf-8")

    installer = await _tenant(client, owner_token, test_prefix, "submodel-tamper-dst")
    installer_org = installer.organization["id"]
    refused = await client.post(
        f"/api/v1/organizations/{installer_org}/submodels/import",
        headers=_bearer(installer.token),
        files={"file": ("tampered.json", raw, "application/json")},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "submodel_package_unverified"


async def test_a_submodel_cannot_do_what_the_policy_forbids(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
) -> None:
    """The permission policy decides, and a denied action never runs."""
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-policy")
    org_id = tenant.organization["id"]
    submodel = await _create_submodel(
        client,
        tenant.token,
        org_id,
        permissions={"denied_actions": ["payment"], "auto_actions": ["read"]},
    )
    await _train(client, tenant.token, org_id, submodel["id"])
    await _approve(client, tenant.token, org_id, submodel["id"])

    # A denied action is refused outright.
    denied = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/actions",
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
    assert denied.status_code == 201, denied.text
    assert denied.json()["allowed"] is False
    assert denied.json()["requires_confirmation"] is False
    plan_id = denied.json()["plan"]["id"]
    # A refused action is recorded as refused, so it can never be reported
    # as performed.
    assert denied.json()["plan"]["status"] == "rejected"

    # It cannot be completed either, because it was never allowed.
    completed = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}"
        f"/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True},
    )
    assert completed.status_code == 409, completed.text
    assert completed.json()["error"]["code"] == "action_not_allowed"


async def test_a_sensitive_action_needs_the_users_confirmation(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
) -> None:
    """A sensitive action waits for the user, and the answer is recorded."""
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-confirm")
    org_id = tenant.organization["id"]
    submodel = await _create_submodel(client, tenant.token, org_id)
    await _train(client, tenant.token, org_id, submodel["id"])
    await _approve(client, tenant.token, org_id, submodel["id"])

    proposed = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}/actions",
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
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["allowed"] is True
    assert proposed.json()["requires_confirmation"] is True
    plan_id = proposed.json()["plan"]["id"]

    # It cannot run before the user confirmed it.
    early = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}"
        f"/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True},
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "action_confirmation_required"

    confirmed = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}"
        f"/actions/{plan_id}/confirm",
        headers=_bearer(tenant.token),
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"]

    finished = await client.post(
        f"/api/v1/organizations/{org_id}/submodels/{submodel['id']}"
        f"/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True, "result": {"receipt": "R-1"}},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "executed"
    assert finished.json()["result"] == {"receipt": "R-1"}


async def test_one_client_cannot_see_another_clients_submodels(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
) -> None:
    """A submodel belongs to one organization."""
    first = await _tenant(client, owner_token, test_prefix, "submodel-iso-a")
    second = await _tenant(client, owner_token, test_prefix, "submodel-iso-b")
    submodel = await _create_submodel(client, first.token, first.organization["id"])

    # The other client cannot list it.
    listed = await client.get(
        f"/api/v1/organizations/{second.organization['id']}/submodels",
        headers=_bearer(second.token),
    )
    assert listed.status_code == 200, listed.text
    assert all(
        item["id"] != submodel["id"] for item in listed.json()
    )

    # And it cannot read it by id.
    read = await client.get(
        f"/api/v1/organizations/{second.organization['id']}"
        f"/submodels/{submodel['id']}",
        headers=_bearer(second.token),
    )
    assert read.status_code == 404, read.text

    # Nor train it.
    trained = await client.post(
        f"/api/v1/organizations/{second.organization['id']}"
        f"/submodels/{submodel['id']}/train",
        headers=_bearer(second.token),
    )
    assert trained.status_code == 404, trained.text
