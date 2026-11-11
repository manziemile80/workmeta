# Vercel Deployment — WorkMeta AI Frontend

> Stage 9A + same-origin fix. Frontend is a Vite SPA (no React Router —
> view state in `App.tsx`). Backend is **separate** (Render); the proxy
> below makes it **same-origin**, so cookies/CSRF work without cross-site
> `fetch` hacks. **API integration is not claimed** until `GET /api/v1/health`
> returns 200 through the Vercel origin.
>
> **Deployed 2026-11-11:** project `workmeta` (team `manziemile1-5955s-projects`),
> production alias **https://workmeta.vercel.app** (HTTP 200). API proxy still
> points at the placeholder Render host until the backend service exists.

## Vercel readiness

- Build: `npm run build:web` (`tsc --noEmit && vite build` in the workspace) passes (331.9 kB JS, gzip 99.5 kB).
- Output: `apps/web/dist/` (served statically).
- `vercel.json` lives at the **repo root** because the Vercel project's Root
  Directory is `.` (the CLI uploads the directory it runs in; deploying from
  `apps/web` omitted the root lockfile and broke `npm ci`).
  It sets framework `vite`, `npm ci`, build `npm run build:web`, output
  `apps/web/dist`, **one** `/api/:path*` rewrite → Render host, one
  `/billing/checkout` → `/index.html` rewrite (payment callback), and security
  headers. No duplicate SPA catch-all.
- `.vercelignore` excludes `var/`, `.env*`, `node_modules`, `.venv`, caches and
  build output so local database dumps/secrets are never uploaded.
- Frontend requests `VITE_API_BASE_URL + '/api/...'` with
  `credentials: include` + `X-CSRF-Token` from the `wm_csrf` cookie.
  With `VITE_API_BASE_URL=/api` they are same-origin through the proxy —
  `SameSite=Lax` cookies flow first-party. No insecure workarounds.
- No backend secrets in the bundle: only `VITE_API_BASE_URL` (public path).
  Grep proof: `Select-String apps/web/src -Pattern 'localhost|127\.0\.0\.1|sk-|SECRET'`
  returns only the dev `docsUrl` fallback + network-error copy, no keys.

## Deploy (Windows PowerShell, exact)

The project is linked and authenticated on this machine (CLI v65.12.1). Deploy
from the **repo root**, never from `apps/web`:

```powershell
vercel link --yes --project workmeta
vercel env add VITE_API_BASE_URL --environment production,preview --value /api --no-sensitive --yes
vercel deploy --prod --yes
```

The project is also connected to `github.com/manziemile80/workmeta`, so pushes
to `main` auto-deploy. Dashboard alternative: Vercel → Add New → Project →
import `manziemile80/workmeta` → **Root Directory `.`**, Vite preset, Build
`npm run build:web`, Output `apps/web/dist`, Install `npm ci` → set
`VITE_API_BASE_URL=/api` → Deploy.

| Setting | Value |
|---|---|
| Framework Preset | Vite |
| Root Directory | `.` (repo root) |
| Build Command | `npm run build:web` |
| Output Directory | `apps/web/dist` |
| Install Command | `npm ci` |
| Node.js Version | 24 |
| Production Branch | `main` |
| Project Name | `workmeta` → `https://workmeta.vercel.app` |

## Domain `workmeta.vercel.app`

- **Claimed 2026-11-11** by project `workmeta`; the production alias returns
  HTTP 200. Keep the name mirrored in `CORS_ORIGINS`, `ALLOWED_HOSTS`
  (backend) and `BILLING_CHECKOUT_CALLBACK_URL`.
- If you ever rename it, update those backend vars — never silently change the
  requested name.

## Frontend environment variables

Set in Vercel Dashboard → Settings → Environment Variables (Production + Preview):

| Variable | Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | **Required for same-origin proxy.** The `/api` prefix is rewritten to the Render backend in `vercel.json`. Never put backend secrets here. |

**One manual step:** edit `vercel.json` at the **repo root** line
`"destination": "https://workmeta-api.onrender.com/api/:path*"` to the real
Render host (the placeholder is not a real hostname), commit, and redeploy.

## Backend CORS / hosts

On the Render service set:

```dotenv
CORS_ORIGINS=<none-or-vercel-preview>  # same-origin proxy: can be empty/no-op
ALLOWED_HOSTS=<render-host>,<vercel-host>
APP_ENV=development  # production would refuse mock; keep mock on free tier
BILLING_CHECKOUT_CALLBACK_URL=https://<vercel-host>/billing/checkout
```

With the proxy, `CORS_ORIGINS` is not consulted for `/api` traffic (same origin),
but keep it set for direct-API calls (mobile/extension bearer).

## Checkout callback

`BILLING_CHECKOUT_CALLBACK_URL` must point at the Vercel domain
`/billing/checkout`. The rewrite serves the SPA; the app reads `?reference=`
and verifies server-side. Update only that backend var — no frontend change.

## Verify

1. Open the Vercel URL — login screen renders (static, no API needed).
2. `curl https://<vercel-url>/api/v1/health` → `{"status":"ok"}` (via proxy).
3. Log in through the Vercel UI (cookie + CSRF round-trip via proxy).
4. `GET /api/v1/ready` through the proxy confirms Neon.

## Remaining backend dependencies

- Backend (Render) + Neon + migrations per `docs/RENDER_DEPLOYMENT.md`.
- `VITE_API_BASE_URL=/api` deployed, vercel.json host set, CORS/ALLOWED_HOSTS set.
- Live payments disabled until sandbox PASS.
