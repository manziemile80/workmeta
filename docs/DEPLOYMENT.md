# Deployment Guide — WorkMeta AI

> Frontend-only on Vercel? See `docs/VERCEL_DEPLOYMENT.md`.
> Full Compose stack (DB + API + workers + web + edge)? See `infra/docker-compose.prod.yml`.

## Topology

```
Internet
  └─ edge (nginx, ports 80/443, TLS) ──► web (nginx static + /api/ proxy)
                                              └─► api (uvicorn, 1+ workers)
                                                      ├─► db (PostgreSQL + pgvector, private net)
                                                      ├─► worker (ingestion pipeline)
                                                      └─► billing-worker (subscription lifecycle)
```

- Only 80/443 on `edge` are public. The database, workers and API internals are on the internal `private` Docker network.
- File storage currently uses a local volume (`workmeta_storage`). Mount an S3-compatible volume in production and set `STORAGE_BACKEND=s3` when that backend ships (currently `local` only — see limitations).
- TLS terminates at `edge`. Obtain certificates (Certbot webroot) into `infra/certs/` and set `server_name` in `infra/nginx/edge.conf`.

## Build and run

From the repository root:

```bash
docker build -f apps/api/Dockerfile -t workmeta-api:$VERSION .
docker build -f apps/web/Dockerfile -t workmeta-web:$VERSION .
cp infra/deploy/env.production.example infra/.env.production  # fill ALL secrets
cd infra
WORKMETA_VERSION=$VERSION docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

The API image defaults to `APP_ENV=production`; startup refuses weak `SESSION_SECRET`, placeholder DB passwords, `BILLING_PROVIDER=mock`, `ALLOWED_HOSTS=*`, or wildcard CORS.

## Migrations (gated, never automatic)

Migrations are **not** run on container start. Before starting new code:

1. Take a backup (`tools/scripts/backup.py`, see `BACKUP.md`) and verify it.
2. Run explicitly: `docker compose -f docker-compose.prod.yml run --rm migrate`
3. Start new replicas. If a migration fails, **do not** start new code; roll back the migration and the previous image (below).

## Environment variables

See `infra/deploy/env.production.example` and `.env.example`. Production essentials:

- `APP_ENV=production`, `APP_BASE_URL`, `CORS_ORIGINS`, `ALLOWED_HOSTS`
- `DATABASE_URL` (wm_app) + `DATABASE_MIGRATION_URL` (owner, migrate only)
- `SESSION_SECRET` (≥32 chars), `SESSION_TTL_DAYS`
- `AI_API_KEY` (server-side only), model settings
- `BILLING_PROVIDER` (`flutterwave`/`paystack` + secrets), callback URL
- `RATE_LIMIT_*` (proxy is authoritative; in-app is a backstop)
- `WORKMETA_VERSION` (image tag)

## Health checks

- `GET /api/v1/health` — liveness (no DB).
- `GET /api/v1/ready` — readiness (DB + pgvector).
- `GET /api/v1/metrics` — Prometheus aggregates (request counts, ingestion queue depth, DB size). No tenant identifiers.
- Docker `HEALTHCHECK` hits `/health` (API) and `/healthz` (web).

Point the load balancer / container orchestrator at `/health` (liveness) and `/ready` (readiness).

## Workers

- `worker` runs `python -m app.workers.ingestion` (claim via `SKIP LOCKED`, `app_is_worker` gate, retries).
- `billing-worker` runs `python -m app.workers.billing` (subscription advance + counter retention).
- Scale ingestion by adding more `worker` replicas (all claim safely via `SKIP LOCKED`).

## Rollback

1. Stop new code: `docker compose ... down` (keeps volumes).
2. Restore previous images: set `WORKMETA_VERSION=<previous tag>` and restart.
3. If a migration was applied, downgrade explicitly: `alembic downgrade -1` (test the round-trip on a staging clone first — CI runs it automatically).
4. If data was corrupted, restore from backup (`tools/scripts/restore.py`, see `BACKUP.md`).

**Never** run a destructive migration against production without a verified backup and an approved window.

## CI/CD

`.github/workflows/ci.yml` runs lint, pip/npm audits, migrate, drift check, round-trip, full DB tests, web typecheck/build, and Docker image builds. There is **no automatic deploy/CD**: promotion is manual via `WORKMETA_VERSION` tags after CI passes, per the gated procedure above.
