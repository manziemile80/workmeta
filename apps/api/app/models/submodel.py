"""WorkMeta AI Submodels — the administrator's trained models.

A **submodel** is a trained model for one *task*: issuing an EBM receipt,
registering a student in CAMIS, applying for a permit on a portal. The
platform administrator records how the work is done — demonstration videos
and written steps — and the AI turns those recordings into workflows that a
submodel can be trained on, tested with, exported from and published.

Four of the five tables are **platform-owned**: they carry no ``org_id``
because they are the administrator's material, not any client's. Their
row-level security (migration ``20261116_0015``) allows:

* reads and writes only from a platform administrator, and
* for a published submodel, reads of the submodel row and its *approved
  workflows* by any signed-in user — that is what "published" means. Raw
  training videos, draft workflows and test questions never leave the
  administrator's side.

The fifth table, ``submodel_action_plans``, is the authority record for an
action performed **on a user's machine** when they use a published submodel.
That row belongs to the user's tenant, so it keeps ``org_id`` and the same
forced tenant policy the knowledge tables use.

Tables:

* ``submodels``             - the submodel: its task, lifecycle, permission
                              policy, training status and published package.
* ``submodel_materials``    - the demonstration videos, audio and notes the
                              AI learns from.
* ``submodel_procedures``   - the workflows extracted from the material.
* ``submodel_tests``        - the questions that check it learned the task.
* ``submodel_action_plans`` - the authority record for an action it performs,
                              including the user's confirmation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    SubmodelActionStatus,
    SubmodelMaterialKind,
    SubmodelOrigin,
    SubmodelStatus,
    SubmodelTestOutcome,
)


class Submodel(Base):
    """One trained model for one task, portable between installations."""

    __tablename__ = "submodels"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_submodels_slug"),
        Index("ix_submodels_status", "status"),
        CheckConstraint(
            "status IN ('draft', 'training', 'trained', 'published', 'retired')",
            name="ck_submodels_status_valid",
        ),
        CheckConstraint(
            "training_status IN ('pending', 'running', 'extracted', 'failed')",
            name="ck_submodels_training_status_valid",
        ),
        CheckConstraint(
            "origin IN ('trained', 'imported')",
            name="ck_submodels_origin_valid",
        ),
        CheckConstraint(
            "target_kind IN ('web', 'desktop')",
            name="ck_submodels_target_kind_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The task the submodel learned. It is what the user asks it about.
    task: Mapped[str] = mapped_column(String(400), nullable=False)
    # Where the task is performed, when it is not obvious from the task.
    system_code: Mapped[str] = mapped_column(String(64), nullable=False)
    system_name: Mapped[str] = mapped_column(String(160), nullable=False)
    system_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="web",
        server_default="web",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubmodelStatus.draft.value,
        server_default=SubmodelStatus.draft.value,
    )
    # Honest training state: "pending" before the first run, "running" while
    # the model is really working, "extracted" only after workflows were
    # stored, "failed" with the reason why. Nothing is ever reported as
    # trained without a completed run.
    training_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    training_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A trained submodel was trained here. An imported one arrived as a
    # package and keeps the knowledge it came with.
    origin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SubmodelOrigin.trained.value,
        server_default=SubmodelOrigin.trained.value,
    )
    # For an imported submodel: where it came from, so an administrator can
    # see who trained it and on which machine.
    source_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The permission policy: what the submodel may do without asking, what
    # needs the user's confirmation and what it may never do.
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # What the submodel needs to be able to do on the user's screen. It is
    # guidance, not a script: an actor still asks the server for authority
    # before it acts.
    capabilities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # The published package: the procedures, the permissions and the task, so
    # an installed copy carries everything it needs.
    package_manifest: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    package_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signing_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SubmodelMaterial(Base):
    """One input the submodel learns from."""

    __tablename__ = "submodel_materials"
    __table_args__ = (
        Index("ix_submodel_materials_submodel", "submodel_id"),
        CheckConstraint(
            "kind IN ('video', 'audio', 'document', 'note')",
            name="ck_submodel_materials_kind_valid",
        ),
        CheckConstraint(
            "status IN ('stored', 'extracting', 'ready', 'failed', 'archived')",
            name="ck_submodel_materials_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submodel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submodels.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SubmodelMaterialKind.note.value,
        server_default=SubmodelMaterialKind.note.value,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="stored",
        server_default="stored",
    )
    # What the analyzer produced: the timeline of screens read off the
    # recording, or the transcript of an audio instruction.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SubmodelProcedure(Base):
    """A workflow the AI extracted from the training material."""

    __tablename__ = "submodel_procedures"
    __table_args__ = (
        Index("ix_submodel_procedures_submodel", "submodel_id", "position"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_submodel_procedures_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submodel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submodels.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The steps of the workflow: what to do, where, what to enter and what
    # should happen next.
    steps: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source_material_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SubmodelTest(Base):
    """A question that checks the submodel learned the task."""

    __tablename__ = "submodel_tests"
    __table_args__ = (
        Index("ix_submodel_tests_submodel", "submodel_id", "created_at"),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="ck_submodel_tests_outcome_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submodel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submodels.id", ondelete="CASCADE"),
        nullable=False,
    )
    question: Mapped[str] = mapped_column(String(2000), nullable=False)
    expected_points: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    covered_points: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missed_points: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubmodelTestOutcome.error.value,
        server_default=SubmodelTestOutcome.error.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubmodelActionPlan(Base):
    """The authority record for an action a submodel performs.

    This row belongs to the tenant where the action happens: it records who
    proposed it, who confirmed it and what happened, so it keeps ``org_id``
    and the forced tenant policy.
    """

    __tablename__ = "submodel_action_plans"
    __table_args__ = (
        Index("ix_submodel_action_plans_org_submodel", "org_id", "submodel_id"),
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executed', 'rejected')",
            name="ck_submodel_action_plans_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    submodel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submodels.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(4000), nullable=False)
    steps: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubmodelActionStatus.proposed.value,
        server_default=SubmodelActionStatus.proposed.value,
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
