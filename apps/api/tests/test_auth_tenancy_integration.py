"""Stage 1 end-to-end and isolation tests against a real PostgreSQL database.

Run with::

    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration -v

Every test creates its own uniquely named organizations and users. A session
fixture removes them afterwards, so the development database stays clean.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_token
from app.main import app
from app.models.membership import Membership

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 with PostgreSQL running",
    ),
]

PASSWORD = "correct-horse-battery-staple"


@asynccontextmanager
async def _engine(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(
    client: AsyncClient,
    email: str,
    password: str = PASSWORD,
    *,
    kind: str = "extension",
    org_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "email": email,
        "password": password,
        "kind": kind,
    }
    if org_id is not None:
        payload["org_id"] = org_id
    response = await client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _create_org(
    client: AsyncClient, owner_token: str, slug: str, name: str | None = None
) -> dict:
    response = await client.post(
        "/api/v1/organizations",
        headers=_bearer(owner_token),
        json={"name": name or slug, "slug": slug, "country": "RW"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _invite(
    client: AsyncClient,
    token: str,
    org_id: str,
    email: str,
    role: str = "client_admin",
) -> dict:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_bearer(token),
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(token: str, email: str, full_name: str = "Test Person") -> int:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "full_name": full_name, "password": PASSWORD},
        )
        return response.status_code


@dataclass(slots=True)
class Tenant:
    organization: dict
    token: str
    email: str
    user_id: str


async def _tenant(
    client: AsyncClient,
    owner_token: str,
    prefix: str,
    label: str,
    *,
    role: str = "client_admin",
    password: str = PASSWORD,
) -> Tenant:
    unique = uuid.uuid4().hex[:6]
    slug = f"{prefix}-{label}-{unique}"
    organization = await _create_org(client, owner_token, slug, f"Org {label} {unique}")
    email = f"{slug}@example.com"
    invitation = await _invite(client, owner_token, organization["id"], email, role)
    assert await _accept(invitation["token"], email) == 200
    login = await _login(client, email, password)
    return Tenant(
        organization=organization,
        token=login["access_token"],
        email=email,
        user_id=login["user"]["id"],
    )


async def test_owner_onboards_client_and_invitation_is_accepted(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "onboard")

    me = await client.get("/api/v1/auth/me", headers=_bearer(tenant.token))
    assert me.status_code == 200
    assert me.json()["active_organization"]["id"] == tenant.organization["id"]

    members = await client.get(
        f"/api/v1/organizations/{tenant.organization['id']}/members",
        headers=_bearer(tenant.token),
    )
    assert members.status_code == 200
    assert [row["email"] for row in members.json()] == [tenant.email]

    audit = await client.get(
        f"/api/v1/organizations/{tenant.organization['id']}/audit",
        headers=_bearer(owner_token),
    )
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()}
    assert {"organization.created", "invitation.created", "invitation.accepted"} <= actions

    listed = await client.get("/api/v1/organizations", headers=_bearer(tenant.token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [tenant.organization["id"]]


async def test_cross_tenant_api_access_is_denied(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "api-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "api-b")
    org_b = tenant_b.organization["id"]
    headers = _bearer(tenant_a.token)

    cases = [
        ("get", f"/api/v1/organizations/{org_b}", None),
        ("get", f"/api/v1/organizations/{org_b}/members", None),
        ("get", f"/api/v1/organizations/{org_b}/invitations", None),
        ("get", f"/api/v1/organizations/{org_b}/audit", None),
        ("patch", f"/api/v1/organizations/{org_b}", {"name": "Hijacked"}),
        (
            "post",
            f"/api/v1/organizations/{org_b}/invitations",
            {"email": "attacker@example.com", "role": "client_admin"},
        ),
    ]
    for method, path, payload in cases:
        response = await client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 404, (path, response.status_code, response.text)

    own = await client.get(
        f"/api/v1/organizations/{tenant_a.organization['id']}/members",
        headers=headers,
    )
    assert own.status_code == 200

    all_orgs = await client.get("/api/v1/organizations", headers=_bearer(owner_token))
    owner_ids = {row["id"] for row in all_orgs.json()}
    assert {tenant_a.organization["id"], org_b} <= owner_ids


async def test_raw_sql_rls_blocks_cross_tenant_access(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant_a = await _tenant(client, owner_token, test_prefix, "rls-a")
    tenant_b = await _tenant(client, owner_token, test_prefix, "rls-b")
    settings = get_settings()
    org_a = tenant_a.organization["id"]
    org_b = tenant_b.organization["id"]

    async with _engine(settings.database_url) as engine:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        "SELECT set_config('app.current_org', :org, true), "
                        "set_config('app.current_user', :usr, true)"
                    ),
                    {"org": org_a, "usr": tenant_a.user_id},
                )
                assert (
                    await connection.scalar(text("SELECT count(*) FROM organizations"))
                ) == 1
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM memberships WHERE org_id = :org"),
                        {"org": org_b},
                    )
                ) == 0
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM audit_logs WHERE org_id = :org"),
                        {"org": org_b},
                    )
                ) == 0
                updated = await connection.execute(
                    text("UPDATE organizations SET name = 'Hijacked' WHERE id = :org"),
                    {"org": org_b},
                )
                assert updated.rowcount == 0

            # SET LOCAL is transaction-scoped: a fresh transaction has no tenant.
            async with connection.begin():
                assert (
                    await connection.scalar(text("SELECT count(*) FROM organizations"))
                ) == 0
                assert (
                    await connection.scalar(text("SELECT count(*) FROM memberships"))
                ) == 0

    # A cross-tenant INSERT must be rejected by the WITH CHECK clause.
    async with _engine(settings.database_url) as engine:
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT set_config('app.current_org', :org, true), "
                        "set_config('app.current_user', :usr, true)"
                    ),
                    {"org": org_a, "usr": tenant_a.user_id},
                )
                await connection.execute(
                    insert(Membership.__table__).values(
                        id=uuid.uuid4(),
                        org_id=uuid.UUID(org_b),
                        user_id=uuid.UUID(tenant_a.user_id),
                        role="client_admin",
                        status="active",
                    )
                )


async def test_cookie_authentication_requires_csrf(
    owner_credentials: dict[str, str],
    test_prefix: str,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as web:
        login = await web.post(
            "/api/v1/auth/login",
            json={
                "email": owner_credentials["email"],
                "password": owner_credentials["password"],
                "kind": "web",
            },
        )
        assert login.status_code == 200
        assert web.cookies.get("wm_session")
        csrf = web.cookies.get("wm_csrf")
        assert csrf

        slug = f"{test_prefix}-csrf-{uuid.uuid4().hex[:6]}"
        body = {"name": slug, "slug": slug, "country": "RW"}

        blocked = await web.post("/api/v1/organizations", json=body)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "csrf_failed"

        allowed = await web.post(
            "/api/v1/organizations",
            json=body,
            headers={"X-CSRF-Token": csrf},
        )
        assert allowed.status_code == 201, allowed.text

        blocked_logout = await web.post("/api/v1/auth/logout")
        assert blocked_logout.status_code == 403
        allowed_logout = await web.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
        )
        assert allowed_logout.status_code == 200
        assert not web.cookies.get("wm_session")

        assert (await web.get("/api/v1/auth/me")).status_code == 401


async def test_invitation_is_single_use_and_can_be_revoked_or_expired(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    unique = uuid.uuid4().hex[:6]
    org = await _create_org(client, owner_token, f"{test_prefix}-inv-{unique}")

    first = await _invite(
        client, owner_token, org["id"], f"{test_prefix}-one-{unique}@example.com"
    )
    assert await _accept(first["token"], first["email"]) == 200
    assert await _accept(first["token"], first["email"]) == 400

    revoked = await _invite(
        client, owner_token, org["id"], f"{test_prefix}-two-{unique}@example.com"
    )
    revoke = await client.delete(
        f"/api/v1/organizations/{org['id']}/invitations/{revoked['id']}",
        headers=_bearer(owner_token),
    )
    assert revoke.status_code == 204
    assert await _accept(revoked["token"], revoked["email"]) == 400

    expired = await _invite(
        client, owner_token, org["id"], f"{test_prefix}-three-{unique}@example.com"
    )
    settings = get_settings()
    async with _engine(settings.database_migration_url) as engine:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE invitations SET expires_at = now() - interval '1 day' "
                    "WHERE token_hash = :hash"
                ),
                {"hash": hash_token(expired["token"])},
            )
    assert await _accept(expired["token"], expired["email"]) == 400


async def test_existing_user_must_prove_password_to_join_second_client(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "existing-a")
    unique = uuid.uuid4().hex[:6]
    org_b = await _create_org(client, owner_token, f"{test_prefix}-existing-b-{unique}")
    invitation = await _invite(client, owner_token, org_b["id"], tenant.email)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as other:
        wrong = await other.post(
            "/api/v1/invitations/accept",
            json={
                "token": invitation["token"],
                "password": "a-different-long-password",
            },
        )
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "invalid_credentials"

        right = await other.post(
            "/api/v1/invitations/accept",
            json={"token": invitation["token"], "password": PASSWORD},
        )
        assert right.status_code == 200, right.text

    login = await _login(client, tenant.email, org_id=org_b["id"])
    assert login["active_organization"]["id"] == org_b["id"]
    assert {row["organization"]["id"] for row in login["memberships"]} >= {
        tenant.organization["id"],
        org_b["id"],
    }


async def test_suspended_membership_and_last_admin_guards(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    admin = await _tenant(client, owner_token, test_prefix, "guard-a")
    org_id = admin.organization["id"]

    unique = uuid.uuid4().hex[:6]
    member_email = f"{test_prefix}-member-{unique}@example.com"
    invitation = await _invite(
        client, owner_token, org_id, member_email, role="client_member"
    )
    assert await _accept(invitation["token"], member_email) == 200
    member = await _login(client, member_email)
    member_id = member["user"]["id"]

    suspended = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_id}",
        headers=_bearer(admin.token),
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200

    blocked = await client.get(
        f"/api/v1/organizations/{org_id}/members", headers=_bearer(member["access_token"])
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "membership_inactive"

    still_me = await client.get(
        "/api/v1/auth/me", headers=_bearer(member["access_token"])
    )
    assert still_me.status_code == 200
    membership = next(
        row
        for row in still_me.json()["memberships"]
        if row["organization"]["id"] == org_id
    )
    assert membership["status"] == "suspended"
    assert membership["role"] == "client_member"

    reactivated = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_id}",
        headers=_bearer(admin.token),
        json={"status": "active"},
    )
    assert reactivated.status_code == 200
    assert (
        await client.get(
            f"/api/v1/organizations/{org_id}/members",
            headers=_bearer(member["access_token"]),
        )
    ).status_code == 403  # client_member cannot list members
    assert (
        await client.get(
            f"/api/v1/organizations/{org_id}", headers=_bearer(member["access_token"])
        )
    ).status_code == 200

    self_change = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{admin.user_id}",
        headers=_bearer(admin.token),
        json={"role": "client_member"},
    )
    assert self_change.status_code == 403
    assert self_change.json()["error"]["code"] == "cannot_modify_self"

    last_admin = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{admin.user_id}",
        headers=_bearer(owner_token),
        json={"role": "client_member"},
    )
    assert last_admin.status_code == 409
    assert last_admin.json()["error"]["code"] == "last_admin"


async def test_failed_login_is_audited_without_leaking_account_state(
    client: AsyncClient,
    test_prefix: str,
) -> None:
    email = f"{test_prefix}-nobody-{uuid.uuid4().hex[:6]}@example.com"
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "definitely-wrong-password"},
    )
    missing = await client.post(
        "/api/v1/auth/login",
        json={
            "email": f"{test_prefix}-missing-{uuid.uuid4().hex[:6]}@example.com",
            "password": "definitely-wrong-password",
        },
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["code"] == missing.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]

    settings = get_settings()
    async with _engine(settings.database_migration_url) as engine:
        async with engine.connect() as connection:
            count = await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE action = 'auth.login_failed' "
                    "AND metadata->>'email' = :email"
                ),
                {"email": email},
            )
    assert count == 1


async def test_duplicate_slug_and_duplicate_member_are_rejected(
    client: AsyncClient,
    owner_token: str,
    test_prefix: str,
) -> None:
    tenant = await _tenant(client, owner_token, test_prefix, "dup")
    slug = tenant.organization["slug"]

    duplicate = await client.post(
        "/api/v1/organizations",
        headers=_bearer(owner_token),
        json={"name": "Duplicate", "slug": slug, "country": "RW"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "slug_taken"

    again = await client.post(
        f"/api/v1/organizations/{tenant.organization['id']}/invitations",
        headers=_bearer(owner_token),
        json={"email": tenant.email, "role": "client_member"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_member"
