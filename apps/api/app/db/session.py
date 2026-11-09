"""Database engine and session management.

The application connects with a restricted role (`wm_app`) that cannot bypass
Row-Level Security. Alembic uses a separate owner connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    connect_args={
        # Fail fast when PostgreSQL is unavailable so readiness checks stay snappy.
        "timeout": 5,
        "command_timeout": 30,
        "server_settings": {"application_name": "workmeta-api"},
    },
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a request-scoped session.

    The session is rolled back on exit so an unhandled error can never leak a
    partially written transaction. Handlers commit successful work explicitly.
    """
    session = SessionFactory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


async def ping_database(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1"))


async def has_pgvector(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    return result.scalar_one_or_none() is not None


async def dispose_engine() -> None:
    await engine.dispose()
