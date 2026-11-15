"""Create a local `.env` from `.env.example` with strong generated secrets.

Usage (from the repository root):
    py -3.12 tools/scripts/generate_env.py
    py -3.12 tools/scripts/generate_env.py --force   # replace an existing .env

The file is written to the repository root and is gitignored. Secrets are never
printed; the owner credentials are available inside `.env` for first login.
"""

from __future__ import annotations

import argparse
import base64
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".env.example"
TARGET = ROOT / ".env"

# One signing key is generated for the whole file, so the agent packages and
# the submodel packages are signed with the same key.
_SIGNING_KEY = Ed25519PrivateKey.generate()


def signing_seed() -> str:
    """The signing key as one line, the way an ``.env`` file can hold it."""
    seed = _SIGNING_KEY.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return "base64:" + base64.b64encode(seed).decode("ascii")


def signing_fingerprint() -> str:
    """A short, stable identifier for the signing key."""
    der = _SIGNING_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")[:16]


def parse_template(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key] = value
    return values


def render(values: dict[str, str], original: str) -> str:
    """Keep comments and ordering from the template, replace values."""
    output: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0]
            output.append(f"{key}={values.get(key, '')}")
        else:
            output.append(line)
    return "\n".join(output).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"{TARGET} already exists. Use --force to replace it.")
        return 1

    original = TEMPLATE.read_text(encoding="utf-8")
    values = parse_template(original)

    owner_password = secrets.token_hex(24)
    app_password = secrets.token_hex(24)

    values.update(
        {
            "POSTGRES_PASSWORD": owner_password,
            "APP_DB_PASSWORD": app_password,
            "DATABASE_URL": (
                f"postgresql+asyncpg://{values['APP_DB_USER']}:{app_password}"
                f"@127.0.0.1:5432/{values['POSTGRES_DB']}"
            ),
            "DATABASE_MIGRATION_URL": (
                f"postgresql+asyncpg://{values['POSTGRES_USER']}:{owner_password}"
                f"@127.0.0.1:5432/{values['POSTGRES_DB']}"
            ),
            "SESSION_SECRET": secrets.token_urlsafe(48),
            "SEED_OWNER_EMAIL": values.get("SEED_OWNER_EMAIL") or "owner@workmeta.ai",
            "SEED_OWNER_PASSWORD": secrets.token_urlsafe(18),
            # A signing key, so the white-label agent package and the
            # WorkMeta AI Submodel packages can be signed in development.
            "EXTENSION_SIGNING_PRIVATE_KEY": signing_seed(),
            "EXTENSION_SIGNING_KEY_ID": signing_fingerprint(),
        }
    )

    TARGET.write_text(render(values, original), encoding="utf-8")
    print(f"Created {TARGET} with generated secrets.")
    print("The platform owner email/password is stored in SEED_OWNER_EMAIL / SEED_OWNER_PASSWORD.")
    print(f"Signing key fingerprint: {values['EXTENSION_SIGNING_KEY_ID']}")
    print("Never commit this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
