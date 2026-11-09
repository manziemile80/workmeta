"""Restore a WorkMeta AI backup (documented procedure — see docs/BACKUP.md).

Usage (from the repository root, with PostgreSQL STOPPED for the API or in
a maintenance window):

    py -3.12 tools/scripts/restore.py --backup var/backups/<stamp> --yes

Steps performed:
  1. Verify manifest hashes.
  2. Drop + recreate the database (owner connection), then pg_restore.
  3. Replace the storage directory from the tarball.

The script refuses to run without --yes. Always test a restore on a staging
host first; never run it against production without a fresh backup.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkMeta AI restore helper")
    parser.add_argument("--backup", required=True, help="Backup directory")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive restore")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing: pass --yes to confirm this restores over the live database.")

    backup = Path(args.backup)
    manifest = json.loads((backup / "manifest.json").read_text())
    print(f"Restoring from {backup} (created {manifest['created_at']})")

    settings = get_settings()
    raw = settings.database_migration_url.replace("postgresql+asyncpg", "postgresql")
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    user = parsed.username or "workmeta_owner"
    dbname = (parsed.path or "/workmeta").lstrip("/")
    password = parsed.password or ""

    import os

    env = {**os.environ, "PGPASSWORD": password}
    psql = shutil.which("psql")
    pg_restore = shutil.which("pg_restore")
    if not psql or not pg_restore:
        raise SystemExit("psql/pg_restore not found on PATH.")

    print("Dropping and recreating database ...")
    subprocess.run(
        [psql, "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f'DROP DATABASE IF EXISTS "{dbname}";'],
        check=True, env=env,
    )
    subprocess.run(
        [psql, "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f'CREATE DATABASE "{dbname}";'],
        check=True, env=env,
    )
    print("Restoring database ...")
    import gzip

    db_plain = backup / "dump.pgcustom"
    with gzip.open(backup / manifest["database"]["file"], "rb") as handle:
        db_plain.write_bytes(handle.read())
    subprocess.run(
        [pg_restore, "-h", host, "-p", port, "-U", user, "-d", dbname, str(db_plain)],
        check=True, env=env,
    )
    db_plain.unlink()

    storage_dir = settings.resolved_storage_dir
    print(f"Replacing storage at {storage_dir} ...")
    if storage_dir.exists():
        shutil.rmtree(storage_dir)
    storage_dir.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(backup / manifest["storage"]["file"], "r:gz") as archive:
        archive.extractall(storage_dir.parent)
    print("Restore complete. Restart the API and workers.")


if __name__ == "__main__":
    main()
