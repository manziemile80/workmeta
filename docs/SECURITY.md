# Security Documentation — WorkMeta AI

> **Your Business. Your AI. Your Way.** Tenant isolation is the product promise.
> This document reflects what is enforced in code, not just policy.

## Authentication and sessions

- Argon2id passwords (`argon2-cffi`), keyed HMAC-SHA256 token hashes (`SESSION_SECRET`), 256-bit random tokens, dummy-hash timing equalization for unknown emails.
- Web sessions: HttpOnly `wm_session` + readable `wm_csrf` double-submit for cookie mutations; extensions use bearer tokens (`kind=extension`). `Secure` is set in production.
- Production requires `SESSION_SECRET` ≥ 32 characters; placeholder DB passwords (`change-me`) are rejected; `ALLOWED_HOSTS=*` and wildcard CORS are rejected.
- In-memory per-IP rate limits on login / registration / invitation acceptance / webhooks / chat (defaults generous; tune `RATE_LIMIT_*` and the reverse proxy as the authoritative limiter).
- **Idle timeout:** sessions die after `SESSION_IDLE_HOURS` (8) of inactivity (`sessions.last_seen_at`); absolute TTL is `SESSION_TTL_DAYS`.
- **MFA (TOTP):** enroll/confirm/disable at `/auth/mfa/*`; login requires a fresh code (replay-protected via `mfa_last_step`). Enforcement is per-user opt-in — promote to platform-admin mandatory in the release gate.
- **Password reset:** `/auth/password-reset/*` — single-use, hash-only, 1h expiry, revokes all sessions; no account oracle (always `ok`).
- **Registration:** `/auth/register` — self-service sign-up that creates the person, their first organization and the Free subscription in one transaction, then signs them in. The password policy is at least 10 characters with a letter and a number; the insert policy only lets a person create an organization while they have no membership at all (migration `20261113_0009`), so one account cannot farm tenants.
- **Suspicious login:** IP-change audits (`auth.suspicious_login`) + `failed_login_count`. No IP-based lockout (mobile users behind carrier NAT keep working); detection is audit-only.

## Authorization and tenant isolation

- Every tenant query sets transaction-local `app.current_org` / `app.current_user` before reading (`db/rls.py`).
- **Forced RLS** (`ENABLE` + `FORCE`) on all tenant tables. `GET /organizations` additionally filters in the ORM (defense in depth).
- `require_org_admin` for billing/assistant mutations; `require_org_member` for knowledge/conversations — **the platform owner has no content bypass** (403 without a real membership row; covered by integration tests).
- Cross-tenant reads return 404, never 403 (no existence oracle).

## Input validation and injection defenses

- All queries are ORM or bound-parameter `text()`; raw SQL uses fixed fragments (ruff `S608` suppressed only with a comment).
- Uploads validated by **magic bytes** (PDF, images, DOCX-ZIP), extension↔content cross-check, filename sanitization, server-generated storage keys, `attachment` + `nosniff` downloads.
- JSON schemas use `extra="forbid"` with bounded `Field`s. Request-ID validated against CRLF/log injection.

## CSRF, CORS, headers, hosts

- Double-submit CSRF on cookie-authenticated unsafe methods; bearer (extension) correctly exempt.
- CORS allow-listed to configured origins (`CORS_ORIGINS`), credential-aware, header/method restricted.
- Headers: `nosniff`, `DENY`, `no-referrer`, `Permissions-Policy`, `CORP`, `CSP: default-src 'none'; frame-ancestors 'none'`, HSTS (production), `Cache-Control: no-store` on auth/billing/platform paths.
- `TrustedHostMiddleware` (`ALLOWED_HOSTS`); reverse-proxy body cap (12 MiB, matches `MAX_REQUEST_BODY_BYTES`) + in-app `BodyLimitMiddleware` (413).

## Payments and webhooks

- Paystack: HMAC-SHA512 over the raw body; Flutterwave: constant-time `verif-hash` compare; mock: HMAC-SHA256.
- Webhook `succeeded` events for real providers are **re-verified with the provider** (`BILLING_WEBHOOK_VERIFY=true`) before activation — a leaked hash alone cannot activate a plan.
- Checkout confirmation always re-verifies server-side; frontend callbacks are never trusted. Amount/currency mismatches fail the payment. `processed_webhook_events` makes delivery idempotent (duplicate → 200, no re-apply). References are unguessable (`WM-<uuid>`).
- **No card data is stored.** Only provider references, amounts, currency and non-sensitive metadata.
- Mock provider is refused in production.

## Trained models (WorkMeta AI Submodels)

- **Per client, never global.** `submodels`, `submodel_materials`, `submodel_procedures`, `submodel_tests` and `submodel_action_plans` all carry `org_id` with forced RLS and the tenant policy; the runtime role cannot read another client's training material. `products` stays global metadata — the *training* is always per client.
- **No claim before proof.** A submodel's workflows are drafts until a human approves them, and an approval is withdrawn when training changes the steps it approved. The gate lives in the service layer, so it cannot be bypassed by calling the API.
- **Grounded answers.** The training prompt and the guidance prompt treat the recording and the workflows as untrusted data, never as instructions, and refuse to invent steps that are not in the material.
- **Signed packages.** The published package is signed with Ed25519 over its canonical JSON (`EXTENSION_SIGNING_PRIVATE_KEY`), and the **workflows themselves** are inside the signature. The public key travels with the package, so a package trained on another machine is verified against the key it carries — and a tampered workflow is refused.
- **Authorised actions.** The agent asks the server before acting. `auto_actions` run on their own, `confirm_actions` (submitting an application, moving money, deleting a record, sending) require the user's confirmation, `denied_actions` are refused, and a step marked sensitive always forces a confirmation. A refused action is stored as refused and can never be reported as performed.
- **Least privilege.** The runtime role gets `SELECT/INSERT/UPDATE` on the tables and no `DELETE`: materials are archived, workflows are rejected, tests and action plans stay as history.

See `docs/SUBMODELS.md` for the full model, lifecycle and runtime contract.

## Secrets and logging

- Secrets come only from environment (`.env`, never committed). `SESSION_SECRET`, DB passwords, `AI_API_KEY`, `FLUTTERWAVE_*` / `PAYSTACK_*` are server-side only and absent from API responses and logs.
- Logs record request ID, method, path, status, duration and client IP only. AI/provider errors log `error_type`/`model`/`retryable`. Unexpected errors return a safe envelope. No passwords, keys, document bytes or conversation content are logged. `audit_logs` is append-only (`UPDATE/DELETE` revoked) but treat it as PII (stores emails/references); define a retention purge.

## Database permissions

- Runtime role `wm_app`: non-superuser, `NOBYPASSRLS`, `SELECT/INSERT/UPDATE` only (no `DELETE`), append-only revokes on `audit_logs`/`usage_events`/`processed_webhook_events`, `alembic_version` fully revoked, `SECURITY DEFINER` functions pinned (`search_path = public, pg_temp`, tenant checks, `REVOKE ... FROM PUBLIC`).
- `users`/`sessions` intentionally lack RLS (must be readable pre-auth for login/session load); hashes limit offline impact. Hardening this further requires `SECURITY DEFINER` lookup functions.

## Known limitations (honest)

- In-memory rate limiter is per-process and resets on restart (proxy is authoritative).
- No MFA yet (planned). No idle session timeout (absolute TTL only). CSRF not rotated on org switch (full session TTL).
- Flutterwave webhook hash is static (not body-bound); mitigated by live re-verification but hash rotation is manual.
- `GET /billing/plans` is unauthenticated by design (public pricing); `/` and `/health` fingerprint version/environment for load balancers.
- `sentry_dsn` is configured but unused. The signing key signs the branded agent package and the submodel packages; without it packages are published unsigned.
