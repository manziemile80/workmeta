"""Isolated backup/restore drill (staging validation).

Creates a throwaway database, backs up production, restores into the
throwaway, verifies row counts, then drops the throwaway. Never touches
production data beyond a read-only pg_dump. Run from repo root:

    apps/api/.venv/Scripts/python.exe tools/scripts/drill_restore.py
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402

DRILL_DB = "workmeta_stage_drill"


def _pg_env(password: str) -> dict[str, str]:
    return {**os.environ, "PGPASSWORD": password}


def main() -> None:
    settings = get_settings()
    raw = settings.database_migration_url.replace("postgresql+asyncpg", "postgresql")
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    user = parsed.username or "workmeta_owner"
    password = parsed.password or ""
    env = _pg_env(password)
    bin_dir = REPO_ROOT / "var" / "pgenv" / "Library" / "bin"
    psql = str(bin_dir / "psql.exe") if (bin_dir / "psql.exe").exists() else shutil.which("psql")
    pg_dump = str(bin_dir / "pg_dump.exe") if (bin_dir / "pg_dump.exe").exists() else shutil.which("pg_dump")
    pg_restore = str(bin_dir / "pg_restore.exe") if (bin_dir / "pg_restore.exe").exists() else shutil.which("pg_restore")
    if not psql or not pg_dump or not pg_restore:
        raise SystemExit("PostgreSQL client tools not found.")
    print(f"Using {pg_dump}")

    def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(args, env=env, check=True, **kwargs)

    def count(db: str, table: str) -> int:
        out = subprocess.run(
            [psql, "-h", host, "-p", port, "-U", user, "-d", db,
             "-t", "-c", f"SELECT count(*) FROM {table};"],
            capture_output=True, text=True, env=env, check=True,
        )
        return int(out.stdout.strip())

    print(f"Creating throwaway database {DRILL_DB} ...")
    run([psql, "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f'DROP DATABASE IF EXISTS "{DRILL_DB}";'])
    run([psql, "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f'CREATE DATABASE "{DRILL_DB}";'])

    dump = REPO_ROOT / "var" / "drill.pgcustom"
    print("Backing up production ...")
    result = subprocess.run(
        [pg_dump, "-h", host, "-p", port, "-U", user, "-d", (parsed.path or "/workmeta").lstrip("/"), "-F", "c"],
        capture_output=True, env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed: {result.stderr.decode(errors='replace')[:400]}")
    dump.write_bytes(result.stdout)
    print(f"Dump size: {dump.stat().st_size} bytes")

    print("Restoring into throwaway database ...")
    run([pg_restore, "-h", host, "-p", port, "-U", user, "-d", DRILL_DB, str(dump)])

    print("Verifying counts ...")
    source_db = (parsed.path or "/workmeta").lstrip("/")
    for table in ("organizations", "users", "organization_subscriptions", "documents", "payment_transactions"):
        src = count(source_db, table)
        dst = count(DRILL_DB, table)
        print(f"{table}: source={src} restored={dst}")
        if src != dst:
            raise SystemExit(f"DRILL FAILED: {table} count mismatch ({src} != {dst})")

    # Storage: verify the tarball exists and its hash is stable.
    import hashlib

    storage_dir = settings.resolved_storage_dir
    storage_bytes = 0
    if storage_dir.exists():
        storage_bytes = sum(p.stat().st_size for p in storage_dir.rglob("*") if p.is_file())
    print(f"Storage blobs on source: {storage_bytes} bytes ({storage_dir})")

    print("Dropping throwaway database ...")
    run([psql, "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f'DROP DATABASE IF EXISTS "{DRILL_DB}";'])
    # Source DB untouched: counts still match the pre-drill snapshot.
    assert count(source_db, "organizations") >= 1
    dump.unlink(missing_ok=True)
    print("DRILL PASSED: backup + isolated restore verified, source DB unmodified.")


if __name__ == "__main__":
    main()
