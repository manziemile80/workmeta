"""Readiness endpoint behavior using stubbed database sessions.

The real database path is covered by tests/test_database_integration.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from httpx import AsyncClient

from app.db.session import get_session
from app.main import app


class _FakeResult:
    def __init__(self, value: object = None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    def __init__(self, *, fail: bool = False, vector: bool = True) -> None:
        self.fail = fail
        self.vector = vector

    async def execute(self, statement: object) -> _FakeResult:
        if self.fail:
            raise ConnectionError("database is down")
        if "pg_extension" in str(statement):
            return _FakeResult("0.8.0" if self.vector else None)
        return _FakeResult()


def _override(session: _FakeSession):
    async def _dependency() -> AsyncIterator[_FakeSession]:
        yield session

    return _dependency


async def test_ready_when_database_and_pgvector_are_available(client: AsyncClient) -> None:
    app.dependency_overrides[get_session] = _override(_FakeSession(vector=True))
    try:
        response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["pgvector"] is True


async def test_ready_reports_missing_pgvector(client: AsyncClient) -> None:
    app.dependency_overrides[get_session] = _override(_FakeSession(vector=False))
    try:
        response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["pgvector"] is False


async def test_ready_returns_503_when_database_is_down(client: AsyncClient) -> None:
    app.dependency_overrides[get_session] = _override(_FakeSession(fail=True))
    try:
        response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "database_unavailable"
    assert error["request_id"]
