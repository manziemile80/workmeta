"""Readiness endpoint behavior using a stubbed database probe.

The probe uses a connection of its own, so that a readiness check answers
quickly and does not take a connection away from the traffic it is checking.
The real database path is covered by tests/test_database_integration.py.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.db.session import probe_database
from app.main import app


def _override(reachable: bool, vector: bool):
    def _dependency() -> tuple[bool, bool]:
        return reachable, vector

    return _dependency


async def test_ready_when_database_and_pgvector_are_available(client: AsyncClient) -> None:
    app.dependency_overrides[probe_database] = _override(True, True)
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
    app.dependency_overrides[probe_database] = _override(True, False)
    try:
        response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["pgvector"] is False


async def test_ready_returns_503_when_database_is_down(client: AsyncClient) -> None:
    app.dependency_overrides[probe_database] = _override(False, False)
    try:
        response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "database_unavailable"
    assert error["request_id"]
