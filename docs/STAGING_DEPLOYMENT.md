# Staging Deployment and Rollback — WorkMeta AI

All commands run on the Ubuntu staging host from the repository root
(`/opt/workmeta/checkout`). WorkMeta version tag is `WORKMETA_VERSION`.

## Variables required by the staging services

Compose (`--env-file infra/.env.staging`) needs: `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `APP_DB_USER`, `APP_DB_PASSWORD`, `POSTGRES_DB`
(default `workmeta_stage`), `WORKMETA_VERSION` (default `latest`).

The API/workers also read `infra/.env.staging` whole (`env_file`), so it must
additionally contain **every** variable from `infra/deploy/env.staging.example`:
`APP_ENV=test`, `APP_BASE_URL=http://127.0.0.1:18080`, `CORS_ORIGINS`,
`ALLOWED_HOSTS=127.0.0.1,localhost,staging.example.com`,
`SESSION_SECRET` (≥32 chars), `SESSION_TTL_DAYS=7`, `SESSION_IDLE_HOURS=8`,
`INVITATION_TTL_DAYS`, `PASSWORD_RESET_TTL_MINUTES`, `BACKUP_RETENTION_DAYS`,
all `AI_*` tunables, `BILLING_PROVIDER`, `FLUTTERWAVE_*`/`PAYSTACK_*`,
`STORAGE_BACKEND=local`, `STORAGE_DIR=/app/var/storage`,
`RATE_LIMIT_*`, `MAX_REQUEST_BODY_BYTES`.

**Staging isolation:** staging uses its own `POSTGRES_*`/`APP_DB_*` credentials,
`SESSION_SECRET`, database `workmeta_stage` and volumes `workmeta_stage_pgdata`
+ `workmeta_stage_storage`. Production variables are never copied here.

## Deploy

```bash
cd /opt/workmeta/checkout

# 0. Fresh backup BEFORE any migration (see docs/BACKUP.md).
#    (From a shell with the venv available, or via docker exec pg_dump.)

# 1. Build images.
docker build -f apps/api/Dockerfile -t workmeta-api:$WORKMETA_VERSION .
docker build -f apps/web/Dockerfile -t workmeta-web:$WORKMETA_VERSION .

# 2. Start the database.
WORKMETA_VERSION=$WORKMETA_VERSION docker compose \
  -f infra/docker-compose.staging.yml --env-file infra/.env.staging up -d db

# 3. Run migrations EXPLICITLY (never on container start).
WORKMETA_VERSION=$WORKMETA_VERSION docker compose \
  -f infra/docker-compose.staging.yml --env-file infra/.env.staging run --rm migrate

# 4. Start everything.
WORKMETA_VERSION=$WORKMETA_VERSION docker compose \
  -f infra/docker-compose.staging.yml --env-file infra/.env.staging up -d

# 5. Status + health.
docker compose -f infra/docker-compose.staging.yml --env-file infra/.env.staging ps
docker compose -f infra/docker-compose.staging.yml --env-file infra/.env.staging logs -f api worker billing-worker web edge --tail 50
```

Startup ordering: `db (healthy)` → `api/worker/billing-worker (after db healthy)`
→ `web (after api)` → `edge (after web)`. Workers claim jobs with
`SKIP LOCKED`, so extra replicas are safe.

## Verify

```bash
# Readiness probes through the edge (not direct container IPs).
curl -s http://127.0.0.1:18080/api/v1/health
curl -s http://127.0.0.1:18080/api/v1/ready
curl -s http://127.0.0.1:18080/api/v1/metrics | head

# Full validator (needs a bearer token for /me).
py tools/scripts/validate_staging.py --base-url http://127.0.0.1:18080 --token <bearer>

# k6 load smoke.
k6 run tools/k6/smoke.js --env BASE_URL=http://127.0.0.1:18080 --env TOKEN=<bearer>
```

Expected: `validate_staging.py` prints `STAGING VALIDATION PASSED`; k6
thresholds `p(95)<800`, `rate<0.02` pass. Migration ordering proof:
`migrate` exits 0 before `api` serves traffic (check `docker compose logs migrate`).

## Rollback

```bash
# 1. Stop everything (volumes are kept).
docker compose -f infra/docker-compose.staging.yml --env-file infra/.env.staging down

# 2. Re-deploy the previous tag.
WORKMETA_VERSION=<previous-tag> docker compose \
  -f infra/docker-compose.staging.yml --env-file infra/.env.staging up -d

# 3. If a migration was applied, downgrade explicitly (staging clone first).
docker compose -f infra/docker-compose.staging.yml --env-file infra/.env.staging run --rm migrate
# Inside the container: alembic downgrade -1  (or to the previous revision)

# 4. If data is damaged, restore from backup (docs/BACKUP.md).
py tools/scripts/restore.py --backup var/backups/<stamp> --yes
```

**Never** run a destructive migration without a verified backup and an
approved window.

## Payment sandbox credentials

Staging never uses live keys. Per provider (Flutterwave / Paystack):

1. Create sandbox credentials in the provider dashboard (API test keys).
2. Set **one** gateway only in `infra/.env.staging`:
   - Flutterwave: `BILLING_PROVIDER=flutterwave`, `FLUTTERWAVE_SECRET_KEY=FLWSECK_TEST-…`,
     `FLUTTERWAVE_WEBHOOK_HASH=<dashboard hash>`, `BILLING_CHECKOUT_CALLBACK_URL=http://127.0.0.1:18080/billing/checkout`
   - Paystack: `BILLING_PROVIDER=paystack`, `PAYSTACK_SECRET_KEY=sk_test_…`,
     `PAYSTACK_WEBHOOK_SECRET=<endpoint secret>`
3. Restart only the API: `docker compose ... up -d --force-recreate api`.
4. Create a checkout via the API, pay in the provider sandbox, then verify:
   `py tools/scripts/test_payment_sandbox.py --provider <flutterwave|paystack> --reference <WM-…> --expect <amount_minor>`
   (script verifies server-side, checks signatures + tamper rejection, no charge).
5. Keep `BILLING_PROVIDER=mock` until the release checklist signs off sandbox
   PASS; never mount live keys on this host.

The `BILLING_PROVIDER` mismatch error from the script is the safety guard:
if the running provider differs from the flag, nothing is verified.
