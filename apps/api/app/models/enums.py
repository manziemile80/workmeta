"""Domain enumerations shared by models, schemas and services.

Values are stored as short strings with database CHECK constraints rather than
native PostgreSQL enums, so adding a role or status later is an ordinary
migration instead of an ``ALTER TYPE`` lock.
"""

from __future__ import annotations

from enum import StrEnum


class OrganizationStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    archived = "archived"


class UserStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    disabled = "disabled"


class MembershipRole(StrEnum):
    # Platform owners are flagged on ``users.is_platform_admin``; they are never
    # stored as a membership role.
    client_admin = "client_admin"
    client_member = "client_member"


class MembershipStatus(StrEnum):
    active = "active"
    suspended = "suspended"


class SessionKind(StrEnum):
    web = "web"
    extension = "extension"


class AssistantStatus(StrEnum):
    draft = "draft"
    active = "active"
    archived = "archived"


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class IngestionJobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"


class UsageKind(StrEnum):
    chat = "chat"
    embedding = "embedding"
    provider_error = "provider_error"
    extraction = "extraction"


class ExtensionStatus(StrEnum):
    """Lifecycle of a trained extension."""

    draft = "draft"
    training = "training"
    ready = "ready"
    published = "published"
    retired = "retired"


class ExtensionTargetKind(StrEnum):
    web = "web"
    desktop = "desktop"


class MaterialKind(StrEnum):
    document = "document"
    video = "video"
    audio = "audio"
    note = "note"


class MaterialStatus(StrEnum):
    stored = "stored"
    extracting = "extracting"
    ready = "ready"
    failed = "failed"
    archived = "archived"


class ProcedureStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"


class ExtensionTestOutcome(StrEnum):
    passed = "passed"
    failed = "failed"
    error = "error"


class ExtensionActionStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    executed = "executed"
    rejected = "rejected"


class BillingInterval(StrEnum):
    monthly = "monthly"
    annual = "annual"


class SubscriptionStatus(StrEnum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    expired = "expired"


class PaymentStatus(StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"


class PaymentKind(StrEnum):
    payment = "payment"
    refund = "refund"


# Pseudo-role used in audit rows and API responses when a platform admin acts on
# a client. It is deliberately not a valid ``memberships.role`` value.
PLATFORM_OWNER_ROLE = "platform_owner"
