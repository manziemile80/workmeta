"""Signing and package-contract tests (no database required).

These cover the parts of the trained-extension package that the installed
agent depends on: the canonical bytes the signature covers, and the ability to
sign and check the bytes of a file the packager wrote.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.config import get_settings
from app.schemas.extension import PackageRead
from app.services import extension_signing


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
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


def test_canonical_json_is_stable() -> None:
    """The same manifest always produces the same bytes."""
    manifest = {"b": 2, "a": {"d": 4, "c": 3}}
    first = extension_signing.canonical_json(manifest)
    second = extension_signing.canonical_json(manifest)
    assert first == second
    # The keys are sorted, so the bytes do not depend on insertion order.
    assert first == extension_signing.canonical_json({"a": {"c": 3, "d": 4}, "b": 2})
    assert json.loads(first.decode("utf-8")) == manifest


def test_canonical_json_keeps_non_ascii_readable() -> None:
    """Non-ASCII text stays as it is rather than being escaped."""
    raw = extension_signing.canonical_json({"title": "Akaranganya"})
    assert "Akaranganya" in raw.decode("utf-8")


def test_a_manifest_signature_verifies(signing_key: str) -> None:
    manifest = {"schema": "workmeta.extension/1", "version": 1}
    signature = extension_signing.sign_manifest(manifest)
    assert extension_signing.verify_manifest(manifest, signature)


def test_a_tampered_manifest_does_not_verify(signing_key: str) -> None:
    manifest = {"schema": "workmeta.extension/1", "version": 1}
    signature = extension_signing.sign_manifest(manifest)
    tampered = dict(manifest)
    tampered["version"] = 2
    assert not extension_signing.verify_manifest(tampered, signature)


def test_a_malformed_signature_is_simply_invalid(signing_key: str) -> None:
    assert not extension_signing.verify_manifest({"a": 1}, "not base64!")
    assert not extension_signing.verify_manifest({"a": 1}, None)


def test_signed_bytes_round_trip(signing_key: str) -> None:
    """The bytes of a packaged file are signed and checked as they are."""
    raw = b'{\n  "apiBaseUrl": "https://api.example.com/api/v1"\n}\n'
    signature = extension_signing.sign_bytes(raw)
    assert extension_signing.verify_bytes(raw, signature)
    assert not extension_signing.verify_bytes(raw + b" ", signature)
    assert not extension_signing.verify_bytes(b"other", signature)


def test_the_public_key_is_the_spki_form_the_browser_needs(
    signing_key: str,
) -> None:
    """The browser imports the key as SPKI, so the API returns exactly that."""
    public_key = extension_signing.public_key_pem()
    assert public_key
    der = base64.b64decode(public_key)
    # An Ed25519 SPKI key is 44 bytes: a 12-byte prefix and the 32-byte key.
    assert len(der) == 44


def test_the_package_carries_the_bytes_its_signature_covers() -> None:
    """The response gives the agent the exact bytes to verify."""
    extension_id = uuid.uuid4()
    manifest = {
        "schema": "workmeta.extension/1",
        "extension_id": str(extension_id),
        "version": 1,
    }
    package = PackageRead(
        extension_id=extension_id,
        slug="ebm-3-2",
        version=1,
        manifest=manifest,
        signature=None,
        signing_key_id=None,
        public_key=None,
        published_at=None,
        canonical_manifest=extension_signing.canonical_json(manifest).decode(
            "utf-8"
        ),
    )
    assert package.canonical_manifest is not None
    assert json.loads(package.canonical_manifest) == manifest
