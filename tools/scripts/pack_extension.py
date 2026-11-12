"""Package the agent for one client.

There is **one** agent codebase. A client gets the same code with its own
name, its own API address, its own trained domains and its own signed
configuration. Nothing else changes, and no secret ever goes into the
package.

The packager:

* copies the built agent into a staging folder;
* rewrites ``manifest.json`` with the client's name, theme and domains;
* writes ``config.json`` — the API address, the organization the agent acts
  for and the domains it may read;
* signs ``config.json`` with the deployment's Ed25519 key, so the agent can
  check that the package it was given is the one the platform produced;
* writes the ZIP the client distributes.

Usage (from the repository root)::

    apps/api/.venv/Scripts/python tools/scripts/pack_extension.py \\
        --client-name "Example Rwanda Ltd" \\
        --api-origin https://api.example.com \\
        --org-id 8f0c... \\
        --domain ebm.example.com \\
        --out var/packages/example-rw.zip

The signing key comes from ``EXTENSION_SIGNING_PRIVATE_KEY`` in the
repository ``.env``. Without one the package is produced **unsigned** and the
agent says so; it never pretends an unsigned package is signed.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.services import extension_signing  # noqa: E402

EXTENSION_DIR = ROOT / "apps" / "extension"
DEFAULT_ICON = EXTENSION_DIR / "public" / "icons" / "icon-128.png"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-name", required=True)
    parser.add_argument(
        "--api-origin",
        required=True,
        help="The API the agent talks to, for example https://api.example.com",
    )
    parser.add_argument(
        "--org-id",
        required=True,
        help="The organization the agent acts for.",
    )
    parser.add_argument(
        "--domain",
        action="append",
        default=[],
        help="A domain the agent may read. Repeat for more than one.",
    )
    parser.add_argument("--description", default="")
    parser.add_argument("--theme-color", default="#0b1220")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--icon", default=str(DEFAULT_ICON))
    parser.add_argument(
        "--out",
        default="",
        help="Where to write the ZIP. Defaults to var/packages/<client>.zip",
    )
    return parser.parse_args()


def _slug(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "-" for character in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "client"


def _stage(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise SystemExit(
            f"The agent has not been built yet. Run `npm run build:extension` "
            f"first (looked in {source})."
        )
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _rewrite_manifest(
    manifest_path: Path,
    *,
    client_name: str,
    api_origin: str,
    domains: list[str],
    description: str,
    theme_color: str,
    version: str,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    origin = api_origin.rstrip("/")
    patterns = [f"https://{domain}/*" for domain in domains]

    manifest["name"] = f"{client_name} Assistant"
    manifest["short_name"] = client_name[:24]
    manifest["version"] = version
    manifest["description"] = description or (
        "Guidance for the systems your organisation trained."
    )
    manifest["theme_color"] = theme_color
    manifest["host_permissions"] = [f"{origin}/*"]
    manifest["action"]["default_title"] = f"Ask {client_name}"
    manifest["content_scripts"] = [
        {
            "matches": patterns,
            "js": ["content.js"],
            "run_at": "document_idle",
        }
    ]
    manifest_path.write_text(
        f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8"
    )
    return manifest


def _write_config(
    target: Path,
    *,
    api_origin: str,
    org_id: str,
    domains: list[str],
    client_name: str,
) -> bytes:
    config = {
        "apiBaseUrl": f"{api_origin.rstrip('/')}/api/v1",
        "orgId": org_id,
        "allowedDomains": domains,
        "clientName": client_name,
        # The key the platform signs packages with. The agent checks a package
        # against this key, not against the key the package carries, so a
        # package cannot introduce its own signer.
        "signingKeyId": extension_signing.signing_key_id(),
    }
    raw = f"{json.dumps(config, indent=2)}\n".encode("utf-8")
    (target / "config.json").write_bytes(raw)
    return raw


def _sign_config(target: Path, raw: bytes) -> bool:
    """Sign the configuration file. Returns whether it was signed."""
    if not extension_signing.is_signing_configured():
        (target / "config.sig").unlink(missing_ok=True)
        (target / "config.pub").unlink(missing_ok=True)
        return False
    signature = extension_signing.sign_bytes(raw)
    (target / "config.sig").write_text(signature, encoding="utf-8")
    public_key = extension_signing.public_key_pem()
    if public_key:
        (target / "config.pub").write_text(public_key, encoding="utf-8")
    return True


def _write_zip(stage: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())


def main() -> int:
    args = _parse_args()
    settings = get_settings()

    if not settings.extension_signing_private_key.strip():
        print(
            "No extension signing key is configured, so the package will be "
            "unsigned. Set EXTENSION_SIGNING_PRIVATE_KEY to sign it.",
            file=sys.stderr,
        )

    slug = _slug(args.client_name)
    stage = ROOT / "var" / "packaging" / slug
    out = (
        Path(args.out)
        if args.out
        else ROOT / "var" / "packages" / f"{slug}-{args.version}.zip"
    )

    _stage(EXTENSION_DIR / "dist", stage)
    _rewrite_manifest(
        stage / "manifest.json",
        client_name=args.client_name,
        api_origin=args.api_origin,
        domains=args.domain,
        description=args.description,
        theme_color=args.theme_color,
        version=args.version,
    )
    raw = _write_config(
        stage,
        api_origin=args.api_origin,
        org_id=args.org_id,
        domains=args.domain,
        client_name=args.client_name,
    )
    signed = _sign_config(stage, raw)

    icon = Path(args.icon)
    if icon.is_file():
        icons = stage / "icons"
        icons.mkdir(parents=True, exist_ok=True)
        for size in (16, 32, 48, 128):
            source = EXTENSION_DIR / "public" / "icons" / f"icon-{size}.png"
            if source.is_file():
                shutil.copyfile(source, icons / f"icon-{size}.png")

    _write_zip(stage, out)

    print(f"Packaged the agent for {args.client_name}.")
    print(f"  Domains it may read: {', '.join(args.domain) or 'none'}")
    print(f"  Configuration signed: {'yes' if signed else 'no'}")
    print(f"  Package: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    if signed:
        key = extension_signing.public_key_pem() or ""
        digest = base64.b64decode(key)[:8].hex() if key else ""
        print(f"  Signing key fingerprint: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
