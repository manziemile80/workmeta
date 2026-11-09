"""Liveness and baseline HTTP behavior."""

from __future__ import annotations

from httpx import AsyncClient


async def test_root_returns_brand_and_links(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["tagline"] == "Your Business. Your AI. Your Way."
    assert body["health"] == "/api/v1/health"


async def test_health_is_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "WorkMeta AI API"
    assert body["version"] == "0.1.0"
    assert body["uptime_seconds"] >= 0


async def test_request_id_is_returned(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "test-123"})
    assert response.headers["X-Request-ID"] == "test-123"


async def test_invalid_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health", headers={"X-Request-ID": "bad id with spaces!"}
    )
    assert response.headers["X-Request-ID"] != "bad id with spaces!"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_security_headers_present(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
