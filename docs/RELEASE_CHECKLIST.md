# Release Checklist — WorkMeta AI

> Updated 2026-11-12 (Stage 9B). Status per line: **PASS** (executed), **FAIL**, **BLOCKED** (missing infra/creds), **NOT TESTED** (not executed).
> Local = this machine, CI = GitHub Actions, Staging = staging host, Prod = production.
> Do not deploy production until every FAIL/BLOCKED/NOT TESTED is PASS or waived with owner + date.
> Ubuntu host setup: `docs/UBUNTU_SETUP.md`. Deploy/verify/rollback: `docs/STAGING_DEPLOYMENT.md`.
> Vercel frontend: `docs/VERCEL_DEPLOYMENT.md` (VITE_API_BASE_URL + CORS/ALLOWED_HOSTS).

## Staging deployment (Stage 8A)
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Ubuntu host provisioned (24.04, Docker 24+, 22/SSH only) | BLOCKED | No staging host | Infra / — | `docs/UBUNTU_SETUP.md` |
| Staging env (`APP_ENV=test`, `SESSION_SECRET≥32`, `ALLOWED_HOSTS` incl localhost, `BILLING_PROVIDER=mock`) | PASS (local) | `env.staging.example` parses (`Settings` OK); Compose interpolates `${POSTGRES_*}`/`${APP_DB_*}` | — | Fill `.env.staging` on host (chmod 600) |
| Compose `config` | NOT TESTED | No Docker binary; YAML+env validated locally only | — | `docker compose config --quiet` on host |
| Images built (`workmeta-api/web:$WORKMETA_VERSION`) | NOT TESTED | No Docker; CI docker job defined | — | `docker build` on host |
| `db healthy` → `migrate` → `api/worker/billing-worker` → `web` → `edge` | NOT TESTED | Startup ordering declared; not run | — | `STAGING_DEPLOYMENT.md` steps 2–5 |
| `/health`, `/ready`, `/metrics` via edge | NOT TESTED | Local coverage via 106 tests | — | `validate_staging.py --base-url http://127.0.0.1:18080` |
| k6 `smoke.js` thresholds | NOT TESTED | k6 not installed; script+targets ready | — | `k6 run tools/k6/smoke.js` |
| Payment sandbox (both gateways) | BLOCKED | `mock` only; no `FLUTTERWAVE_*`/`PAYSTACK_*` | Payments / — | `STAGING_DEPLOYMENT.md#payment-sandbox-credentials` |
| Rollback rehearsed | NOT TESTED | Procedure written; downgrade round-trip PASS locally | — | Rehearse on staging |

## Infrastructure validation
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Docker images build (api/web) | NOT TESTED | No Docker binary locally; CI `docker` job defined, not run here | — | Run CI docker job |
| Compose config valid (staging+prod) | PASS (local) | `yaml.safe_load` + service list `['api','billing-worker','db','edge','migrate','web','worker']`; api/web healthchecks present; staging env parses (`Settings` OK); edge-stage 12 MB cap | — | `docker compose config` on host |
| Staging stack up (db/api/workers/web/edge) | BLOCKED | No Docker binary, no staging host (`docker`/`ssh config`/`infra/.env.staging` all absent locally) | Infra / — | Provision Ubuntu host per `UBUNTU_SETUP.md`, then `STAGING_DEPLOYMENT.md` |
| `/health`, `/ready`, `/metrics` on staging | NOT TESTED | Local coverage via 106 integration tests (health/ready/metrics asserts) | — | `validate_staging.py` on staging |
| Gated migrate + `alembic check` | PASS (local) | `check`: no drift; round-trip `0007→0006→0007` passed | — | Same on staging |
| TLS / HSTS / `ALLOWED_HOSTS` | BLOCKED | Edge TLS certs not present; staging is HTTP localhost by design | Infra / — | Certbot on staging |
| Volumes persist; private net | PASS (local) | Compose `internal: true` private net; named volumes declared | — | Restart-persistence check on host |

## Security validation
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| No secrets in repo | PASS (local) | No `.env`/keys found (git not initialized); only `.example` templates; toolchain certs in `.venv`/`var/` only | — | Secret scan in CI |
| Prod secret strength / `BILLING_PROVIDER!=mock` | PASS (local) | `model_validator` rejects weak/placeholder/mock; `test_production_requires_strong_secrets` passed | — | — |
| Rate limits, body cap, idle timeout, MFA, reset | PASS (local) | `test_security_integration` (4) + `test_security2_integration` (3, MFA/replay/idle/reset) passed | — | MFA mandatory-for-admins decision |
| Platform content boundary | PASS (local) | Billing isolation test (403 without membership) passed; RLS forced | — | — |
| Raw-SQL tenant attacks fail closed | PASS (local) | Auth tenancy + RAG isolation suites passed (106 total) | — | — |
| Email delivery for reset/invite | NOT TESTED | Tokens returned in response for manual delivery; no SMTP | — | Wire email provider |
| IP/UA risk detection (no lockout) | PASS (audit-only) | `auth.suspicious_login` on IP change; no lockout implemented (mobile-safe) | — | Alerting on audit |

## Payment validation
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Flutterwave sandbox round-trip | BLOCKED | `BILLING_PROVIDER='mock'`, no `FLUTTERWAVE_*` in `.env` | Payments / — | Mount sandbox keys + `test_payment_sandbox.py` |
| Paystack sandbox round-trip | BLOCKED | Same (no `PAYSTACK_*`) | Payments / — | Same |
| Webhook sig/duplicate/amount/currency (mock) | PASS (local) | Billing suite: good/bad sig, duplicate, amount mismatch, re-verify path | — | Real-provider webhook replay |
| Refund + renewal (mock) | PASS (local) | Refund/revenue + renewal tests passed | — | Sandbox refund |
| Live keys unmounted | PASS (local) | Prod validator refuses `mock`; no live keys in templates | — | Keep until sandbox PASS |

## Backup restoration validation
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Staging backup + `--verify-only` | PASS (local) | `backup.py` ran (26 KB dump + tarball); hashes `size_ok/hash_ok True` | — | Schedule nightly |
| Isolated restore + integrity | PASS (local) | `drill_restore.py`: orgs 11/11, users 12/12, subs 11/11, docs 0/0, payments 0/0; source unmodified; throwaway dropped | — | Restore onto staging host once |
| Storage restore + doc accessibility | NOT TESTED | Storage bytes verified present (760 B) but no tarball-restore + download check in drill | Ops / — | Extend drill to tarball extract + GET |
| Nightly cron + off-site copy | NOT TESTED | No schedule configured | Ops / — | Cron/Task Scheduler + object copy |
| RPO 24h / RTO 2h named | PASS (doc) | `docs/BACKUP.md` | — | Sign owners |

## Load testing
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| k6 thresholds (p95<800 ms, err<2%) | NOT TESTED | k6 not installed; no staging URL | Perf / — | Install k6, run `tools/k6/smoke.js` on staging |
| Local concurrency evidence | PASS (local) | Stage 5 concurrency test (1/4 chats succeed, rest 429) | — | — |
| DB conns / queue depth / CPU-mem | NOT TESTED | `/metrics` exposes queue + DB size; no scrape run | — | Prometheus + Grafana + k6 |

## Monitoring
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| `/metrics` aggregates | PASS (local) | `render_metrics` tested; no tenant IDs in output | — | Scrape + alerts |
| Alerts (5xx/p95/queue/backup-age) | NOT TESTED | No alert rules defined | Ops / — | Prometheus alertmanager |
| Log aggregation + secret grep | PASS (local) | Structlog JSON in prod; no secrets/content in logs (audit review) | — | Ship logs + grep gate |
| Audit trail spot-check | PASS (local) | Audit actions asserted in integration tests | — | — |

## Deployment
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Release tag + CI green | NOT TESTED | CI defined; no tag run here | — | Tag `WORKMETA_VERSION` after CI |
| Gated migrate + `up -d` + smoke | NOT TESTED | Requires staging host | Ops / — | `DEPLOYMENT.md` procedure |
| Cookies/TLS headers in prod | NOT TESTED | Security headers asserted locally; `Secure` prod-gated | — | Curl checks on staging |

## Rollback
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Previous-tag rollback | PASS (doc) | `DEPLOYMENT.md` procedure | — | Rehearse on staging |
| Downgrade round-trip | PASS (local) | `alembic downgrade -1` → `upgrade head` passed locally | — | Same on staging |
| Pre-deploy restore rehearsal | PASS (local) | Isolated drill passed (see above) | — | Timed RTO on staging |

## Final approval
| Requirement | Status | Evidence | Owner / Date | Next action |
|---|---|---|---|---|
| Owner / tech / security sign-off | NOT TESTED | Blank (see signatures) | — | Sign after all gates PASS |

**Release verdict: NOT PRODUCTION-READY** — blocked on sandbox payments, staging boot, TLS, load test, email delivery, S3, and alerting.

Owner: ____________  Date: ______  Tech: ____________  Security: ____________
