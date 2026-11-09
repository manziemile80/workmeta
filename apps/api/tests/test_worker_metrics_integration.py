"""Stage 6: billing worker and metrics endpoint tests."""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

from app.workers.billing import advance_one, sweep
from tests.test_auth_tenancy_integration import _bearer, _tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]


async def test_metrics_endpoint_renders_aggregates(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    # Make some traffic first so counters exist.
    await client.get("/api/v1/health", headers=_bearer(owner_token))
    response = await client.get("/api/v1/metrics")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "workmeta_uptime_seconds" in body
    assert "workmeta_http_requests_total" in body
    # Metrics must never contain tenant content (org names/slugs).
    assert test_prefix not in body


async def test_billing_worker_advance_is_idempotent(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "bill-worker")
    from uuid import UUID

    org_id = UUID(tenant.organization["id"])
    changed = await advance_one(org_id)
    assert changed is False
    result = await sweep()
    assert result["checked"] >= 1
    assert result["failures"] == 0
