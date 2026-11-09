# Vercel Deployment — WorkMeta AI Frontend

> Stage 9A + same-origin fix. Frontend is a Vite SPA (no React Router —
> view state in `App.tsx`). Backend is **separate** (Render); the proxy
> below makes it **same-origin**, so cookies/CSRF work without cross-site
> `fetch` hacks. **API integration is not claimed** until `GET /api/v1/health`
> returns 200 through the Vercel origin.

## Vercel readiness

- Build: `npm run build` (`tsc --noEmit && vite build`) passes (331 kB JS, gzip 99 kB).
- Output: `dist/` (served statically).
- `vercel.json`: framework `vite`, `npm ci`, build `npm run build`, output `dist`,
  **one** `/api/:path*` rewrite → Render host, one `/billing/checkout` → `/index.html`
  rewrite (payment callback), security headers. No duplicate SPA catch-all.
- Frontend requests `VITE_API_BASE_URL + '/api/...'` with
  `credentials: include` + `X-CSRF-Token` from the `wm_csrf` cookie.
  With `VITE_API_BASE_URL=/api` they are same-origin through the proxy —
  `SameSite=Lax` cookies flow first-party. No insecure workarounds.
- No backend secrets in the bundle: only `VITE_API_BASE_URL` (public path).
  Grep proof: `Select-String apps/web/src -Pattern 'localhost|127\.0\.0\.1|sk-|SECRET'`
  returns only the dev `docsUrl` fallback + network-error copy, no keys.

## Deploy (Windows PowerShell, exact)

Vercel CLI is **not** installed/authenticated on this machine — deploy via
the dashboard (recommended) or CLI after install:

```powershell
# 1. Install + login (browser opens)
npm i -g vercel
vercel login

# 2. Link + deploy (from repo root; Root Directory apps/web is in vercel.json)
vercel init
#    Framework: Vite | Root: apps/web | Build: npm run build | Output: dist | Install: npm ci
vercel link --name workmeta
vercel deploy --prod
#    Then set VITE_API_BASE_URL=/api in Dashboard, redeploy.
```

Dashboard alternative (recommended): Vercel → Add New → Project → import
`manziemile80/workmeta` → Root `apps/web`, Vite preset, Build `npm run build`,
Output `dist`, Install `npm ci` → set `VITE_API_BASE_URL=/api` → Deploy.

| Setting | Value |
|---|---|
| Framework Preset | Vite |
| Root Directory | `apps/web` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Install Command | `npm ci` |
| Node.js Version | 24 |
| Production Branch | `main` |
| Project Name | `workmeta` → `https://workmeta.vercel.app` |

## Domain `workmeta.vercel.app`

- **Availability: NOT TESTED** here (no Vercel account/CLI on this machine,
  so the name could not be checked or claimed). In the dashboard, create the
  project with that name; Vercel reports availability on submit.
- If taken, pick a close variant (e.g. `workmeta-app.vercel.app`) — never
  silently change the requested name. The chosen name must be mirrored in
  `CORS_ORIGINS`, `ALLOWED_HOSTS` (backend) and
  `BILLING_CHECKOUT_CALLBACK_URL`.

## Frontend environment variables

Set in Vercel Dashboard → Settings → Environment Variables (Production + Preview):

| Variable | Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | **Required for same-origin proxy.** The `/api` prefix is rewritten to the Render backend in `vercel.json`. Never put backend secrets here. |

**One manual step:** edit `apps/web/vercel.json` line
`"destination": "https://workmeta-api.onrender.com/api/:path*"` to your real
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
