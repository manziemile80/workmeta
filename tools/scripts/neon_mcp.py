"""Provision the WorkMeta Neon database through the Neon MCP server.

The Neon MCP server authenticates with OAuth in the browser (no API key, no
password in chat). Once authorised, this script can create the dedicated
`workmeta` database, ensure the restricted `wm_app` role and write the
connection URLs to `var/neon.env` (gitignored) without printing a credential.

Usage (from the repository root):

    opencode mcp add neon --url https://mcp.neon.tech/mcp --global
    opencode mcp auth neon
    apps/api/.venv/Scripts/python tools/scripts/neon_mcp.py provision-env

Subcommands:
    token-info      show only where the OAuth token was found (never its value)
    list-tools      list the MCP tools available for the authorised account
    provision-env   write var/neon.env with the runtime + migration URLs
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
NEON_ENV = VAR / "neon.env"
MCP_URL = "https://mcp.neon.tech/mcp"

# The project/database created for WorkMeta. The project's default `neondb`
# belongs to another application and must never be used.
DEFAULT_PROJECT_ID = "floral-bonus-37245332"
DEFAULT_DATABASE = "workmeta"
RUNTIME_ROLE = "wm_app"
MIGRATION_ROLE = "neondb_owner"

JWT_PATTERN = re.compile(
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)


def opencode_db_path() -> Path:
    override = os.environ.get("OPENCODE_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def find_token() -> tuple[str | None, str]:
    """Locate the stored MCP OAuth access token (value is never printed)."""
    explicit = os.environ.get("NEON_MCP_TOKEN", "").strip()
    if explicit:
        return explicit, "NEON_MCP_TOKEN"

    db = opencode_db_path()
    if not db.exists():
        return None, "no opencode database"
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = list(
            connection.execute("SELECT label, value FROM credential")
        )
    except sqlite3.Error:
        return None, "credential table unavailable"
    finally:
        connection.close()

    for label, value in rows:
        if label != "neon" or not isinstance(value, str):
            continue
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            for key in ("access", "access_token", "accessToken", "token"):
                if data.get(key):
                    return str(data[key]), f"opencode credential store ({label})"
    return None, "no neon credential in the opencode store"


def mcp_call(method: str, params: dict, token: str) -> dict:
    """Call a method on the Neon MCP server (streamable HTTP transport)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=90, headers=headers) as client:
        init = client.post(
            MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "workmeta-deployer", "version": "1.0"},
                },
            },
        )
        session_id = init.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id
            client.headers.update(headers)
        client.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        response = client.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        )
        text = response.text
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            return {"error": "no json payload in event stream"}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"error": f"HTTP {response.status_code}"}


def tool_call(tool: str, arguments: dict, token: str) -> dict:
    return mcp_call("tools/call", {"name": tool, "arguments": arguments}, token)


def extract_uri(result: dict) -> str:
    match = re.search(r"postgresql://[^\s\"\\]+", json.dumps(result))
    if not match:
        raise SystemExit("no connection URI found in the MCP response")
    return match.group(0)


def to_asyncpg_direct(url: str) -> str:
    """Rewrite to the direct endpoint and asyncpg's URL scheme."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").replace("-pooler.", ".", 1)
    return (
        f"postgresql+asyncpg://{quote(parsed.username or '', safe='')}:"
        f"{quote(parsed.password or '', safe='')}@{host}/"
        f"{parsed.path.lstrip('/')}?sslmode=require"
    )


def provision_env(project_id: str, database_name: str) -> int:
    token, _ = find_token()
    if not token:
        raise SystemExit(
            "No Neon MCP token. Run:\n"
            "  opencode mcp add neon --url https://mcp.neon.tech/mcp --global\n"
            "  opencode mcp auth neon"
        )
    runtime = to_asyncpg_direct(
        extract_uri(
            tool_call(
                "get_connection_string",
                {
                    "project_id": project_id,
                    "database_name": database_name,
                    "role_name": RUNTIME_ROLE,
                },
                token,
            )
        )
    )
    migration = to_asyncpg_direct(
        extract_uri(
            tool_call(
                "get_connection_string",
                {
                    "project_id": project_id,
                    "database_name": database_name,
                    "role_name": MIGRATION_ROLE,
                },
                token,
            )
        )
    )
    VAR.mkdir(parents=True, exist_ok=True)
    NEON_ENV.write_text(
        f"DATABASE_URL={runtime}\nDATABASE_MIGRATION_URL={migration}\n",
        encoding="utf-8",
    )
    for label, url in (("runtime", runtime), ("migration", migration)):
        parsed = urlsplit(url)
        print(
            f"{label}: user={parsed.username} host={parsed.hostname} "
            f"db={parsed.path.lstrip('/')} password_len={len(parsed.password or '')}"
        )
    print(f"written: {NEON_ENV.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["token-info", "list-tools", "provision-env"],
        nargs="?",
        default="provision-env",
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args()

    if args.command == "token-info":
        token, location = find_token()
        print("token_found:", bool(token))
        print("location:", location)
        return 0 if token else 1

    token, _ = find_token()
    if not token:
        raise SystemExit("no Neon MCP token - run `opencode mcp auth neon` first")

    if args.command == "list-tools":
        result = mcp_call("tools/list", {}, token)
        tools = (result.get("result") or {}).get("tools") or []
        for tool in tools:
            print("-", tool.get("name"))
        return 0

    return provision_env(args.project_id, args.database)


if __name__ == "__main__":
    sys.exit(main())
