# Backup and Disaster Recovery — WorkMeta AI

## What is backed up

- **Database (logical)** — full `pg_dump` (custom format, gzipped) via `tools/scripts/backup.py`.
- **Database (provider snapshot)** — a Neon branch snapshot of the same data,
  created by the same script when `NEON_API_KEY` is set or the Neon MCP
  session is authorised (`opencode mcp auth neon`).
- **Storage** — tarball of `STORAGE_DIR` (uploaded document blobs).
- **Manifest** — `manifest.json` with SHA-256 + byte sizes for verification.

RPO/RTO targets: RPO ≤ 24h (daily backup), RTO ≤ 2h (restore on a fresh host). Adjust to pilot needs.

> On Neon, point-in-time restore covers the last **6 hours** (this plan's
> history window). A branch snapshot is the daily safety net.

## Procedure

```powershell
# Nightly (from the repository root, cron/Task Scheduler or container cron)
py -3.12 tools/scripts/backup.py

# Verify the newest backup at any time
py -3.12 tools/scripts/backup.py --verify-only
```

- Backups land in `var/backups/<UTC-timestamp>/` (gitignored). Copy them off-host (object storage / encrypted drive) the same day.
- Retention: keep 30 daily backups (prune older than 30 days). Off-site copies are the disaster-recovery copy.
- `pg_dump`/`psql`/`pg_restore` must be on PATH, and the client version must be
  **at least as new as the server** (Neon runs PostgreSQL 18 - use the
  PostgreSQL 18 client tools). An older `pg_dump` refuses to dump a newer
  server; the script reports this explicitly.
- Without `pg_dump`, the script still creates the Neon branch snapshot and
  records it in `manifest.json` under `neon_snapshot`.

### Storage caveat

`STORAGE_DIR` on the free Render tier is an **ephemeral disk**: uploaded
blobs disappear when the instance restarts. Back it up separately, or switch
to `STORAGE_BACKEND=s3` so the objects live outside the container.

## Restore drill (must be tested before pilot)

1. Fresh host with PostgreSQL 16 + pgvector, migrations at head.
2. Stop the API and workers.
3. `py -3.12 tools/scripts/restore.py --backup var/backups/<stamp> --yes`
   (the script drops and recreates the database named in
   `DATABASE_MIGRATION_URL` - point it at a scratch database for a drill).
4. Restart API + workers, run `/ready`, log in, verify one assistant/document/chat round-trip.
5. Re-run the full test suite against the restored database.

> **A backup is only valid once a restore has succeeded.** Run one drill per release.
> Last drill: a dump was restored into a scratch database and compared -
> 21 tables, 15 RLS tables, 21 `wm_app` grants, revision `20261112_0007`.

### Restoring a Neon snapshot

A branch snapshot is a copy-on-write copy of the database inside the same
project: open it in the Neon console (or with the MCP `create_branch` /
branch tools), point a test deployment at its endpoint, verify, and switch
the production connection string over with a redeploy. No dump file needed.

## Disaster recovery

- Total host loss: provision a new host, restore the latest off-site backup, restore storage, start containers (`docs/DEPLOYMENT.md`), rotate all secrets if the host was compromised (session secret, DB passwords, provider keys, webhook hash).
- Database corruption only: restore the latest database dump into the existing cluster, keep storage as-is.
- Storage loss only: restore the storage tarball onto a running database.
- Never restore over production without a fresh backup and an approved maintenance window.
