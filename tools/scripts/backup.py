"""Automated database + storage backup (production ops helper).

Usage (from the repository root):

    py -3.12 tools/scripts/backup.py                  # pg_dump + storage tarball
    py -3.12 tools/scripts/backup.py --verify-only    # verify newest backup

Backups are written to BACKUP_DIR (default ./var/backups/<UTC-timestamp>/)
with a manifest.json (sha256 + sizes).

Two database backup mechanisms are supported:

1. **Logical dump (default)** - ``pg_dump`` against ``DATABASE_MIGRATION_URL``.
   The client version must be >= the server version (Neon runs PostgreSQL 18,
   so use pg_dump 18); older clients refuse to dump a newer server.
2. **Provider snapshot (Neon)** - when ``NEON_API_KEY`` is set and the database
   is a Neon endpoint, a copy-on-write branch is created as a point-in-time
   backup that can be inspected, queried and restored inside Neon.

Retention (default 30 days) and off-site copying are NOT handled here; see
docs/BACKUP.md. Run backups from cron/Task Scheduler or a container cron.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402

NEON_API = "https://console.neon.tech/api/v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _neon_request(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{NEON_API}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Neon API {method} {path} failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Neon API {method} {path} failed: {exc.reason}") from exc


def _neon_snapshot_mcp(stamp: str, host: str) -> dict | None:
    """Provider snapshot through the Neon MCP server (OAuth, no API key).

    Mirrors `tools/scripts/neon_mcp.py`: the branch is a copy-on-write
    snapshot of the database that can be inspected and restored in Neon.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools" / "scripts"))
    try:
        from neon_mcp import find_token, tool_call  # type: ignore
    except ImportError:
        return None
    token, _ = find_token()
    if not token:
        return None

    projects = tool_call("list_projects", {}, token)
    project_id = None
    for project in projects if isinstance(projects, list) else []:
        for endpoint in project.get("endpoints") or []:
            if endpoint.get("host") == host:
                project_id = project["id"]
                break
        if project_id:
            break
    if not project_id:
        raise RuntimeError(f"No Neon project owns the endpoint {host}.")

    branches = tool_call("list_branches", {"project_id": project_id}, token)
    branch_list = branches if isinstance(branches, list) else []
    default_branch = next(
        (b for b in branch_list if b.get("default")),
        branch_list[0] if branch_list else None,
    )
    if default_branch is None:
        raise RuntimeError("The Neon project has no branch to snapshot.")

    created = tool_call(
        "create_branch",
        {
            "project_id": project_id,
            "name": f"backup-{stamp}",
            "parent_id": default_branch["id"],
        },
        token,
    )
    return {
        "project_id": project_id,
        "branch_id": created.get("id"),
        "branch_name": created.get("name"),
        "created_at": created.get("created_at"),
        "mechanism": "neon-mcp-branch",
    }


def _neon_snapshot(stamp: str) -> dict | None:
    """Create a Neon branch snapshot of the production database."""
    token = os.environ.get("NEON_API_KEY", "").strip()
    settings = get_settings()
    parsed = urlparse(settings.database_migration_url.replace("postgresql+asyncpg", "postgresql"))
    host = parsed.hostname or ""
    if not host.endswith(".neon.tech"):
        return None
    if not token:
        return _neon_snapshot_mcp(stamp, host)

    projects = _neon_request("GET", "/projects", token).get("projects") or []
    project_id = None
    for project in projects:
        for endpoint in project.get("endpoints") or []:
            if endpoint.get("host") == host:
                project_id = project["id"]
                break
        if project_id:
            break
    if not project_id:
        raise RuntimeError(
            f"No Neon project owns the endpoint {host}; cannot create a snapshot."
        )

    branches = _neon_request(
        "GET", f"/projects/{project_id}/branches", token
    ).get("branches") or []
    default_branch = next(
        (b for b in branches if b.get("default")), branches[0] if branches else None
    )
    if default_branch is None:
        raise RuntimeError("The Neon project has no branch to snapshot.")

    created = _neon_request(
        "POST",
        f"/projects/{project_id}/branches",
        token,
        {
            "name": f"backup-{stamp}",
            "parent_id": default_branch["id"],
        },
    )
    branch = created.get("branch") or created
    return {
        "project_id": project_id,
        "branch_id": branch.get("id"),
        "branch_name": branch.get("name"),
        "created_at": branch.get("created_at"),
    }


def create_backup(output: Path) -> Path:
    settings = get_settings()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    # 1a. Provider-native snapshot (Neon branch) when credentials are present.
    snapshot: dict | None = None
    try:
        snapshot = _neon_snapshot(stamp)
    except RuntimeError as exc:
        print(f"Neon snapshot skipped: {exc}")
    if snapshot:
        print(
            f"Neon snapshot created: {snapshot['branch_name']} "
            f"({snapshot['branch_id']})"
        )

    # 1b. Logical dump (preferred when a matching pg_dump is available).
    pg_dump = shutil.which("pg_dump")
    db_file = output / "workmeta.sql.gz"
    if pg_dump:
        import gzip

        print(f"Running pg_dump -> {db_file} ...")
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
            message = result.stderr.decode(errors="replace")[:500]
            raise RuntimeError(
                f"pg_dump failed: {message}. The client must be at least as new as "
                "the server (Neon runs PostgreSQL 18 - use pg_dump 18)."
            )
        with gzip.open(db_file, "wb") as handle:
            handle.write(result.stdout)
    else:
        print(
            "pg_dump not found on PATH - skipping the logical dump. Install the "
            "PostgreSQL client tools matching the server version (18 for Neon)."
        )
        if snapshot is None:
            raise RuntimeError(
                "No backup was produced: install PostgreSQL client tools or set "
                "NEON_API_KEY so a Neon snapshot can be taken instead."
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
        "storage": {"file": storage_file.name, "sha256": _sha256(storage_file), "bytes": storage_file.stat().st_size},
    }
    if db_file.exists():
        manifest["database"] = {
            "file": db_file.name,
            "sha256": _sha256(db_file),
            "bytes": db_file.stat().st_size,
        }
    if snapshot:
        manifest["neon_snapshot"] = snapshot
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Backup complete: {output}")
    return output


def verify_backup(output: Path) -> bool:
    manifest = json.loads((output / "manifest.json").read_text())
    ok = True
    for section in ("database", "storage"):
        entry = manifest.get(section)
        if entry is None:
            print(f"{section}: not part of this backup (skipped)")
            continue
        path = output / entry["file"]
        if not path.exists():
            print(f"MISSING {path}")
            ok = False
            continue
        size_ok = path.stat().st_size == entry["bytes"]
        hash_ok = _sha256(path) == entry["sha256"]
        print(f"{section}: size_ok={size_ok} hash_ok={hash_ok}")
        ok = ok and size_ok and hash_ok
    snapshot = manifest.get("neon_snapshot")
    if snapshot:
        print(
            "neon_snapshot: branch={} id={}".format(
                snapshot.get("branch_name"), snapshot.get("branch_id")
            )
        )
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
