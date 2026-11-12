"""API v1 router composition.

Feature routers are added in later stages as they are implemented — never as
empty placeholders.
"""

from fastapi import APIRouter

from app.api.v1 import (
    assistants,
    auth,
    billing,
    conversations,
    documents,
    health,
    invitations,
    memberships,
    organizations,
    platform,
    products,
    submodels,
    usage,
)
from app.api.v1 import audit as audit_routes

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(organizations.router)
api_router.include_router(assistants.router)
api_router.include_router(submodels.router)
api_router.include_router(documents.router)
api_router.include_router(conversations.assistant_router)
api_router.include_router(conversations.router)
api_router.include_router(memberships.router)
api_router.include_router(invitations.router)
api_router.include_router(audit_routes.router)
api_router.include_router(usage.router)
api_router.include_router(usage.assistant_router)
api_router.include_router(billing.router)
api_router.include_router(billing.org_router)
api_router.include_router(platform.router)
