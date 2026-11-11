"""Signing of extension packages.

A published extension is delivered to the client as a manifest (recognition
rules, approved procedures, permissions). The manifest is signed so the
installed agent can prove that the package it runs is the one the client
published, and so the server can verify a package that was downloaded.

The signature is Ed25519 (RFC 8032) over the canonical JSON encoding of the
manifest. The public key travels with the package, so verification needs no
shared secret: the agent verifies with WebCrypto, the server verifies with
``verify_manifest``.

Signing is optional. When no private key is configured the package is still
published, but it is marked unsigned and the console says so - WorkMeta never
claims a package is signed when it is not.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.core.config import get_settings


class SigningUnavailable(Exception):
    """Raised when a signature is requested but no usable key is configured."""


def canonical_json(manifest: dict[str, Any]) -> bytes:
    """Encode a manifest so that the same content always signs identically."""
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _private_key() -> Ed25519PrivateKey | None:
    pem = (get_settings().extension_signing_private_key or "").strip()
    if not pem:
        return None
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception:  # noqa: BLE001 - a bad key must not break the service
        return None
    if isinstance(key, Ed25519PrivateKey):
        return key
    return None


def signing_key_id() -> str:
    return get_settings().extension_signing_key_id or "dev-1"


def is_signing_configured() -> bool:
    return _private_key() is not None


def sign_manifest(manifest: dict[str, Any]) -> str:
    """Return the base64 Ed25519 signature of the manifest."""
    key = _private_key()
    if key is None:
        raise SigningUnavailable(
            "No extension signing key is configured on this deployment."
        )
    return base64.b64encode(key.sign(canonical_json(manifest))).decode("ascii")


def public_key_pem() -> str | None:
    """Return the base64 SPKI public key that verifies the signatures."""
    key = _private_key()
    if key is None:
        return None
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def verify_manifest(manifest: dict[str, Any], signature: str | None) -> bool:
    """Check a manifest against its signature."""
    key = _private_key()
    if key is None or not signature:
        return False
    try:
        raw = base64.b64decode(signature, validate=True)
    except Exception:  # noqa: BLE001 - a malformed signature is simply invalid
        return False
    try:
        key.public_key().verify(raw, canonical_json(manifest))
    except InvalidSignature:
        return False
    return True


def public_key_from_pem(pem: str) -> Ed25519PublicKey:
    """Load a public key (used by the tests and by tooling)."""
    der = base64.b64decode(pem, validate=True)
    key = serialization.load_der_public_key(der)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Not an Ed25519 public key")
    return key
