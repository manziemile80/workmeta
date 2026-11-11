# Production Hardening — WorkMeta AI (Stage 3)

> Audited against the live deployment: frontend `https://workmeta.vercel.app`,
> backend `https://workmeta.onrender.com`, database Neon PostgreSQL.

## 1. `APP_ENV=development` — what it actually changes

`APP_ENV` is a single switch with three side effects. Everything else that
"looks like development" is covered by explicit overrides now.

| Behaviour | Production | Staging (current) | How it is handled now |
|---|---|---|---|
| `SESSION_SECRET` length check | required (≥32) | not checked | `ENFORCE_PRODUCTION_CHECKS=true` turns the check on |
| Placeholder database URLs rejected | required | not checked | same override |
| `ALLOWED_HOSTS` must list real hosts | required | not checked | same override |
| `CORS_ORIGINS` must not be `*` | required | not checked | same override |
| `Secure` session cookies | on | off | `COOKIE_SECURE=true` |
| HSTS header | on | off | `ENABLE_HSTS=true` |
| `/docs`, `/redoc`, `/openapi.json` | disabled | served | `ENABLE_DOCS=false` |
| JSON structured logs | on | console | `LOG_FORMAT=json` |
| `BILLING_PROVIDER=mock` | rejected | allowed | **still open** — see below |

`ENFORCE_PRODUCTION_CHECKS=true` applies the production invariants while
`APP_ENV` stays `development`. That is deliberate: `production` also forbids
the mock billing provider, which is the only provider that works without
payment-gateway credentials.

### Remaining gap: `BILLING_PROVIDER=mock`
The live deployment uses the mock gateway, so checkout completes instantly and
no money moves. To flip to `APP_ENV=production`:

1. Create Flutterwave or Paystack sandbox credentials.
2. Set `BILLING_PROVIDER=flutterwave` (or `paystack`) plus
   `FLUTTERWAVE_SECRET_KEY` / `FLUTTERWAVE_WEBHOOK_HASH` (or the Paystack
   equivalents) on Render.
3. Set `APP_ENV=production` and `ENFORCE_PRODUCTION_CHECKS=true`.
4. Re-run the sandbox payment test (`tools/scripts/test_payment_sandbox.py`).

Until then, treat the deployment as a **staging tier**: no real payments, and
`/api/v1/billing/*` mutating endpoints are not customer-facing.

## 2. Database least privilege

The runtime connects as `wm_app`, never as the owner:

| Property | Value |
|---|---|
| `rolsuper` | false |
| `rolbypassrls` | false |
| `rolcreatedb` / `rolcreaterole` | false |
| `SELECT` / `INSERT` grants | 21 tables each |
| `UPDATE` grants | 19 tables (audit log is append-only) |
| `DELETE` grants | **0** (explicitly revoked by migration `0001`) |
| `alembic_version` | no grant - the runtime cannot rewrite migration state |
| `neondb_owner` (owner) | `BYPASSRLS=true` - **only** used by migrations, and it is no longer deployed to Render |

`DATABASE_MIGRATION_URL` was removed from the Render service, so a container
compromise cannot obtain a `BYPASSRLS` credential. Run migrations from a
workstation or CI with `var/neon.env`.

## 3. Authentication, sessions, CSRF, tenancy

| Control | Implementation | Verified |
|---|---|---|
| Password hashing | Argon2id | `tests/test_security*.py` |
| Session cookie | `HttpOnly`, `Secure`, `SameSite=Lax`, host-only | live check |
| CSRF | double-submit cookie, `X-CSRF-Token` must match on unsafe methods | live: 403 without the header |
| Session revocation | logout / logout-all, idle timeout | live: `/me` → 401 after logout |
| MFA | TOTP enrolment + replay protection | `tests/test_security2_integration.py` |
| Tenant isolation | PostgreSQL RLS on 15 tables + ORM-level filters | live + 106 unit/integration tests |
| Platform admin | **no content bypass** - platform admins are not tenant members | `tests/test_knowledge_rag_integration.py::test_platform_owner_has_no_content_bypass` |
| Registration | invitation-only, tokens stored hashed, single live invite per email | live invitation flow |

## 4. Error handling

All errors use one envelope:

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```

* Framework errors (404, 405, ...) now use the same envelope instead of
  FastAPI's `{"detail": ...}`.
* Database outages return `503 database_unavailable`; a missing database URL
  returns `503 database_not_configured`.
* Unexpected errors return `500 internal_error` with a generic message; the
  traceback is logged, never returned.
* Responses never contain the database URL, the role name, a stack trace or a
  secret.

## 5. Monitoring

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | liveness, `database_configured`, `docs_enabled` |
| `GET /api/v1/ready` | readiness: database reachable + `pgvector` present |
| `GET /api/v1/metrics` | Prometheus counters (requests, uptime, queue depth) |

Render health check path is `/api/v1/health`. Logs are one JSON object per line
(`LOG_FORMAT=json`), so Render's log search can filter on `event`, `status_code`
or `request_id`.

## 6. Backups

* **Provider snapshot (current):** a Neon branch snapshot of the database,
  created by `tools/scripts/backup.py` (API key or the OAuth-backed MCP client).
  Verified: the snapshot contains 21 tables, the owner row and revision
  `20261112_0007`.
* **Logical dump:** `pg_dump` custom format + `manifest.json` with SHA-256.
  The client version must be at least the server version (Neon runs
  PostgreSQL 18 - use `pg_dump` 18).
* **Restore drill (verified):** a dump was restored into a scratch database and
  compared: 21 tables, 15 RLS tables, 21 `wm_app` grants, revision
  `20261112_0007`.
* **Point-in-time restore:** Neon keeps a 6-hour history window on this plan.
* **Storage caveat:** `STORAGE_DIR` on Render is an ephemeral disk - uploaded
  blobs are lost when the free instance restarts. Use `STORAGE_BACKEND=s3` for
  durable uploads, or back up the disk separately.

## 7. Dependency posture

* `npm audit --omit=dev --audit-level=high` → 0 vulnerabilities.
* `pip-audit` is part of CI (`.github/workflows/ci.yml`); run it locally with
  `apps/api/.venv/Scripts/python.exe -m pip_audit`.

## 8. Known residual risks

1. Mock billing provider (see §1).
2. `AI_API_KEY` is empty, so the API answers with its deterministic fake
   provider - no real model calls, no cost, but also no AI features.
3. Free Render sleeps when idle (30-60 s cold start) and has no persistent disk.
4. The Neon project's `neondb` database belongs to another application; the
   WorkMeta data lives in the dedicated `workmeta` database.
5. The Render API key was shared in a chat and must be rotated.
