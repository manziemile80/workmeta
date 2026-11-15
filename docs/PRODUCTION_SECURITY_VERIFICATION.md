# WORKMETA — Production Security Verification

## Overview
After both Render and Vercel deployments succeed, verify these security
aspects before declaring the application production-ready.

## RENDER — Security Checks

### 1. Session Cookies & HTTPS
- [ ] HTTPS is enforced (Render provides HTTPS termination automatically)
- [ ] Session cookies have `SameSite=Lax` (default) + `Secure` (production)
- [ ] Verify: Open `https://workmeta-api.onrender.com` in browser
- [ ] Inspect `wm_session` cookie: should have `Secure` flag in production
- [ ] In production (`APP_ENV=production`), the `_production_secrets()` validator
  ensures `SESSION_SECRET >= 32 chars` — already verified in `.env`

### 2. Authentication & Authorization
- [ ] Login/create organization works via API
- [ ] Bearer token authentication (extension: `kind: extension`)
- [ ] Tenant isolation: cross-org API calls return 404
- [ ] Platform owner cannot see client documents/conversations (by design)
- [ ] RBAC: `client_admin` role has appropriate permissions

### 3. CORS Restrictions
- [ ] `CORS_ORIGINS` set to `https://workmeta.vercel.app` (your Vercel domain)
- [ ] `ALLOWED_HOSTS` set to `workmeta-api.onrender.com,workmeta.vercel.app`
- [ ] Neither should be `["*"]` in production
- [ ] API responses include `Access-Control-Allow-Origin` header
- [ ] Preflight `OPTIONS` requests succeed for `/api/*` endpoints

### 4. Database Security
- [ ] `wm_app` role does NOT have superuser, CREATEDB, or BYPASSRLS
- [ ] RLS (Row-Level Security) is ENABLED and FORCE on all tenant tables
- [ ] Policies read `app.current_org` / `app.current_user` settings
- [ ] No raw SQL can leak one tenant's context into another request
- [ ] pgvector and citext extensions are created (already verified)

### 5. Secret Management
- [ ] No secrets in browser dev tools (grep for `sk-`, `SECRET`, passwords)
- [ ] All secrets marked `sync: false` in Render Dashboard
- [ ] `.env` is gitignored and never committed
- [ ] Session secret matches generated value from `.env`
- [ ] DATABASE_URL and DATABASE_MIGRATION_URL are not exposed in any way
- [ ] AI_API_KEY (if set) is server-side only; never shipped to browsers

### 6. File Upload Validation
- [ ] `MAX_UPLOAD_BYTES=10485760` (10 MiB) enforced
- [ ] `MAX_DOCUMENT_PAGES=200` enforced
- [ ] Uploads validated by magic bytes (MIME type detection)
- [ ] Files stored under random keys (path traversal impossible)
- [ ] Download served only through authorized endpoint with `Content-Disposition: attachment`

### 7. Production Debug Settings
- [ ] `APP_ENV=production` in Render Dashboard (not `development`)
- [ ] Production validator `_production_secrets()` passes:
  - `SESSION_SECRET >= 32 chars` ✅
  - `DATABASE_URL` no longer has `change-me` placeholder ✅
  - `BILLING_PROVIDER != "mock"` — needs real provider for launch ⚠️
  - `ALLOWED_HOSTS` not `["*"]` — must list public hostnames ✅
  - `CORS_ORIGINS` not `["*"]` ✅
- [ ] `DEBUG` mode not enabled (FastAPI/uvicorn default is off)

### 8. Rate Limiting
- [ ] `RATE_LIMIT_ENABLED=true` in Render env
- [ ] Login rate limit: `60 per minute` (enforced in-memory)
- [ ] Invite accept rate limit: `60 per minute`
- [ ] Webhook rate limit: `300 per minute`
- [ ] Chat rate limit: `300 per minute`
- [ ] Reverse proxy (nginx/TLS edge) is the authoritative limiter;
  in-app values are a backstop

### 9. Tenant Isolation
- [ ] Create two test organizations via API
- [ ] Verify org A cannot read org B's documents, conversations, etc.
- [ ] Verify org A cannot access org B's API endpoints
- [ ] RLS policies are the mechanism; test by making requests as `wm_app`
  without org context (should fail with 403)

### 10. Password Hashing
- [ ] Argon2id used for all password hashing (`argon2.PasswordHasher()`)
- [ ] No plaintext passwords stored anywhere
- [ ] Password verification uses constant-time comparison
- [ ] Dummy password verification timed to not reveal account existence

## VERCEL — Security Checks

### 1. Frontend-Backend Communication
- [ ] No CORS errors in browser console
- [ ] API calls go through `/api/*` path (proxied by vercel.json)
- [ ] Cookies are same-origin (Vercel + Render proxy architecture)
- [ ] `VITE_API_BASE_URL = /api` is set in Vercel Environment Variables

### 2. No Secret Exposure
- [ ] Grep the frontend bundle for `sk-`, `SECRET`, passwords
- [ ] Use: `Select-String apps/web/src -Pattern 'localhost|127\.0\.0\.1|sk-|SECRET'`
- [ ] Should return only dev `docsUrl` fallback + network-error copy, no keys
- [ ] `AI_API_KEY` never in the bundle (server-side only)

### 3. CSP & Headers
- [ ] Vercel automatically adds security headers
- [ ] `X-Content-Type-Options: nosniff` ✅ (in vercel.json)
- [ ] `X-Frame-Options: DENY` ✅ (in vercel.json)
- [ ] `Referrer-Policy: no-referrer` ✅ (in vercel.json)

## FULL STACK INTEGRATION SECURITY

### 1. End-to-End Flow
```powershell
# 1. Open https://workmeta.vercel.app in browser
# 2. Login (static page)
# 3. Create/organization via /api/v1/organizations
# 4. Upload a document (tests magic bytes validation)
# 5. Ask a question via /api/v1/conversations
# 6. Verify citation and RLS (cross-org queries return 404)
# 7. No console CORS errors
# 7. Backend responds correctly with pgvector-backed answers
```

### 2. CSRF Protection
- [ ] FastAPI CSRF middleware is configured
- [ ] `wm_csrf` cookie is set on API responses
- [ ] POST requests include `X-CSRF-Token` header
- [ ] The Vercel proxy makes cookies same-origin, which naturalizes CSRF
- [ ] Double-submit pattern works without additional configuration

### 3. Request ID Validation
- [ ] Unexpected errors return safe envelope without stack traces
- [ ] Request IDs are validated and returned for support correlation
- [ ] No stack traces leaked in production responses

## AUTOMATED SECURITY TESTS (If Available)

### Run If Possible:
```powershell
# Security header check
curl -I https://workmeta-api.onrender.com/api/v1/health
# Verify: Strict-Transport-Security, X-Content-Type-Options, X-Frame-Options

# CORS check
curl -OPTIONS https://workmeta-api.onrender.com/api/v1/health
# Verify: Access-Control-Allow-Origin header

# Tenant isolation test (advanced)
# Create org A, create org B, try org A's API calls as org B
# Should all return 404 Not Found (the API never discloses that another
# tenant's organization exists; 403 means "visible, but your role is too low")
```

## PRODUCTION READINESS DECISION MATRIX

| Category | Fully ✅ | Partially ⚠️ | Not ✅ | Remarks |
|---|---|---|---|---|
| HTTPS / cookies | ✅ Render provides HTTPS | | | |
| Authentication | ✅ Bearer token auth | | | |
| Authorization/RLS | ✅ Forced RLS on tables | | | Test with cross-org |
| CORS | ⚠️ Coordinated between Render/Vercel | | | Set after Vercel deploy |
| Secrets | ✅ `sync: false`, .env gitignored | | | |
| File upload | ✅ Magic bytes + size limits | | | |
| Debug mode | ✅ `APP_ENV=production` blocks mock | | | |
| Rate limiting | ✅ In-memory + proxy backstop | | | |
| Tenant isolation | ✅ RLS + org_id + assistant_id scoping | | | Test with cross-org |
| Password hashing | ✅ Argon2id | | | |
| CSRF | ✅ Double-submit (via proxy) | | | |
| CSP headers | ✅ Vercel auto-adds | | | |
| CSRF + cookies | ✅ Same-origin via proxy | | | |

## FINAL SECURITY CHECKLIST (Before Going Live)

- [ ] Render: `APP_ENV=production`
- [ ] Render: `ALLOWED_HOSTS` = deployed hostnames (not `["*"]`)
- [ ] Render: `CORS_ORIGINS` = Vercel domain (not `["*"]`)
- [ ] Vercel: `VITE_API_BASE_URL = /api` set
- [ ] Vercel: No secrets in frontend bundle
- [ ] Database: pgvector + citext extensions created
- [ ] Database: All migrations applied (head revision)
- [ ] Database: RLS policies on all tenant tables
- [ ] No `BILLING_PROVIDER=mock` if going live (or document as evaluation)
- [ ] Session secret >= 32 characters ✅ (already verified)
- [ ] API health: `GET /api/v1/health` → 200, database ok, pgvector true ✅
- [ ] API readiness: `GET /api/v1/ready` → 200 ✅
- [ ] Cross-org API calls return 404 (tenant isolation verified)
- [ ] Browser console: no CORS errors
- [ ] No stack traces in error responses
- [ ] File upload: magic bytes + size limits enforced
- [ ] Billing: mock for evaluation, or Flutterwave/Paystack for live

**Go live when all ✅ boxes are checked.**

⚠️ Items marked ⚠️ need attention but may be acceptable for evaluation/mVP.

❌ Items not checked must be resolved before going live.