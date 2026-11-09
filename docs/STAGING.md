# Staging Validation — WorkMeta AI

## What staging is

A promotion-gate replica of production: same images, same migrations, same
workers, staging secrets, **sandbox payments only**. See
`infra/docker-compose.staging.yml`, `infra/nginx/edge-stage.conf` and
`infra/deploy/env.staging.example`.

Docker is not available on the development machine, so the Compose stack
was validated only in CI (`docker compose config`) and by the local API
integration suite (106 tests, same code path). Up staging with:

```bash
cp infra/deploy/env.staging.example infra/.env.staging  # fill staging secrets
cd infra
WORKMETA_VERSION=<tag> docker compose -f docker-compose.staging.yml --env-file .env.staging up -d --build
docker compose -f docker-compose.staging.yml run --rm migrate
# then: py ../tools/scripts/validate_staging.py --base-url http://127.0.0.1:18080
```

## Validated on this machine (executed)

- Full integration suite (health/ready/metrics, auth, billing, workers) — 106 tests, same code as staging.
- `tools/scripts/validate_staging.py` helpers exist for the staging host (not run here — no listener).
- Isolated backup/restore drill (`tools/scripts/drill_restore.py`): production backup → throwaway `workmeta_stage_drill` restore → row-count check → drop. **Drill passed.**

## Deployment mistakes found and fixed (Stage 8A/8B review)

1. **Edge → web networking (FIXED):** `web` was on `private` only, `edge` on
   `proxy` only — nginx `upstream workmeta_web` could not resolve. `web` is now
   on `[private, proxy]` in both staging and prod Compose.
2. **Staging env host mismatch (FIXED):** `ALLOWED_HOSTS`/`CORS_ORIGINS` used
   `https://staging.example.com` while the edge binds `127.0.0.1:18080`/HTTP.
   Example now uses `http://127.0.0.1:18080` + `127.0.0.1,localhost,staging.example.com`.
3. **Edge upload cap (FIXED):** `edge-stage.conf` lacked `client_max_body_size`;
   added `12m` to match `MAX_REQUEST_BODY_BYTES` / web tier.
4. **No healthchecks (FIXED):** added API (`/api/v1/health`) + web (`/healthz`)
   healthchecks to the staging stack.
5. **Build context (documented):** both Dockerfiles `COPY` repo-root-relative
   paths (`apps/api/...`), so builds must run **from the repo root** with
   `docker build -f apps/api/Dockerfile .` — noted in `UBUNTU_SETUP`/`STAGING_DEPLOYMENT`.
6. **DB password drift (documented):** the `workmeta_stage_pgdata` volume pins
   the first-boot passwords; changing `POSTGRES_PASSWORD`/`APP_DB_PASSWORD`
   later requires `down -v` (data loss) or `ALTER ROLE` — see deploy guide.
7. **Staging `APP_ENV=test` (documented):** deliberately not `production` so the
   mock gateway can boot until sandbox keys are mounted; switch to production
   + real gateway only after the release checklist signs off.

## Not validated here (blocked)

- Docker image builds / Compose `up` (no Docker binary). Covered by CI `docker` job.
- Payment sandbox (no sandbox credentials; provider is `mock`). Run
  `tools/scripts/test_payment_sandbox.py` on staging once keys exist.
- k6 load test (k6 not installed). Script + thresholds in `tools/k6/smoke.js`.

## Gate checklist before promotion

- [ ] `docker compose` stack healthy; `/health`, `/ready`, `/metrics` green.
- [ ] Tenant admin + worker connectivity verified (upload → ingest → chat).
- [ ] Sandbox checkout → verify → webhook → invoice → refund round-trip.
- [ ] Backup → isolated restore → spot checks (document download, chat).
- [ ] k6 thresholds met (p95 < 800 ms, error rate < 2%).
- [ ] `docs/RELEASE_CHECKLIST.md` all items signed off.

## Host setup and deployment

- Server provisioning: `docs/UBUNTU_SETUP.md`
- Exact deploy/verify/rollback commands: `docs/STAGING_DEPLOYMENT.md`
- Payment sandbox credential setup: `docs/STAGING_DEPLOYMENT.md#payment-sandbox-credentials`
