"""Submodel tests against a real PostgreSQL database.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v

The tests cover the promises the feature makes:

* the **administrator** trains a submodel on demonstration material,
  approves the workflows the AI extracted, tests it and publishes it — or
  exports it as a signed package that installs on another machine;
* **clients** can only see and use what the administrator published, and
  cannot reach any administrative route, whatever they guess;
* a sensitive action the submodel proposes waits for the user's
  confirmation and is recorded with who confirmed it.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
from collections.abc import Iterator, Sequence

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import app
from app.services.ai import ChatCompletion, FakeAIProvider, get_ai_provider
from app.services.storage import LocalStorage, get_storage
from tests.test_auth_tenancy_integration import _bearer, _tenant

ADMIN = "/api/v1/platform/submodels"
CLIENT = "/api/v1/submodels"


def media_available() -> bool:
    """Whether this deployment can read a recording without a system binary."""
    from app.services.media import pyav_available
    from app.services.ocr import rapidocr_available

    return pyav_available() and rapidocr_available()

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


# ---------------------------------------------------------------------------
# Administrator helpers — every one of these calls is admin-only.
# ---------------------------------------------------------------------------


async def _create_submodel(client: AsyncClient, token: str, **overrides) -> dict:
    payload = {
        "name": "EBM receipts",
        "task": "issue a receipt for a walk-in customer",
        "system_code": "ebm",
        "system_name": "EBM",
        "system_version": "3.2",
    }
    payload.update(overrides)
    response = await client.post(
        ADMIN, headers=_bearer(token), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _add_note(client: AsyncClient, token: str, submodel_id: str) -> None:
    note = await client.post(
        f"{ADMIN}/{submodel_id}/materials/notes",
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


async def _train(client: AsyncClient, token: str, submodel_id: str) -> dict:
    """Add material and train."""
    await _add_note(client, token, submodel_id)
    trained = await client.post(
        f"{ADMIN}/{submodel_id}/train", headers=_bearer(token)
    )
    assert trained.status_code == 200, trained.text
    return trained.json()


async def _approve(client: AsyncClient, token: str, submodel_id: str) -> list[dict]:
    """Approve every extracted workflow."""
    listed = await client.get(
        f"{ADMIN}/{submodel_id}/workflows", headers=_bearer(token)
    )
    assert listed.status_code == 200, listed.text
    approved = []
    for workflow in listed.json():
        response = await client.patch(
            f"{ADMIN}/{submodel_id}/workflows/{workflow['id']}",
            headers=_bearer(token),
            json={"status": "approved"},
        )
        assert response.status_code == 200, response.text
        approved.append(response.json())
    return approved


async def _pass_test(client: AsyncClient, token: str, submodel_id: str) -> dict:
    tested = await client.post(
        f"{ADMIN}/{submodel_id}/tests",
        headers=_bearer(token),
        json={
            "question": "How do I issue a walk-in receipt?",
            "expected_points": ["Open the sales screen"],
        },
    )
    assert tested.status_code == 201, tested.text
    assert tested.json()["outcome"] == "passed", tested.text
    return tested.json()


async def _publish(client: AsyncClient, token: str, submodel_id: str) -> dict:
    response = await client.post(
        f"{ADMIN}/{submodel_id}/publish", headers=_bearer(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "published"
    assert body["published_at"]
    return body


# ---------------------------------------------------------------------------
# The whole workflow: admin trains, tests and publishes; the client uses it.
# ---------------------------------------------------------------------------


async def test_an_admin_trains_tests_publishes_and_a_client_uses_it(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-train")

    # 1. Only the administrator can create a submodel for a task.
    submodel = await _create_submodel(client, owner_token)
    assert submodel["status"] == "draft"
    assert submodel["origin"] == "trained"
    assert submodel["task"] == "issue a receipt for a walk-in customer"
    assert submodel["training_status"] == "pending"

    # 2. Train it on the demonstration material.
    trained = await _train(client, owner_token, submodel["id"])
    assert trained["training_status"] == "extracted"
    assert len(trained["workflows"]) == 1
    workflow = trained["workflows"][0]
    assert workflow["title"] == "Issue a walk-in receipt"
    assert workflow["status"] == "draft"
    assert workflow["steps"][1]["sensitive"] is True

    # 3. The workflows are drafts until a human approves them.
    listed = await client.get(
        f"{ADMIN}/{submodel['id']}/workflows", headers=_bearer(owner_token)
    )
    assert listed.status_code == 200
    assert all(item["status"] == "draft" for item in listed.json())

    # 4. The client cannot read the draft workflows: they never left the
    # administrator's side.
    client_read = await client.get(
        f"{CLIENT}/{submodel['id']}", headers=_bearer(tenant.token)
    )
    assert client_read.status_code == 404, client_read.text

    # 5. Approve them, then the submodel must pass a test before publishing.
    await _approve(client, owner_token, submodel["id"])
    too_early = await client.post(
        f"{ADMIN}/{submodel['id']}/publish", headers=_bearer(owner_token)
    )
    assert too_early.status_code == 409, too_early.text
    assert too_early.json()["error"]["code"] == "submodel_needs_a_passing_test"

    await _pass_test(client, owner_token, submodel["id"])
    published = await _publish(client, owner_token, submodel["id"])
    assert published["version"] == 1
    assert published["approved_workflows_count"] == 1
    assert published["passed_tests_count"] == 1

    # 6. Now the client sees it and can use it.
    catalog = await client.get(
        CLIENT, headers=_bearer(tenant.token)
    )
    assert catalog.status_code == 200, catalog.text
    assert [item["id"] for item in catalog.json()] == [submodel["id"]]

    answered = await client.post(
        f"{CLIENT}/{submodel['id']}/guidance",
        headers=_bearer(tenant.token),
        json={"question": "How do I issue a walk-in receipt?"},
    )
    assert answered.status_code == 200, answered.text
    assert "Open the sales screen" in answered.json()["answer"]
    assert answered.json()["workflows_used"] == ["Issue a walk-in receipt"]

    # 7. Exporting is still the administrator's job.
    package = await client.get(
        f"{ADMIN}/{submodel['id']}/package", headers=_bearer(owner_token)
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["workflow_count"] == 1
    assert body["payload"]["workflows"][0]["title"] == "Issue a walk-in receipt"

    download = await client.get(
        f"{ADMIN}/{submodel['id']}/download", headers=_bearer(owner_token)
    )
    assert download.status_code == 200, download.text
    document = json.loads(download.text)
    assert document["schema"] == "workmeta.submodel-package/1"
    assert document["signature"]
    assert document["public_key"]


async def test_clients_only_see_published_submodels(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """Unpublished means invisible: the list, the read and every use of it."""
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-pub")

    published = await _create_submodel(client, owner_token, name="Published one")
    await _train(client, owner_token, published["id"])
    await _approve(client, owner_token, published["id"])
    await _pass_test(client, owner_token, published["id"])
    await _publish(client, owner_token, published["id"])

    draft = await _create_submodel(client, owner_token, name="Draft one")

    # The administrator sees both.
    admin_list = await client.get(ADMIN, headers=_bearer(owner_token))
    assert admin_list.status_code == 200, admin_list.text
    admin_ids = {item["id"] for item in admin_list.json()}
    assert {published["id"], draft["id"]} <= admin_ids

    # The client sees only the published one.
    catalog = await client.get(CLIENT, headers=_bearer(tenant.token))
    assert catalog.status_code == 200, catalog.text
    client_ids = {item["id"] for item in catalog.json()}
    assert published["id"] in client_ids
    assert draft["id"] not in client_ids

    # Reading the draft by id is a 404, not a 403 — it does not exist for
    # a client, whether or not the id is guessed correctly.
    read = await client.get(
        f"{CLIENT}/{draft['id']}", headers=_bearer(tenant.token)
    )
    assert read.status_code == 404, read.text

    # Using a draft is refused the same way.
    guidance = await client.post(
        f"{CLIENT}/{draft['id']}/guidance",
        headers=_bearer(tenant.token),
        json={"question": "How do I do this?"},
    )
    assert guidance.status_code == 404, guidance.text

    # Unpublishing takes it away again, immediately.
    unpublished = await client.post(
        f"{ADMIN}/{published['id']}/unpublish", headers=_bearer(owner_token)
    )
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["status"] == "trained"
    catalog = await client.get(CLIENT, headers=_bearer(tenant.token))
    assert published["id"] not in {item["id"] for item in catalog.json()}

    # Publishing again raises the version, so a package downloaded earlier
    # is distinguishable from the new one.
    republished = await _publish(client, owner_token, published["id"])
    assert republished["version"] == 2


async def test_a_client_cannot_reach_any_admin_route(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    """Every administrative route refuses a client with 403 (or 401)."""
    admin_tenant = await _tenant(
        client, owner_token, test_prefix, "submodel-admin-403", role="client_admin"
    )
    member_tenant = await _tenant(
        client, owner_token, test_prefix, "submodel-member-403", role="client_member"
    )
    submodel_id = str(uuid.uuid4())
    material_id = str(uuid.uuid4())
    workflow_id = str(uuid.uuid4())

    # (method, path, json/files). Every one of these is administrator work.
    attempts: list[tuple[str, str, object]] = [
        ("GET", ADMIN, None),
        ("GET", f"{ADMIN}/capabilities", None),
        ("POST", ADMIN, {"name": "x", "task": "t", "system_code": "c",
                         "system_name": "n", "system_version": "1"}),
        ("GET", f"{ADMIN}/{submodel_id}", None),
        ("PATCH", f"{ADMIN}/{submodel_id}", {"name": "renamed"}),
        ("DELETE", f"{ADMIN}/{submodel_id}", None),
        ("POST", f"{ADMIN}/{submodel_id}/publish", None),
        ("POST", f"{ADMIN}/{submodel_id}/unpublish", None),
        ("POST", f"{ADMIN}/{submodel_id}/train", None),
        ("GET", f"{ADMIN}/{submodel_id}/materials", None),
        ("POST", f"{ADMIN}/{submodel_id}/materials/notes",
         {"title": "t", "text": "x"}),
        ("GET", f"{ADMIN}/{submodel_id}/materials/{material_id}", None),
        ("DELETE", f"{ADMIN}/{submodel_id}/materials/{material_id}", None),
        ("POST", f"{ADMIN}/{submodel_id}/materials/{material_id}/analyze", None),
        ("GET", f"{ADMIN}/{submodel_id}/workflows", None),
        ("PATCH", f"{ADMIN}/{submodel_id}/workflows/{workflow_id}",
         {"status": "approved"}),
        ("POST", f"{ADMIN}/{submodel_id}/tests",
         {"question": "q?", "expected_points": ["a"]}),
        ("GET", f"{ADMIN}/{submodel_id}/tests", None),
        ("GET", f"{ADMIN}/{submodel_id}/package", None),
        ("GET", f"{ADMIN}/{submodel_id}/download", None),
        ("GET", f"{ADMIN}/{submodel_id}/actions", None),
    ]

    for token in (admin_tenant.token, member_tenant.token):
        for method, path, payload in attempts:
            response = await client.request(
                method, path, headers=_bearer(token), json=payload
            )
            assert response.status_code == 403, (
                f"{method} {path} answered {response.status_code} "
                f"for {token[:8]}: {response.text}"
            )
            assert response.json()["error"]["code"] == "forbidden"

    # The multipart routes refuse before reading the body as well.
    for token in (admin_tenant.token, member_tenant.token):
        upload = await client.post(
            f"{ADMIN}/{submodel_id}/materials/upload",
            headers=_bearer(token),
            files={"file": ("demo.mp4", b"not-a-video", "video/mp4")},
        )
        assert upload.status_code == 403, upload.text
        imported = await client.post(
            f"{ADMIN}/import",
            headers=_bearer(token),
            files={"file": ("pkg.json", b"{}", "application/json")},
        )
        assert imported.status_code == 403, imported.text

    # Anonymous callers are refused before anything else.
    anonymous = await client.get(ADMIN)
    assert anonymous.status_code == 401, anonymous.text
    anonymous_post = await client.post(
        ADMIN, json={"name": "x", "task": "t", "system_code": "c",
                     "system_name": "n", "system_version": "1"}
    )
    assert anonymous_post.status_code == 401, anonymous_post.text

    # And the platform owner really can reach them: the same calls with the
    # administrator's token must not be refused as forbidden.
    allowed = await client.get(ADMIN, headers=_bearer(owner_token))
    assert allowed.status_code == 200, allowed.text
    capabilities = await client.get(
        f"{ADMIN}/capabilities", headers=_bearer(owner_token)
    )
    assert capabilities.status_code == 200, capabilities.text
    assert "max_media_upload_bytes" in capabilities.json()


# ---------------------------------------------------------------------------
# Installing a package on another machine
# ---------------------------------------------------------------------------


async def test_an_installed_submodel_works_on_its_own(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """The whole point: a submodel trained on one machine works on another.

    The administrator exports the package, installs it on "another machine"
    (this platform) and a client tenant uses it. The training material never
    reaches the client.
    """
    submodel = await _create_submodel(client, owner_token)
    await _train(client, owner_token, submodel["id"])
    await _approve(client, owner_token, submodel["id"])

    download = await client.get(
        f"{ADMIN}/{submodel['id']}/download", headers=_bearer(owner_token)
    )
    assert download.status_code == 200, download.text
    raw = download.content

    # The administrator installs it, as they would on the other computer.
    installed = await client.post(
        f"{ADMIN}/import",
        headers=_bearer(owner_token),
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
    tested = await _pass_test(client, owner_token, copy["id"])

    # Once published, a client tenant uses it — and the answer comes from
    # the workflows that travelled with the package.
    await _publish(client, owner_token, copy["id"])
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-dst")
    answered = await client.post(
        f"{CLIENT}/{copy['id']}/guidance",
        headers=_bearer(tenant.token),
        json={"question": "How do I issue a walk-in receipt?"},
    )
    assert answered.status_code == 200, answered.text
    assert "Open the sales screen" in answered.json()["answer"]
    assert answered.json()["workflows_used"] == ["Issue a walk-in receipt"]
    assert tested["outcome"] == "passed"


async def test_a_tampered_package_is_not_installed(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """A package that was changed after it was signed must be refused."""
    submodel = await _create_submodel(client, owner_token)
    await _train(client, owner_token, submodel["id"])
    await _approve(client, owner_token, submodel["id"])

    download = await client.get(
        f"{ADMIN}/{submodel['id']}/download", headers=_bearer(owner_token)
    )
    document = json.loads(download.text)
    document["package"]["task"] = "something else entirely"
    raw = json.dumps(document).encode("utf-8")

    refused = await client.post(
        f"{ADMIN}/import",
        headers=_bearer(owner_token),
        files={"file": ("tampered.json", raw, "application/json")},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "submodel_package_unverified"


# ---------------------------------------------------------------------------
# What a published submodel may do in a client's tenant
# ---------------------------------------------------------------------------


async def _published_for_actions(
    client: AsyncClient, owner_token: str, **overrides
) -> dict:
    submodel = await _create_submodel(client, owner_token, **overrides)
    await _train(client, owner_token, submodel["id"])
    await _approve(client, owner_token, submodel["id"])
    await _pass_test(client, owner_token, submodel["id"])
    return await _publish(client, owner_token, submodel["id"])


async def test_a_submodel_cannot_do_what_the_policy_forbids(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
) -> None:
    """The permission policy decides, and a denied action never runs."""
    tenant = await _tenant(client, owner_token, test_prefix, "submodel-policy")
    submodel = await _published_for_actions(
        client,
        owner_token,
        permissions={"denied_actions": ["payment"], "auto_actions": ["read"]},
    )

    # A denied action is refused outright.
    denied = await client.post(
        f"{CLIENT}/{submodel['id']}/actions",
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
        f"{CLIENT}/{submodel['id']}/actions/{plan_id}/complete",
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
    other = await _tenant(client, owner_token, test_prefix, "submodel-other")
    submodel = await _published_for_actions(client, owner_token)

    proposed = await client.post(
        f"{CLIENT}/{submodel['id']}/actions",
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

    # Another tenant cannot see this plan: action plans are tenant rows.
    elsewhere = await client.get(
        f"{CLIENT}/{submodel['id']}/actions", headers=_bearer(other.token)
    )
    assert elsewhere.status_code == 200, elsewhere.text
    assert elsewhere.json() == []

    # It cannot run before the user confirmed it.
    early = await client.post(
        f"{CLIENT}/{submodel['id']}/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True},
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "action_confirmation_required"

    confirmed = await client.post(
        f"{CLIENT}/{submodel['id']}/actions/{plan_id}/confirm",
        headers=_bearer(tenant.token),
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"]

    finished = await client.post(
        f"{CLIENT}/{submodel['id']}/actions/{plan_id}/complete",
        headers=_bearer(tenant.token),
        json={"succeeded": True, "result": {"receipt": "R-1"}},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "executed"
    assert finished.json()["result"] == {"receipt": "R-1"}

    # The administrator can audit the plan across tenants.
    audited = await client.get(
        f"{ADMIN}/{submodel['id']}/actions", headers=_bearer(owner_token)
    )
    assert audited.status_code == 200, audited.text
    assert {row["id"] for row in audited.json()} >= {plan_id}


# ---------------------------------------------------------------------------
# Training from a real recording
# ---------------------------------------------------------------------------


async def _await_read(
    client: AsyncClient,
    token: str,
    submodel_id: str,
    material_id: str,
) -> dict:
    """Follow a material until its background read finishes."""
    for _ in range(120):
        fetched = await client.get(
            f"{ADMIN}/{submodel_id}/materials/{material_id}",
            headers=_bearer(token),
        )
        assert fetched.status_code == 200, fetched.text
        material = fetched.json()
        if material["status"] in ("ready", "failed"):
            return material
        await asyncio.sleep(0.05)
    raise AssertionError("the recording read never finished")


def _recording() -> bytes:
    """A small recording that shows three screens of a task in a row."""
    import av
    from PIL import Image, ImageDraw

    screens = [
        "EBM Login Username Password Sign in",
        "EBM Sales New receipt Amount Save",
        "EBM Receipt Print Total 1200",
    ]
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        try:
            stream = container.add_stream("libx264", rate=2)
        except Exception:  # noqa: BLE001 - the fallback is the point
            stream = container.add_stream("mpeg4", rate=2)
        stream.width = 640
        stream.height = 240
        stream.pix_fmt = "yuv420p"
        for text in screens:
            image = Image.new("RGB", (640, 240), "white")
            ImageDraw.Draw(image).text((20, 100), text, fill="black")
            frame = av.VideoFrame.from_image(image)
            for _ in range(6):
                for packet in stream.encode(frame):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


@pytest.mark.skipif(
    not media_available(),
    reason="the media extra is not installed (pip install -e '.[media]')",
)
async def test_an_admin_trains_a_submodel_from_a_video(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
    fake_ai: SubmodelAIProvider,
    storage: LocalStorage,
    signing_key: str,
) -> None:
    """The whole promise, with a real recording.

    The administrator uploads a recording, it is read in the background into
    the screens it shows and what changed between them, and the submodel is
    trained on that timeline — not on a summary of a video.
    """
    submodel = await _create_submodel(client, owner_token)

    # 1. Upload the recording. The read starts in the background: the
    # response is the material still being read.
    recording = _recording()
    uploaded = await client.post(
        f"{ADMIN}/{submodel['id']}/materials/upload",
        headers=_bearer(owner_token),
        files={"file": ("walk-in-receipt.mp4", recording, "video/mp4")},
        data={"title": "Walk-in receipt"},
    )
    assert uploaded.status_code == 201, uploaded.text
    material = uploaded.json()
    assert material["kind"] == "video"
    assert material["status"] == "extracting"

    # 2. The read finishes on its own: follow the material until it is ready.
    material = await _await_read(
        client, owner_token, submodel["id"], material["id"]
    )
    assert material["status"] == "ready", material["error_message"]
    assert material["extracted_units"] >= 2
    timeline = material["extracted_text"]
    assert "SCREEN 1" in timeline
    assert "WHAT CHANGED BETWEEN THE SCREENS" in timeline

    # 3. Reading it again works too: the analysis is scheduled the same way.
    read = await client.post(
        f"{ADMIN}/{submodel['id']}/materials/{material['id']}/analyze",
        headers=_bearer(owner_token),
    )
    assert read.status_code == 202, read.text
    assert read.json()["status"] == "extracting"
    read_material = await _await_read(
        client, owner_token, submodel["id"], material["id"]
    )
    assert read_material["status"] == "ready", read_material["error_message"]
    assert read_material["extracted_units"] >= 2

    # 4. Train on it.
    trained = await client.post(
        f"{ADMIN}/{submodel['id']}/train", headers=_bearer(owner_token)
    )
    assert trained.status_code == 200, trained.text
    assert trained.json()["training_status"] == "extracted"
    assert len(trained.json()["workflows"]) == 1

    # 5. Approve, test, publish and export: the package carries the workflow.
    await _approve(client, owner_token, submodel["id"])
    await _pass_test(client, owner_token, submodel["id"])
    await _publish(client, owner_token, submodel["id"])
    download = await client.get(
        f"{ADMIN}/{submodel['id']}/download", headers=_bearer(owner_token)
    )
    assert download.status_code == 200, download.text
    document = json.loads(download.text)
    assert document["package"]["workflows"][0]["title"] == "Issue a walk-in receipt"
    assert document["signature"]
