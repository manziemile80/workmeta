"""Submodel packages: exporting a trained model and installing it elsewhere.

A submodel is **portable**. The package it publishes carries the workflows
it learned, the permission policy it runs under and the task it is for, so a
copy installed on another machine works on its own: it does not need the
training material, the tenant's documents or the machine that trained it.

The package is a single JSON file. It is signed with Ed25519 over its
canonical form, so the machine that installs it can check that nobody
changed the workflows between the training machine and this one.

Two properties are deliberate:

* the **signature covers the workflows**, not just a description of them, so
  a tampered workflow is detected;
* the **key id travels with the package**, so an administrator can see
  which key signed it and refuse a package signed by a key they do not
  trust.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.enums import SubmodelOrigin, SubmodelStatus
from app.models.submodel import Submodel, SubmodelProcedure
from app.services import extension_signing
from app.services.submodels import (
    approved_procedures,
    build_package,
    normalise_permissions,
    unique_slug,
)

PACKAGE_SCHEMA = "workmeta.submodel-package/1"


@dataclass(slots=True)
class ExportablePackage:
    """A submodel package, ready to be written to a file."""

    filename: str
    payload: dict[str, Any]
    #: The canonical bytes that are signed.
    canonical: bytes
    signature: str
    signing_key_id: str
    public_key: str
    digest: str

    def to_json(self) -> str:
        """The package as it is downloaded."""
        return json.dumps(
            {
                "schema": PACKAGE_SCHEMA,
                "package": self.payload,
                "signature": self.signature,
                "signing_key_id": self.signing_key_id,
                "public_key": self.public_key,
            },
            indent=2,
            ensure_ascii=False,
        )


def _slugify(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "-" for character in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "submodel"


async def build_package_document(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    submodel: Submodel,
) -> ExportablePackage:
    """Build the package for a submodel.

    Only **approved** workflows are exported. A workflow nobody approved is
    not something the client vouches for, so it does not travel.
    """
    workflows = await approved_procedures(
        session, org_id=org_id, submodel_id=submodel.id
    )
    if not workflows:
        raise AppError(
            "Approve at least one workflow before exporting this submodel.",
            status_code=409,
            code="submodel_no_workflows",
        )

    payload = build_package(submodel, workflows)
    canonical = extension_signing.canonical_json(payload)

    if not extension_signing.is_signing_configured():
        raise AppError(
            "Exporting a submodel needs a signing key on this deployment. "
            "Set EXTENSION_SIGNING_PRIVATE_KEY.",
            status_code=503,
            code="submodel_signing_unavailable",
        )

    signature = extension_signing.sign_bytes(canonical)
    public_key = extension_signing.public_key_pem() or ""
    return ExportablePackage(
        filename=f"{submodel.slug}-v{submodel.version}.wmsubmodel.json",
        payload=payload,
        canonical=canonical,
        signature=signature,
        signing_key_id=extension_signing.signing_key_id(),
        public_key=public_key,
        digest=hashlib.sha256(canonical).hexdigest(),
    )


@dataclass(slots=True)
class VerifiedPackage:
    """A package that was checked and is safe to install."""

    payload: dict[str, Any]
    signature: str
    signing_key_id: str
    public_key: str
    digest: str
    verified: bool
    reason: str


def verify_package_document(document: dict[str, Any]) -> VerifiedPackage:
    """Check a package that arrived from somewhere else.

    The signature is checked against the public key that travels *with* the
    package, so a package trained on another machine verifies here even though
    this deployment never had the training machine's private key.

    That proves the workflows were not changed on the way; it does not prove
    *who* signed them, so the key id is reported and the caller decides
    whether it trusts it.
    """
    schema = document.get("schema")
    if schema != PACKAGE_SCHEMA:
        raise AppError(
            "This file is not a WorkMeta submodel package.",
            status_code=400,
            code="submodel_package_invalid",
        )
    payload = document.get("package")
    signature = document.get("signature")
    public_key = document.get("public_key")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise AppError(
            "This submodel package is incomplete.",
            status_code=400,
            code="submodel_package_invalid",
        )

    canonical = extension_signing.canonical_json(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    verified = False
    reason = ""
    if not signature:
        reason = (
            "This package is not signed, so it cannot be verified. "
            "Only install packages from a source you trust."
        )
    elif not isinstance(public_key, str) or not public_key:
        reason = "The package carries no signing key, so it cannot be verified."
    elif extension_signing.verify_bytes_with_public_key(
        canonical, signature, public_key
    ):
        verified = True
    else:
        reason = (
            "The package signature does not match its contents, so it was "
            "changed after it was signed."
        )

    return VerifiedPackage(
        payload=payload,
        signature=signature,
        signing_key_id=str(document.get("signing_key_id") or ""),
        public_key=public_key if isinstance(public_key, str) else "",
        digest=digest,
        verified=verified,
        reason=reason,
    )


async def install_submodel(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    verified: VerifiedPackage,
    created_by: uuid.UUID | None = None,
    source_label: str | None = None,
) -> Submodel:
    """Install a package into this organization.

    The workflows travel with the package, so the installed copy is usable
    immediately: it does not need the training material again.

    ``source_label`` is what the installer says about where the package came
    from. It is kept so an administrator can see the provenance later.
    """
    payload = verified.payload
    name = str(payload.get("name") or "Imported submodel").strip()
    task = str(payload.get("task") or "").strip()
    if not task:
        raise AppError(
            "This submodel package does not say what it is for.",
            status_code=400,
            code="submodel_package_invalid",
        )
    system = payload.get("system") or {}
    if not isinstance(system, dict):
        system = {}

    slug = await unique_slug(
        session, org_id=org_id, base=_slugify(str(payload.get("slug") or name))
    )
    label = (source_label or "").strip() or str(
        payload.get("source_label") or ""
    ).strip()
    submodel = Submodel(
        org_id=org_id,
        slug=slug,
        name=name,
        description=str(payload.get("description") or "").strip() or None,
        task=task,
        system_code=str(system.get("code") or "unknown").strip().lower(),
        system_name=str(system.get("name") or "Unknown system").strip(),
        system_version=str(system.get("version") or "1").strip(),
        target_kind=(
            str(system.get("target_kind") or "web")
            if str(system.get("target_kind") or "web") in ("web", "desktop")
            else "web"
        ),
        status=SubmodelStatus.trained.value,
        training_status="extracted",
        origin=SubmodelOrigin.imported.value,
        source_label=label or None,
        source_digest=verified.digest,
        permissions=normalise_permissions(payload.get("permissions")),
        capabilities=list(payload.get("capabilities") or []),
        version=int(payload.get("version") or 1),
        package_manifest=payload,
        package_signature=verified.signature,
        signing_key_id=verified.signing_key_id or None,
        created_by=created_by,
    )
    session.add(submodel)
    await session.flush()

    # The workflows arrive with the package and are approved: they were
    # approved before the package was signed.
    workflows = payload.get("workflows")
    if not isinstance(workflows, list):
        workflows = []
    for position, workflow in enumerate(workflows, start=1):
        if not isinstance(workflow, dict):
            continue
        title = str(workflow.get("title") or "").strip()
        if not title:
            continue
        steps = workflow.get("steps")
        session.add(
            SubmodelProcedure(
                org_id=org_id,
                submodel_id=submodel.id,
                title=title,
                summary=str(workflow.get("summary") or "").strip() or None,
                steps=list(steps) if isinstance(steps, list) else [],
                source_material_ids=[],
                confidence=float(workflow.get("confidence") or 0.0),
                status="approved",
                position=position,
            )
        )

    await session.flush()
    await session.refresh(submodel)
    return submodel


async def count_installed(session: AsyncSession, *, org_id: uuid.UUID) -> int:
    """How many submodels this organization has."""
    return int(
        await session.scalar(
            select(func.count(Submodel.id)).where(Submodel.org_id == org_id)
        )
        or 0
    )


def package_digest(payload: dict[str, Any]) -> str:
    """The digest of a package's canonical form."""
    return hashlib.sha256(extension_signing.canonical_json(payload)).hexdigest()


def decode_package(raw: bytes) -> dict[str, Any]:
    """Read a package that was uploaded."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(
            "This file is not a readable submodel package.",
            status_code=400,
            code="submodel_package_invalid",
        ) from exc
    if not isinstance(document, dict):
        raise AppError(
            "This file is not a readable submodel package.",
            status_code=400,
            code="submodel_package_invalid",
        )
    return document


def encode_public_key(public_key: str) -> str:
    """The public key in the form a package carries it."""
    if not public_key:
        return ""
    try:
        base64.b64decode(public_key, validate=True)
    except Exception:  # noqa: BLE001 - a malformed key is simply not a key
        return ""
    return public_key
