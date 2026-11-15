# WorkMeta AI — Verification Report

**As of 2026-11-14.** This report records what was actually executed and
observed on this workstation. Detailed per-item status lives in
`docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md`; deployment procedures live in
`docs/RENDER_DEPLOYMENT_GUIDE.md` and `docs/VERCEL_DEPLOYMENT_GUIDE.md`.

---

## 1. Verified Results

### 1.1 Backend — lint and unit tests
| Check | Command | Result |
|---|---|---|
| Lint | `ruff check .` (apps/api) | **PASS** — no findings |
| Unit tests | `pytest -q` | **PASS** — 110 collected, 62 passed, 48 skipped (the skipped ones need a database) |

### 1.2 Backend — integration suite against the live Neon database
Run with `RUN_DB_TESTS=1` against `ep-mute-base-be5pm73v` (Neon, us-east-2),
`pgvector` + `citext` enabled, migrations at head `20261112_0007`.

| File | Tests | Result |
|---|---|---|
| `tests/test_database_integration.py` | 3 | **PASS** |
| `tests/test_auth_tenancy_integration.py` | 9 | **PASS** |
| `tests/test_worker_metrics_integration.py` | 2 | **PASS** |
| `tests/test_billing_integration.py` | 13 | see §1.5 |
| `tests/test_knowledge_rag_integration.py` | 14 | see §1.5 |
| `tests/test_security_integration.py` | 4 | see §1.5 |
| `tests/test_security2_integration.py` | 3 | see §1.5 |

What the passing files prove:

- The runtime role `wm_app` is **not** superuser and **cannot** bypass RLS.
- Tenant isolation holds at both the API layer and the SQL layer: a member of
  organization A gets `404` for organization B — the API never confirms that
  another tenant's organization exists.
- The platform owner has **no** read access to client documents or
  conversations, even with owner privileges.
- Invitation lifecycle (issue → accept → revoke/expire → single use) is
  enforced, including "existing user must prove their password" when joining a
  second organization.
- MFA enrolment, replay rejection, password reset single-use, idle-session
  expiry, failed-login auditing without account-state leakage.
- Worker metrics render aggregates only; the billing worker advance is
  idempotent.

### 1.3 Frontend — type check and production build
| Check | Command | Result |
|---|---|---|
| Type check + build | `npm run build:web` (Node 24.21.0) | **PASS** — `tsc --noEmit` clean, Vite 6.4.5 built 1630 modules in 8.54s |
| Output | `apps/web/dist/` | index.html 0.72 kB · CSS 27.25 kB · JS 332.17 kB (99.57 kB gzip) |

### 1.4 Live HTTP smoke test
`python tools/scripts/smoke_local_api.py --base-url http://127.0.0.1:8000`
against a local uvicorn instance backed by Neon — **19/19 checks passed**:

| Check | Observed |
|---|---|
| `GET /api/v1/health` | 200 `{"status":"ok","service":"WorkMeta AI API","version":"0.1.0","environment":"development","uptime_seconds":…}` |
| `GET /api/v1/ready` | 200 `{"status":"ready","database":"ok","pgvector":true,…}` |
| `GET /docs` | 200 (OpenAPI browser) |
| `POST /api/v1/auth/login` | 200 for the seeded owner (bearer **and** cookie session) |
| `GET /api/v1/auth/me` | 200 |
| `POST /api/v1/organizations` | 201; `GET /api/v1/organizations` lists both |
| Cookie write without `X-CSRF-Token` | **403** (double-submit enforced) |
| Cookie write with `X-CSRF-Token` | 201 |
| Invitation → accept → sign in | 201 → 200 → 200 |
| Member reads own organization | 200 |
| Member reads another tenant's organization | **404** (existence not disclosed) |

### 1.5 Same-origin proxy (dev and preview)
With the API on port 8000:

| Server | `/api/v1/health` | `/api/v1/ready` | `/docs` | `/` |
|---|---|---|---|---|
| `npm run dev:web` (5173) | 200 | 200 | 200 | 200 |
| `npm run preview:web` (4173) | 200 | — | 200 | 200 |

This mirrors the Vercel rewrite (`/api/:path*` → Render), so the browser only
ever talks to one origin and the session cookie stays first-party.

---

## 2. Changes made in this session

| Path | Change | Reason |
|---|---|---|
| `apps/web/src/lib/api.ts` | `normaliseBaseUrl()` + `API_ROOT`; requests now built as `${base}/api/v1${path}` | The old code appended the caller-supplied path, which produced `/api/api/v1/...` and broke the same-origin proxy. A redundant `/api` or `/api/v1` in `VITE_API_BASE_URL` is now stripped. |
| `apps/web/vercel.json` | Added `/docs` and `/openapi.json` rewrites | The OpenAPI browser is served at the backend root, not under `/api/v1`. |
| `apps/web/vite.config.ts` | Proxy `/api`, `/docs`, `/openapi.json`; applied to `preview` as well as `server` | The dev/preview server must forward the same paths the Vercel rewrite forwards. |
| `apps/api/pyproject.toml` | `requires-python` `>=3.12,<3.14` → `>=3.12` | The upper bound blocked the available Python 3.14.7 with no technical reason. |
| `apps/api/tests/conftest.py` | New session fixture that restores the seeded Free plan limits | The billing tests tighten the shared `free` plan row. A killed test run skipped the restore and left the row patched, which made later runs fail. The row is now reset before every session. |
| `tools/scripts/smoke_local_api.py` | New | Repeatable live smoke test of health, readiness, auth, CSRF and tenant isolation against a running API. |
| `docs/*.md` | Corrected expectations | `/api/v1/health` does **not** return `database`/`pgvector` (those live on `/api/v1/ready`), and cross-tenant reads return **404**, not 403. |

### 2.1 In-process worker (new)

Render's Free plan has no background-worker service. Without a worker, an
uploaded document stays `queued` forever: the knowledge base never fills up
and retrieval finds nothing. The API now runs the same loops in-process.

| Path | Change | Reason |
|---|---|---|
| `apps/api/app/workers/embedded.py` | New — ingestion + billing loop with a stop event | Drains the queue inside the API process. Safe next to a dedicated worker: jobs are claimed with `FOR UPDATE SKIP LOCKED`, so no job is processed twice. |
| `apps/api/app/main.py` | Lifespan starts the loop and drains it on shutdown | The worker shares the app's lifetime; shutdown waits up to 5s for the current job before cancelling. |
| `apps/api/app/core/config.py` | `embedded_worker_enabled`, `embedded_worker_billing_seconds` | Off by default — a dedicated worker stays the default deployment. |
| `apps/api/tests/test_embedded_worker.py` | New (4 tests) | Drains the queue, survives a failing poll, sweeps billing once per interval, starts and stops with the app. |
| `render.yaml`, `.env.example` | `EMBEDDED_WORKER_ENABLED=true` on the Free service | Free has no worker service; the API has to drain its own queue. |

Verified against the live Neon database with `EMBEDDED_WORKER_ENABLED=true`:

| Observation | Result |
|---|---|
| `embedded_worker_started poll_seconds=2.0` | Logged at startup, next to `api_starting` |
| A stale queued job from the previous session was claimed and processed | `ingestion_job_claimed job_id=afacbba4-…` |
| The job failed (its stored blob was gone) and was re-queued with backoff | `ingestion_job_failed error_type=AppError`, job back to `queued` with `attempts=1` — the loop kept running |
| Full smoke test with the worker running | **19/19 checks passed** |

---

## 3. Corrections to earlier documentation

Two claims in the previous revision of these documents were wrong and are now
fixed:

1. **Health payload.** `GET /api/v1/health` returns
   `{"status":"ok","service":…,"version":…,"environment":…,"uptime_seconds":…}`.
   It does not touch the database. `database` and `pgvector` are reported by
   `GET /api/v1/ready`.
2. **Cross-tenant status code.** A caller without access to another tenant's
   organization receives **404 Not Found**, not 403. `403` is reserved for role
   and permission failures inside an organization the caller can already see.
   This is deliberate: a 404 does not confirm that the other tenant exists.

---

## 4. Current deployment status

| Component | Status | Notes |
|---|---|---|
| PostgreSQL (Neon, free tier) | **Live** | 7 migrations applied, head `20261112_0007`; `vector`, `citext`, `pgcrypto` enabled |
| `wm_app` runtime role | **Live** | Restricted: no superuser, no bypassrls |
| Platform owner | **Seeded** | Credentials in `.env` (gitignored) |
| Backend build (Dockerfile, `render.yaml`) | **Configuration-ready** | 40+ env vars, secrets `sync: false` |
| Background workers | **In-process** | `EMBEDDED_WORKER_ENABLED=true` covers the Free plan, which has no worker service; a dedicated worker is preferred on paid plans |
| Frontend build (Vercel) | **Build verified** | `npm run build:web` passes locally |
| Render service | **Not deployed** | Needs an authenticated Render account |
| Vercel project | **Not deployed** | Needs an authenticated Vercel account |
| Production env vars | **Not set** | Must be entered in the Render/Vercel dashboards, never committed |
| `BILLING_PROVIDER` | `mock` | Fine for evaluation; production requires Flutterwave/Paystack |

---

## 5. Remaining blockers

1. **No Render/Vercel account on this machine** — the deployment itself cannot
   be executed here. Everything up to the cloud boundary is verified.
2. **No Docker** — the container image is not built locally; CI builds it.
3. **Production secrets** — `DATABASE_URL`, `DATABASE_MIGRATION_URL`,
   `SESSION_SECRET`, `AI_API_KEY`, `CORS_ORIGINS` and `ALLOWED_HOSTS` must be
   set in the dashboards.
4. **Neon pooler latency** — each new connection costs several seconds through
   the Neon pooler, which makes the integration suite slow (minutes per file)
   and adds latency to a cold Render instance. Consider the direct (non-pooler)
   connection string for the API runtime, or a warm pool.
5. **Backups** — Render Free has an ephemeral disk and Neon free tier has no
   automated backups configured; acceptable for an MVP, not for production.

---

## 6. Next action

Create the Render Docker service and the Vercel project, copy the `.env` values
into the Render dashboard (secrets `sync: false`), set `VITE_API_BASE_URL=/api`
in Vercel, then re-run
`python tools/scripts/smoke_local_api.py --base-url https://workmeta-api.onrender.com`
against the deployed backend to confirm the same 19 checks pass in production.
