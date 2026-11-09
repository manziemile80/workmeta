"""Password and token helper unit tests (no database required)."""

from __future__ import annotations

from app.core.security import (
    generate_csrf_token,
    generate_invitation_token,
    generate_session_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    digest = hash_password("a-strong-test-password")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "a-strong-test-password")
    assert not verify_password(digest, "the-wrong-password")
    assert not password_needs_rehash(digest)


def test_invalid_hash_never_raises() -> None:
    assert not verify_password("not-a-hash", "anything")
    assert password_needs_rehash("not-a-hash")


def test_dummy_verification_is_cheap_but_does_not_raise() -> None:
    verify_dummy_password("irrelevant")


def test_session_tokens_are_random_and_prefixed() -> None:
    first = generate_session_token()
    second = generate_session_token()
    assert first.startswith("wms_")
    assert second.startswith("wms_")
    assert first != second


def test_invitation_and_csrf_tokens_are_random() -> None:
    assert generate_invitation_token().startswith("wmi_")
    assert generate_csrf_token() != generate_csrf_token()


def test_token_hash_is_stable_and_not_the_token() -> None:
    token = generate_session_token()
    digest = hash_token(token)
    assert digest == hash_token(token)
    assert digest != token
    assert len(digest) == 64
