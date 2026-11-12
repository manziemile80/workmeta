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
        # A remote database over a slow link needs more than a couple of
        # seconds to establish a session, so the timeout is a setting rather
        # than a fixed number. The readiness probe uses its own, shorter
        # timeout, so it stays snappy whatever this is set to.
        "timeout": settings.database_connect_timeout_seconds,
        "command_timeout": settings.database_command_timeout_seconds,
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


async def probe_database(
    *, timeout_seconds: int | None = None
) -> tuple[bool, bool]:
    """Check the database on a connection of its own.

    The probe reuses the application pool when it is warm, so a readiness
    check is quick on a running deployment. When the pool is cold it opens a
    connection with the configured connect timeout, which is generous enough
    for a slow link.

    Returns ``(reachable, pgvector)``. A database that cannot be reached is
    reported, not raised, so the caller decides what to answer.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            result = await connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            return True, result.scalar_one_or_none() is not None
    except Exception:  # noqa: BLE001 - the caller reports the failure
        return False, False


async def dispose_engine() -> None:
    await engine.dispose()
