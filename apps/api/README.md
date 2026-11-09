# WorkMeta AI — API

FastAPI backend for the WorkMeta AI platform.

```bash
# from apps/api
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

- Liveness: `GET /api/v1/health`
- Readiness: `GET /api/v1/ready`
- Metrics (Prometheus aggregates): `GET /api/v1/metrics`
- OpenAPI docs (development only): `GET /docs`

Production: see `../..//docs/DEPLOYMENT.md` (Docker, edge TLS proxy, gated
migrations, workers, rollback). Security model: `docs/SECURITY.md`.
Backups/restores: `docs/BACKUP.md`.

Configuration comes from the repository root `.env` file. Never commit secrets.

### Billing worker

```powershell
.\.venv\Scripts\python -m app.workers.billing          # keep polling
.\.venv\Scripts\python -m app.workers.billing --once   # one sweep and exit
```

Advances subscription lifecycle state per tenant (renewal/past-due/expired)
via `app_billing_orgs()` and prunes old usage counters. Never reads document
or conversation content.

## Stage 1 endpoints

Authentication uses opaque sessions. Web clients receive an HttpOnly
`wm_session` cookie plus a readable `wm_csrf` cookie; extension clients request
`"kind": "extension"` and use the returned bearer `access_token`. Cookie-based
mutations must send `X-CSRF-Token` matching `wm_csrf`.

| Method | Path | Access |
|---|---|---|
| POST | `/api/v1/auth/login` | Public |
| POST | `/api/v1/auth/logout` | Authenticated |
| POST | `/api/v1/auth/logout-all` | Authenticated |
| GET | `/api/v1/auth/me` | Authenticated |
| POST | `/api/v1/auth/switch-organization` | Authenticated |
| GET | `/api/v1/organizations` | Authenticated (tenant-scoped) |
| POST | `/api/v1/organizations` | Platform admin |
| GET | `/api/v1/organizations/{org_id}` | Active member or platform admin |
| PATCH | `/api/v1/organizations/{org_id}` | Client admin or platform admin |
| GET | `/api/v1/organizations/{org_id}/members` | Client admin or platform admin |
| PATCH | `/api/v1/organizations/{org_id}/members/{user_id}` | Client admin or platform admin |
| GET | `/api/v1/organizations/{org_id}/invitations` | Client admin or platform admin |
| POST | `/api/v1/organizations/{org_id}/invitations` | Client admin or platform admin |
| DELETE | `/api/v1/organizations/{org_id}/invitations/{invitation_id}` | Client admin or platform admin |
| POST | `/api/v1/invitations/accept` | Public (valid token) |
| GET | `/api/v1/organizations/{org_id}/audit` | Client admin or platform admin |

Invitation tokens are returned once in the create response (`token`) and are
never stored in plaintext. Delivery is manual until email is implemented.

## Stage 2 endpoints

| Method | Path | Access |
|---|---|---|
| GET | `/api/v1/products` | Authenticated |
| POST | `/api/v1/organizations/{org_id}/assistants` | Client admin or platform admin |
| GET | `/api/v1/organizations/{org_id}/assistants` | Active member |
| GET | `/api/v1/organizations/{org_id}/assistants/{assistant_id}` | Active member |
| PATCH | `/api/v1/organizations/{org_id}/assistants/{assistant_id}` | Client admin or platform admin |
| POST | `/api/v1/organizations/{org_id}/assistants/{assistant_id}/documents` | Client admin (multipart) |
| GET | `/api/v1/organizations/{org_id}/assistants/{assistant_id}/documents` | Active member |
| GET | `/api/v1/organizations/{org_id}/assistants/{assistant_id}/documents/{document_id}` | Active member |
| GET | `.../documents/{document_id}/download` | Active member |
| POST | `.../documents/{document_id}/reprocess` | Client admin (failed documents only) |
| POST | `/api/v1/organizations/{org_id}/assistants/{assistant_id}/conversations` | Active member |
| GET | `/api/v1/organizations/{org_id}/assistants/{assistant_id}/conversations` | Active member (own threads) |
| GET | `/api/v1/organizations/{org_id}/conversations/{conversation_id}` | Thread owner |
| POST | `/api/v1/organizations/{org_id}/conversations/{conversation_id}/messages` | Thread owner |

Knowledge and conversations use `require_org_member`, so the platform owner
cannot read client content without a real membership.

### Ingestion worker

Uploads commit a row and queue an `ingestion_jobs` entry; they never block the
request. Run the worker from `apps/api`:

```powershell
.\.venv\Scripts\python -m app.workers.ingestion          # keep polling
.\.venv\Scripts\python -m app.workers.ingestion --drain  # empty the queue
.\.venv\Scripts\python -m app.workers.ingestion --once   # one job
```

The worker claims jobs with `FOR UPDATE SKIP LOCKED`, retries failures up to
`INGESTION_MAX_ATTEMPTS`, and marks a document `failed` with a readable error
after the last attempt.

With `AI_API_KEY` empty (or `AI_PROVIDER=fake`) a deterministic offline provider
is used. Tests always run offline and never call a paid model.

## Stage 3 provider and model settings

Real calls use any OpenAI-compatible chat-completions and embeddings endpoint.
Credentials stay in the server environment and are never returned by an API or
bundled into the web/extension clients.

| Variable | Meaning |
|---|---|
| `AI_PROVIDER`, `AI_BASE_URL`, `AI_API_KEY` | Provider selection and server-side credential |
| `AI_CHAT_MODEL`, `AI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | Deployment model defaults |
| `AI_TIMEOUT_SECONDS` | Per-request timeout |
| `AI_TEMPERATURE`, `CHAT_MAX_OUTPUT_TOKENS` | Default generation settings |
| `AI_MAX_RETRIES`, `AI_RETRY_BACKOFF_SECONDS`, `AI_RETRY_MAX_BACKOFF_SECONDS` | Transient-failure retry budget |

Transient rate limits, timeouts, connection errors and 5xx responses are retried
with bounded exponential backoff. Authentication errors, unknown models and
malformed requests fail immediately. Chat converts exhausted `AIError`s into
`503 ai_unavailable` and does not persist a partial user/assistant turn.

`PATCH /organizations/{org_id}/assistants/{assistant_id}` accepts validated
per-assistant generation overrides:

```json
{
  "model_options": {
    "model": "gpt-4.1-mini",
    "temperature": 0.2,
    "max_output_tokens": 600
  },
  "retrieval_config": {"top_k": 6, "min_score": 0.2}
}
```

The embedding model and dimensions are intentionally deployment-wide. Every
document records the model/dimensions that produced its vectors, and retrieval
filters on both so incompatible vector spaces are never compared.

## Tenant isolation

Tenant tables (`organizations`, `memberships`, `invitations`, `assistants`,
`documents`, `document_chunks`, `ingestion_jobs`, `conversations`, `messages`,
`usage_events`, `audit_logs`) use PostgreSQL Row-Level Security with `ENABLE`
and `FORCE ROW LEVEL SECURITY`. Every authenticated request sets
transaction-local `app.current_org` and `app.current_user` values before reading
tenant data. The runtime role `wm_app` is a non-superuser without `BYPASSRLS`
and has no `DELETE` privilege; audit rows and usage metering are append-only.
The worker gate (`app_is_worker()`) applies only to the job queue, never to
document or chat content.

Run the isolation suite with a real database:

```powershell
$env:RUN_DB_TESTS = "1"
.\.venv\Scripts\python -m pytest -v
```
