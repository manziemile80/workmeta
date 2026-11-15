# Free-Tier Deployment Limitations — WorkMeta AI

Development-only. Never use these limits for production.

## Render Free Web Service
- **Sleeps** after ~15 min inactivity; first request cold-starts (~30–60s).
- **Single instance**, 512 MB RAM; dashboards show `onrender.com` host.
- **No background-worker service** — the workers run inside the API process
  instead (`EMBEDDED_WORKER_ENABLED=true`, see `app/workers/embedded.py`).

### What the in-process worker changes
- **Document ingestion** is processed while the instance is awake: uploads are
  chunked and embedded a few seconds after they are queued. While the service
  sleeps, queued rows stay `queued` and are picked up after the cold start.
  The queue is durable, so nothing is lost.
- **A separate worker service could not help anyway**: the disk is ephemeral
  per service, so a worker in its own container would not find the uploaded
  blob. The API that received the upload has to do the ingestion.
- **Billing lifecycle** still advances lazily on every billing/quota read
  (`ensure_subscription`), and the embedded sweep advances it in the
  background as well, so trials/past-due/expiry stay correct.
- `app_is_worker()` gate and tenant isolation are untouched. Jobs are claimed
  with `FOR UPDATE SKIP LOCKED`, so an embedded worker and a dedicated worker
  never process the same job.

## Disk
- **Ephemeral** — `STORAGE_DIR=/app/var/storage` resets on restart/redeploy. Uploaded blobs vanish; re-upload after restart.

## Neon Free
- **Pooled endpoint** for the API (`DATABASE_URL`); direct only for the one-off migration.
- **No automated backups** on Free — take `tools/scripts/backup.py` copies manually.

## Vercel Free
- Build ~1 min; bandwidth/edge limits are generous for dev traffic.

## Payments
- `BILLING_PROVIDER=mock` always on free hosting. **Never enable live payments** without sandbox PASS (`RELEASE_CHECKLIST.md`).
