"""Self-service registration tests against a real PostgreSQL database.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v

Registration is the only way to create an account without the platform owner,
so the tests cover the whole chain: person, first organization, Free
subscription, session cookie and the tenant isolation that follows.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

PASSWORD = "correct-horse-battery-staple1"


@asynccontextmanager
async def _engine(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _register(
    client: AsyncClient,
    *,
    email: str,
    organization_name: str,
    full_name: str = "Test Person",
    password: str = PASSWORD,
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": full_name,
            "email": email,
            "password": password,
            "organization_name": organization_name,
            "country": "RW",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_registration_creates_account_organization_and_free_plan(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    email = f"{test_prefix}-register-{unique}@example.com"
    organization_name = f"{test_prefix} Register {unique}"

    payload = await _register(
        client, email=email, organization_name=organization_name
    )

    assert payload["user"]["email"] == email
    assert payload["user"]["is_platform_admin"] is False
    assert payload["active_organization"]["name"] == organization_name
    assert len(payload["memberships"]) == 1
    assert payload["memberships"][0]["role"] == "client_admin"
    assert payload["memberships"][0]["organization"]["id"] == (
        payload["active_organization"]["id"]
    )

    # The web session cookie is set, so the console works right away.
    assert client.cookies.get("wm_session")
    assert client.cookies.get("wm_csrf")

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == email

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "kind": "extension"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    org_id = payload["active_organization"]["id"]

    billing = await client.get(
        f"/api/v1/organizations/{org_id}/billing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert billing.status_code == 200, billing.text
    assert billing.json()["subscription"]["plan"]["code"] == "free"
    assert billing.json()["subscription"]["status"] == "active"


async def test_duplicate_email_is_rejected(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    email = f"{test_prefix}-duplicate-{unique}@example.com"
    organization_name = f"{test_prefix} Duplicate {unique}"

    await _register(client, email=email, organization_name=organization_name)

    again = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Test Person",
            "email": email.upper(),
            "password": PASSWORD,
            "organization_name": f"{organization_name} Two",
            "country": "RW",
        },
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "account_exists"


async def test_weak_passwords_are_rejected(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    for password in ("short1", "alllettersonly", "12345678901234"):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Test Person",
                "email": f"{test_prefix}-weak-{unique}@example.com",
                "password": password,
                "organization_name": f"{test_prefix} Weak {unique}",
                "country": "RW",
            },
        )
        assert response.status_code == 422, (password, response.text)
        assert response.json()["error"]["code"] == "validation_error"


async def test_same_organization_name_gets_a_unique_slug(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    organization_name = f"{test_prefix} Shared {unique}"

    first = await _register(
        client,
        email=f"{test_prefix}-shared-a-{unique}@example.com",
        organization_name=organization_name,
    )
    second = await _register(
        client,
        email=f"{test_prefix}-shared-b-{unique}@example.com",
        organization_name=organization_name,
    )

    first_slug = first["active_organization"]["slug"]
    second_slug = second["active_organization"]["slug"]
    assert first_slug != second_slug
    assert second_slug.startswith(first_slug)


async def test_registered_person_cannot_create_a_second_organization(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    email = f"{test_prefix}-single-org-{unique}@example.com"
    payload = await _register(
        client,
        email=email,
        organization_name=f"{test_prefix} Single {unique}",
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "kind": "extension"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Creating organizations stays a platform-owner action.
    denied = await client.post(
        "/api/v1/organizations",
        headers=headers,
        json={
            "name": f"{test_prefix} Second {unique}",
            "slug": f"{test_prefix}-second-{unique}",
            "country": "RW",
        },
    )
    assert denied.status_code == 403

    # And the row-level security policy refuses a second insert for a person
    # who already is a member of a tenant.
    settings = get_settings()
    async with _engine(settings.database_url) as engine:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        "SELECT set_config('app.current_org', :org, true), "
                        "set_config('app.current_user', :usr, true)"
                    ),
                    {
                        "org": payload["active_organization"]["id"],
                        "usr": payload["user"]["id"],
                    },
                )
                with pytest.raises(DBAPIError):
                    await connection.execute(
                        text(
                            "INSERT INTO organizations "
                            "(id, name, slug, country, status) "
                            "VALUES (:id, :name, :slug, 'RW', 'active')"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "name": f"{test_prefix} Sneaky {unique}",
                            "slug": f"{test_prefix}-sneaky-{unique}",
                        },
                    )
