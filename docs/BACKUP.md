# Backup and Disaster Recovery — WorkMeta AI

## What is backed up

- **Database** — full `pg_dump` (custom format, gzipped) via `tools/scripts/backup.py`.
- **Storage** — tarball of `STORAGE_DIR` (uploaded document blobs).
- **Manifest** — `manifest.json` with SHA-256 + byte sizes for verification.

RPO/RTO targets: RPO ≤ 24h (daily backup), RTO ≤ 2h (restore on a fresh host). Adjust to pilot needs.

## Procedure

```powershell
# Nightly (from the repository root, cron/Task Scheduler or container cron)
py -3.12 tools/scripts/backup.py

# Verify the newest backup at any time
py -3.12 tools/scripts/backup.py --verify-only
```

- Backups land in `var/backups/<UTC-timestamp>/` (gitignored). Copy them off-host (object storage / encrypted drive) the same day.
- Retention: keep 30 daily backups (prune older than 30 days). Off-site copies are the disaster-recovery copy.
- `pg_dump`/`psql`/`pg_restore` must be on PATH (PostgreSQL client tools).

## Restore drill (must be tested before pilot)

1. Fresh host with PostgreSQL 16 + pgvector, migrations at head.
2. Stop the API and workers.
3. `py -3.12 tools/scripts/restore.py --backup var/backups/<stamp> --yes`
4. Restart API + workers, run `/ready`, log in, verify one assistant/document/chat round-trip.
5. Re-run the full test suite against the restored database.

> **A backup is only valid once a restore has succeeded.** Run one drill per release.

## Disaster recovery

- Total host loss: provision a new host, restore the latest off-site backup, restore storage, start containers (`docs/DEPLOYMENT.md`), rotate all secrets if the host was compromised (session secret, DB passwords, provider keys, webhook hash).
- Database corruption only: restore the latest database dump into the existing cluster, keep storage as-is.
- Storage loss only: restore the storage tarball onto a running database.
- Never restore over production without a fresh backup and an approved maintenance window.
