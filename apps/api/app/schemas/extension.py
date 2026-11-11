"""Extension contracts: training, procedures, tests, actions and guidance."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TargetKind = Literal["web", "desktop"]
ProcedureState = Literal["draft", "approved", "rejected"]
TestOutcome = Literal["passed", "failed", "error"]
ActionState = Literal["proposed", "confirmed", "executed", "rejected"]
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


def _clean(value: str) -> str:
    return " ".join(value.split())


class ExtensionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    # The knowledge scope for this extension: its documents and chunks.
    assistant_id: uuid.UUID
    system_code: str = Field(min_length=2, max_length=64)
    system_name: str = Field(min_length=2, max_length=160)
    # The version of the system this extension is trained for. Two extensions
    # for different versions of the same system can live side by side.
    system_version: str = Field(min_length=1, max_length=64)
    target_kind: TargetKind = "web"
    # Recognition rules: URL globs for web targets.
    url_patterns: list[str] = Field(default_factory=list, max_length=50)
    # Recognition rules for desktop targets: window titles, process names.
    app_hints: dict[str, Any] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, max_length=20000)
    permissions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "system_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = _clean(value)
        if len(cleaned) < 2:
            raise ValueError("Must not be blank")
        return cleaned

    @field_validator("system_code", "system_version")
    @classmethod
    def _code_not_blank(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("Must not be blank")
        return cleaned

    @field_validator("url_patterns")
    @classmethod
    def _patterns_clean(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class ExtensionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    system_name: str | None = Field(default=None, min_length=2, max_length=160)
    system_version: str | None = Field(default=None, min_length=1, max_length=64)
    target_kind: TargetKind | None = None
    url_patterns: list[str] | None = Field(default=None, max_length=50)
    app_hints: dict[str, Any] | None = None
    instructions: str | None = Field(default=None, max_length=20000)
    permissions: dict[str, Any] | None = None


class ExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    assistant_id: uuid.UUID
    slug: str
    name: str
    system_code: str
    system_name: str
    system_version: str
    target_kind: str
    url_patterns: list[str]
    app_hints: dict[str, Any]
    status: str
    training_status: str
    instructions: str | None
    permissions: dict[str, Any]
    version: int
    published_at: datetime | None
    package_signature: str | None
    signing_key_id: str | None
    created_at: datetime
    updated_at: datetime
    # Aggregates the console shows next to the extension.
    materials_count: int = 0
    procedures_count: int = 0
    approved_procedures_count: int = 0
    tests_count: int = 0
    passed_tests_count: int = 0


class MaterialCreate(BaseModel):
    """A written procedure or instruction the client types in."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=2, max_length=20000)


class MaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    extension_id: uuid.UUID
    kind: str
    title: str
    filename: str | None
    mime_type: str | None
    size_bytes: int
    document_id: uuid.UUID | None
    note_text: str | None
    status: str
    extracted_text: str | None
    extracted_units: int | None
    error_message: str | None
    created_at: datetime


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=2000)
    # Where to click (menu, button, field label).
    where: str | None = Field(default=None, max_length=500)
    # What to enter.
    value: str | None = Field(default=None, max_length=2000)
    # What should happen, so the user can tell it worked.
    expected_result: str | None = Field(default=None, max_length=1000)
    # A sensitive step needs the user's confirmation before it runs.
    sensitive: bool = False


class ProcedureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    extension_id: uuid.UUID
    title: str
    summary: str | None
    steps: list[dict[str, Any]]
    source_material_ids: list[str]
    confidence: float
    status: str
    position: int
    created_at: datetime
    updated_at: datetime


class ProcedureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProcedureState
    title: str | None = Field(default=None, min_length=2, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    steps: list[ProcedureStep] | None = None


class ExtractionResult(BaseModel):
    """What the analysis produced."""

    extension_id: uuid.UUID
    training_status: str
    procedures: list[ProcedureRead]
    analysed_materials: int
    analysed_characters: int
    message: str


class TestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=4, max_length=2000)
    # The facts the answer has to contain for the test to pass.
    expected_points: list[str] = Field(min_length=1, max_length=20)

    @field_validator("expected_points")
    @classmethod
    def _points_clean(cls, value: list[str]) -> list[str]:
        cleaned = [_clean(item) for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("At least one expected point is required")
        return cleaned


class TestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    extension_id: uuid.UUID
    question: str
    expected_points: list[str]
    answer: str | None
    covered_points: list[str]
    missed_points: list[str]
    score: float
    outcome: str
    created_at: datetime


class ActionPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    description: str = Field(min_length=4, max_length=4000)
    steps: list[ProcedureStep] = Field(default_factory=list, max_length=100)


class ActionPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    extension_id: uuid.UUID
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


class ActionDecision(BaseModel):
    """What the server decided about a proposed action."""

    plan: ActionPlanRead
    allowed: bool
    requires_confirmation: bool
    reason: str


class ActionConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class ActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    succeeded: bool = True
    result: dict[str, Any] = Field(default_factory=dict)


class GuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=2000)
    # The page the user is on, so the answer can be about the right step.
    context: dict[str, Any] = Field(default_factory=dict)


class GuidanceResponse(BaseModel):
    extension_id: uuid.UUID
    system_name: str
    system_version: str
    answer: str
    steps: list[dict[str, Any]]
    grounded: bool
    procedures_used: list[str]
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class PackageRead(BaseModel):
    """The signed package the client installs."""

    extension_id: uuid.UUID
    slug: str
    version: int
    manifest: dict[str, Any]
    signature: str | None
    signing_key_id: str | None
    public_key: str | None
    published_at: datetime | None
