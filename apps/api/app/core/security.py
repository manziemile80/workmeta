"""Password hashing and opaque token helpers.

Passwords use Argon2id. Session and invitation tokens are 256-bit random
values; only their SHA-256 digest is ever written to the database.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()

# Verified when an email is unknown so login latency does not reveal whether an
# account exists.
_DUMMY_HASH = _hasher.hash("workmeta-dummy-password-not-valid")

SESSION_TOKEN_PREFIX = "wms_"  # noqa: S105 - token prefix, not a secret
INVITATION_TOKEN_PREFIX = "wmi_"  # noqa: S105 - token prefix, not a secret
CSRF_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def verify_dummy_password(password: str) -> None:
    """Burn comparable CPU time when the account does not exist."""
    verify_password(_DUMMY_HASH, password)


def generate_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def generate_invitation_token() -> str:
    return INVITATION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Keyed hash so a database dump alone is not enough to test tokens.

    Tokens are already 256-bit random values; the session secret adds a second
    barrier and gives SESSION_SECRET a real operational purpose.
    """
    key = get_settings().session_secret.encode("utf-8")
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# TOTP (RFC 4226 / 6238) for MFA — stdlib only, no new dependency.
# Secrets are server-generated base64 blobs; codes are 6 digits, 30 s step,
# ±1 step window. Replay is prevented by storing the last used step.
# ---------------------------------------------------------------------------

def generate_mfa_secret() -> str:
    import base64

    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _totp_counter(when: datetime | None = None) -> int:
    when = when or datetime.now(UTC)
    return int(when.timestamp()) // 30


def _hotp(secret_b32: str, counter: int) -> str:
    import base64
    import struct

    try:
        key = base64.b32decode(secret_b32.upper())
    except Exception:
        return ""
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (
        ((digest[offset] & 0x7F) << 24)
        | (digest[offset + 1] << 16)
        | (digest[offset + 2] << 8)
        | digest[offset + 3]
    ) % 1_000_000
    return f"{code_int:06d}"


def verify_totp(secret_b32: str, code: str, *, when: datetime | None = None) -> int | None:
    """Return the matched step or None. ``code`` must be exactly 6 digits."""
    cleaned = "".join(code.split())
    if len(cleaned) != 6 or not cleaned.isdigit():
        return None
    step = _totp_counter(when)
    for delta in (-1, 0, 1):
        candidate = step + delta
        if candidate < 0:
            continue
        if constant_time_equals(_hotp(secret_b32, candidate), cleaned):
            return candidate
    return None


def totp_uri(secret_b32: str, email: str, issuer: str = "WorkMeta AI") -> str:
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(email)}"
        f"?secret={secret_b32}&issuer={quote(issuer)}"
    )
