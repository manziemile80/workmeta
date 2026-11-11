"""Provision the WorkMeta staging cloud resources (Neon + Render) on free tiers.

Usage (from the repo root, with the API venv's Python):

    apps/api/.venv/Scripts/python tools/scripts/provision_cloud.py neon
    apps/api/.venv/Scripts/python tools/scripts/provision_cloud.py render
    apps/api/.venv/Scripts/python tools/scripts/provision_cloud.py all

Credentials are read from the process environment or, if present, from
``var/cloud.env`` (gitignored):

    RENDER_API_KEY=rnd_...
    NEON_API_KEY=...
    NEON_ORG_ID=org-...        # optional, auto-detected
    RENDER_OWNER_ID=own-...    # optional, auto-detected
    NEON_REGION=aws-us-east-2  # optional
    RENDER_REGION=ohio         # optional

Generated database passwords and connection URLs are written to
``var/neon.env`` / ``var/neon.json`` and never printed. Secrets are never
committed (``var/`` is gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
CLOUD_ENV = VAR / "cloud.env"
SECRETS_ENV = VAR / "deploy-secrets.env"
NEON_ENV = VAR / "neon.env"
NEON_JSON = VAR / "neon.json"
RENDER_JSON = VAR / "render.json"
API_VENV_PY = ROOT / "apps" / "api" / ".venv" / "Scripts" / "python.exe"
API_DIR = ROOT / "apps" / "api"

NEON_API = "https://console.neon.tech/api/v2"
RENDER_API = "https://api.render.com/v1"
VERCEL_FRONTEND = "https://workmeta.vercel.app"
REPO_URL = "https://github.com/manziemile80/workmeta"
APP_ROLE = "wm_app"
PROJECT_NAME = "workmeta"
SERVICE_NAME = "workmeta-api"

FIXED_RENDER_ENV = {
    "APP_ENV": "development",
    "APP_NAME": "WorkMeta AI API",
    "API_V1_PREFIX": "/api/v1",
    "LOG_LEVEL": "INFO",
    "MAX_REQUEST_BODY_BYTES": "12582912",
    "SESSION_TTL_DAYS": "7",
    "SESSION_IDLE_HOURS": "8",
    "INVITATION_TTL_DAYS": "7",
    "PASSWORD_RESET_TTL_MINUTES": "60",
    "BACKUP_RETENTION_DAYS": "30",
    "AI_PROVIDER": "openai",
    "AI_BASE_URL": "https://api.openai.com/v1",
    "AI_CHAT_MODEL": "gpt-4.1-mini",
    "AI_EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_DIMENSIONS": "1536",
    "AI_TIMEOUT_SECONDS": "60",
    "AI_TEMPERATURE": "0",
    "AI_MAX_RETRIES": "2",
    "CHAT_MAX_OUTPUT_TOKENS": "800",
    "BILLING_PROVIDER": "mock",
    "BILLING_CURRENCY": "RWF",
    "BILLING_TRIAL_DAYS": "14",
    "BILLING_PAST_DUE_GRACE_DAYS": "7",
    "FLUTTERWAVE_BASE_URL": "https://api.flutterwave.com/v3",
    "PAYSTACK_BASE_URL": "https://api.paystack.co",
    "STORAGE_BACKEND": "local",
    "STORAGE_DIR": "/app/var/storage",
    "RATE_LIMIT_ENABLED": "true",
    "RATE_LIMIT_LOGIN_PER_MINUTE": "60",
    "RATE_LIMIT_INVITE_ACCEPT_PER_MINUTE": "60",
    "RATE_LIMIT_WEBHOOK_PER_MINUTE": "300",
    "RATE_LIMIT_CHAT_PER_MINUTE": "300",
}


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding the real environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Missing {name}. Put it in var/cloud.env or export it, then retry."
        )
    return value


def api(
    method: str,
    url: str,
    token: str,
    body: dict | list | None = None,
    timeout: int = 60,
) -> object:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise SystemExit(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc


def unwrap_list(payload: object, key: str) -> list[dict]:
    items = payload if isinstance(payload, list) else []
    result = []
    for item in items:
        if isinstance(item, dict) and key in item:
            result.append(item[key])
        elif isinstance(item, dict):
            result.append(item)
    return result


def mask_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or "?"
    return f"{parsed.scheme}://{parsed.username or '?'}@{host}/{parsed.path.lstrip('/')}"


def write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Neon
# ---------------------------------------------------------------------------

def neon_pick_org(token: str) -> str:
    explicit = os.environ.get("NEON_ORG_ID", "").strip()
    if explicit:
        return explicit
    payload = api("GET", f"{NEON_API}/organizations", token)
    orgs = unwrap_list(payload, "organizations")
    if not orgs:
        raise SystemExit(
            "No Neon organization found for this API key. Pass NEON_ORG_ID."
        )
    return orgs[0]["id"]


def neon_project_id(token: str) -> str | None:
    explicit = os.environ.get("NEON_PROJECT_ID", "").strip()
    if explicit:
        return explicit
    payload = api("GET", f"{NEON_API}/projects", token)
    projects = unwrap_list(payload, "projects")
    for project in projects:
        if project.get("name") == PROJECT_NAME:
            return project["id"]
    return None


def neon_wait_ready(token: str, project_id: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    terminal = {"finished", "skipped", "cancelled"}
    failed = {"failed"}
    while time.time() < deadline:
        payload = api("GET", f"{NEON_API}/projects/{project_id}/operations", token)
        operations = unwrap_list(payload, "operations")
        if any(op.get("status") in failed for op in operations):
            raise SystemExit("A Neon project operation failed; inspect the console.")
        pending = [op for op in operations if op.get("status") not in terminal]
        if not pending:
            return
        time.sleep(3)
    raise SystemExit("Neon project operations did not finish in time.")


def neon_create_or_reuse() -> dict:
    owner_hint = os.environ.get("NEON_OWNER_URL", "").strip()
    token = os.environ.get("NEON_API_KEY", "").strip()

    if owner_hint:
        # Owner-URL mode: no API key needed. The full Postgres URL already
        # contains the password; extensions and the wm_app role are prepared
        # with it, and the password used for the runtime role is generated.
        parsed = urllib.parse.urlsplit(owner_hint)
        role = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        database = parsed.path.lstrip("/")
        host = parsed.hostname or ""
        if not all([role, password, database, host]):
            raise SystemExit(
                "NEON_OWNER_URL must be a full postgresql://user:password@host/db URL."
            )
        existing = os.environ.get("NEON_PROJECT_ID", "") or f"owner-url:{host}"
        print(f"Neon: using existing database {database} on {host}")
    else:
        token = require("NEON_API_KEY")
        existing = neon_project_id(token)
        if existing:
            raise SystemExit(
                f"Neon project {PROJECT_NAME} already exists ({existing}) and the API "
                "cannot return its owner password. Set NEON_OWNER_URL to its owner "
                "connection string in var/cloud.env and rerun."
            )
        org_id = neon_pick_org(token)
        body = {
            "project": {
                "name": PROJECT_NAME,
                "region_id": os.environ.get("NEON_REGION", "aws-us-east-2"),
                "pg_version": 17,
                "org_id": org_id,
            }
        }
        print(f"Neon: creating project {PROJECT_NAME} in {body['project']['region_id']} ...")
        project_payload = api("POST", f"{NEON_API}/projects", token, body)
        existing = project_payload["project"]["id"]
        neon_wait_ready(token, existing)

        role = None
        database = None
        password = None
        host = None
        if isinstance(project_payload, dict):
            roles = project_payload.get("roles") or []
            databases = project_payload.get("databases") or []
            endpoints = project_payload.get("endpoints") or []
            uris = project_payload.get("connection_uris") or []
            if uris:
                params = uris[0].get("connection_parameters", {})
                role = params.get("role")
                database = params.get("database")
                password = params.get("password")
                host = params.get("host")
            if roles and not password:
                role = role or roles[0].get("name")
                password = password or roles[0].get("password")
            if databases and not database:
                database = database or databases[0].get("name")
            if endpoints and not host:
                host = endpoints[0].get("host")

        if not all([role, database, password, host]):
            raise SystemExit(
                "Neon did not return a usable connection URI (role/password/host). "
                "Create the project in the Neon console and set NEON_OWNER_URL, then rerun."
            )

    owner_url = (
        f"postgresql://{urllib.parse.quote(role)}:{urllib.parse.quote(password)}"
        f"@{host}/{database}?sslmode=require"
    )

    app_password = secrets.token_urlsafe(32)
    setup_neon_sql(owner_url, database, app_password)

    app_host = host
    runtime_url = (
        f"postgresql+asyncpg://{APP_ROLE}:{urllib.parse.quote(app_password)}"
        f"@{app_host}/{database}?sslmode=require"
    )
    migration_url = (
        f"postgresql+asyncpg://{urllib.parse.quote(role)}:{urllib.parse.quote(password)}"
        f"@{host}/{database}?sslmode=require"
    )

    write_env(
        NEON_ENV,
        {
            "DATABASE_URL": runtime_url,
            "DATABASE_MIGRATION_URL": migration_url,
        },
    )
    NEON_JSON.write_text(
        json.dumps(
            {
                "project_id": existing,
                "host": host,
                "database": database,
                "owner_role": role,
                "app_role": APP_ROLE,
                "runtime_url": mask_url(runtime_url),
                "migration_url": mask_url(migration_url),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    secrets_values = {}
    if SECRETS_ENV.exists():
        for raw in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                key, value = raw.split("=", 1)
                secrets_values[key.strip()] = value.strip()
    secrets_values["WM_APP_DB_PASSWORD"] = app_password
    secrets_values.setdefault("SESSION_SECRET", secrets.token_urlsafe(48))
    secrets_values.setdefault("BILLING_MOCK_WEBHOOK_SECRET", secrets.token_urlsafe(32))
    secrets_values.setdefault("SEED_OWNER_EMAIL", "owner@workmeta.ai")
    secrets_values.setdefault("SEED_OWNER_PASSWORD", secrets.token_urlsafe(18))
    write_env(SECRETS_ENV, secrets_values)

    print(f"Neon: ready ({existing}, {database} on {host})")
    print(f"Neon: runtime role {APP_ROLE}, extensions vector+citext ensured")
    print(f"Neon: connection URLs written to {NEON_ENV.relative_to(ROOT)}")
    return {"project_id": existing, "host": host, "database": database}


def setup_neon_sql(owner_url: str, database: str, app_password: str) -> None:
    import asyncio

    import asyncpg

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise SystemExit(f"Unsafe database name from Neon: {database!r}")

    parsed = urllib.parse.urlsplit(owner_url)
    context = ssl.create_default_context()

    async def run() -> None:
        connection = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            database=database,
            ssl=context,
        )
        try:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await connection.execute("CREATE EXTENSION IF NOT EXISTS citext")
            exists = await connection.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE
            )
            literal_password = app_password.replace("'", "''")
            if not exists:
                await connection.execute(
                    f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{literal_password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                )
            else:
                await connection.execute(
                    f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{literal_password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                )
            await connection.execute(
                f'GRANT CONNECT ON DATABASE "{database}" TO {APP_ROLE}'
            )
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        finally:
            await connection.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

RENDER_ENV_KEYS = {
    "DATABASE_URL",
    "DATABASE_MIGRATION_URL",
    "SESSION_SECRET",
    "BILLING_MOCK_WEBHOOK_SECRET",
    "CORS_ORIGINS",
    "ALLOWED_HOSTS",
    "BILLING_CHECKOUT_CALLBACK_URL",
    "AI_API_KEY",
}


def load_secrets() -> dict[str, str]:
    values: dict[str, str] = {}
    if SECRETS_ENV.exists():
        for raw in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                key, value = raw.split("=", 1)
                values[key.strip()] = value.strip()
    for key in list(values):
        if key.startswith("SEED_") or key.startswith("WM_"):
            continue
        os.environ.setdefault(key, values[key])
    return values


def render_owner(token: str) -> str:
    explicit = os.environ.get("RENDER_OWNER_ID", "").strip()
    if explicit:
        return explicit
    owners = unwrap_list(api("GET", f"{RENDER_API}/owners", token), "owner")
    if not owners:
        raise SystemExit("No Render workspace found for this API key.")
    return owners[0]["id"]


def render_find_service(token: str) -> dict | None:
    payload = api("GET", f"{RENDER_API}/services?limit=100", token)
    services = unwrap_list(payload, "service")
    for service in services:
        if service.get("name") == SERVICE_NAME:
            return service
    return None


def render_env_vars(host: str) -> list[dict[str, str]]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    migration_url = os.environ.get("DATABASE_MIGRATION_URL", "").strip()
    if not database_url or not migration_url:
        raise SystemExit(
            "Neon URLs are missing. Run the `neon` step first (writes var/neon.env)."
        )
    merged = dict(FIXED_RENDER_ENV)
    merged.update(
        {
            "DATABASE_URL": database_url,
            "DATABASE_MIGRATION_URL": migration_url,
            "SESSION_SECRET": os.environ.get("SESSION_SECRET", ""),
            "BILLING_MOCK_WEBHOOK_SECRET": os.environ.get(
                "BILLING_MOCK_WEBHOOK_SECRET", ""
            ),
            "AI_API_KEY": os.environ.get("AI_API_KEY", ""),
            "CORS_ORIGINS": VERCEL_FRONTEND,
            "ALLOWED_HOSTS": f"{host},{VERCEL_FRONTEND.replace('https://', '')}",
            "BILLING_CHECKOUT_CALLBACK_URL": f"{VERCEL_FRONTEND}/billing/checkout",
        }
    )
    missing = [
        key
        for key in RENDER_ENV_KEYS
        if key != "AI_API_KEY" and not merged.get(key)
    ]
    if missing:
        raise SystemExit(f"Missing required Render env values: {sorted(missing)}")
    return [{"key": key, "value": value} for key, value in merged.items()]


def render_wait_deploy(token: str, service_id: str, timeout: int = 1200) -> None:
    deadline = time.time() + timeout
    last = None
    failed = {"build_failed", "pre_deploy_failed", "canceled", "deactivated"}
    while time.time() < deadline:
        payload = api(
            "GET", f"{RENDER_API}/services/{service_id}/deploys?limit=1", token
        )
        deploys = unwrap_list(payload, "deploy")
        status = deploys[0].get("status") if deploys else "pending"
        if status != last:
            print(f"Render: deploy status={status}")
            last = status
        if status == "live":
            return
        if status in failed:
            raise SystemExit(f"Render deploy failed with status {status}")
        time.sleep(10)
    raise SystemExit("Render deploy did not become live in time.")


def render_create_or_reuse() -> dict:
    token = require("RENDER_API_KEY")
    service = render_find_service(token)
    if service:
        service_id = service["id"]
        host = (service.get("serviceDetails") or {}).get("url", "").replace(
            "https://", ""
        )
        print(f"Render: reusing service {SERVICE_NAME} ({service_id}, {host})")
        if not host:
            host = f"{SERVICE_NAME}.onrender.com"
        api(
            "PUT",
            f"{RENDER_API}/services/{service_id}/env-vars",
            token,
            render_env_vars(host),
        )
        print("Render: environment variables updated")
        render_wait_deploy(token, service_id)
    else:
        owner_id = render_owner(token)
        body = {
            "type": "web_service",
            "name": SERVICE_NAME,
            "ownerId": owner_id,
            "repo": REPO_URL,
            "branch": "main",
            "autoDeployTrigger": "off",
            "serviceDetails": {
                "runtime": "docker",
                "plan": "free",
                "region": os.environ.get("RENDER_REGION", "ohio"),
                "numInstances": 1,
                "healthCheckPath": "/api/v1/health",
                "envSpecificDetails": {
                    "dockerfilePath": "./apps/api/Dockerfile",
                    "dockerContext": ".",
                },
            },
            "envVars": render_env_vars("*"),
        }
        print(f"Render: creating service {SERVICE_NAME} ...")
        created = api("POST", f"{RENDER_API}/services", token, body)
        service = created.get("service", created)
        service_id = service["id"]
        service_details = service.get("serviceDetails") or {}
        host = (service_details.get("url") or "").replace("https://", "")
        if not host:
            # Fetch until the URL is assigned.
            for _ in range(12):
                service = api("GET", f"{RENDER_API}/services/{service_id}", token)
                service_details = service.get("serviceDetails") or {}
                host = (service_details.get("url") or "").replace("https://", "")
                if host:
                    break
                time.sleep(5)
        if not host:
            raise SystemExit("Render did not assign a service URL yet; inspect the dashboard.")
        api(
            "PUT",
            f"{RENDER_API}/services/{service_id}/env-vars",
            token,
            render_env_vars(host),
        )
        print(f"Render: service created, URL {host}. Waiting for deploy ...")
        render_wait_deploy(token, service_id)

    url = f"https://{host}"
    RENDER_JSON.write_text(
        json.dumps({"service_id": service_id, "host": host, "url": url}, indent=2),
        encoding="utf-8",
    )
    print(f"Render: live at {url}")
    return {"service_id": service_id, "host": host, "url": url}


# ---------------------------------------------------------------------------
# Migrate / seed
# ---------------------------------------------------------------------------

def run_migrations() -> None:
    if not NEON_ENV.exists():
        raise SystemExit("var/neon.env is missing; run the neon step first.")
    env = dict(os.environ)
    for raw in NEON_ENV.read_text(encoding="utf-8").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            env[key.strip()] = value.strip()
    print("Alembic: applying migrations ...")
    subprocess.run(
        [str(API_VENV_PY), "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        check=True,
    )
    subprocess.run(
        [str(API_VENV_PY), "-m", "alembic", "current"],
        cwd=API_DIR,
        env=env,
        check=True,
    )


def seed_owner() -> None:
    values = load_secrets()
    email = values.get("SEED_OWNER_EMAIL") or os.environ.get("SEED_OWNER_EMAIL")
    password = values.get("SEED_OWNER_PASSWORD") or os.environ.get("SEED_OWNER_PASSWORD")
    if not email or not password:
        raise SystemExit("SEED_OWNER_EMAIL/SEED_OWNER_PASSWORD are missing.")
    env = dict(os.environ)
    if NEON_ENV.exists():
        for raw in NEON_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in raw:
                key, value = raw.split("=", 1)
                env[key.strip()] = value.strip()
    print(f"Seed: ensuring platform owner {email} ...")
    subprocess.run(
        [
            str(API_VENV_PY),
            str(ROOT / "tools" / "scripts" / "seed_owner.py"),
            "--email",
            email,
            "--password",
            password,
            "--name",
            "Platform Owner",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step", choices=["neon", "render", "migrate", "seed", "all"], nargs="?"
    )
    args = parser.parse_args()
    step = args.step or "all"

    load_env_file(CLOUD_ENV)
    load_env_file(SECRETS_ENV)
    load_env_file(NEON_ENV)
    load_secrets()

    if step in ("neon", "all"):
        neon_create_or_reuse()
        load_env_file(NEON_ENV)
    if step in ("migrate", "all"):
        run_migrations()
    if step in ("seed", "all"):
        seed_owner()
    if step in ("render", "all"):
        if not NEON_ENV.exists():
            raise SystemExit("Run the neon step first (var/neon.env missing).")
        load_env_file(NEON_ENV)
        result = render_create_or_reuse()
        print("\nNext: update the Vercel proxy destination to:")
        print(f"  https://{result['host']}/api/:path*")

    return 0


if __name__ == "__main__":
    sys.exit(main())
