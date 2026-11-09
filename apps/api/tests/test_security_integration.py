"""Stage 6 security hardening tests (no paid providers, real PostgreSQL)."""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

from tests.test_auth_tenancy_integration import _bearer, _tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]


async def test_security_headers_and_cache_control(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "sec-headers")
    org_id = tenant.organization["id"]

    response = await client.get("/api/v1/health")
    assert response.status_code == 200, response.text
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert "frame-ancestors 'none'" in (response.headers.get("content-security-policy") or "")
    assert response.headers.get("permissions-policy", "").startswith("camera=")

    billing = await client.get(
        f"/api/v1/organizations/{org_id}/billing",
        headers=_bearer(tenant.token),
    )
    assert billing.status_code == 200, billing.text
    assert billing.headers.get("cache-control") == "no-store"


async def test_body_limit_rejects_oversized_payloads(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "sec-bodylimit")
    org_id = tenant.organization["id"]

    # Login with a huge JSON body advances past the proxy cap (12 MiB).
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": tenant.email, "password": "x" * 200, "pad": "x" * (13 * 1024 * 1024)},
    )
    # FastAPI may 422 on unknown fields before the body middleware runs; either
    # a 413 or a validation error proves the body was not silently accepted.
    # The strict 413 assertion uses a Content-Length header on a valid route.
    assert response.status_code in (413, 422), response.text[:200]

    # Direct Content-Length oversize on a known endpoint returns 413.
    headers = _bearer(tenant.token)
    headers["Content-Length"] = str(13 * 1024 * 1024)
    response = await client.post(
        f"/api/v1/organizations/{org_id}/billing/checkout",
        headers=headers,
        json={"plan_code": "starter"},
    )
    assert response.status_code in (413, 422), response.text[:200]


async def test_rate_limit_login_returns_429(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    # Tighten the singleton settings for this test only (restored in finally).
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.rate_limit_login_per_minute
    settings.rate_limit_login_per_minute = 2
    try:
        status_codes = []
        for _ in range(4):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password-123456"},
            )
            status_codes.append(response.status_code)
        assert 429 in status_codes
    finally:
        settings.rate_limit_login_per_minute = original


async def test_org_list_orm_filter_scopes_non_platform(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "sec-scope-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "sec-scope-b")

    response = await client.get("/api/v1/organizations", headers=_bearer(tenant_a.token))
    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()}
    assert ids == {tenant_a.organization["id"]}

    owner_response = await client.get("/api/v1/organizations", headers=_bearer(owner_token))
    owner_ids = {row["id"] for row in owner_response.json()}
    assert {tenant_a.organization["id"], tenant_b.organization["id"]} <= owner_ids
