"""Extension services: lifecycle, permissions, recognition and publishing.

An extension is trained for one target system at one version and belongs to
exactly one client organization. The service layer owns the rules that make
that isolation real:

* an extension is only ever read inside its own organization;
* an extension may only be published once it has been trained, has at least
  one approved procedure and has passed at least one test;
* the agent may only perform actions the client's permission policy allows,
  and sensitive actions always need the user's confirmation.
"""

from __future__ import annotations

import fnmatch
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.enums import (
    ExtensionStatus,
    ExtensionTestOutcome,
    ProcedureStatus,
)
from app.models.extension import (
    Extension,
    ExtensionActionPlan,
    ExtensionProcedure,
    ExtensionTest,
    TrainingMaterial,
)
from app.services import extension_signing

_SLUG_CHARS = re.compile(r"[^a-z0-9]+")

# Action types the agent may perform. Anything else is refused unless the
# client's own policy names it explicitly.
KNOWN_ACTION_TYPES = frozenset(
    {
        "navigate",
        "read",
        "fill_form",
        "download",
        "submit_application",
        "create_invoice",
        "payment",
        "delete_record",
        "send",
        "other",
    }
)

# Operations that always need the user's confirmation before they run.
SENSITIVE_ACTION_TYPES = frozenset(
    {
        "submit_application",
        "create_invoice",
        "payment",
        "delete_record",
        "send",
    }
)

DEFAULT_PERMISSIONS: dict[str, Any] = {
    # Performed without asking.
    "auto_actions": ["navigate", "read", "fill_form", "download"],
    # Performed only after the user confirms.
    "confirm_actions": sorted(SENSITIVE_ACTION_TYPES),
    # Never performed.
    "denied_actions": [],
}


def default_permissions() -> dict[str, Any]:
    return {
        "auto_actions": list(DEFAULT_PERMISSIONS["auto_actions"]),
        "confirm_actions": list(DEFAULT_PERMISSIONS["confirm_actions"]),
        "denied_actions": list(DEFAULT_PERMISSIONS["denied_actions"]),
    }


def extension_slug(system_code: str, system_version: str) -> str:
    """Build a stable slug for a system at a version."""
    parts = [
        _SLUG_CHARS.sub("-", system_code.strip().lower()).strip("-"),
        _SLUG_CHARS.sub("-", system_version.strip().lower()).strip("-"),
    ]
    slug = "-".join(part for part in parts if part) or "extension"
    return slug[:64]


def normalise_permissions(value: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a client policy with the defaults and drop unknown actions."""
    merged = default_permissions()
    if not value:
        return merged
    for key in ("auto_actions", "confirm_actions", "denied_actions"):
        raw = value.get(key)
        if not isinstance(raw, list):
            continue
        actions = [
            str(item).strip().lower()
            for item in raw
            if isinstance(item, str) and item.strip()
        ]
        merged[key] = [
            action for action in dict.fromkeys(actions) if action in KNOWN_ACTION_TYPES
        ]
    # An action cannot be both automatic and denied.
    merged["auto_actions"] = [
        action
        for action in merged["auto_actions"]
        if action not in merged["denied_actions"]
    ]
    # A denied action never needs a confirmation, it is simply refused.
    merged["confirm_actions"] = [
        action
        for action in merged["confirm_actions"]
        if action not in merged["denied_actions"]
    ]
    return merged


async def get_extension(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    extension_id: uuid.UUID,
) -> Extension:
    """Load an extension inside its own organization."""
    extension = await session.scalar(
        select(Extension).where(
            Extension.id == extension_id, Extension.org_id == org_id
        )
    )
    if extension is None:
        raise AppError(
            "Extension not found.", status_code=404, code="extension_not_found"
        )
    return extension


async def unique_slug(
    session: AsyncSession, *, org_id: uuid.UUID, base: str
) -> str:
    slug = base[:64]
    candidate = slug
    suffix = 2
    while await session.scalar(
        select(Extension.id).where(
            Extension.org_id == org_id, Extension.slug == candidate
        )
    ):
        tail = f"-{suffix}"
        candidate = f"{slug[: 64 - len(tail)]}{tail}"
        suffix += 1
    return candidate


async def create_extension(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID,
    name: str,
    system_code: str,
    system_name: str,
    system_version: str,
    target_kind: str = "web",
    url_patterns: list[str] | None = None,
    app_hints: dict[str, Any] | None = None,
    instructions: str | None = None,
    permissions: dict[str, Any] | None = None,
) -> Extension:
    extension = Extension(
        org_id=org_id,
        assistant_id=assistant_id,
        slug=await unique_slug(
            session,
            org_id=org_id,
            base=extension_slug(system_code, system_version),
        ),
        name=name,
        system_code=system_code,
        system_name=system_name,
        system_version=system_version,
        target_kind=target_kind,
        url_patterns=list(url_patterns or []),
        app_hints=dict(app_hints or {}),
        status=ExtensionStatus.draft.value,
        training_status="pending",
        instructions=instructions,
        permissions=normalise_permissions(permissions),
    )
    session.add(extension)
    await session.flush()
    return extension


async def list_extensions(
    session: AsyncSession, *, org_id: uuid.UUID
) -> list[Extension]:
    rows = await session.scalars(
        select(Extension)
        .where(Extension.org_id == org_id)
        .order_by(Extension.created_at.desc())
    )
    return list(rows)


async def extension_counts(
    session: AsyncSession, *, org_id: uuid.UUID, extension_id: uuid.UUID
) -> dict[str, int]:
    """Aggregates the console shows next to an extension."""
    materials = int(
        await session.scalar(
            select(func.count(TrainingMaterial.id)).where(
                TrainingMaterial.org_id == org_id,
                TrainingMaterial.extension_id == extension_id,
            )
        )
        or 0
    )
    procedures = int(
        await session.scalar(
            select(func.count(ExtensionProcedure.id)).where(
                ExtensionProcedure.org_id == org_id,
                ExtensionProcedure.extension_id == extension_id,
            )
        )
        or 0
    )
    approved = int(
        await session.scalar(
            select(func.count(ExtensionProcedure.id)).where(
                ExtensionProcedure.org_id == org_id,
                ExtensionProcedure.extension_id == extension_id,
                ExtensionProcedure.status == ProcedureStatus.approved.value,
            )
        )
        or 0
    )
    tests = int(
        await session.scalar(
            select(func.count(ExtensionTest.id)).where(
                ExtensionTest.org_id == org_id,
                ExtensionTest.extension_id == extension_id,
            )
        )
        or 0
    )
    passed = int(
        await session.scalar(
            select(func.count(ExtensionTest.id)).where(
                ExtensionTest.org_id == org_id,
                ExtensionTest.extension_id == extension_id,
                ExtensionTest.outcome == ExtensionTestOutcome.passed.value,
            )
        )
        or 0
    )
    return {
        "materials_count": materials,
        "procedures_count": procedures,
        "approved_procedures_count": approved,
        "tests_count": tests,
        "passed_tests_count": passed,
    }


async def approved_procedures(
    session: AsyncSession, *, org_id: uuid.UUID, extension_id: uuid.UUID
) -> list[ExtensionProcedure]:
    rows = await session.scalars(
        select(ExtensionProcedure)
        .where(
            ExtensionProcedure.org_id == org_id,
            ExtensionProcedure.extension_id == extension_id,
            ExtensionProcedure.status == ProcedureStatus.approved.value,
        )
        .order_by(ExtensionProcedure.position.asc())
    )
    return list(rows)


def matches_url(extension: Extension, url: str) -> bool:
    """True when the URL belongs to the trained system."""
    for pattern in extension.url_patterns:
        if not pattern:
            continue
        if fnmatch.fnmatch(url.lower(), pattern.lower()):
            return True
    return False


def matches_app(extension: Extension, hints: dict[str, Any]) -> bool:
    """True when the running application matches the trained desktop software."""
    configured = extension.app_hints or {}
    for key in ("window_title", "process_name", "executable"):
        expected = configured.get(key)
        actual = hints.get(key)
        if not expected or not actual:
            continue
        if fnmatch.fnmatch(str(actual).lower(), str(expected).lower()):
            return True
    return False


def recognizes(
    extension: Extension, *, url: str | None = None, app: dict[str, Any] | None = None
) -> bool:
    """Does this extension apply to the page or application in front of the user?"""
    if extension.target_kind == "desktop":
        return bool(app) and matches_app(extension, app or {})
    if url:
        return matches_url(extension, url)
    return False


def decide_action(
    extension: Extension, action_type: str, *, has_sensitive_step: bool = False
) -> tuple[bool, bool, str]:
    """Decide whether an action may run and whether it needs a confirmation.

    Returns ``(allowed, requires_confirmation, reason)``.
    """
    permissions = extension.permissions or default_permissions()
    denied = set(permissions.get("denied_actions") or [])
    auto = set(permissions.get("auto_actions") or [])
    confirm = set(permissions.get("confirm_actions") or [])

    action = action_type.strip().lower()
    if action not in KNOWN_ACTION_TYPES:
        return (
            False,
            False,
            "This action type is not allowed by the extension policy.",
        )
    if action in denied:
        return False, False, "This action is not allowed by the client policy."
    if action in auto and not has_sensitive_step:
        return True, False, "The action is allowed without confirmation."
    if action in confirm or has_sensitive_step:
        return (
            True,
            True,
            "The user has to confirm this action before it runs.",
        )
    # Unknown to the policy: allowed, but never without a confirmation.
    return (
        True,
        True,
        "The user has to confirm this action before it runs.",
    )


def build_manifest(
    extension: Extension,
    procedures: list[ExtensionProcedure],
    *,
    assistant_name: str | None = None,
) -> dict[str, Any]:
    """Build the package the client installs."""
    return {
        "schema": "workmeta.extension/1",
        "extension_id": str(extension.id),
        "slug": extension.slug,
        "name": extension.name,
        "version": extension.version,
        "issued_at": datetime.now(UTC).isoformat(),
        "system": {
            "code": extension.system_code,
            "name": extension.system_name,
            "version": extension.system_version,
            "target_kind": extension.target_kind,
            "url_patterns": list(extension.url_patterns or []),
            "app_hints": dict(extension.app_hints or {}),
        },
        "permissions": normalise_permissions(extension.permissions),
        "assistant": {
            "id": str(extension.assistant_id),
            "name": assistant_name or "",
        },
        "procedures": [
            {
                "id": str(procedure.id),
                "title": procedure.title,
                "summary": procedure.summary,
                "steps": list(procedure.steps or []),
                "confidence": procedure.confidence,
            }
            for procedure in procedures
        ],
    }


async def publish_extension(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    extension: Extension,
    assistant_name: str | None = None,
) -> Extension:
    """Publish the extension - but only when it is trained and tested.

    WorkMeta never claims a system is supported before its workflows have been
    trained and tested, so the gate is enforced here and not in the UI.
    """
    procedures = await approved_procedures(
        session, org_id=org_id, extension_id=extension.id
    )
    if extension.training_status != "extracted":
        raise AppError(
            "Analyse the training materials before publishing this extension.",
            status_code=409,
            code="extension_not_trained",
        )
    if not procedures:
        raise AppError(
            "Approve at least one procedure before publishing this extension.",
            status_code=409,
            code="extension_no_procedures",
        )
    passed = int(
        await session.scalar(
            select(func.count(ExtensionTest.id)).where(
                ExtensionTest.org_id == org_id,
                ExtensionTest.extension_id == extension.id,
                ExtensionTest.outcome == ExtensionTestOutcome.passed.value,
            )
        )
        or 0
    )
    if passed == 0:
        raise AppError(
            "Run at least one successful test before publishing this extension.",
            status_code=409,
            code="extension_not_tested",
        )

    manifest = build_manifest(
        extension, procedures, assistant_name=assistant_name
    )
    signature: str | None = None
    key_id: str | None = None
    if extension_signing.is_signing_configured():
        signature = extension_signing.sign_manifest(manifest)
        key_id = extension_signing.signing_key_id()

    extension.package_manifest = manifest
    extension.package_signature = signature
    extension.signing_key_id = key_id
    extension.status = ExtensionStatus.published.value
    extension.published_at = datetime.now(UTC)
    extension.version = extension.version + 1
    await session.flush()
    await session.refresh(extension)
    return extension


async def record_action(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    extension: Extension,
    action_type: str,
    description: str,
    steps: list[dict[str, Any]],
    has_sensitive_step: bool = False,
) -> tuple[ExtensionActionPlan, bool, bool, str]:
    """Record a proposed action and the decision taken on it."""
    allowed, requires_confirmation, reason = decide_action(
        extension, action_type, has_sensitive_step=has_sensitive_step
    )
    plan = ExtensionActionPlan(
        org_id=org_id,
        extension_id=extension.id,
        action_type=action_type,
        description=description,
        steps=steps,
        status="proposed" if allowed else "rejected",
        requires_confirmation=requires_confirmation,
    )
    session.add(plan)
    await session.flush()
    return plan, allowed, requires_confirmation, reason
