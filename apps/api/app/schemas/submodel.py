"""Submodel contracts: creating, training, testing, packaging and using."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SubmodelTargetKind = Literal["web", "desktop"]
ProcedureState = Literal["draft", "approved", "rejected"]
SubmodelTestOutcome = Literal["passed", "failed", "error"]
ActionType = Literal[
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
]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


class SubmodelCreate(BaseModel):
    """Create a submodel for a task."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    # The task the submodel is for, in the client's own words.
    task: str = Field(min_length=4, max_length=400)
    system_code: str = Field(min_length=2, max_length=64)
    system_name: str = Field(min_length=2, max_length=160)
    system_version: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    target_kind: SubmodelTargetKind = "web"
    permissions: dict[str, Any] | None = None
    capabilities: list[Any] = Field(default_factory=list)

    @field_validator("name", "system_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Must not be blank")
        return cleaned

    @field_validator("task")
    @classmethod
    def _task_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 4:
            raise ValueError("Describe the task in a few words.")
        return cleaned

    @field_validator("system_code")
    @classmethod
    def _code_not_blank(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if len(cleaned) < 2:
            raise ValueError("Must not be blank")
        return cleaned


class SubmodelUpdate(BaseModel):
    """Change a submodel."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    task: str | None = Field(default=None, min_length=4, max_length=400)
    system_code: str | None = Field(default=None, min_length=2, max_length=64)
    system_name: str | None = Field(default=None, min_length=2, max_length=160)
    system_version: str | None = Field(default=None, min_length=1, max_length=64)
    target_kind: SubmodelTargetKind | None = None
    permissions: dict[str, Any] | None = None
    capabilities: list[Any] | None = None


class SubmodelRead(BaseModel):
    """A submodel."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    task: str
    system_code: str
    system_name: str
    system_version: str
    target_kind: str
    status: str
    training_status: str
    origin: str
    source_label: str | None
    source_digest: str | None
    permissions: dict[str, Any]
    capabilities: list[Any]
    version: int
    package_signature: str | None
    signing_key_id: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # The counters the console shows next to the submodel.
    materials_count: int = 0
    workflows_count: int = 0
    approved_workflows_count: int = 0
    tests_count: int = 0
    passed_tests_count: int = 0


class SubmodelMaterialCreate(BaseModel):
    """A written procedure or instruction the client types in."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=2, max_length=20000)


class SubmodelMaterialRead(BaseModel):
    """One training material."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    submodel_id: uuid.UUID
    kind: str
    title: str
    filename: str | None
    mime_type: str | None
    size_bytes: int
    status: str
    extracted_text: str | None
    extracted_units: int | None
    error_message: str | None
    created_at: datetime


class SubmodelStep(BaseModel):
    """One step of a workflow."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=2000)
    where: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=2000)
    expected_result: str | None = Field(default=None, max_length=1000)
    sensitive: bool = False


class SubmodelWorkflowRead(BaseModel):
    """A workflow the AI extracted."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    submodel_id: uuid.UUID
    title: str
    summary: str | None
    steps: list[dict[str, Any]]
    source_material_ids: list[Any]
    confidence: float
    status: str
    position: int
    created_at: datetime
    updated_at: datetime


class SubmodelWorkflowUpdate(BaseModel):
    """Approve or reject an extracted workflow."""

    model_config = ConfigDict(extra="forbid")

    status: ProcedureState
    title: str | None = Field(default=None, min_length=2, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    steps: list[SubmodelStep] | None = None


class SubmodelTrainingResult(BaseModel):
    """What training produced."""

    submodel_id: uuid.UUID
    training_status: str
    workflows: list[SubmodelWorkflowRead]
    analysed_materials: int
    analysed_characters: int
    message: str


class SubmodelTestCreate(BaseModel):
    """A question that checks the submodel learned the task."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=4, max_length=2000)
    expected_points: list[str] = Field(min_length=1, max_length=20)

    @field_validator("expected_points")
    @classmethod
    def _points_cleaned(cls, value: list[str]) -> list[str]:
        cleaned = [_clean(item) for item in value]
        cleaned = [item for item in cleaned if item]
        if not cleaned:
            raise ValueError("At least one expected point is required")
        return cleaned


class SubmodelTestRead(BaseModel):
    """One test result."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    submodel_id: uuid.UUID
    question: str
    expected_points: list[Any]
    answer: str | None
    covered_points: list[Any]
    missed_points: list[Any]
    score: float
    outcome: str
    created_at: datetime


class SubmodelGuidanceRequest(BaseModel):
    """Ask the submodel a question."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)


class SubmodelGuidanceResponse(BaseModel):
    """The submodel's answer."""

    submodel_id: uuid.UUID
    task: str
    answer: str
    steps: list[dict[str, Any]]
    grounded: bool
    workflows_used: list[str]
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class SubmodelActionPlanCreate(BaseModel):
    """Ask whether an action may be performed."""

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    description: str = Field(min_length=4, max_length=4000)
    steps: list[SubmodelStep] = Field(default_factory=list, max_length=100)


class SubmodelActionPlanRead(BaseModel):
    """The authority record for an action."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    submodel_id: uuid.UUID
    action_type: str
    description: str
    steps: list[dict[str, Any]]
    status: str
    requires_confirmation: bool
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class SubmodelActionDecision(BaseModel):
    """What the server decided about a proposed action."""

    plan: SubmodelActionPlanRead
    allowed: bool
    requires_confirmation: bool
    reason: str


class SubmodelActionConfirm(BaseModel):
    """The user's answer about a sensitive action."""

    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class SubmodelActionComplete(BaseModel):
    """What happened."""

    model_config = ConfigDict(extra="forbid")

    succeeded: bool = True
    result: dict[str, Any] = Field(default_factory=dict)


class SubmodelPackageInfo(BaseModel):
    """A package, and whether it verified."""

    filename: str
    digest: str
    signing_key_id: str
    verified: bool
    reason: str
    workflow_count: int
    payload: dict[str, Any]
