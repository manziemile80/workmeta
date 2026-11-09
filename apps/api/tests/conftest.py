"""Shared pytest fixtures."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_password
from app.main import app
from app.models.user import User

RUN_DB_TESTS = os.getenv("RUN_DB_TESTS") == "1"


@asynccontextmanager
async def temp_engine(url: str) -> AsyncIterator[AsyncEngine]:
    """Engine bound to the current event loop, disposed on exit.

    Test fixtures must never leave pool connections behind: asyncpg connections
    are tied to the loop that created them.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture(scope="session")
def test_prefix() -> str:
    return f"it{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def owner_credentials(test_prefix: str) -> AsyncIterator[dict[str, str]]:
    if not RUN_DB_TESTS:
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")

    settings = get_settings()
    email = f"{test_prefix}-owner@example.com"
    password = "test-owner-password-123"

    async with temp_engine(settings.database_url) as engine:
        async with engine.begin() as connection:
            await connection.execute(
                insert(User.__table__).values(
                    id=uuid.uuid4(),
                    email=email,
                    password_hash=hash_password(password),
                    full_name="Test Platform Owner",
                    is_platform_admin=True,
                    status="active",
                )
            )

    credentials = {"email": email, "password": password}
    yield credentials

    # Best-effort cleanup. The owner connection is a superuser in development
    # and CI, so RLS does not interfere.
    async with temp_engine(settings.database_migration_url) as engine:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM audit_logs WHERE actor_user_id IN "
                     "(SELECT id FROM users WHERE email LIKE :pattern) "
                     "OR org_id IN (SELECT id FROM organizations WHERE slug LIKE :pattern) "
                     "OR (action = 'auth.login_failed' AND metadata->>'email' "
                     "~ '^it[0-9a-f]{8}-[^@]*@example\\.com$')"),
                {"pattern": f"{test_prefix}%"},
            )
            await connection.execute(
                text("DELETE FROM organizations WHERE slug LIKE :pattern"),
                {"pattern": f"{test_prefix}%"},
            )
            await connection.execute(
                text("DELETE FROM users WHERE email LIKE :pattern"),
                {"pattern": f"{test_prefix}%"},
            )


@pytest.fixture(scope="session")
async def owner_token(owner_credentials: dict[str, str]) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.post(
            "/api/v1/auth/login",
            json={**owner_credentials, "kind": "extension"},
        )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token
