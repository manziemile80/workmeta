"""Least-privilege verification of the runtime role `wm_app` against Neon.

Connects with the runtime credentials from ``var/neon.env`` and proves that the
role cannot reach migration state, cannot delete rows, cannot update the
append-only audit trail and cannot create objects. Credentials are never
printed.

Usage (from the repository root):

    apps/api/.venv/Scripts/python.exe tools/scripts/verify_privileges.py
"""

from __future__ import annotations

import asyncio
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


async def main() -> int:
    import asyncio as _asyncio
    import ssl

    import asyncpg

    env = read_env(ROOT / "var" / "neon.env")
    url = urllib.parse.urlsplit(env["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql"))
    failures: list[str] = []

    connection = await asyncpg.connect(
        host=url.hostname,
        port=url.port or 5432,
        user=urllib.parse.unquote(url.username or ""),
        password=urllib.parse.unquote(url.password or ""),
        database=(url.path or "/workmeta").lstrip("/"),
        ssl=ssl.create_default_context(),
        timeout=15,
    )
    try:
        who = await connection.fetchrow(
            "select current_user as usr, "
            "(select rolsuper from pg_roles where rolname = current_user) as super, "
            "(select rolbypassrls from pg_roles where rolname = current_user) as bypass, "
            "(select rolcreatedb from pg_roles where rolname = current_user) as createdb"
        )
        print(
            f"runtime_role={who['usr']} super={who['super']} "
            f"bypassrls={who['bypass']} createdb={who['createdb']}"
        )
        for flag in ("super", "bypass", "createdb"):
            if who[flag]:
                failures.append(f"runtime role has {flag}=true")

        try:
            await connection.fetchval("select version_num from alembic_version")
            failures.append("runtime role can read alembic_version")
            print("alembic_version_read: ALLOWED (unexpected)")
        except Exception as exc:  # noqa: BLE001
            print(f"alembic_version_read: DENIED ({type(exc).__name__})")

        try:
            await connection.execute("delete from users where false")
            failures.append("runtime role can delete from users")
            print("delete_users: ALLOWED (unexpected)")
        except Exception as exc:  # noqa: BLE001
            print(f"delete_users: DENIED ({type(exc).__name__})")

        try:
            await connection.execute("update audit_logs set action = 'x' where false")
            failures.append("runtime role can update audit_logs")
            print("update_audit_logs: ALLOWED (unexpected)")
        except Exception as exc:  # noqa: BLE001
            print(f"update_audit_logs: DENIED ({type(exc).__name__})")

        rows = await connection.fetchval("select count(*) from users")
        # `users` and `sessions` are intentionally not RLS-protected: the
        # authentication flow must read them before a tenant context exists
        # (documented in migration 0007). The runtime therefore sees the user
        # directory - accepted design, reported as a residual risk.
        print(f"users_visible_without_rls_context: {rows} (documented design)")

        try:
            await connection.execute("create table public.wm_probe(id int)")
            failures.append("runtime role can create tables")
            print("create_table: ALLOWED (unexpected)")
        except Exception as exc:  # noqa: BLE001
            print(f"create_table: DENIED ({type(exc).__name__})")
    finally:
        await connection.close()

    print()
    if failures:
        print("PRIVILEGE_FAILURES:")
        for item in failures:
            print(" -", item)
        return 1
    print("LEAST_PRIVILEGE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
