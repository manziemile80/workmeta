"""Real database connectivity test.

Runs only when RUN_DB_TESTS=1 so unit tests stay fast and dependency-free.
CI and the Stage 0 verification run set this flag with PostgreSQL available.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from app.db.session import engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]


async def test_database_is_reachable() -> None:
    async with engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


async def test_runtime_role_cannot_bypass_rls() -> None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    assert row.rolbypassrls is False
    assert row.rolsuper is False


async def test_extensions_are_available() -> None:
    async with engine.connect() as connection:
        extensions = {
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT extname FROM pg_extension")
                )
            ).all()
        }
    assert "vector" in extensions
    assert "citext" in extensions
    assert "pgcrypto" in extensions
