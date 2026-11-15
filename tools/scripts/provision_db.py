"""Provision a managed PostgreSQL database for WorkMeta AI.

The local Docker stack provisions itself through
``infra/postgres/init/01-roles.sh``. A managed database (Neon, Railway,
Supabase, ...) needs the same setup done by hand, and doing it by hand is
error-prone: forgetting the restricted runtime role means the application
connects as the owner and every Row-Level Security policy is bypassed.

This script is idempotent. It:

  1. enables the required extensions (vector, citext, pgcrypto);
  2. creates the restricted runtime role (``wm_app`` by default) with
     LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS;
  3. grants CONNECT on the database and USAGE on the schema;
  4. verifies the role really cannot bypass RLS.

Usage (from the repository root):

    py -3.12 tools/scripts/provision_db.py

The script connects with ``DATABASE_MIGRATION_URL`` (the owner role) and
reads the runtime role name and password from ``DATABASE_URL``. Secrets are
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402

REQUIRED_EXTENSIONS = ("vector", "citext", "pgcrypto")
SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _runtime_credentials() -> tuple[str, str, str]:
    """Return (role, password, database) for the restricted runtime role."""
    settings = get_settings()
    url = make_url(settings.database_url)
    role = url.username or "wm_app"
    if not SAFE_IDENTIFIER.match(role):
        raise SystemExit(
            f"ERROR: runtime role {role!r} must match ^[a-z_][a-z0-9_]*$"
        )
    password = url.password or ""
    if not password:
        raise SystemExit(
            "ERROR: DATABASE_URL carries no password for the runtime role. "
            "Set it to the password used when creating the role."
        )
    database = url.database or ""
    if not SAFE_IDENTIFIER.match(database):
        raise SystemExit(f"ERROR: unsafe database name {database!r}")
    return role, password, database


async def _provision() -> int:
    settings = get_settings()
    role, password, database = _runtime_credentials()

    engine = create_async_engine(
        settings.database_migration_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            current = (await connection.execute(text("SELECT current_user"))).scalar_one()
            if current == role:
                raise SystemExit(
                    "ERROR: DATABASE_MIGRATION_URL uses the runtime role. "
                    "Migrations must run as the owner role."
                )

            for extension in REQUIRED_EXTENSIONS:
                await connection.execute(
                    text(f"CREATE EXTENSION IF NOT EXISTS {extension}")
                )
                print(f"OK    extension {extension}")

            await connection.execute(
                text(
                    "DO $do$ BEGIN "
                    f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                    f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOBYPASSRLS NOREPLICATION INHERIT; "
                    "END IF; "
                    "END $do$"
                )
            )
            # Only the password may be changed afterwards: a non-superuser owner
            # cannot alter the SUPERUSER/BYPASSRLS attributes of an existing
            # role, so they are fixed at creation time above.
            #
            # ``ALTER ROLE`` is a utility statement and cannot take bind
            # parameters, so PostgreSQL itself quotes the literal with
            # format('%I', ...) / format('%L', ...). The password is passed as
            # a bind parameter and never interpolated into SQL text.
            alter = (
                await connection.execute(
                    text(
                        "SELECT format("
                        "'ALTER ROLE %I LOGIN PASSWORD %L', "
                        "CAST(:role AS text), CAST(:password AS text))"
                    ),
                    {"role": role, "password": password},
                )
            ).scalar_one()
            await connection.execute(text(alter))
            print(f"OK    role {role} (NOSUPERUSER, NOBYPASSRLS)")

            attributes = (
                await connection.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                        "FROM pg_roles WHERE rolname = :role"
                    ),
                    {"role": role},
                )
            ).one()
            if attributes.rolsuper or attributes.rolbypassrls:
                raise SystemExit(
                    f"ERROR: role {role} already exists with elevated privileges. "
                    "Drop it (DROP ROLE) and re-run this script."
                )

            await connection.execute(
                text(f"GRANT CONNECT ON DATABASE {database} TO {role}")
            )
            await connection.execute(
                text(f"GRANT USAGE ON SCHEMA public TO {role}")
            )
            print(f"OK    grants on database {database} and schema public")
    finally:
        await engine.dispose()

    # Verify the role really is restricted, using the runtime credentials.
    runtime_engine = create_async_engine(
        settings.database_url, poolclass=NullPool
    )
    try:
        async with runtime_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, current_user "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            if row.rolsuper or row.rolbypassrls:
                print(
                    f"FAIL  role {row.current_user} can bypass RLS "
                    "(superuser or BYPASSRLS)"
                )
                return 1
            print(f"OK    verified: {row.current_user} cannot bypass RLS")
    finally:
        await runtime_engine.dispose()

    print(
        "\nDatabase provisioned. Next: run the migrations with the owner role:\n"
        "    cd apps/api && python -m alembic upgrade head"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    return asyncio.run(_provision())


if __name__ == "__main__":
    raise SystemExit(main())
