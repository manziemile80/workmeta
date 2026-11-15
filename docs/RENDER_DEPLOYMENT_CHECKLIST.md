# Render Deployment — Exact Steps (Ready-to-Execute)

## Prerequisites (Before Starting)
- [ ] Render account created at https://render.com
- [ ] Neon PostgreSQL database ready with connection string
- [ ] Vercel account created at https://vercel.com (optional, for frontend)
- [ ] GitHub repository connected (or prepare to upload manually)
- [ ] This `.env` file updated with Neon URLs (completed)

## Step 1: Create Render Docker Service
```powershell
# In Render Dashboard:
# 1. New Service → Docker
# 2. Name: workmeta-api
# 3. Runtime: Docker → Dockerfile path: apps/api/Dockerfile
# 4. Build context: Repository root (leave blank, defaults to repo root)
# 5. Plan: Free
# 6. Region: Oregon (or nearest to users)
# 7. Auto-deploy: false (manual — avoids surprise rebuilds)
# 8. Create service
```

## Step 2: Set Environment Variables in Render Dashboard
Navigate to Service → Environment and add each variable:

| Key | Value | Notes |
|---|---|---|
| `APP_ENV` | `development` | Free tier intentional |
| `APP_NAME` | `WorkMeta AI API` | Display name |
| `API_V1_PREFIX` | `/api/v1` | Routing prefix |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `CORS_ORIGINS` | _(empty or frontend origin)_ | `sync: false` — dashboard-only |
| `ALLOWED_HOSTS` | _(empty or render host)_ | `sync: false` — dashboard-only |
| `MAX_REQUEST_BODY_BYTES` | `12582912` | 12 MiB cap |
| `DATABASE_URL` | `postgresql+asyncpg://<runtime-role>:<password>@ep-mute-base-be5pm73v-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | `sync: false` — critical; copy the value from the gitignored `.env` |
| `DATABASE_MIGRATION_URL` | `postgresql+asyncpg://neondb_owner:<password>@ep-mute-base-be5pm73v.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require` | `sync: false` — critical; copy the value from the gitignored `.env` |
| `SESSION_SECRET` | _(from the gitignored `.env`)_ | `sync: false` |
| `SESSION_TTL_DAYS` | `7` | Session lifetime |
| `SESSION_IDLE_HOURS` | `8` | Idle timeout |
| `INVITATION_TTL_DAYS` | `7` | Invitation lifetime |
| `PASSWORD_RESET_TTL_MINUTES` | `60` | Password reset TTL |
| `BACKUP_RETENTION_DAYS` | `30` | Backup retention |
| `AI_PROVIDER` | `openai` | Provider configuration |
| `AI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `AI_API_KEY` | _(empty for fake provider, or `sk-...` for real)_ | `sync: false` — server-side only |
| `AI_CHAT_MODEL` | `gpt-4.1-mini` | Chat model override per-assistant |
| `AI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model (deployment-wide) |
| `EMBEDDING_DIMENSIONS` | `1536` | Vector dimensions |
| `AI_TIMEOUT_SECONDS` | `60` | Request timeout |
| `AI_TEMPERATURE` | `0` | Default temperature |
| `AI_MAX_RETRIES` | `2` | Max retry attempts |
| `CHAT_MAX_OUTPUT_TOKENS` | `800` | Max output tokens |
| `BILLING_PROVIDER` | `mock` | Keep on free tier until sandbox PASS |
| `BILLING_CURRENCY` | `RWF` | Rwandan Francs |
| `BILLING_TRIAL_DAYS` | `14` | Trial period |
| `BILLING_PAST_DUE_GRACE_DAYS` | `7` | Grace period |
| `BILLING_MOCK_WEBHOOK_SECRET` | `dev-mock-webhook-secret` | `sync: false` |
| `BILLING_CHECKOUT_CALLBACK_URL` | `https://<vercel-host>/billing/checkout` | Update after Vercel deploy; `sync: false` |
| `FLUTTERWAVE_BASE_URL` | `https://api.flutterwave.com/v3` | Payment gateway base |
| `FLUTTERWAVE_SECRET_KEY` | _(empty for mock)_ | `sync: false` |
| `FLUTTERWAVE_WEBHOOK_HASH` | _(empty for mock)_ | `sync: false` |
| `PAYSTACK_BASE_URL` | `https://api.paystack.co` | Payment gateway base |
| `PAYSTACK_SECRET_KEY` | _(empty for mock)_ | `sync: false` |
| `PAYSTACK_WEBHOOK_SECRET` | _(empty for mock)_ | `sync: false` |
| `STORAGE_BACKEND` | `local` | Local file storage |
| `STORAGE_DIR` | `/app/var/storage` | Storage directory (container path) |
| `RATE_LIMIT_ENABLED` | `true` | Rate limiting enabled |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `60` | Login rate limit |
| `RATE_LIMIT_INVITE_ACCEPT_PER_MINUTE` | `60` | Invite accept rate limit |
| `RATE_LIMIT_WEBHOOK_PER_MINUTE` | `300` | Webhook rate limit |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `300` | Chat rate limit |
| `EMBEDDED_WORKER_ENABLED` | `true` | Free has no worker service, so the API drains its own queue in-process |

## Step 3: Deploy and Verify
```powershell
# Render will auto-build from Dockerfile
# Or trigger manual deploy: Render → Service → Deploys → Manual Deploy

# Wait for build to complete (1-2 minutes minimum on Free tier)

# Verify health endpoint:
curl https://workmeta-api.onrender.com/api/v1/health

# Expected response (with Neon DB):
{
  "status": "ok",
  "database": "ok",
  "pgvector": true,
  "version": "0.1.0",
  "environment": "development"
}

# Verify readiness endpoint:
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

## Step 4: (Optional) Set Render Hostname
If using custom domain:
- Render → Service → Custom Domain
- Add: `workmeta-api.your-domain.com`
- Update `ALLOWED_HOSTS` to include the hostname

## Step 5: Vercel Frontend Deployment
```powershell
# In terminal (from repository root):
# 1. Install Vercel CLI (if not already): npm i -g vercel
# 2. Login: vercel login (browser opens)
# 3. Link project: vercel link --name workmeta (Root: apps/web)
# 4. Set environment variable: vercel env set VITE_API_BASE_URL /api
# 5. Deploy: vercel deploy --prod
# 6. Note the Vercel URL (e.g., workmeta.vercel.app)

# Or via dashboard:
# Vercel → Add New → Project → import repo
#   Root: apps/web | Build: npm run build | Output: dist | Install: npm ci
# Set: VITE_API_BASE_URL=/api (Production + Preview)
# Deploy
```

## Step 6: Configure Cross-Platform Security
After both services are deployed:

```powershell
# 1. In Render Dashboard → Service → Environment:
#    Update ALLOWED_HOSTS to include Vercel domain:
#    workmeta-api.onrender.com, workmeta.vercel.app

# 2. In Vercel Dashboard → Settings → Environment:
#    Verify VITE_API_BASE_URL = /api

# 3. Test cross-origin API through proxy:
curl https://workmeta.vercel.app/api/v1/health

# Should return 200 (proxy rewrites /api/* to Render backend)

# 3. Test CSRF flow:
# - Open https://workmeta.vercel.app in browser
# - Login renders (static, no API needed initially)
# - API calls through /api/* path should work with cookies

# 4. Test billing callback URL:
#    Must point at Vercel domain: /billing/checkout
#    The vercel.json rewrite serves the SPA;
#    the app reads ?reference= and verifies server-side
```

## Step 7: Post-Deployment Validation Checklist

### Render Verification
- [ ] `GET /api/v1/health` → 200, `database: ok`, `pgvector: true`
- [ ] `GET /api/v1/ready` → 200, all checks passing
- [ ] API responds to bearer token requests (extension auth)
- [ ] CORS headers present on API responses
- [ ] No 500 errors on admin routes (/api/v1/organizations, etc.)

### Vercel Verification
- [ ] Frontend loads at Vercel URL
- [ ] Static assets serve correctly (dist/)
- [ ] `/api/*` calls proxy to Render successfully
- [ ] `/billing/checkout` falls back to index.html SPA
- [ ] No console errors for CORS or network failures

### Database Verification
- [ ] `alembic current` shows `20261112_0007 (head)`
- [ ] `CREATE EXTENSION IF EXISTS vector;` works
- [ ] `CREATE EXTENSION IF EXISTS citext;` works
- [ ] Tenant isolation: cross-org API calls return 404

### Security Verification
- [ ] No secrets in browser dev tools (grep for `sk-`, `SECRET`, passwords)
- [ ] Session cookies have `SameSite=Lax` + `Secure` (production)
- [ ] `ALLOWED_HOSTS` not `["*"]` in production
- [ ] `CORS_ORIGINS` not `["*"]` in production
- [ ] `BILLING_PROVIDER` not `mock` if going live (or document as evaluation mode)

## Troubleshooting Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `pgvector: Missing` | pgvector extension not created | `CREATE EXTENSION IF NOT EXISTS vector;` in Neon SQL editor |
| `database_unavailable` | Wrong DATABASE_URL or DB paused | Verify Neon project is active; check connection string |
| Health returns 503 | AI provider unavailable | Set `AI_API_KEY=` for offline fake provider, or configure real key |
| CORS errors | ALLOWED_HOSTS or CORS_ORIGINS not set | Set to deployed hostnames in Render dashboard |
| Build fails on Render | Dockerfile issue | Verify `CMD ["bash", "-c", "python -m uvicorn ..."]` structure |
| Frontend 404 on refresh | SPA routing not configured | Verify vercel.json rewrites are correct |
| Billing webhook 404 | Callback URL misconfigured | Set `BILLING_CHECKOUT_CALLBACK_URL` to Vercel domain |
| Sessions not persisting | `APP_ENV=production` rejects mock | Keep `APP_ENV=development` on Free tier until ready for prod |
| `alembic upgrade head` hangs | Migration running long time | Check database connectivity; verify extensions are created |

## Rollback Plan
If deployment fails:
1. Render → Service → Delete (preserves database and data)
2. Or: Render → Service → Manual Deploy → Previous successful deployment
3. Vercel → Settings → Delete Site (frontend only, backend unaffected)
4. Database remains intact on Neon; re-apply migrations if needed:
   ```powershell
   python -m alembic upgrade head
   ```

## Required Manual Actions (After Initial Setup)
1. ✅ Verify `.env` DATABASE_URL values match Neon connection string exactly
2. ✅ Set `ALLOWED_HOSTS` to Render hostname + Vercel hostname (if applicable)
3. ✅ Set `BILLING_CHECKOUT_CALLBACK_URL` to Vercel domain + `/billing/checkout`
4. ✅ Test all health endpoints before declaring production-ready
5. ✅ Document deployment URLs for team reference
6. ✅ Set up monitoring alert if (or when) error rates increase