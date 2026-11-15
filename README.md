# WorkMeta AI

**Your Business. Your AI. Your Way.**

WorkMeta AI is a secure, multi-tenant platform for creating and selling
client-specific AI assistants. Each business client gets private knowledge, a
configured assistant, and a separately branded browser extension — built from
one maintainable core, never from forked codebases.

> **Current status: Stages 0–6 implemented — billing/monetization (Stage 5) plus production hardening: security audit fixes, Docker/proxy stack, billing worker, metrics, backup tooling and CI.**
> Identity, tenancy, RBAC and isolation (Stage 1) → knowledge/RAG (Stage 2) →
> real AI provider (Stage 3) → streaming/OCR/dashboards (Stage 4) → billing,
> subscriptions, quota enforcement, billing UI and platform admin (Stage 5) →
> security hardening, rate limiting, TrustedHost, body limits, webhook
> re-verification, Dockerfiles, prod Compose + TLS proxy, billing worker,
> `/metrics`, backup/restore scripts and extended CI (Stage 6) →
> self-service registration (Stage 9) → the installed MV3 agent (Stage 11) →
> WorkMeta AI Submodels: models trained from a video demo of the task
> (Stage 12) → the trained extensions removed (Stage 13).
> See [docs/PLAN.md](docs/PLAN.md) and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## What exists right now

| Component | Status | What it does |
|---|---|---|
| FastAPI backend | ✅ Working | Liveness, readiness, metrics, structured logs, security headers, error envelope |
| PostgreSQL + pgvector | ✅ Working | Restricted runtime role, extensions, forced RLS |
| React web shell | ✅ Working | Login, billing dashboard, pricing/checkout, platform admin |
| Alembic migrations | ✅ Working | Up to `20261111_0006`; `alembic check` reports no drift |
| CI | ✅ Working | Lint, audits, migrate, drift + round-trip, DB tests, web build, Docker builds |
| Auth & tenancy | ✅ Stage 1 | Login/logout, opaque sessions, RBAC, clients, invitations, RLS |
| Knowledge / RAG | ✅ Stage 2 | Upload, ingestion worker, chunking, hybrid retrieval, cited chat |
| Real AI provider | ✅ Stage 3 | OpenAI-compatible chat/embeddings, timeouts, retries, per-assistant model settings |
| Streaming / OCR / dashboards | ✅ Stage 4 | SSE, optional OCR, document lifecycle, usage reports |
| Billing & quotas | ✅ Stage 5 | Plans, checkout, webhooks, quotas, billing UI, platform admin |
| Production hardening | ✅ Stage 6 | Rate limits, TrustedHost, body caps, webhook re-verify, Docker/prod Compose, workers, metrics, backups |
| Self-service registration | ✅ Stage 9 | Sign-up page, per-client organization, Free plan, live verification |
| Installed agent | ✅ Stage 11 | One MV3 codebase: signs in, asks the trained models, confirms actions before they run |
| WorkMeta AI Submodels | ✅ Stage 12 | Models trained from a video demo of the task: the recording becomes a timeline of screens and actions, the workflows are approved and tested, and the model is downloaded as a signed package that works on any other computer |
| Trained extensions removed | ✅ Stage 13 | The extension subsystem is gone; submodels are the only trained model on the platform |

Nothing is simulated in the UI: every value is read from the API.

Stages 4–5 added the real dashboards, streaming, OCR, document deletion,
billing, subscriptions and platform administration. Stage 6 hardened
production: see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md),
[docs/SECURITY.md](docs/SECURITY.md) and [docs/BACKUP.md](docs/BACKUP.md).
Stage 12 added WorkMeta AI Submodels — models trained from a video demo: see
[docs/SUBMODELS.md](docs/SUBMODELS.md).

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Node.js | 20+ (24 tested) | `npm.cmd` on Windows if PowerShell blocks `npm.ps1` |
| Python | 3.12 | 3.14 is not targeted yet |
| Docker Desktop | current | Provides PostgreSQL 16 + pgvector |
| Git | current | — |

### Windows: npm execution policy

If `npm --version` fails with `npm.ps1 cannot be loaded`, use `npm.cmd` or run
once in PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## Quick start (Docker)

From the repository root:

```powershell
# 1. Create .env with strong random secrets (gitignored)
py -3.12 tools/scripts/generate_env.py

# 1b. Make sure it has a signing key, so the agent packages and the
#     submodel packages can be signed
py -3.12 tools/scripts/generate_signing_key.py --force

# 2. Start PostgreSQL 16 + pgvector
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml ps

```powershell
# 3. Install the backend (first time only)
cd apps/api
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

# 3b. Optional: read training recordings without installing anything on the
#     machine (PyAV carries the codec libraries, the ONNX engine its models)
.\.venv\Scripts\python -m pip install -e ".[media]"

# 4. Apply database migrations (from apps/api)
.\.venv\Scripts\python -m alembic upgrade head

# 5. Seed the first platform owner (from the repository root)
cd ..\..
apps\api\.venv\Scripts\python tools\scripts\seed_owner.py

# 6. Run the API (from apps/api)
cd apps\api
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# 7. In a second terminal, from apps/api: run the ingestion worker
cd apps\api
.\.venv\Scripts\python -m app.workers.ingestion

# 8. In a third terminal, from the repository root: install + run the web app
npm.cmd install
npm.cmd run dev:web
```

Open:

- Web status page: **http://localhost:5173**
- API health: **http://localhost:8000/api/v1/health**
- API readiness: **http://localhost:8000/api/v1/ready**
- OpenAPI docs: **http://localhost:8000/docs**

### What you should see

- The status page shows the navy/teal WorkMeta AI interface.
- **API: Online**, **PostgreSQL: Reachable**, **pgvector: Enabled**, **v0.1.0**.
- The “Foundation checks” list shows the two live checks passing.
- `GET /api/v1/ready` returns:

```json
{
  "status": "ready",
  "database": "ok",
  "pgvector": true,
  "version": "0.1.0",
  "environment": "development"
}
```

- `tools/scripts/seed_owner.py` creates the platform owner from
  `SEED_OWNER_EMAIL` / `SEED_OWNER_PASSWORD` and is safe to re-run
  (`--force` resets the password).

### Stage 1 API walkthrough

Stage 1 has no login screen yet; use the API (or `/docs`) to verify onboarding.
Extensions use bearer tokens, so request `"kind": "extension"` when testing
without a browser.

```powershell
# 0. Load the seeded owner credentials from .env (PowerShell does not do this automatically)
$ownerEmail = ((Get-Content .env | Where-Object { $_ -match '^SEED_OWNER_EMAIL=' }) -split '=', 2)[1]
$ownerPassword = ((Get-Content .env | Where-Object { $_ -match '^SEED_OWNER_PASSWORD=' }) -split '=', 2)[1]

# 1. Sign in as the platform owner
$login = Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login `
  -ContentType 'application/json' `
  -Body (@{ email = $ownerEmail; password = $ownerPassword; kind = 'extension' } | ConvertTo-Json)
$owner = @{ Authorization = "Bearer $($login.access_token)" }

# 2. Create a client organization
$org = Invoke-RestMethod -Method Post http://localhost:8000/api/v1/organizations `
  -Headers $owner -ContentType 'application/json' `
  -Body (@{ name = 'Example Rwanda Ltd'; slug = 'example-rw'; country = 'RW' } | ConvertTo-Json)

# 3. Invite its first administrator (the token is returned once)
$invite = Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/organizations/$($org.id)/invitations" `
  -Headers $owner -ContentType 'application/json' `
  -Body (@{ email = 'admin@example.com'; role = 'client_admin' } | ConvertTo-Json)

# 4. Accept the invitation (creates the user and a web session)
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/invitations/accept `
  -ContentType 'application/json' `
  -Body (@{ token = $invite.token; full_name = 'Client Admin'; password = 'a-long-unique-password' } | ConvertTo-Json)
```

Deliver invitation tokens manually for now; email delivery is not implemented.
Invitations are single-use, expire after `INVITATION_TTL_DAYS`, and can be
revoked before acceptance.

### Stage 2 API walkthrough (knowledge → cited chat)

Knowledge and conversations require a real tenant membership, so use the client
admin created above (the platform owner intentionally has no content bypass).
Set `AI_API_KEY` for real answers; with no key the deterministic offline provider
is used, which is what development and tests run against.

```powershell
# 1. Sign in as the client admin (the account created by invitation)
$login = Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login `
  -ContentType 'application/json' `
  -Body (@{ email = 'admin@example.com'; password = 'a-long-unique-password'; kind = 'extension' } | ConvertTo-Json)
$admin = @{ Authorization = "Bearer $($login.access_token)" }
$orgId = $login.active_organization.id

# 2. Create an assistant for the client
$assistant = Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/organizations/$orgId/assistants" `
  -Headers $admin -ContentType 'application/json' `
  -Body (@{ name = 'EBM Assistant'; product_code = 'ebm' } | ConvertTo-Json)

# 3. Upload a PDF, Word (.docx), text or Markdown document (queue starts immediately)
curl.exe -X POST "http://localhost:8000/api/v1/organizations/$orgId/assistants/$($assistant.id)/documents" `
  -H "Authorization: Bearer $($login.access_token)" `
  -F "file=@.\docs\receipt-guide.txt" -F "title=Receipt Guide"

# 4. From apps/api, process the queue (or keep the worker above running)
.\.venv\Scripts\python -m app.workers.ingestion --drain

# 5. Start a conversation and ask a question
$conversation = Invoke-RestMethod -Method Post `
  "http://localhost:8000/api/v1/organizations/$orgId/assistants/$($assistant.id)/conversations" `
  -Headers $admin -ContentType 'application/json' -Body '{}'

$answer = Invoke-RestMethod -Method Post `
  "http://localhost:8000/api/v1/organizations/$orgId/conversations/$($conversation.id)/messages" `
  -Headers $admin -ContentType 'application/json' `
  -Body (@{ question = 'How do I issue an EBM receipt for a walk-in customer?' } | ConvertTo-Json)

$answer.grounded
$answer.assistant_message.content
$answer.assistant_message.citations
```

The document moves `uploaded → processing → ready` (or `failed` with a readable
error). Retrieval is hybrid vector + PostgreSQL full-text, strictly scoped by
`org_id` and `assistant_id`. A question the approved material cannot answer
returns a clear refusal with no citations and does not call the model.

### Stage 3 provider and model configuration

Set the provider in the repository `.env` (never in the web app or extension):

```dotenv
AI_PROVIDER=openai
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-...                 # server-side only; empty selects the fake provider
AI_CHAT_MODEL=gpt-4.1-mini
AI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
AI_TIMEOUT_SECONDS=60
AI_TEMPERATURE=0
AI_MAX_RETRIES=2
AI_RETRY_BACKOFF_SECONDS=0.5
AI_RETRY_MAX_BACKOFF_SECONDS=8
CHAT_MAX_OUTPUT_TOKENS=800
```

Any OpenAI-compatible endpoint (Azure OpenAI, OpenRouter, a local gateway) works
when it exposes the same embeddings and chat-completions API. Transient rate
limits, timeouts, connection failures and 5xx responses are retried with bounded
exponential backoff. Authentication errors, unknown models and malformed
requests fail immediately, and chat returns `503 ai_unavailable` without saving a
partial turn.

Each assistant can override only the chat model and generation settings. The
embedding model and vector dimensions stay deployment-wide because documents and
queries must share one vector space:

```powershell
$assistant = Invoke-RestMethod -Method Patch `
  "http://localhost:8000/api/v1/organizations/$orgId/assistants/$($assistant.id)" `
  -Headers $admin -ContentType 'application/json' `
  -Body (@{
    model_options = @{
      model = 'gpt-4.1-mini'
      temperature = 0.2
      max_output_tokens = 600
    }
    retrieval_config = @{ top_k = 6; min_score = 0.2 }
  } | ConvertTo-Json -Depth 3)
```

Every ready document records the embedding model and dimensions used to produce
its vectors. Retrieval filters on both, so changing `AI_EMBEDDING_MODEL` is safe
but makes older documents invisible until they are re-ingested (re-ingestion is
part of the Stage 4 retention work).

---

## Tests

```powershell
cd apps/api

# Lint
.\.venv\Scripts\python -m ruff check .

# Unit tests (no database required)
.\.venv\Scripts\python -m pytest -m "not integration" -v

# Full suite, including real PostgreSQL and tenant-isolation tests
$env:RUN_DB_TESTS = "1"
.\.venv\Scripts\python -m pytest -v

# Confirm models and migrations agree (requires a migrated database)
.\.venv\Scripts\python -m alembic check
```

```powershell
# From the repository root — production web build and type check
npm.cmd run build:web
```

The Stage 1 suite includes two-tenant API attacks and raw-SQL attacks against
the runtime role. Both must fail closed; see
`tests/test_auth_tenancy_integration.py`.

---

## Environment variables

Copy `.env.example` to `.env` or generate it. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development`, `test`, `production` |
| `DATABASE_URL` | Runtime connection — restricted `wm_app` role |
| `DATABASE_MIGRATION_URL` | Alembic connection — owner role |
| `DATABASE_CONNECT_TIMEOUT_SECONDS`, `DATABASE_COMMAND_TIMEOUT_SECONDS` | How long a connection or a statement may take |
| `SESSION_SECRET` | Keyed HMAC for session/invitation token hashes |
| `SESSION_TTL_DAYS`, `INVITATION_TTL_DAYS` | Session and invitation lifetimes |
| `AI_API_KEY` | **Server-side only.** Never shipped to browsers. Empty ⇒ offline fake provider |
| `AI_PROVIDER`, `AI_BASE_URL` | `openai` (or any compatible endpoint) and its base URL |
| `AI_CHAT_MODEL`, `AI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | Model configuration; embedding settings are deployment-wide |
| `AI_TIMEOUT_SECONDS`, `AI_TEMPERATURE`, `CHAT_MAX_OUTPUT_TOKENS` | Request timeout and default generation limits |
| `AI_MAX_RETRIES`, `AI_RETRY_BACKOFF_SECONDS`, `AI_RETRY_MAX_BACKOFF_SECONDS` | Transient-failure retries and bounded backoff |
| `MAX_UPLOAD_BYTES`, `MAX_DOCUMENT_PAGES` | Upload and PDF page limits |
| `MAX_MEDIA_UPLOAD_BYTES` | How large a training recording may be (the media upload paths only) |
| `CHUNK_TOKENS`, `CHUNK_OVERLAP_TOKENS` | Ingestion chunking (default 900 / 150) |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_MIN_SCORE` | Hybrid retrieval breadth and relevance floor |
| `INGESTION_MAX_ATTEMPTS`, `WORKER_POLL_SECONDS` | Worker retries and idle polling |
| `STORAGE_BACKEND`, `STORAGE_DIR` | Local files now, S3-compatible later |
| `OCR_ENGINE` | `none`, `rapidocr`, `tesseract` or `auto` — OCR for images, scanned PDFs and the frames of a recording |
| `MEDIA_VIDEO_FRAME_INTERVAL_SECONDS`, `MEDIA_VIDEO_MAX_FRAMES` | How a training recording is sampled |
| `EXTENSION_SIGNING_PRIVATE_KEY` | Ed25519 key for the agent packages and the submodel packages (Stage 5/12) |
| `OCR_ENGINE`, `MEDIA_VIDEO_*`, `ANALYSIS_MAX_*` | Reading a video demo and bounding the training |
| `SEED_OWNER_EMAIL`, `SEED_OWNER_PASSWORD` | First platform owner (Stage 1) |
| `BILLING_PROVIDER`, `FLUTTERWAVE_*`, `PAYSTACK_*` | Payment gateway (Stage 5, sandbox first) |
| `ALLOWED_HOSTS`, `MAX_REQUEST_BODY_BYTES`, `RATE_LIMIT_*` | Host/body/rate hardening (Stage 6) |

Production startup refuses weak `SESSION_SECRET`, placeholder DB passwords,
`BILLING_PROVIDER=mock`, `ALLOWED_HOSTS=*`, or wildcard CORS.

---

## Project structure

```
workmeta/
├── apps/
│   ├── api/                 FastAPI backend (Stages 0–6)
│   │   ├── app/
│   │   │   ├── api/v1/      auth, orgs, members, invitations, assistants,
│   │   │   │                documents, conversations, products, audit,
│   │   │   │                billing, platform, usage, health (metrics),
│   │   │   │                submodels (training, tests, package, actions)
│   │   │   ├── core/        config, security, logging, errors, middleware
│   │   │   │                (rate limit, body cap, TrustedHost, metrics)
│   │   │   ├── db/          engine, session, RLS helpers, declarative base
│   │   │   ├── models/      identity, tenancy, assistants, documents, chat,
│   │   │   │                billing, usage counters, submodels
│   │   │   ├── prompts/     versioned grounded-answer policy
│   │   │   ├── schemas/     Pydantic request/response contracts
│   │   │   ├── services/    auth, storage, AI provider, extraction, retrieval,
│   │   │   │                billing, payments, quota, submodels,
│   │   │   │                video analysis, submodel packaging
│   │   │   └── workers/     ingestion queue worker + billing worker
│   │   ├── alembic/         migrations (schema, RLS policies, grants)
│   │   ├── Dockerfile       production API image
│   │   └── tests/           unit + real-database suites (incl. billing/security)
│   └── web/                 React + TypeScript + Tailwind shell (+ Dockerfile)
│   └── extension/           the installed agent (MV3): background worker,
│                            content script, side panel, manifest template
├── packages/
│   └── design-tokens/       navy/teal brand tokens (CSS + TS)
├── infra/
│   ├── docker-compose.yml      dev database (pgvector)
│   ├── docker-compose.prod.yml production stack (db, api, workers, web, edge)
│   ├── nginx/                  edge TLS proxy template
│   ├── deploy/                 nginx static config + production env example
│   └── postgres/init/          extensions + restricted app role
├── tools/scripts/           generate_env, seed_owner, backup, restore,
│                            payment-sandbox validator, extension packaging
├── docs/                    PLAN, DEPLOYMENT, SECURITY, BACKUP,
│                            SUBMODELS (models trained from a video demo),
│                            AGENT_EXTENSION (the installed agent),
│                            SUBMODELS (trainable, downloadable models)
└── .env.example
├── tools/
│   └── scripts/             generate_env.py, seed_owner.py
├── infra/
│   ├── docker-compose.yml   PostgreSQL 16 + pgvector
│   └── postgres/init/       extensions + restricted app role
├── docs/
│   └── PLAN.md              architecture, data model, security, roadmap
└── .env.example
```

---

## Security foundations already in place

- The runtime database role `wm_app` is **not** a superuser, cannot create
  databases or roles, and **cannot bypass Row-Level Security**.
- Tenant tables (`organizations`, `memberships`, `invitations`, `assistants`,
  `documents`, `document_chunks`, `ingestion_jobs`, `conversations`, `messages`,
  `usage_events`, `audit_logs`) have RLS **enabled and forced**. Policies read
  transaction-local `app.current_org` / `app.current_user` settings, so pooled
  connections cannot leak one tenant's context into another request.
- The job worker can discover work through a narrow `app_is_worker()` gate on
  the queue table only; it never gains access to document or chat content
  without switching to the claimed job's tenant context.
- Uploads are validated by magic bytes, stored under random keys and served only
  through an authorized download endpoint with `Content-Disposition: attachment`.
- Retrieval combines pgvector similarity and PostgreSQL full-text ranking inside
  the same RLS-protected transaction. Weak matches are dropped before any model
  call, which makes the honest "not in the approved documents" path real.
- Retrieval is scoped by both `org_id` and `assistant_id`, and only compares
  vectors produced by the document's recorded embedding model and dimensions.
- AI requests have an explicit timeout and a bounded retry budget for transient
  failures. Permanent provider errors fail fast; chat returns a safe 503 and
  rolls back the turn instead of saving a partial answer.
- The runtime role has no `DELETE` privilege; the audit trail is append-only
  (INSERT + SELECT only) and `alembic_version` is fully revoked.
- Passwords use Argon2id. Sessions and invitations use 256-bit random tokens
  stored only as keyed HMAC-SHA256 hashes.
- Web sessions use an HttpOnly, SameSite=Lax cookie; cookie-authenticated
  mutations require a double-submit CSRF token.
- CORS is restricted to configured origins; credentials are supported.
- Security headers are applied to every response.
- Request IDs are validated and returned for support correlation.
- Unexpected errors return a safe envelope without stack traces.
- Production requires an explicit session secret.
- AI keys live only in server environment variables. The extension will never
  receive provider credentials.
- The installed agent holds no provider credentials and no secret at all: its
  package carries only public configuration, signed with Ed25519 and checked
  in the browser with WebCrypto. Its session token stays in the service
  worker, its content script runs only on the client's own domains, and the
  visible text of a page is treated as untrusted data, never as instructions.

The default platform-owner privacy rule is part of the design: the owner sees
organizations, plans and audit metadata, **not** client documents or
conversations. Knowledge and chat routes require a real tenant membership even
for the platform owner (`require_org_member`), and the integration suite verifies
the 403. Any future support access must be explicit, consented and audited.

Tenant isolation is not asserted in prose only: `tests/test_auth_tenancy_integration.py`
creates two clients and attempts cross-tenant reads and writes through the API
and through raw SQL as `wm_app`. `tests/test_knowledge_rag_integration.py` does
the same for documents, chunks, conversations and cited answers, including the
worker gate. Stage 3 adds fake-provider tests for retrieval accuracy, assistant
boundaries, provider failures, conversation history and model overrides. Every
attempt must return nothing or fail.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `npm.ps1 cannot be loaded` | Use `npm.cmd` or set the CurrentUser execution policy |
| `pgvector: Missing` | You are not using the `pgvector/pgvector:pg16` image; recreate the volume with `docker compose ... down -v` |
| `database_unavailable` | PostgreSQL is not running or `.env` credentials are wrong |
| Port 5432 already in use | Stop the other PostgreSQL service or change the published port |
| Docker install fails | Docker Desktop requires administrator rights and WSL2; install from an elevated terminal |
| `esbuild` install script blocked | `npm.cmd approve-scripts esbuild` then `npm.cmd install` |

---

## Roadmap

0. **Foundations** — complete
1. **Auth, tenancy & admin API** — complete
2. **Knowledge ingestion and grounded RAG chat with citations** — complete
3. **Real AI integration, configurable models and RAG hardening** — complete
4. **Streaming, OCR, document deletion and dashboards** — complete
5. **Billing, subscriptions, quotas and platform administration** — complete (this task's Stage 5)
6. **Production hardening: security audit, rate limits, Docker/prod stack, workers, metrics, backups, CI** — complete (this task's Stage 6)

Remaining: real-gateway sandbox validation with live sandbox keys, S3 storage backend, MFA, and a tested restore drill on a staging host. No live payment processing until sandbox verification succeeds.
