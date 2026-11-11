# Cloud Provisioning (Neon + Render) — WorkMeta AI

Free-tier, single-instance deployment. The Vercel frontend is already live at
**https://workmeta.vercel.app**; this covers the missing backend and database.

## 1. Credentials to provide

| Variable | Where to create it | Why |
|---|---|---|
| `NEON_API_KEY` | Neon Console → Account settings → API keys | Create the project, role, extensions and connection URLs |
| `NEON_OWNER_URL` (alternative to the key) | Neon → Project → **Connect** → copy the **direct** connection string for the owner role | Reuse an existing project; the script prepares extensions and `wm_app` without creating anything |
| `NEON_OWNER_PASSWORD` (simplest) | Neon → Project → **Connect** → the password of role `neondb_owner` | Same as above, but you only copy one value; host/database/role default to the WorkMeta project |
| `RENDER_API_KEY` | Render Dashboard → Account Settings → API Keys | Create the Docker web service and set its env vars |
| `NEON_ORG_ID` (optional) | Neon → Organization settings | Auto-detected for personal keys; set if the account has several orgs |
| `RENDER_OWNER_ID` (optional) | Render → Workspace settings | Auto-detected from the API key |

> The Neon **Auth** URL (`...neonauth....neon.tech/.../.well-known/jwks.json`)
> is not a database endpoint. Use the Postgres host from **Connect**:
> `...@ep-<name>.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require`.

**Render must already have the GitHub App authorized for
`manziemile80/workmeta`.** Without that, service creation fails with a
GitHub-connection error; authorize it at Render → Account Settings → GitHub.

Put the credentials in the gitignored file `var/cloud.env`:

```dotenv
# Option A (full automation, new project):
NEON_API_KEY=...
# Option B (reuse the existing project/database) - full connection string:
NEON_OWNER_URL=postgresql://neondb_owner:<password>@ep-<name>.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require
# Option B' - only the password, everything else is defaulted:
NEON_OWNER_PASSWORD=<password of neondb_owner>

RENDER_API_KEY=...
```

> **Headless alternative (no password needed at all):** add the Neon MCP server
> and authenticate with OAuth, which exposes the project's SQL access:
>
> ```powershell
> opencode mcp add neon --url https://mcp.neon.tech/mcp --global
> opencode mcp auth neon
> ```
>
> The browser login authorises the MCP client; the connection string can then
> be retrieved (or the owner password rotated) without anyone pasting a secret.

Do not commit this file (`var/` and `.env*` are ignored).

## 2. Run the provisioning script

```powershell
# from the repo root
apps\api\.venv\Scripts\python.exe tools\scripts\provision_cloud.py all
```

The `all` step performs, in order:

1. **Neon** — create/reuse project `workmeta` (Postgres 17), wait for it, enable
   `vector` + `citext`, create the restricted `wm_app` LOGIN role with a fresh
   password, grant `CONNECT`/schema usage, and write:
   - `var/neon.env` — `DATABASE_URL` (wm_app) + `DATABASE_MIGRATION_URL` (owner)
   - `var/neon.json` — masked URLs for reference
   - `var/deploy-secrets.env` — app DB password, `SESSION_SECRET`,
     `BILLING_MOCK_WEBHOOK_SECRET`, seed-owner credentials
2. **Migrations** — `alembic upgrade head` with those URLs (expect
   `20261112_0007 (head)`).
3. **Seed** — create the platform owner from `var/deploy-secrets.env`
   (`owner@workmeta.ai` by default). Public self-signup is intentionally absent.
4. **Render** — create the `workmeta-api` Docker web service (`apps/api/Dockerfile`,
   repo-root context, free plan), set every env var including
   `CORS_ORIGINS=https://workmeta.vercel.app`,
   `ALLOWED_HOSTS=<onrender-host>,workmeta.vercel.app`,
   `BILLING_CHECKOUT_CALLBACK_URL=https://workmeta.vercel.app/billing/checkout`,
   and wait until the deploy status is `live`.

Individual steps: `neon`, `migrate`, `seed`, `render`.

## 3. Point Vercel at the backend

Set the rewrite destination in the root `vercel.json` to the Render host printed
by the script:

```json
{ "source": "/api/:path*", "destination": "https://<onrender-host>/api/:path*" }
```

Then deploy from the repo root (the project's Root Directory is `.`):

```powershell
vercel deploy --prod --yes
```

## 4. Verify

```powershell
curl.exe -s https://workmeta.vercel.app/api/v1/health
curl.exe -s https://workmeta.vercel.app/api/v1/ready
```

`/ready` must report `pgvector: true`. Then log in with the seeded owner through
the Vercel UI (cookies + CSRF go through the same-origin proxy).

## Notes

- Use Neon's **direct** endpoint for both URLs on this single-instance free
  deployment; the pooler's transaction mode can break asyncpg prepared
  statements. The asyncpg pool (5 + 10) is well within Neon Free limits.
- `APP_ENV=development` is intentional on the free tier: `production` refuses
  `BILLING_PROVIDER=mock`. The deployment still sets `COOKIE_SECURE=true`,
  `ENABLE_HSTS=true` and `ENABLE_DOCS=false`, so session cookies are `Secure`,
  HSTS is emitted and `/docs` is disabled even though the environment name is
  `development`. Live payments stay disabled until a real gateway is configured.
- Free Render sleeps after inactivity (~30–60 s cold start) and has ephemeral
  storage. See `docs/FREE_DEPLOYMENT_LIMITATIONS.md`.
