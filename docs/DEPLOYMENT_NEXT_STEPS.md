# WorkMeta AI — Deployment Next Steps

## Immediate Required Actions

### 1. Provision Managed PostgreSQL
- **Option A**: Create a Neon project (free tier)
  - Get connection string: `postgresql+asyncpg://user:password@ep-name.neon.io/dbname?sslmode=require`
  - Enable `pgvector` extension: `CREATE EXTENSION IF NOT EXISTS vector;`
- **Option B**: Use Render-managed PostgreSQL (same Docker image)
- **Option C**: Use Railway, Supabase, or AnySQLizr with pgvector support

### 2. Apply Database Migrations
Once the database is provisioned:

```bash
# From the repository root
export DATABASE_URL="postgresql+asyncpg://..."
export DATABASE_MIGRATION_URL="postgresql+asyncpg://owner:password@..."

# From apps/api directory
python -m alembic upgrade head

# Verify:
python -m alembic current  # Should print head
```

### 3. Deploy Backend to Render
1. In Render Dashboard: New Service → Docker
2. Name: `workmeta-api`
3. Runtime: Docker → Dockerfile path: `apps/api/Dockerfile`
4. Build context: Repository root
5. Plan: Free
6. Region: Nearest to users
7. Auto-deploy: `false` (manual — avoids surprise rebuilds)
8. Environment variables (Dashboard → Environment):
   - `APP_ENV=development`
   - `APP_NAME=WorkMeta AI API`
   - `API_V1_PREFIX=/api/v1`
   - `LOG_LEVEL=INFO`
   - `CORS_ORIGINS=<frontend-origin>` (sync: false)
   - `ALLOWED_HOSTS=<render-host>,<vercel-host>` (sync: false)
   - `MAX_REQUEST_BODY_BYTES=12582912`
   - `DATABASE_URL=<neon-or-managed-url>` (sync: false)
   - `DATABASE_MIGRATION_URL=<neon-or-managed-url-owner>` (sync: false)
   - `SESSION_SECRET=<keep-generated-value>` (sync: false)
   - `SESSION_TTL_DAYS=7`
   - `SESSION_IDLE_HOURS=8`
   - `INVITATION_TTL_DAYS=7`
   - `PASSWORD_RESET_TTL_MINUTES=60`
   - `BACKUP_RETENTION_DAYS=30`
   - `AI_PROVIDER=openai`
   - `AI_BASE_URL=https://api.openai.com/v1`
   - `AI_API_KEY=` (empty = offline fake provider; set for real provider)
   - `AI_CHAT_MODEL=gpt-4.1-mini`
   - `AI_EMBEDDING_MODEL=text-embedding-3-small`
   - `EMBEDDING_DIMENSIONS=1536`
   - `AI_TIMEOUT_SECONDS=60`
   - `AI_TEMPERATURE=0`
   - `AI_MAX_RETRIES=2`
   - `CHAT_MAX_OUTPUT_TOKENS=800`
   - `BILLING_PROVIDER=mock` (keep on free tier until sandbox PASS)
   - `BILLING_CURRENCY=RWF`
   - `BILLING_TRIAL_DAYS=14`
   - `BILLING_PAST_DUE_GRACE_DAYS=7`
   - `BILLING_MOCK_WEBHOOK_SECRET=dev-mock-webhook-secret` (sync: false)
   - `BILLING_CHECKOUT_CALLBACK_URL=https://<vercel-host>/billing/checkout` (sync: false)
   - `FLUTTERWAVE_BASE_URL=https://api.flutterwave.com/v3` (sync: false)
   - `FLUTTERWAVE_SECRET_KEY=` (sync: false)
   - `FLUTTERWAVE_WEBHOOK_HASH=` (sync: false)
   - `PAYSTACK_BASE_URL=https://api.paystack.co` (sync: false)
   - `PAYSTACK_SECRET_KEY=` (sync: false)
   - `PAYSTACK_WEBHOOK_SECRET=` (sync: false)
   - `STORAGE_BACKEND=local`
   - `STORAGE_DIR=/app/var/storage`
   - `RATE_LIMIT_ENABLED=true`
   - `RATE_LIMIT_LOGIN_PER_MINUTE=60`
   - `RATE_LIMIT_INVITE_ACCEPT_PER_MINUTE=60`
   - `RATE_LIMIT_WEBHOOK_PER_MINUTE=300`
   - `RATE_LIMIT_CHAT_PER_MINUTE=300`

### 4. Deploy Frontend to Vercel
1. Install Vercel CLI (or use dashboard)
2. Link project: `vercel link --name workmeta` (root: `apps/web`)
3. Set environment variable: `VITE_API_BASE_URL=/api`
4. Deploy: `vercel deploy --prod`
5. In Vercel Dashboard → Settings → Domain: set custom domain or use `workmeta.vercel.app`
6. In Render Dashboard: set `ALLOWED_HOSTS` to include the Vercel domain
7. In Vercel Dashboard → Settings → Environment: verify CORS settings

### 5. Configure Production Security
After initial deployment:

```bash
# Verify health endpoint
curl https://<render-host>/api/v1/health
# Expected: {"status":"ok","service":"WorkMeta AI API","version":"0.1.0","environment":"development","uptime_seconds":12.3}

# Verify readiness endpoint
curl https://<render-host>/api/v1/ready
# Expected: {"status":"ready","database":"ok","pgvector":true,"version":"0.1.0","environment":"development"}

# If using real AI provider, set AI_API_KEY and test:
# curl -X POST https://<render-host>/api/v1/organizations/.../assistants \
#   -H "Authorization: Bearer ..." \
#   -H "Content-Type: application/json" \
#   -Body '{"name":"Test","product_code":"ebm"}'
```

### 6. Post-Deployment Validation
- [ ] `GET /api/v1/health` returns 200 with database & pgvector status
- [ ] `GET /api/v1/ready` returns 200 with all checks passing
- [ ] Cross-origin API calls work through Vercel proxy (`/api/:path*` rewrite)
- [ ] Billing endpoints return mock responses (if using mock provider)
- [ ] Admin API functions (create org, create assistant, upload doc) work correctly
- [ ] RLS policies restrict cross-tenant access (test with separate orgs)

### Troubleshooting
| Symptom | Cause | Fix |
|---|---|---|
| `pgvector: Missing` | PostgreSQL doesn't have pgvector extension | Run: `CREATE EXTENSION IF NOT EXISTS vector;` |
| `database_unavailable` | Wrong credentials or DB not running | Check DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD |
| `alembic current` hangs | Migration running long time | Check database connectivity; verify pgvector extension |
| Health check returns 503 | AI provider unavailable | Set `AI_API_KEY=` for offline fake provider, or configure real key |
| CORS errors | ALLOWED_HOSTS or CORS_ORIGINS not set | Set to deployed hostname(s) in Render dashboard |
| Build fails on Render | Dockerfile issue | Verify `CMD ["bash", "-c", "python -m uvicorn ..."]` works |

### Success Criteria
Deployment is successful when:
1. ✅ Render service health: `GET /api/v1/health` → 200, database ok, pgvector true
2. ✅ Render service ready: `GET /api/v1/ready` → 200, all checks passing
3. ✅ Vercel UI loads at deployed URL
4. ✅ Cross-origin API calls work through `/api/:path*` rewrite
5. ✅ Billing/mock webhooks function correctly
6. ✅ No secrets exposed in browser bundles or logs
7. ✅ Tenant isolation verified (cross-tenant reads return 404, not 403 — the API
   never confirms that another tenant's organization exists)