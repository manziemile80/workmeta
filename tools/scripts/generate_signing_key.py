"""Create or replace the Ed25519 signing key in the repository `.env`.

Usage (from the repository root):

    py -3.12 tools/scripts/generate_signing_key.py
    py -3.12 tools/scripts/generate_signing_key.py --force   # replace an existing key

The key signs the white-label agent packages and the WorkMeta AI Submodel
packages. It is written to `EXTENSION_SIGNING_PRIVATE_KEY` in the gitignored
`.env`; the secret is never printed. The fingerprint is printed, because that
is what an administrator compares against the one the console shows.

Without a key the packages are produced **unsigned**: the console says so and
WorkMeta never claims a package is signed when it is not. A development
deployment that wants to try the download-and-install path needs one.
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / ".env"

KEY_NAME = "EXTENSION_SIGNING_PRIVATE_KEY"
KEY_ID_NAME = "EXTENSION_SIGNING_KEY_ID"


def fingerprint(public_key: Ed25519PrivateKey) -> str:
    """A short, stable identifier for the key."""
    der = public_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")[:16]


def seed_base64(key: Ed25519PrivateKey) -> str:
    """The key as one line, the way an ``.env`` file can hold it."""
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return "base64:" + base64.b64encode(seed).decode("ascii")


def replace_line(text: str, name: str, value: str) -> str:
    """Set one key in an env file, keeping every other line as it was."""
    output: list[str] = []
    replaced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0]
            if key == name:
                output.append(f"{name}={value}")
                replaced = True
                continue
        output.append(line)
    if not replaced:
        output.append(f"{name}={value}")
    return "\n".join(output).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing signing key",
    )
    args = parser.parse_args()

    if not TARGET.exists():
        print(f"{TARGET} does not exist. Run tools/scripts/generate_env.py first.")
        return 1

    original = TARGET.read_text(encoding="utf-8")
    if KEY_NAME in original and not args.force:
        for line in original.splitlines():
            if line.startswith(f"{KEY_NAME}=") and line.split("=", 1)[1].strip():
                print(f"{TARGET} already has a signing key. Use --force to replace it.")
                return 1

    key = Ed25519PrivateKey.generate()
    updated = replace_line(original, KEY_NAME, seed_base64(key))
    updated = replace_line(updated, KEY_ID_NAME, fingerprint(key))
    TARGET.write_text(updated, encoding="utf-8")

    print(f"Wrote a new Ed25519 signing key to {TARGET}.")
    print(f"  Signing key fingerprint: {fingerprint(key)}")
    print("  The private key was not printed. Never commit this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
