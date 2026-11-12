"""Submodel tests that need no database.

These cover the parts that decide what a submodel is allowed to do and what
a recording actually teaches: the permission policy, the way a recording
becomes a sequence of screens and changes, and the package that carries a
trained model to another machine.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.config import get_settings
from app.services import extension_signing
from app.services.submodel_packaging import (
    PACKAGE_SCHEMA,
    decode_package,
    verify_package_document,
)
from app.services.submodel_runtime import covers_point
from app.services.submodel_training import content_changed, parse_analysis
from app.services.submodels import build_package, decide_action, normalise_permissions
from app.services.video_analysis import (
    FrameReading,
    build_transitions,
    group_states,
    render_timeline,
    similarity,
)


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure an Ed25519 signing key for the duration of a test."""
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
# The permission policy
# ---------------------------------------------------------------------------


def test_a_safe_action_runs_without_asking() -> None:
    allowed, needs_confirmation, _ = decide_action(None, "read")
    assert allowed is True
    assert needs_confirmation is False


def test_a_sensitive_action_always_needs_a_confirmation() -> None:
    allowed, needs_confirmation, _ = decide_action(None, "payment")
    assert allowed is True
    assert needs_confirmation is True


def test_a_sensitive_step_needs_a_confirmation_even_for_a_safe_action() -> None:
    allowed, needs_confirmation, _ = decide_action(
        None, "read", has_sensitive_step=True
    )
    assert allowed is True
    assert needs_confirmation is True


def test_a_denied_action_is_refused() -> None:
    allowed, needs_confirmation, reason = decide_action(
        {"denied_actions": ["payment"]}, "payment"
    )
    assert allowed is False
    assert needs_confirmation is False
    assert "not allowed" in reason


def test_an_unknown_action_type_is_refused() -> None:
    allowed, _, reason = decide_action(None, "launch_missiles")
    assert allowed is False
    assert "not allowed" in reason


def test_the_policy_drops_unknown_actions() -> None:
    policy = normalise_permissions({"auto_actions": ["read", "fly"], "denied_actions": []})
    assert policy["auto_actions"] == ["read"]


def test_an_action_cannot_be_both_automatic_and_denied() -> None:
    policy = normalise_permissions(
        {"auto_actions": ["read"], "denied_actions": ["read"]}
    )
    assert "read" not in policy["auto_actions"]


def test_a_denied_action_is_removed_from_the_confirmation_list() -> None:
    policy = normalise_permissions(
        {"confirm_actions": ["payment"], "denied_actions": ["payment"]}
    )
    assert "payment" not in policy["confirm_actions"]


# ---------------------------------------------------------------------------
# Reading a recording
# ---------------------------------------------------------------------------


def test_screens_that_look_the_same_count_once() -> None:
    readings = [
        FrameReading(seconds=0.0, text="Login | Username | Sign in"),
        FrameReading(seconds=2.0, text="login | username | sign in"),
        FrameReading(seconds=4.0, text="Login | Username | Sign in"),
    ]
    states = group_states(readings)
    assert len(states) == 1
    assert states[0].frame_count == 3


def test_a_different_screen_is_a_new_state() -> None:
    readings = [
        FrameReading(seconds=0.0, text="Login | Username | Sign in"),
        FrameReading(seconds=2.0, text="Sales | Receipts | New receipt"),
    ]
    states = group_states(readings)
    assert len(states) == 2
    assert states[1].seconds == 2.0


def test_the_change_between_two_screens_is_recorded() -> None:
    readings = [
        FrameReading(seconds=0.0, text="Sales menu"),
        FrameReading(seconds=3.0, text="New receipt form"),
    ]
    states = group_states(readings)
    transitions = build_transitions(states)
    assert len(transitions) == 1
    assert transitions[0].before == "Sales menu"
    assert transitions[0].after == "New receipt form"
    assert transitions[0].seconds == 3.0


def test_the_timeline_shows_the_screens_in_order() -> None:
    readings = [
        FrameReading(seconds=0.0, text="Sales menu"),
        FrameReading(seconds=3.0, text="New receipt form"),
    ]
    states = group_states(readings)
    timeline = render_timeline(
        type("Analysis", (), {"states": states, "transitions": build_transitions(states)})()
    )
    assert timeline.index("SCREEN 1") < timeline.index("SCREEN 2")
    assert "WHAT CHANGED BETWEEN THE SCREENS" in timeline
    assert "before: Sales menu" in timeline


def test_screens_are_compared_by_what_they_show_not_how_they_are_written() -> None:
    assert similarity("Sales Screen", "sales   screen") == 1.0
    assert similarity("Sales Screen", "New Receipt Form") < 0.5


# ---------------------------------------------------------------------------
# Reading the model's answer
# ---------------------------------------------------------------------------


def test_the_model_answer_becomes_workflows_with_steps() -> None:
    answer = json.dumps(
        {
            "workflows": [
                {
                    "title": "Issue a walk-in receipt",
                    "summary": "Open the sales screen and print the receipt.",
                    "confidence": 0.8,
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
                }
            ]
        }
    )
    workflows = parse_analysis(answer)
    assert len(workflows) == 1
    assert workflows[0].title == "Issue a walk-in receipt"
    assert workflows[0].steps[0].where == "Menu: Sales"
    assert workflows[0].steps[0].sensitive is False


def test_a_malformed_answer_yields_no_workflows() -> None:
    assert parse_analysis("I am sorry, I could not read that.") == []
    assert parse_analysis("") == []


def test_a_sensitive_step_is_kept_sensitive() -> None:
    answer = json.dumps(
        {
            "workflows": [
                {
                    "title": "Collect a payment",
                    "steps": [
                        {
                            "order": 1,
                            "instruction": "Confirm the payment",
                            "where": "Button: Pay",
                            "value": "1200",
                            "sensitive": True,
                        }
                    ],
                }
            ]
        }
    )
    workflows = parse_analysis(answer)
    assert workflows[0].steps[0].sensitive is True


def test_a_point_is_only_covered_when_the_answer_says_it() -> None:
    assert covers_point("Open the sales screen", "Open the sales screen") is True
    assert covers_point("Open the sales screen", "open the sales screen") is True
    assert covers_point("Open the sales screen", "Print the receipt") is False


def test_an_unchanged_workflow_keeps_its_approval() -> None:
    steps = [{"order": 1, "instruction": "Open the sales screen"}]
    assert (
        content_changed("Open the sales screen", steps, "Open the sales screen", steps)
        is False
    )


def test_a_changed_workflow_loses_its_approval() -> None:
    """An approval is a promise about specific steps."""
    previous = [{"order": 1, "instruction": "Open the sales screen"}]
    changed = [{"order": 1, "instruction": "Open the sales register"}]
    assert content_changed(None, previous, None, changed) is True
    assert content_changed("old summary", previous, "new summary", previous) is True


# ---------------------------------------------------------------------------
# The package that carries a trained model
# ---------------------------------------------------------------------------


def _package_document(payload: dict, signature: str, public_key: str) -> dict:
    return {
        "schema": PACKAGE_SCHEMA,
        "package": payload,
        "signature": signature,
        "signing_key_id": "test-key-1",
        "public_key": public_key,
    }


def test_a_signed_package_verifies(signing_key: str) -> None:
    payload = {"schema": "workmeta.submodel/1", "task": "issue a receipt"}
    canonical = extension_signing.canonical_json(payload)
    document = _package_document(
        payload,
        extension_signing.sign_bytes(canonical),
        extension_signing.public_key_pem() or "",
    )

    verified = verify_package_document(document)
    assert verified.verified is True
    assert verified.reason == ""


def test_a_changed_package_is_refused(signing_key: str) -> None:
    payload = {"schema": "workmeta.submodel/1", "task": "issue a receipt"}
    canonical = extension_signing.canonical_json(payload)
    signature = extension_signing.sign_bytes(canonical)
    document = _package_document(
        payload,
        signature,
        extension_signing.public_key_pem() or "",
    )
    # Someone changes the workflow after it was signed.
    document["package"]["task"] = "issue an invoice"

    verified = verify_package_document(document)
    assert verified.verified is False
    assert "changed after it was signed" in verified.reason


def test_an_unsigned_package_is_refused(signing_key: str) -> None:
    document = _package_document(
        {"schema": "workmeta.submodel/1", "task": "issue a receipt"},
        "",
        extension_signing.public_key_pem() or "",
    )
    verified = verify_package_document(document)
    assert verified.verified is False
    assert "not signed" in verified.reason


def test_a_file_that_is_not_a_package_is_refused() -> None:
    from app.core.errors import AppError

    with pytest.raises(AppError):
        verify_package_document({"schema": "something-else"})


def test_a_package_that_is_not_json_is_refused() -> None:
    from app.core.errors import AppError

    with pytest.raises(AppError):
        decode_package(b"this is not a package")


def test_the_package_carries_the_workflows_it_was_signed_with(
    signing_key: str,
) -> None:
    payload = {
        "schema": "workmeta.submodel/1",
        "task": "issue a receipt",
        "workflows": [
            {
                "title": "Issue a walk-in receipt",
                "steps": [{"order": 1, "instruction": "Open the sales screen"}],
            }
        ],
    }
    canonical = extension_signing.canonical_json(payload)
    document = _package_document(
        payload,
        extension_signing.sign_bytes(canonical),
        extension_signing.public_key_pem() or "",
    )
    verified = verify_package_document(document)
    assert verified.verified is True
    assert verified.payload["workflows"][0]["title"] == "Issue a walk-in receipt"
    # The digest is stable, so two installs of the same package agree.
    assert len(verified.digest) == 64
    assert base64.b64decode(verified.public_key)


def test_a_package_signed_elsewhere_still_verifies(
    signing_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The machine that installs the package need not have the signing key.

    The public key travels inside the package, so a submodel trained on one
    computer installs on another one that never had the training key.
    """
    other = Ed25519PrivateKey.generate()
    other_public = base64.b64encode(
        other.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode("ascii")

    payload = {"schema": "workmeta.submodel/1", "task": "issue a receipt"}
    canonical = extension_signing.canonical_json(payload)
    signature = base64.b64encode(other.sign(canonical)).decode("ascii")

    document = _package_document(payload, signature, other_public)
    verified = verify_package_document(document)
    assert verified.verified is True


def test_a_package_signed_with_another_key_is_refused(
    signing_key: str,
) -> None:
    """The key that travels with the package is the key that must verify it."""
    other = Ed25519PrivateKey.generate()
    other_pem = base64.b64encode(
        other.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode("ascii")

    payload = {"schema": "workmeta.submodel/1", "task": "issue a receipt"}
    canonical = extension_signing.canonical_json(payload)
    # Signed by this deployment, but the package claims a different key.
    document = _package_document(
        payload, extension_signing.sign_bytes(canonical), other_pem
    )
    verified = verify_package_document(document)
    assert verified.verified is False


# ---------------------------------------------------------------------------
# The payload the package carries
# ---------------------------------------------------------------------------


def _submodel_stub():
    import uuid as _uuid

    from app.models.submodel import Submodel

    return Submodel(
        id=_uuid.uuid4(),
        org_id=_uuid.uuid4(),
        slug="ebm-issue-a-receipt",
        name="EBM receipts",
        description=None,
        task="issue a receipt for a walk-in customer",
        system_code="ebm",
        system_name="EBM",
        system_version="3.2",
        target_kind="web",
        status="trained",
        training_status="extracted",
        origin="trained",
        permissions={"auto_actions": ["read"], "denied_actions": []},
        capabilities=[],
        version=1,
    )


def test_the_package_payload_is_a_plain_document() -> None:
    """The payload is built synchronously, so it can be signed as it is.

    A payload that was a coroutine would still "work" until it was serialised,
    and then the download would fail with an opaque error.
    """
    payload = build_package(_submodel_stub(), [])
    assert isinstance(payload, dict)
    assert payload["task"] == "issue a receipt for a walk-in customer"
    assert payload["system"]["code"] == "ebm"
    assert payload["workflows"] == []
    # It has to survive the canonical JSON encoding the signature covers.
    assert extension_signing.canonical_json(payload)


# ---------------------------------------------------------------------------
# Uploading a recording
# ---------------------------------------------------------------------------


def test_only_the_media_upload_paths_get_the_larger_body_limit() -> None:
    """A recording is far larger than a document; other routes keep the cap."""
    from app.core.middleware import is_media_upload_path

    assert is_media_upload_path(
        "/api/v1/organizations/abc/submodels/def/materials/upload"
    )
    assert is_media_upload_path(
        "/api/v1/organizations/abc/extensions/def/materials/upload"
    )
    # Everything else keeps the small cap.
    assert not is_media_upload_path("/api/v1/organizations/abc/submodels/def")
    assert not is_media_upload_path(
        "/api/v1/organizations/abc/assistants/def/documents"
    )
    assert not is_media_upload_path("/api/v1/auth/login")


async def test_a_big_body_is_refused_on_an_ordinary_route(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap still protects every route that is not a media upload."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_request_body_bytes", 64)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "x" * 500},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
