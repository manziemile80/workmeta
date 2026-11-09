# Free-Tier Deployment Limitations — WorkMeta AI

Development-only. Never use these limits for production.

## Render Free Web Service
- **Sleeps** after ~15 min inactivity; first request cold-starts (~30–60s).
- **Single instance**, 512 MB RAM; dashboards show `onrender.com` host.
- **No background workers** — ingestion/billing workers cannot run.

### What breaks without workers
- **Document ingestion** stays `uploaded/processing` (queued rows are durable; run `python -m app.workers.ingestion --drain` manually on a schedule when you need it).
- **Billing lifecycle** still advances lazily on every billing/quota read (`ensure_subscription`), so trials/past-due/expiry stay correct without the billing worker.
- `app_is_worker()` gate and tenant isolation are untouched.

## Disk
- **Ephemeral** — `STORAGE_DIR=/app/var/storage` resets on restart/redeploy. Uploaded blobs vanish; re-upload after restart.

## Neon Free
- **Pooled endpoint** for the API (`DATABASE_URL`); direct only for the one-off migration.
- **No automated backups** on Free — take `tools/scripts/backup.py` copies manually.

## Vercel Free
- Build ~1 min; bandwidth/edge limits are generous for dev traffic.

## Payments
- `BILLING_PROVIDER=mock` always on free hosting. **Never enable live payments** without sandbox PASS (`RELEASE_CHECKLIST.md`).
