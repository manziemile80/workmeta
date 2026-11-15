"""Live smoke test against a running API instance.

Exercises the operational and authentication paths end to end over HTTP, the
same way a browser or the Vercel proxy would:

    GET  /api/v1/health          liveness
    GET  /api/v1/ready           readiness (database + pgvector)
    GET  /docs                   OpenAPI browser
    POST /api/v1/auth/login      owner sign-in (cookie + bearer)
    GET  /api/v1/auth/me         current identity
    POST /api/v1/organizations   create two client organizations
    GET  /api/v1/organizations/{id}   cross-tenant read must not leak
    POST /api/v1/organizations   create without CSRF token must be rejected

Usage (from the repository root):

    python tools/scripts/smoke_local_api.py --base-url http://127.0.0.1:8000

The owner credentials come from SEED_OWNER_EMAIL / SEED_OWNER_PASSWORD in the
repository `.env`. Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(name)
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name} {detail}")
    return condition


async def _login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "kind": "extension"},
    )
    response.raise_for_status()
    return response.json()


async def _create_org(
    client: httpx.AsyncClient, token: str, slug: str, name: str
) -> dict:
    response = await client.post(
        "/api/v1/organizations",
        json={"name": name, "slug": slug, "country": "RW"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


async def _run(base_url: str) -> int:
    settings = get_settings()
    email = settings.seed_owner_email
    password = settings.seed_owner_password
    if not email or not password:
        print("SEED_OWNER_EMAIL / SEED_OWNER_PASSWORD are not configured.")
        return 2

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        health = await client.get("/api/v1/health")
        check(
            "GET /api/v1/health returns 200",
            health.status_code == 200,
            health.text[:200],
        )
        if health.status_code == 200:
            payload = health.json()
            check(
                "health reports status=ok with version and environment",
                payload.get("status") == "ok"
                and bool(payload.get("version"))
                and bool(payload.get("environment")),
                str(payload)[:200],
            )

        ready = await client.get("/api/v1/ready")
        check(
            "GET /api/v1/ready returns 200",
            ready.status_code == 200,
            ready.text[:200],
        )
        if ready.status_code == 200:
            payload = ready.json()
            check(
                "ready reports database=ok and pgvector=true",
                payload.get("database") == "ok" and payload.get("pgvector") is True,
                str(payload)[:200],
            )

        docs = await client.get("/docs")
        check(
            "GET /docs serves the OpenAPI browser",
            docs.status_code == 200 and "swagger" in docs.text.lower(),
            str(docs.status_code),
        )

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "kind": "extension"},
        )
        check(
            "POST /api/v1/auth/login returns 200 for the seeded owner",
            login.status_code == 200,
            login.text[:200],
        )
        if login.status_code != 200:
            return 1
        owner = login.json()
        token = owner.get("access_token", "")

        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        check(
            "GET /api/v1/auth/me returns the signed-in owner",
            me.status_code == 200 and me.json().get("user", {}).get("email") == email,
            me.text[:200],
        )

        unique = uuid.uuid4().hex[:6]
        slug_a = f"smoke-a-{unique}"
        slug_b = f"smoke-b-{unique}"
        org_a = await _create_org(client, token, slug_a, f"Smoke A {unique}")
        org_b = await _create_org(client, token, slug_b, f"Smoke B {unique}")
        check(
            "POST /api/v1/organizations creates a client organization",
            bool(org_a.get("id")) and bool(org_b.get("id")),
            str(org_a)[:200],
        )

        listed = await client.get(
            "/api/v1/organizations", headers={"Authorization": f"Bearer {token}"}
        )
        listed_ids = {row["id"] for row in listed.json()} if listed.status_code == 200 else set()
        check(
            "GET /api/v1/organizations lists both organizations for the owner",
            listed.status_code == 200
            and {org_a["id"], org_b["id"]} <= listed_ids,
            listed.text[:200],
        )

        cross = await client.get(
            f"/api/v1/organizations/{org_b['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        check(
            "owner can read an organization it administers",
            cross.status_code == 200,
            f"{cross.status_code} {cross.text[:160]}",
        )

        await _check_csrf_flow(base_url, email, password, unique)
        await _check_tenant_isolation(base_url, token, org_a, org_b, unique)

    print()
    print(f"passed: {len(PASSED)}  failed: {len(FAILED)}")
    if FAILED:
        for name in FAILED:
            print(f"  failed: {name}")
        return 1
    return 0


async def _check_csrf_flow(
    base_url: str, email: str, password: str, unique: str
) -> None:
    """A cookie session must send the double-submit token on writes."""
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as web:
        login = await web.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "kind": "web"},
        )
        if not check(
            "POST /api/v1/auth/login (kind=web) sets a session cookie",
            login.status_code == 200 and "wm_session" in web.cookies,
            login.text[:200],
        ):
            return

        no_csrf = await web.post(
            "/api/v1/organizations",
            json={"name": "No CSRF", "slug": f"smoke-nocsrf-{unique}", "country": "RW"},
        )
        check(
            "cookie write without a CSRF token is rejected with 403",
            no_csrf.status_code == 403,
            f"{no_csrf.status_code} {no_csrf.text[:160]}",
        )

        with_csrf = await web.post(
            "/api/v1/organizations",
            json={"name": "With CSRF", "slug": f"smoke-csrf-{unique}", "country": "RW"},
            headers={"X-CSRF-Token": web.cookies.get("wm_csrf", "")},
        )
        check(
            "cookie write with a CSRF token succeeds",
            with_csrf.status_code == 201,
            f"{with_csrf.status_code} {with_csrf.text[:160]}",
        )


async def _check_tenant_isolation(
    base_url: str,
    owner_token: str,
    org_a: dict,
    org_b: dict,
    unique: str,
) -> None:
    """A member of organization A must not be able to see organization B."""
    member_email = f"smoke-member-{unique}@example.com"
    member_password = "smoke-member-password-123"

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as owner:
        invite = await owner.post(
            f"/api/v1/organizations/{org_a['id']}/invitations",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"email": member_email, "role": "client_admin"},
        )
        if not check(
            "POST invitation invites a member into organization A",
            invite.status_code == 201,
            invite.text[:200],
        ):
            return

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as member:
        accept = await member.post(
            "/api/v1/invitations/accept",
            json={
                "token": invite.json()["token"],
                "full_name": "Smoke Member",
                "password": member_password,
            },
        )
        if not check(
            "POST /api/v1/invitations/accept creates the member account",
            accept.status_code == 200,
            accept.text[:200],
        ):
            return

        login = await member.post(
            "/api/v1/auth/login",
            json={
                "email": member_email,
                "password": member_password,
                "kind": "extension",
            },
        )
        if not check(
            "the invited member can sign in",
            login.status_code == 200,
            login.text[:200],
        ):
            return
        member_token = login.json().get("access_token", "")
        member_headers = {"Authorization": f"Bearer {member_token}"}

        own = await member.get(
            f"/api/v1/organizations/{org_a['id']}", headers=member_headers
        )
        check(
            "the member can read its own organization",
            own.status_code == 200,
            f"{own.status_code} {own.text[:160]}",
        )

        foreign = await member.get(
            f"/api/v1/organizations/{org_b['id']}", headers=member_headers
        )
        check(
            "the member cannot read another tenant's organization",
            foreign.status_code == 404,
            f"{foreign.status_code} {foreign.text[:160]}",
        )

        foreign_list = await member.get(
            "/api/v1/organizations", headers=member_headers
        )
        foreign_ids = (
            {row["id"] for row in foreign_list.json()}
            if foreign_list.status_code == 200
            else set()
        )
        check(
            "the member's organization list excludes other tenants",
            org_b["id"] not in foreign_ids and org_a["id"] in foreign_ids,
            foreign_list.text[:200],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="API base URL"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.base_url.rstrip("/")))


if __name__ == "__main__":
    raise SystemExit(main())
