# WORKMETA — Render Deployment Guide

## Prerequisites (Before Starting)
- [ ] GitHub repository connected (workmeta at GitHub)
- [ ] Render account created at https://render.com
- [ ] Neon PostgreSQL database ready (completed — see DATABASE_URL below)
- [ ] This guide for step-by-step configuration

## DATABASE CONNECTION (Neon)
| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://<runtime-role>:<password>@ep-mute-base-be5pm73v-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | `sync: false` — dashboard secret; copy the exact value from the gitignored `.env` |
| `DATABASE_MIGRATION_URL` | `postgresql+asyncpg://neondb_owner:<password>@ep-mute-base-be5pm73v.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | `sync: false` — dashboard secret; owner role for migrations |

> The password is deliberately not written in this document. Read it from the
> local `.env` (never committed) and paste it into the Render dashboard.

**Already verified:**
- pgvector extension: `CREATE EXTENSION IF NOT EXISTS vector;`
- citext extension: `CREATE EXTENSION IF NOT EXISTS citext;`
- Alembic migrations: All 7 applied (stamp at `20261112_0007 head`)
- Current revision: `20261112_0007 (head)`

## RENDER SERVICE SETUP

### Step 1: Create New Web Service
1. Log in to Render Dashboard (https://render.com)
2. New Service → Docker
3. **Name:** `workmeta-api`
4. **Root Directory:** (leave blank — defaults to repo root)
5. **Dockerfile path:** `apps/api/Dockerfile`
6. **Plan:** `Free`
7. **Region:** `Oregon` (or nearest to your users)
8. **Auto-deploy:** `false` (manual — avoids surprise rebuilds)
9. Click **Create Web Service**

### Step 2: Configure Environment Variables
Navigate to **Service → Environment** and add each variable (click "Add Variable" for each):

| Variable | Value | sync? | Notes |
|---|---|---|---|
| `APP_ENV` | `development` | false | Free tier intentional |
| `APP_NAME` | `WorkMeta AI API` | false | Display name |
| `API_V1_PREFIX` | `/api/v1` | false | Routing prefix |
| `LOG_LEVEL` | `INFO` | false | Log verbosity |
| `CORS_ORIGINS` | (empty) | false | Will set after Vercel deploy; `sync: false` |
| `ALLOWED_HOSTS` | (empty) | false | Will set after Vercel deploy; `sync: false` |
| `MAX_REQUEST_BODY_BYTES` | `12582912` | false | 12 MiB cap |
| `DATABASE_URL` | `postgresql+asyncpg://<runtime-role>:<password>@ep-mute-base-be5pm73v-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | **true** | Critical — Neon connection; copy from the gitignored `.env` |
| `DATABASE_MIGRATION_URL` | `postgresql+asyncpg://neondb_owner:<password>@ep-mute-base-be5pm73v.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | **true** | Critical — owner role for migrations; copy from the gitignored `.env` |
| `SESSION_SECRET` | _(from the gitignored `.env`)_ | true | From generated .env; `sync: false` |
| `SESSION_TTL_DAYS` | `7` | false | Session lifetime |
| `SESSION_IDLE_HOURS` | `8` | false | Idle timeout |
| `INVITATION_TTL_DAYS` | `7` | false | Invitation lifetime |
| `PASSWORD_RESET_TTL_MINUTES` | `60` | false | Password reset TTL |
| `BACKUP_RETENTION_DAYS` | `30` | false | Backup retention |
| `AI_PROVIDER` | `openai` | false | Provider configuration |
| `AI_BASE_URL` | `https://api.openai.com/v1` | false | OpenAI-compatible endpoint |
| `AI_API_KEY` | (empty) | true | Empty = offline fake provider; set later for real provider; `sync: false` |
| `AI_CHAT_MODEL` | `gpt-4.1-mini` | false | Chat model override per-assistant |
| `AI_EMBEDDING_MODEL` | `text-embedding-3-small` | false | Embedding model (deployment-wide) |
| `EMBEDDING_DIMENSIONS` | `1536` | false | Vector dimensions |
| `AI_TIMEOUT_SECONDS` | `60` | false | Request timeout |
| `AI_TEMPERATURE` | `0` | false | Default temperature |
| `AI_MAX_RETRIES` | `2` | false | Max retry attempts |
| `CHAT_MAX_OUTPUT_TOKENS` | `800` | false | Max output tokens |
| `BILLING_PROVIDER` | `mock` | false | Keep on free tier until sandbox PASS |
| `BILLING_CURRENCY` | `RWF` | false | Rwandan Francs |
| `BILLING_TRIAL_DAYS` | `14` | false | Trial period |
| `BILLING_PAST_DUE_GRACE_DAYS` | `7` | false | Grace period |
| `BILLING_MOCK_WEBHOOK_SECRET` | `dev-mock-webhook-secret` | true | `sync: false` |
| `BILLING_CHECKOUT_CALLBACK_URL` | (empty for now) | true | Will set after Vercel deploy; `sync: false` |
| `FLUTTERWAVE_BASE_URL` | `https://api.flutterwave.com/v3` | false | Payment gateway base |
| `FLUTTERWAVE_SECRET_KEY` | (empty) | true | `sync: false` |
| `FLUTTERWAVE_WEBHOOK_HASH` | (empty) | true | `sync: false` |
| `PAYSTACK_BASE_URL` | `https://api.paystack.co` | false | Payment gateway base |
| `PAYSTACK_SECRET_KEY` | (empty) | true | `sync: false` |
| `PAYSTACK_WEBHOOK_SECRET` | (empty) | true | `sync: false` |
| `STORAGE_BACKEND` | `local` | false | Local file storage |
| `STORAGE_DIR` | `/app/var/storage` | false | Container path (matches Dockerfile) |
| `RATE_LIMIT_ENABLED` | `true` | false | Rate limiting enabled |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `60` | false | Login rate limit |
| `RATE_LIMIT_INVITE_ACCEPT_PER_MINUTE` | `60` | false | Invite accept rate limit |
| `RATE_LIMIT_WEBHOOK_PER_MINUTE` | `300` | false | Webhook rate limit |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `300` | false | Chat rate limit |
| `EMBEDDED_WORKER_ENABLED` | `true` | false | Free has no worker service, so the API drains its own queue in-process |

### Step 3: Deploy
1. In the Render Dashboard, go to **Service → Deploys**
2. Click **Manual Deploy** → **Deploy Site**
3. Wait 1-3 minutes for the build to complete (Free tier may take longer)
4. The service will start automatically

### Step 4: Verify Health Endpoint
Once the deploy completes:

```powershell
# Find the Render URL from the service dashboard
# It will be something like: https://workmeta-api.onrender.com

# Test health endpoint:
curl https://workmeta-api.onrender.com/api/v1/health

# Expected response:
{
  "status": "ok",
  "database": "ok",
  "pgvector": true,
  "version": "0.1.0",
  "environment": "development"
}

# Test readiness endpoint:
curl https://workmeta-api.onrender.com/api/v1/ready

# Expected response:
{
  "status": "ready",
  "database": "ok",
  "pgvector": true,
  "version": "0.1.0",
  "environment": "development"
}
```

## RENDER CONFIGURATION VERIFICATION

### Dockerfile Validation
The `apps/api/Dockerfile` is already configured correctly:
- Multi-stage build (build → runtime)
- Python `3.12-slim-bookworm` image
- venv at `/opt/venv`
- Copies `apps/api/app/` to `/app`
- `EXPOSE 8000`
- HEALTHCHECK on `/api/v1/health`
- CMD: `bash -c "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips=*"`

### render.yaml (Already Verified)
The `render.yaml` file in the repo root is pre-configured with all 40+ env vars. The Render Dashboard environment variables should match these values.

## CORS & ALLOWED_HOSTS CONFIGURATION

### Initial Setup (Free Tier)
1. Start with `CORS_ORIGINS` and `ALLOWED_HOSTS` as **empty strings** (or omit them)
2. After Vercel is deployed (Phase 3), update:
   - `CORS_ORIGINS`: `https://workmeta.vercel.app` (your Vercel domain)
   - `ALLOWED_HOSTS`: `workmeta-api.onrender.com,workmeta.vercel.app`

### Why Start Empty?
- The `_production_secrets()` validator in `app/core/config.py` checks that `ALLOWED_HOSTS` is not `["*"]` and `CORS_ORIGINS` is not `["*"]` in production
- Starting empty avoids validation errors before the frontend is deployed
- After Vercel deploy, the proxy (`/api/:path*` → Render) makes cookies first-party, which relaxes CORS requirements

## BACKGROUND WORKERS (Free Tier Note)
**Important:** The Render Free tier does NOT support background worker services.

The API therefore runs the worker loops in-process
(`EMBEDDED_WORKER_ENABLED=true`, implemented in `app/workers/embedded.py`):

- **Ingestion worker** (`app.workers.ingestion`): uploads are chunked and
  embedded a few seconds after they are queued, while the instance is awake.
  While the service sleeps, the queued rows stay `queued` and are processed
  after the cold start — the queue is durable, so nothing is lost.
- **Billing worker** (`app.workers.billing`): the embedded sweep advances the
  subscription lifecycle in the background; it also advances lazily via
  `ensure_subscription` on every billing/quota read (no scheduler needed).

The embedded loop is safe to run next to a dedicated worker: ingestion jobs
are claimed with `FOR UPDATE SKIP LOCKED`, so no job is processed twice.

### Free Tier Workaround
- Keep `EMBEDDED_WORKER_ENABLED=true` (set in `render.yaml`) so the queue drains
- Run migrations manually via **Render Shell** (Free has no pre-deploy hook)
- Or upgrade to a paid plan, deploy `python -m app.workers.ingestion` as its own
  worker service and set `EMBEDDED_WORKER_ENABLED=false`

## MIGRATION EXECUTION (Should Already Be Done)
Since the Neon database already has all migrations applied:

```powershell
# Verify (should already be at head):
alembic current  # → 20261112_0007 (head)

# If migrations are NOT applied for any reason:
alembic upgrade head  # Apply any pending migrations
```

The database was already stamped at `20261112_0007` via `alembic stamp head` earlier in this session.

## RENDER SHELL (For Manual Operations)
If you need to run commands inside the running container:

```powershell
# Render Dashboard → Service → Shell
# Run: alembic current
# Run: python -m app.workers.ingestion --drain
# Run: python -c "import app.main; print('OK')"
```

## TROUBLESHOOTING RENDER DEPLOYMENT

| Issue | Cause | Fix |
|---|---|---|
| Build fails | Dockerfile path wrong or COPY paths incorrect | Verify `apps/api/Dockerfile` exists; check repo root context |
| Health returns 503 | AI provider issue | Keep `AI_API_KEY=` empty for offline fake provider |
| `pgvector: Missing` | Extension not created in Neon | Already done; ensure Neon project is active |
| Health returns database error | Wrong DATABASE_URL | Verify URL matches Neon exactly (including `+asyncpg`) |
| App crashes on start | Import error | Verify Python 3.12 compatibility; constraint updated to `>=3.12` |
| Rate limit errors | Normal under load | In-memory limit; proxy is authoritative limiter |
| CORS errors | Hosts not set | Update after Vercel deploy (Phase 3) |
| Session issues | `APP_ENV=production` rejects mock | Keep `APP_ENV=development` on Free tier |

## RENDER DEPLOYMENT CHECKLIST

### Before Deploy
- [ ] GitHub repo connected (or upload manually)
- [ ] Render account created
- [ ] Neon PostgreSQL verified (pgvector + citext + migrations)
- [ ] `.env` updated with Neon URLs (completed)
- [ ] `render.yaml` values match intended Dashboard values

### After Deploy
- [ ] Service status: `Live` (not errored)
- [ ] `GET /api/v1/health` → 200, `database: ok`, `pgvector: true`
- [ ] `GET /api/v1/ready` → 200, all checks passing
- [ ] No 500 errors on admin API routes
- [ ] Session secret is working (login/create org test)

### Secrets Check
- [ ] All secrets marked `sync: false` (not in logs, not in git)
- [ ] No `.env` committed to GitHub
- [ ] Session secret matches generated value
- [ ] DATABASE_URL and DATABASE_MIGRATION_URL are correct Neon strings

### Ready for Vercel
- [ ] Render API responding correctly
- [ ] Ready to configure Vercel frontend (Phase 3)

## NEXT: PROCEED TO PHASE 3 — VERCEL DEPLOYMENT

Continue to the Vercel deployment guide after Render is confirmed working.