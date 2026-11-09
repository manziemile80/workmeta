# Render + Neon Deployment — WorkMeta AI (Free Tier)

> Free development deployment. Mock payments only — **never live keys**.
> RLS, tenant isolation, webhook verification and authorization are unchanged.

## Dashboard settings (Render)

| Setting | Value |
|---|---|
| Service type | Web Service (Docker) |
| Name | `workmeta-api` |
| Runtime | Docker |
| Dockerfile path | `apps/api/Dockerfile` |
| Build context | Repository root (Dockerfile `COPY apps/api/...`) |
| Plan | Free |
| Region | Nearest to users (e.g. Oregon) |
| Auto-deploy | `false` (manual — avoids surprise rebuilds) |
| Health check path | `/api/v1/health` (liveness; use `/api/v1/ready` to probe DB) |

**Startup command:** handled by the image (`bash -c "uvicorn ... --port ${PORT:-8000}"`).
Render injects `$PORT`. The API listens on `0.0.0.0:$PORT`.

- Build: `docker build -f apps/api/Dockerfile -t workmeta-api .` (repo root context).
- Run (local check): `docker run -e PORT=8000 -e APP_ENV=development -p 8000:8000 workmeta-api`.

## Environment variables (Render Dashboard → Environment)

`render.yaml` declares every key (`sync: false` = dashboard-only secrets).
Required — set real values, never commit them:

| Variable | Value / source |
|---|---|
| `DATABASE_URL` | Neon connection string, scheme rewritten to `postgresql+asyncpg://`, `?sslmode=require` kept |
| `DATABASE_MIGRATION_URL` | Same Neon string (owner role) |
| `SESSION_SECRET` | ≥32 random chars (`secrets.token_urlsafe(48)`) |
| `AI_API_KEY` | OpenAI-compatible key, or empty (fake offline provider) |
| `BILLING_MOCK_WEBHOOK_SECRET` | Random; mock webhooks sign with it |
| `CORS_ORIGINS` | Vercel frontend origin, e.g. `https://your-project.vercel.app` |
| `ALLOWED_HOSTS` | Render host, e.g. `workmeta-api.onrender.com` (+ Vercel host if frontend is also proxied) |
| `BILLING_CHECKOUT_CALLBACK_URL` | `https://your-project.vercel.app/billing/checkout` |
| `APP_ENV` | `development` **(intentional on free tier)** — `production` refuses `BILLING_PROVIDER=mock` and placeholder hosts. Keep gateway on `mock` until sandbox PASS. |

Neon: create a free project, copy the connection string, replace `postgresql://`
with `postgresql+asyncpg://`, keep `?sslmode=require`. Enable `pgvector`
extension (Neon → SQL: `CREATE EXTENSION IF NOT EXISTS vector;`). Pool via
Neon's pooled endpoint if connection counts grow; asyncpg pool is 5 + 10 overflow.

## Migrations (safe, one-off)

Free has no pre-deploy hook and the container never auto-migrates. From a
local shell with `DATABASE_MIGRATION_URL` pointed at Neon:

```bash
cd apps/api
DATABASE_MIGRATION_URL=<neon-url> .\.venv\Scripts\python.exe -m alembic upgrade head
```

Or Render → Shell → same command with the service env loaded. Verify:
`alembic current` prints `20261112_0007 (head)`, then `curl <render>/api/v1/ready`.

## Free-tier limits (what cannot run)

- **Ingestion worker** (`app.workers.ingestion`) — no separate service on Free.
  Uploads commit + queue; documents stay `uploaded/processing` until a worker
  drains. Run manually on a schedule (Render Shell / local `--drain`).
- **Billing worker** (`app.workers.billing`) — same; lifecycle still advances
  lazily via `ensure_subscription` on every billing/quota read (no scheduler needed).
- **Ephemeral disk** — `STORAGE_DIR` resets on restart; re-upload after restart.
- **Sleep** — service cold-starts (~30–60s) after inactivity; set Render health
  check to `/api/v1/health` to avoid LB killing a slow boot.

## Vercel → Render connection

Frontend `VITE_API_BASE_URL=https://<render-host>`. Cross-origin cookie auth
requires `SameSite=None; Secure` awareness: the API sets `SameSite=Lax` +
`Secure` (production) on `wm_session`/`wm_csrf`. Cross-site `fetch` with
`credentials: include` on `SameSite=Lax` **does not** send the cookie on
cross-site POST. Options (pick one honestly):

1. **Same-origin proxy (recommended):** Vercel `vercel.json` rewrite
   `/api/*` → `https://<render-host>/api/*` and clear `VITE_API_BASE_URL`.
   Cookies then stay first-party. CSRF double-submit already implemented.
2. **Bearer (extension):** `Authorization: Bearer …` works cross-origin
   (no cookies). Web UI uses cookies, so this alone doesn't fix the Vercel UI.

CORS on the API must list the exact Vercel origin (`CORS_ORIGINS`) with
`allow_credentials=true` (already set). `ALLOWED_HOSTS` must list the Render
host. No backend code change needed for either.
