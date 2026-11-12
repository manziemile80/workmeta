"""Submodel lifecycle: creating, training, publishing and packaging.

A submodel is trained from material — usually screen recordings — and the
result is a set of approved workflows. Those workflows, together with the
permission policy, are what a submodel *carries*. That is what makes it
portable: an exported package holds the knowledge, so an installed copy works
on a machine that never saw the training material.

Everything here is tenant-scoped. A submodel belongs to one organization and
the runtime role can only ever read its own tenant's rows.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.enums import (
    SubmodelActionStatus,
    SubmodelOrigin,
    SubmodelStatus,
)
from app.models.submodel import (
    Submodel,
    SubmodelActionPlan,
    SubmodelMaterial,
    SubmodelProcedure,
    SubmodelTest,
)

# The action types a submodel may be asked to perform. The same vocabulary the
# installed agent uses, so the runtime is consistent across the platform.
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


def normalise_permissions(value: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a policy with the defaults and drop unknown actions."""
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


def decide_action(
    permissions: dict[str, Any] | None,
    action_type: str,
    *,
    has_sensitive_step: bool = False,
) -> tuple[bool, bool, str]:
    """Decide whether an action may run and whether it needs a confirmation.

    Returns ``(allowed, requires_confirmation, reason)``.
    """
    policy = normalise_permissions(permissions)
    denied = set(policy["denied_actions"])
    auto = set(policy["auto_actions"])
    confirm = set(policy["confirm_actions"])

    action = action_type.strip().lower()
    if action not in KNOWN_ACTION_TYPES:
        return (
            False,
            False,
            "This action type is not allowed by the submodel policy.",
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


async def get_submodel(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    submodel_id: uuid.UUID,
) -> Submodel:
    """Load a submodel inside its own organization."""
    submodel = await session.scalar(
        select(Submodel).where(
            Submodel.id == submodel_id, Submodel.org_id == org_id
        )
    )
    if submodel is None:
        raise AppError(
            "Submodel not found.", status_code=404, code="submodel_not_found"
        )
    return submodel


async def unique_slug(
    session: AsyncSession, *, org_id: uuid.UUID, base: str
) -> str:
    """A slug that is free inside this organization."""
    slug = base[:64]
    candidate = slug
    suffix = 2
    while await session.scalar(
        select(Submodel.id).where(
            Submodel.org_id == org_id, Submodel.slug == candidate
        )
    ):
        tail = f"-{suffix}"
        candidate = f"{slug[: 64 - len(tail)]}{tail}"
        suffix += 1
    return candidate


async def create_submodel(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    task: str,
    system_code: str,
    system_name: str,
    system_version: str,
    description: str | None = None,
    target_kind: str = "web",
    permissions: dict[str, Any] | None = None,
    capabilities: list[Any] | None = None,
    created_by: uuid.UUID | None = None,
) -> Submodel:
    """Create a submodel in its own organization."""
    slug = await unique_slug(
        session,
        org_id=org_id,
        base=_slugify(f"{system_code}-{task}"),
    )
    submodel = Submodel(
        org_id=org_id,
        slug=slug,
        name=name.strip(),
        description=(description or "").strip() or None,
        task=task.strip(),
        system_code=system_code.strip().lower(),
        system_name=system_name.strip(),
        system_version=system_version.strip(),
        target_kind=target_kind,
        status=SubmodelStatus.draft.value,
        training_status="pending",
        origin=SubmodelOrigin.trained.value,
        permissions=normalise_permissions(permissions),
        capabilities=list(capabilities or []),
        created_by=created_by,
    )
    session.add(submodel)
    await session.flush()
    await session.refresh(submodel)
    return submodel


def _slugify(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "submodel"


async def list_submodels(
    session: AsyncSession, *, org_id: uuid.UUID
) -> list[Submodel]:
    """Every submodel in this organization, newest first."""
    rows = await session.scalars(
        select(Submodel)
        .where(Submodel.org_id == org_id)
        .order_by(Submodel.created_at.desc())
    )
    return list(rows)


async def approved_procedures(
    session: AsyncSession, *, org_id: uuid.UUID, submodel_id: uuid.UUID
) -> list[SubmodelProcedure]:
    """The workflows a submodel may use."""
    rows = await session.scalars(
        select(SubmodelProcedure)
        .where(
            SubmodelProcedure.org_id == org_id,
            SubmodelProcedure.submodel_id == submodel_id,
            SubmodelProcedure.status == "approved",
        )
        .order_by(SubmodelProcedure.position.asc())
    )
    return list(rows)


async def submodel_counts(
    session: AsyncSession, *, org_id: uuid.UUID, submodel_id: uuid.UUID
) -> dict[str, int]:
    """The counters the console shows next to a submodel."""
    materials = int(
        await session.scalar(
            select(func.count(SubmodelMaterial.id)).where(
                SubmodelMaterial.org_id == org_id,
                SubmodelMaterial.submodel_id == submodel_id,
            )
        )
        or 0
    )
    procedures = int(
        await session.scalar(
            select(func.count(SubmodelProcedure.id)).where(
                SubmodelProcedure.org_id == org_id,
                SubmodelProcedure.submodel_id == submodel_id,
            )
        )
        or 0
    )
    approved = int(
        await session.scalar(
            select(func.count(SubmodelProcedure.id)).where(
                SubmodelProcedure.org_id == org_id,
                SubmodelProcedure.submodel_id == submodel_id,
                SubmodelProcedure.status == "approved",
            )
        )
        or 0
    )
    tests = int(
        await session.scalar(
            select(func.count(SubmodelTest.id)).where(
                SubmodelTest.org_id == org_id,
                SubmodelTest.submodel_id == submodel_id,
            )
        )
        or 0
    )
    passed = int(
        await session.scalar(
            select(func.count(SubmodelTest.id)).where(
                SubmodelTest.org_id == org_id,
                SubmodelTest.submodel_id == submodel_id,
                SubmodelTest.outcome == "passed",
            )
        )
        or 0
    )
    return {
        "materials_count": materials,
        "workflows_count": procedures,
        "approved_workflows_count": approved,
        "tests_count": tests,
        "passed_tests_count": passed,
    }


async def record_action(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    submodel: Submodel,
    action_type: str,
    description: str,
    steps: list[dict[str, Any]],
) -> tuple[SubmodelActionPlan, bool, bool, str]:
    """Record the authority for an action and what the policy decided."""
    has_sensitive_step = any(bool(step.get("sensitive")) for step in steps)
    allowed, requires_confirmation, reason = decide_action(
        submodel.permissions, action_type, has_sensitive_step=has_sensitive_step
    )
    plan = SubmodelActionPlan(
        org_id=org_id,
        submodel_id=submodel.id,
        action_type=action_type,
        description=description,
        steps=steps,
        # An action the policy refuses is recorded as rejected, so it can
        # never be completed later by calling the wrong endpoint.
        status=(
            SubmodelActionStatus.proposed.value
            if allowed
            else SubmodelActionStatus.rejected.value
        ),
        requires_confirmation=requires_confirmation,
    )
    session.add(plan)
    await session.flush()
    await session.refresh(plan)
    return plan, allowed, requires_confirmation, reason


def build_package(
    submodel: Submodel,
    procedures: list[SubmodelProcedure],
) -> dict[str, Any]:
    """The package a submodel publishes.

    It carries the workflows and the permission policy, so an installed copy
    has everything it needs to work on its own.
    """
    return {
        "schema": "workmeta.submodel/1",
        "submodel_id": str(submodel.id),
        "slug": submodel.slug,
        "name": submodel.name,
        "description": submodel.description,
        "task": submodel.task,
        "version": submodel.version,
        "origin": submodel.origin,
        # Where this package came from, so the machine that installs it can
        # show it to an administrator.
        "source_label": submodel.source_label
        or f"{submodel.name} ({submodel.system_name} {submodel.system_version})",
        "system": {
            "code": submodel.system_code,
            "name": submodel.system_name,
            "version": submodel.system_version,
            "target_kind": submodel.target_kind,
        },
        "permissions": normalise_permissions(submodel.permissions),
        "capabilities": list(submodel.capabilities or []),
        "workflows": [
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
