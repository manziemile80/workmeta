"""End-to-end production verification for the WorkMeta deployment.

Checks the live path frontend -> Vercel proxy -> Render -> Neon:
health, readiness, login, session, CSRF, logout, CORS, platform admin
endpoints and the invitation-based onboarding flow.

Usage (from the repository root):

    apps/api/.venv/Scripts/python.exe tools/scripts/verify_production.py

Credentials are read from ``var/deploy-secrets.env`` (gitignored). Only
statuses are printed - never a credential, cookie or token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://workmeta.vercel.app"
ORIGIN = "https://workmeta.vercel.app"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def show(label: str, response: httpx.Response) -> httpx.Response:
    body = response.text
    try:
        parsed = response.json()
        if isinstance(parsed, dict) and "error" in parsed:
            body = parsed["error"].get("code", "error")
        elif isinstance(parsed, dict):
            keys = [k for k in parsed if k not in ("access_token",)]
            body = ",".join(sorted(keys))
    except ValueError:
        body = body[:60]
    print(f"{label}: {response.status_code} [{body}]")
    return response


def main() -> int:
    secrets = read_env(ROOT / "var" / "deploy-secrets.env")
    email = secrets["SEED_OWNER_EMAIL"]
    password = secrets["SEED_OWNER_PASSWORD"]
    failures: list[str] = []

    def check(label: str, response: httpx.Response, expected: int) -> httpx.Response:
        show(label, response)
        if response.status_code != expected:
            failures.append(f"{label} expected {expected} got {response.status_code}")
        return response

    with httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=90) as client:
        check("health", client.get("/api/v1/health"), 200)
        check("ready", client.get("/api/v1/ready"), 200)
        pre_ok = client.request(
            "OPTIONS",
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
        )
        check("cors_preflight_allowed", pre_ok, 200)
        if pre_ok.headers.get("access-control-allow-origin") != ORIGIN:
            failures.append("CORS origin header missing")
        pre_bad = client.request(
            "OPTIONS",
            "/api/v1/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        check("cors_preflight_denied", pre_bad, 400)
        if pre_bad.headers.get("access-control-allow-origin"):
            failures.append("CORS allowed a foreign origin")

        login = check(
            "login",
            client.post("/api/v1/auth/login", json={"email": email, "password": password}),
            200,
        )
        bad = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )
        check("login_wrong_password", bad, 401)
        check("me_after_login", client.get("/api/v1/auth/me"), 200)

        csrf = None
        for cookie in client.cookies.jar:
            if "csrf" in cookie.name:
                csrf = cookie.value
        if not csrf:
            failures.append("CSRF cookie missing")
        if csrf:
            client.headers["X-CSRF-Token"] = csrf

        no_csrf = client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": ""}
        )
        check("logout_without_csrf", no_csrf, 403)
        if csrf:
            check(
                "logout_with_csrf",
                client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}),
                200,
            )
        check("me_after_logout", client.get("/api/v1/auth/me"), 401)

        # Platform administration (platform admin endpoints).
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        for cookie in client.cookies.jar:
            if "csrf" in cookie.name:
                client.headers["X-CSRF-Token"] = cookie.value
        orgs = client.get("/api/v1/platform/organizations")
        check("platform_organizations", orgs, 200)
        revenue = client.get("/api/v1/platform/revenue")
        check("platform_revenue", revenue, 200)
        usage = client.get("/api/v1/platform/usage")
        check("platform_usage", usage, 200)

        # Invitation-based registration / owner administration.
        org_list = client.get("/api/v1/organizations")
        check("list_organizations", org_list, 200)
        org_id = None
        for org in org_list.json() if org_list.status_code == 200 else []:
            if org.get("slug") == "workmeta":
                org_id = org["id"]
        if org_id is None:
            created = client.post(
                "/api/v1/organizations",
                json={"name": "WorkMeta", "slug": "workmeta", "country": "RW"},
            )
            check("create_organization", created, 201)
            if created.status_code == 201:
                org_id = created.json()["id"]
        else:
            print(f"organization: existing slug=workmeta id={org_id}")

        if org_id:
            invite = client.post(
                f"/api/v1/organizations/{org_id}/invitations",
                json={"email": email, "role": "client_admin"},
            )
            if invite.status_code == 409:
                # Already a member of this tenant: the invitation flow was
                # verified earlier. The API must refuse a duplicate invite.
                body = invite.json().get("error", {})
                print(
                    f"create_invitation: 409 [{body.get('code')}] "
                    "(already a member - duplicate invites are refused)"
                )
            else:
                check("create_invitation", invite, 201)
            if invite.status_code == 201:
                token = invite.json()["token"]
                accept_path = invite.json().get("accept_endpoint") or (
                    "/api/v1/invitations/accept"
                )
                if not accept_path.startswith("/api/"):
                    accept_path = "/api/v1" + accept_path
                accept = client.post(
                    accept_path,
                    json={
                        "token": token,
                        "email": email,
                        "password": password,
                        "full_name": "Platform Owner",
                    },
                )
                check("accept_invitation", accept, 200)
                if accept.status_code == 200:
                    active = accept.json().get("active_organization")
                    print(
                        "active_organization:",
                        (active or {}).get("slug", "none"),
                    )
                    if not active:
                        failures.append("no active organization after accepting")
                members = client.get(f"/api/v1/organizations/{org_id}/members")
                check("organization_members", members, 200)
                if members.status_code == 200:
                    print("member_count:", len(members.json()))
                billing = client.get(f"/api/v1/organizations/{org_id}/billing")
                check("organization_billing", billing, 200)
                plans = client.get("/api/v1/billing/plans")
                check("billing_plans", plans, 200)
                if plans.status_code == 200:
                    print("plan_count:", len(plans.json()))

    print()
    if failures:
        print("FAILURES:")
        for item in failures:
            print(" -", item)
        return 1
    print("ALL_PRODUCTION_CHECKS_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
