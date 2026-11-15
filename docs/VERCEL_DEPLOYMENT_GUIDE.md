# WORKMETA — Vercel Deployment Guide

## Prerequisites (Before Starting)
- [ ] GitHub repository connected (workmeta at GitHub)
- [ ] Render backend deployed and running (completed — Phase 2)
- [ ] Vercel account created at https://vercel.com
- [ ] Node.js/npm available (v24 as specified in vercel.json)
- [ ] This guide for step-by-step configuration

## VERCEL PROJECT SETUP

### Step 1: Connect GitHub Repository
1. Log in to Vercel Dashboard (https://vercel.com)
2. Click **Add New → Project**
3. **Import Repository**: Select your GitHub repository (`workmeta`)
4. **Root Directory:** `apps/web` (specify exactly)
5. **Framework Preset:** `Vite`
6. **Build Command:** `npm run build` (auto-filled, keep as-is)
7. **Install Command:** `npm ci` (auto-filled, keep as-is)
8. **Output Directory:** `dist` (auto-filled, keep as-is)
9. **Node.js Version:** `24` (as specified in vercel.json; keep as-is)
10. **Project Name:** `workmeta` (or your preferred name)
11. Click **Create Project**

### Step 2: Configure Environment Variables
Navigate to **Project → Settings → Environment Variables** and add:

| Variable | Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | **Required** — enables same-origin proxy via vercel.json rewrites |
| (Add only this variable for minimal setup) | | |

**Do NOT add** any backend secrets to the frontend bundle. The frontend only needs `VITE_API_BASE_URL`.

### Step 3: Deploy
1. In the Vercel Dashboard, go to **Deployments**
2. Click **Trigger Deploy** → **Deploy**
3. Wait 2-5 minutes for Vite build to complete
4. The deployment will be live at a URL like: `https://workmeta.vercel.app`

### Step 3: Verify Deployment
1. Open the Vercel URL in a browser
2. The login screen should render (static, no API needed initially)
3. Open console — should have no CORS or network errors

## VERCEL CONFIGURATION VERIFICATION

### `apps/web/vercel.json` (Already Verified)
The `vercel.json` is pre-configured with:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "vite",
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "installCommand": "npm ci",
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://workmeta-api.onrender.com/api/:path*" },
    { "source": "/billing/checkout", "destination": "/index.html" }
  ],
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "Referrer-Policy", "value": "no-referrer" }
      ]
    }
  ]
}
```

### Rewrites Explanation
| Rewrite | Purpose |
|---|---|
| `/api/:path*` → `https://workmeta-api.onrender.com/api/:path*` | Proxies all API calls to Render backend |
| `/billing/checkout` → `/index.html` | SPA fallback for billing checkout page |

### Same-Origin Proxy Architecture
- Vercel rewrites `/api/*` → Render backend
- Because the API calls are now **same-origin** (served from the Vercel domain),
  cookies become first-party
- `SameSite=Lax` + `Secure` cookies (set by Render API) will flow naturally
- CSRF double-submit works without cross-site workarounds
- `VITE_API_BASE_URL=/api` is the key — it tells the frontend to use `/api`
  paths, which the vercel.json rewrites to the Render backend

### Vite Configuration (`vite.config.ts`)
The Vite config should be standard — no special proxy config needed because
vercel.json handles the rewrites. The config should have:

```typescript
// vite.config.ts (standard Vite, no custom proxy needed)
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // No custom server/proxy needed — vercel.json handles it
})
```

## VERCEL DEPLOYMENT CHECKLIST

### Before Deploy
- [ ] GitHub repo connected (Root: `apps/web`)
- [ ] Framework: `Vite`
- [ ] Build Command: `npm run build`
- [ ] Output Directory: `dist`
- [ ] Node.js Version: `24`
- [ ] Project Name: chosen
- [ ] Environment variable: `VITE_API_BASE_URL = /api` added
- [ ] No other environment variables needed (AI keys are server-side)

### After Deploy
- [ ] Deployment status: `Ready` (not errored)
- [ ] Frontend URL opens in browser (e.g., `https://workmeta.vercel.app`)
- [ ] Login screen renders (static, no API needed)
- [ ] No console errors for CORS or network failures
- [ ] `VITE_API_BASE_URL` is set correctly in Vercel Dashboard

### API Proxy Test
```powershell
# Test that the frontend can reach the backend through the proxy
# Open the Vercel URL and check:

# 1. The page loads without network errors
# 2. If there are API forms, they use /api/ paths
# 3. Console has no cross-origin warnings

# Alternative: use curl through the proxy
curl -s https://workmeta.vercel.app/api/v1/health

# Expected: Should be redirected or return 404 (since there's no API at
# the root, but no CORS errors). The important thing is NO CORS errors.
```

## VERCEL + RENDER INTEGRATION

### Step 4: Configure Cross-Platform Security
After both services are deployed:

#### In Render Dashboard → Service → Environment:
1. Update `ALLOWED_HOSTS` to include the Vercel domain:
   ```
   workmeta-api.onrender.com,workmeta.vercel.app
   ```
2. Update `CORS_ORIGINS` to include the Vercel domain:
   ```
   https://workmeta.vercel.app
   ```
   (sync: false — dashboard only)

#### In Vercel Dashboard → Settings → Environment:
1. Verify `VITE_API_BASE_URL = /api` is set
2. Do NOT add `ALLOWED_HOSTS` or `CORS_ORIGINS` to Vercel — these are
   backend concerns; adding them to the frontend bundle is unnecessary
   and could expose internal configuration.

### Step 5: Test Complete Flow
```powershell
# 1. Open the Vercel URL in a browser
# 2. Login (static page, no API call needed initially)
# 3. Navigate to features that use /api/* paths
# 4. Verify no CORS errors in console
# 5. Test API calls work through the proxy

# 2. Test billing checkout path:
#    /billing/checkout should fall back to index.html (SPA)
#    The app reads ?reference= and verifies server-side

# 3. Test health through proxy:
#    Open: https://workmeta.vercel.app/api/v1/health
#    Should either return 404 (no API at root) or redirect,
#    but WITHOUT CORS errors
```

## VERCEL DEPLOYMENT TROUBLESHOOTING

| Issue | Cause | Fix |
|---|---|---|
| Build fails | Node version mismatch | Ensure Node.js v24; check vercel.json |
| `VITE_API_BASE_URL` not set | Environment variable not added | Add in Vercel Dashboard → Settings → Environment |
| CORS errors in console | Hosts not coordinated | Coordinate: Render `ALLOWED_HOSTS` + `CORS_ORIGINS` + Vercel `VITE_API_BASE_URL=/api` |
| `/api/*` calls return 404 | Rewrite not working | Verify `VITE_API_BASE_URL = /api` is set; verify vercel.json rewrites |
| Console logs show “blocked by CORS” | Cookies not first-party | Ensure the architecture: Vercel → `/api/*` → Render; cookies same-origin |
| Build takes too long | Dependency installation slow | First build on Free tier can be slow; subsequent builds are faster |
| Custom domain issues | DNS not configured | Optional; use default `workmeta.vercel.app` for testing |

## VERCEL DEPLOYMENT CHECKLIST

### Pre-Deploy Checklist
- [ ] GitHub repo: Root = `apps/web`
- [ ] Framework = `Vite`
- [ ] Build command = `npm run build`
- [ ] Output directory = `dist`
- [ ] Node.js version = `24`
- [ ] Environment variable `VITE_API_BASE_URL = /api` added
- [ ] No secrets in environment variables
- [ ] vercel.json is the default (not customized beyond defaults)

### Post-Deploy Checklist
- [ ] Deployment status: `Ready`
- [ ] Frontend URL accessible (e.g., `https://workmeta.vercel.app`)
- [ ] Login screen renders (static)
- [ ] No CORS errors in console
- [ ] `VITE_API_BASE_URL` confirmed in Dashboard
- [ ] `/billing/checkout` falls back to SPA correctly
- [ ] Ready to coordinate with Render (Phase 4)

## READY FOR PHASE 4 — PRODUCTION SECURITY

After both Render and Vercel are deployed and verified, proceed to Phase 4
to check production security settings. If either deployment is still in progress,
complete that phase first before moving to security.

## NEXT: PROCEED TO PHASE 4 — PRODUCTION SECURITY

Continue to security verification after both deployments succeed.