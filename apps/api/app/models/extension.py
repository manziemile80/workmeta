"""Trained, client-specific extensions for institutional systems.

An extension is what a client installs in their browser or desktop agent. It is
trained for **one** target system (a portal, an institutional system, a piece
of software) at **one** version, and everything it knows lives in the client's
own organization:

* ``extensions``            - the extension itself: target system, version,
                               recognition rules, permissions, lifecycle.
* ``training_materials``    - the inputs the AI analyses: manuals (documents),
                               screen recordings, audio instructions and written
                               procedures.
* ``extension_procedures``  - the extracted operating procedures, approved by a
                               human before they can be used.
* ``extension_tests``       - the questions the client asks to check that the
                               assistant really understands the system.
* ``extension_action_plans``- the authority record for "may the agent do this
                               on the user's behalf?" including the confirmation
                               the user gave before a sensitive operation.

Every table carries ``org_id`` and is protected by forced row-level security,
so one client can never read another client's training materials, procedures,
tests or permissions.
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
    ExtensionStatus,
    ExtensionTargetKind,
    ExtensionTestOutcome,
    MaterialKind,
    MaterialStatus,
    ProcedureStatus,
)


class Extension(Base):
    """One trained extension for one target system at one version."""

    __tablename__ = "extensions"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_extensions_org_slug"),
        Index("ix_extensions_org_id_status", "org_id", "status"),
        Index("ix_extensions_assistant_id", "assistant_id"),
        CheckConstraint(
            "status IN ('draft', 'training', 'ready', 'published', 'retired')",
            name="status_valid",
        ),
        CheckConstraint(
            "training_status IN ('pending', 'extracting', 'extracted', 'failed')",
            name="training_status_valid",
        ),
        CheckConstraint(
            "target_kind IN ('web', 'desktop')",
            name="target_kind_valid",
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
    # The knowledge scope: documents, chunks and retrieval for this extension.
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistants.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Which system is being trained, and which version of it.
    system_code: Mapped[str] = mapped_column(String(64), nullable=False)
    system_name: Mapped[str] = mapped_column(String(160), nullable=False)
    system_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ExtensionTargetKind.web.value,
        server_default=ExtensionTargetKind.web.value,
    )
    # Recognition: URL globs for web targets, window/process hints for desktop.
    url_patterns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    app_hints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ExtensionStatus.draft.value,
        server_default=ExtensionStatus.draft.value,
    )
    training_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    # The client's own written procedures. They are analysis input, never
    # instructions that can override the platform answer policy.
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the agent may do without asking, what needs confirmation and what
    # is never allowed. See app/services/extensions.py for the default policy.
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The signed package the client installs.
    package_manifest: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    package_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signing_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TrainingMaterial(Base):
    """One input the AI analyses: a manual, a recording or a written note."""

    __tablename__ = "training_materials"
    __table_args__ = (
        Index("ix_training_materials_org_extension", "org_id", "extension_id"),
        Index("ix_training_materials_status", "org_id", "status"),
        CheckConstraint(
            "kind IN ('document', 'video', 'audio', 'note')",
            name="kind_valid",
        ),
        CheckConstraint(
            "status IN ('stored', 'extracting', 'ready', 'failed', 'archived')",
            name="status_valid",
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
    extension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extensions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MaterialKind.note.value,
        server_default=MaterialKind.note.value,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when the material is a document already ingested by the knowledge
    # pipeline (PDF, Word, text, Markdown, screenshots).
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    # The client's written instructions.
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MaterialStatus.stored.value,
        server_default=MaterialStatus.stored.value,
    )
    # Text produced by the extractor (OCR of video frames, transcript).
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


class ExtensionProcedure(Base):
    """An operating procedure extracted from the training materials."""

    __tablename__ = "extension_procedures"
    __table_args__ = (
        UniqueConstraint(
            "extension_id", "title", name="uq_extension_procedures_title"
        ),
        Index("ix_extension_procedures_org_extension", "org_id", "extension_id"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="status_valid",
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
    extension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extensions.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ordered steps: instruction, where to click, what to enter, expected result
    # and whether the step is sensitive (needs a confirmation).
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source_material_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ProcedureStatus.draft.value,
        server_default=ProcedureStatus.draft.value,
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


class ExtensionTest(Base):
    """A question the client used to check that the assistant understands."""

    __tablename__ = "extension_tests"
    __table_args__ = (
        Index("ix_extension_tests_org_extension", "org_id", "extension_id"),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="outcome_valid",
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
    extension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extensions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Points the answer has to cover for the test to pass.
    expected_points: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    covered_points: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missed_points: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    outcome: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ExtensionTestOutcome.failed.value,
        server_default=ExtensionTestOutcome.failed.value,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExtensionActionPlan(Base):
    """The authority record for an action the agent wants to perform.

    The agent asks the server whether an action is allowed. Sensitive actions
    (submitting an application, a financial transaction, deleting a record) are
    only allowed after the user confirmed them; the confirmation is recorded
    here so the audit trail can show who authorised what.
    """

    __tablename__ = "extension_action_plans"
    __table_args__ = (
        Index("ix_extension_action_plans_org_extension", "org_id", "extension_id"),
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executed', 'rejected')",
            name="status_valid",
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
    extension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extensions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="proposed",
        server_default="proposed",
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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
