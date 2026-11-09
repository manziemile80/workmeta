# Vercel Deployment — WorkMeta AI Frontend

> Stage 9A. The frontend in `apps/web` is a Vite SPA (no React Router — view
> state in `App.tsx`). Backend is **not** deployed by Vercel; the API must
> already be reachable (staging/production host) before the frontend can
> function. API integration is **not claimed** until `GET /api/v1/health`
> returns 200 through the Vercel frontend origin.

## Vercel readiness

- Build: `npm run build` (`tsc --noEmit && vite build`) passes (331 kB JS, gzip 99 kB).
- Output: `dist/` (served statically).
- `vercel.json` (minimal): framework `vite`, `npm ci`, build `npm run build`,
  output `dist`, rewrites for `/billing/checkout` + SPA fallback, security
  headers. No `/api` proxy rewrite (API is a separate origin — see below).
- No backend secrets in the bundle: only `VITE_API_BASE_URL` (public origin).
  Grep proof: `Select-String apps/web/src -Pattern 'localhost|127\.0\.0\.1|sk-|SECRET'`
  returns only the dev `docsUrl` fallback + network-error copy, no keys.

## Vercel project settings (Dashboard → Settings)

| Setting | Value |
|---|---|
| Framework Preset | Vite |
| Root Directory | `apps/web` (if the repo root is the project) — or leave blank with `vercel.json` inside `apps/web` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Install Command | `npm ci` |
| Node.js Version | 24 |
| Production Branch | `main` |

## Frontend environment variables

Set in Vercel Dashboard → Settings → Environment Variables (Production + Preview):

| Variable | Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `https://api.example.com` (no trailing slash) | **Required.** Frontend calls `${VITE_API_BASE_URL}/api/v1/...` with `credentials: include` + `X-CSRF-Token` from the `wm_csrf` cookie. If empty, requests are relative (`/api/...`) and hit Vercel itself (404). |

Copy `.vercel.example` values only; never commit real secrets.

## Backend CORS for the Vercel domain

On the API host set:

```dotenv
CORS_ORIGINS=https://your-project.vercel.app,https://staging.example.com
# also:
ALLOWED_HOSTS=api.example.com,<your-vercel-host>  # TrustedHost allow-list
APP_ENV=production
```

Cookies (`wm_session`, `wm_csrf`) are `HttpOnly` + `SameSite=Lax` + `Secure`
in production. Cross-site (`vercel.app` → `api.example.com`) requires CORS
`allow_credentials=true` (already set) **and** the `X-CSRF-Token` double-submit
(already implemented). No backend code change is needed.

## Checkout callback URL

Payment providers redirect to `BILLING_CHECKOUT_CALLBACK_URL` (backend
config, e.g. `https://your-project.vercel.app/billing/checkout`). The
`vercel.json` rewrite serves the SPA at that path; the app reads
`?reference=` from the URL and verifies server-side. Update
`BILLING_CHECKOUT_CALLBACK_URL` on the API host to the Vercel domain before
enabling payments.

## Verify (Vercel preview/production)

1. Open the Vercel URL — the login screen renders (static, no API needed).
2. `curl https://<vercel-url>/billing/checkout?reference=TEST` returns the
   SPA (rewrite works) — the app shows "Verify & activate" with the reference.
3. With `VITE_API_BASE_URL` set and the API up:
   `curl -s https://<api>/api/v1/health` → `{"status":"ok"}`, then log in
   through the Vercel UI (cookie + CSRF round-trip).
4. `GET /api/v1/metrics` on the API host confirms aggregates.

## Remaining backend dependencies

- Backend deployment (staging/prod host) + CORS/ALLOWED_HOSTS per above.
- `AI_API_KEY`, `SESSION_SECRET`, gateway sandbox keys on the API host only.
- Live payments stay disabled until sandbox PASS (`docs/RELEASE_CHECKLIST.md`).
