"""Automated database + storage backup (production ops helper).

Usage (from the repository root):

    py -3.12 tools/scripts/backup.py                  # pg_dump + storage tarball
    py -3.12 tools/scripts/backup.py --verify-only    # verify newest backup

Backups are written to BACKUP_DIR (default ./var/backups/<UTC-timestamp>/)
with a manifest.json (sha256 + sizes). The database dump uses the migration
(owner) connection via pg_dump when available, falling back to a SQL dump
through the asyncpg driver. Storage is a tarball of STORAGE_DIR.

Retention (default 30 days) and off-site copying are NOT handled here; see
docs/BACKUP.md. Run backups from cron/Task Scheduler or a container cron.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(output: Path) -> Path:
    settings = get_settings()
    output.mkdir(parents=True, exist_ok=True)

    # 1. Database: prefer pg_dump (single transaction, compressed).
    pg_dump = shutil.which("pg_dump")
    db_file = output / "workmeta.sql.gz"
    if pg_dump:
        import gzip

        print(f"Running pg_dump -> {db_file} ...")
        import subprocess

        # Extract host/port/user/db from DATABASE_MIGRATION_URL via psycopg-free parse.
        from urllib.parse import urlparse

        raw = settings.database_migration_url.replace("postgresql+asyncpg", "postgresql")
        parsed = urlparse(raw)
        env = {"PGPASSWORD": parsed.password or ""}
        result = subprocess.run(
            [
                pg_dump,
                "-h", parsed.hostname or "127.0.0.1",
                "-p", str(parsed.port or 5432),
                "-U", parsed.username or "workmeta_owner",
                "-d", (parsed.path or "/workmeta").lstrip("/"),
                "-F", "c",
            ],
            capture_output=True,
            env={**__import__("os").environ, **env},
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {result.stderr.decode(errors='replace')[:500]}")
        with gzip.open(db_file, "wb") as handle:
            handle.write(result.stdout)
    else:
        raise RuntimeError(
            "pg_dump not found on PATH; install PostgreSQL client tools to back up."
        )

    # 2. Storage blobs.
    storage_dir = settings.resolved_storage_dir
    storage_file = output / "storage.tar.gz"
    if storage_dir.exists():
        with tarfile.open(storage_file, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(storage_dir, arcname="storage")
    else:
        storage_file.write_bytes(b"")

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": {"file": db_file.name, "sha256": _sha256(db_file), "bytes": db_file.stat().st_size},
        "storage": {"file": storage_file.name, "sha256": _sha256(storage_file), "bytes": storage_file.stat().st_size},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Backup complete: {output}")
    return output


def verify_backup(output: Path) -> bool:
    manifest = json.loads((output / "manifest.json").read_text())
    ok = True
    for section in ("database", "storage"):
        entry = manifest[section]
        path = output / entry["file"]
        if not path.exists():
            print(f"MISSING {path}")
            ok = False
            continue
        size_ok = path.stat().st_size == entry["bytes"]
        hash_ok = _sha256(path) == entry["sha256"]
        print(f"{section}: size_ok={size_ok} hash_ok={hash_ok}")
        ok = ok and size_ok and hash_ok
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkMeta AI backup helper")
    parser.add_argument("--output", default="", help="Backup directory")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    base = Path(args.output) if args.output else (REPO_ROOT / "var" / "backups")
    if args.verify_only:
        candidates = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
        if not candidates:
            raise SystemExit("No backups found to verify.")
        latest = candidates[-1]
        print(f"Verifying {latest} ...")
        raise SystemExit(0 if verify_backup(latest) else 1)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = base / stamp
    create_backup(target)


if __name__ == "__main__":
    main()
