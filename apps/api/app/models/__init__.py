"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` so Alembic
autogenerate and the migration environment can see the full schema.
"""

from app.models.assistant import Assistant
from app.models.audit import AuditLog
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.ingestion_job import IngestionJob
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.message import Message
from app.models.organization import Organization
from app.models.organization_subscription import OrganizationSubscription
from app.models.password_reset import PasswordReset
from app.models.payment_transaction import PaymentTransaction
from app.models.processed_webhook_event import ProcessedWebhookEvent
from app.models.product import Product
from app.models.session import UserSession
from app.models.submodel import (
    Submodel,
    SubmodelActionPlan,
    SubmodelMaterial,
    SubmodelProcedure,
    SubmodelTest,
)
from app.models.subscription_plan import SubscriptionPlan
from app.models.usage_counter import UsageCounter
from app.models.usage_event import UsageEvent
from app.models.user import User

__all__ = [
    "Assistant",
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "IngestionJob",
    "Invitation",
    "Membership",
    "Message",
    "Organization",
    "OrganizationSubscription",
    "PasswordReset",
    "PaymentTransaction",
    "ProcessedWebhookEvent",
    "Product",
    "SubscriptionPlan",
    "Submodel",
    "SubmodelActionPlan",
    "SubmodelMaterial",
    "SubmodelProcedure",
    "SubmodelTest",
    "UsageCounter",
    "UsageEvent",
    "UserSession",
    "User",
]
