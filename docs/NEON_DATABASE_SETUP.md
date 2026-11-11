# Neon Database Setup — WorkMeta AI (Free Tier)

> RLS, tenant isolation and least-privilege grants are preserved on Neon.
> Never paste connection strings into git, logs, or chat.

## Where to get the strings (Neon dashboard)

1. Neon → project `workmeta` → **Connection string**.
2. Use the **direct** endpoint for both strings on the free/single-instance
   deployment. The app's asyncpg pool is 5 + 10 overflow, far below Neon Free's
   connection limit, and the direct endpoint avoids PgBouncer transaction-mode
   prepared-statement failures.
   (If you later need the pooler, set the runtime URL to the `-pooler` host and
   verify `asyncpg` prepared statements — do not assume it works.)
3. Strings end with `?sslmode=require&channel_binding=require`. Keep `sslmode`;
   the app strips `channel_binding` and rewrites `sslmode` to `ssl` for asyncpg.

## Required extensions (before migrations)

Run these once in Neon's SQL editor **before** `alembic upgrade head`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
```

`vector` backs the embedding columns/indexes (migration `0002`); `citext` backs
`users.email` / `invitations.email` (migration `0001`). Without `citext` the
first migration fails.

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `/api/v1/health` → `database_configured: false` | `DATABASE_URL` is missing or still the placeholder | Set `DATABASE_URL`/`DATABASE_MIGRATION_URL` on Render and redeploy |
| `/api/v1/ready` → 503 `database_not_configured` | Same as above (the API distinguishes *not configured* from *unreachable*) | Same as above |
| `/api/v1/ready` → 503 `database_unavailable` | The URL is set but Postgres cannot be reached (DNS/SSL/credentials) | Check the URL host, `sslmode=require`, and that `wm_app` may `CONNECT` |
| `/api/v1/ready` → 200 but `pgvector: false` | `vector` extension missing | `CREATE EXTENSION IF NOT EXISTS vector;` as the owner |
| Render deploy `update_failed` + `IndexError` in `config.py` | The image layout has fewer parent directories | Fixed in `35408d6` - pull `main` and redeploy |

`/api/v1/login` returns the same 503 codes while the database is unavailable; the
response body never contains the URL, the role or any credential.

## Create the restricted runtime role (before migrations)

The API must **not** connect as the Neon owner. Migrations grant privileges to
the role in `DATABASE_URL`'s username, so create `wm_app` first and run
migrations with `DATABASE_URL` pointing at it:

```sql
CREATE ROLE wm_app LOGIN PASSWORD '<generated-32+-char-password>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE neondb TO wm_app;
GRANT USAGE ON SCHEMA public TO wm_app;
```

If the Neon owner role is not allowed to `CREATE ROLE` (Neon restricts it for
some plans), create the role in the console instead: **Neon → project → Roles →
Create role** (`wm_app`, generate a password), then put that exact password in
`var/deploy-secrets.env` as `WM_APP_DB_PASSWORD=<password>`. The provisioning
script will use it as-is and never try to alter the role.

Migration `0001` then grants `SELECT/INSERT/UPDATE` on all tables, sequence
usage, and default privileges — but never `DELETE`, `UPDATE` on `audit_logs`, or
any privilege on `alembic_version`.

## DATABASE_URL vs DATABASE_MIGRATION_URL

| Variable | Role | Why |
|---|---|---|
| `DATABASE_URL` (API/workers) | `wm_app` | Restricted runtime role; RLS applies |
| `DATABASE_MIGRATION_URL` (Alembic) | Neon owner | Migrations need DDL + extension privileges |

Both must be `postgresql+asyncpg://`. `DATABASE_URL`'s username **must** remain
`wm_app`, otherwise `_app_role()` grants runtime privileges to the owner role
and least privilege is silently lost.

## pgvector / citext check

```sql
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('vector', 'citext');
```

`/api/v1/ready` reports `pgvector: true`.

## Windows one-off migration (no Render Shell needed)

From the repo root (never prints the URL):

```powershell
cd apps/api
$env:DATABASE_MIGRATION_URL = "<neon-owner-direct-url>"
$env:DATABASE_URL = "<wm_app-direct-url>"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
Remove-Item Env:DATABASE_MIGRATION_URL
Remove-Item Env:DATABASE_URL
```

Expect `20261112_0007 (head)`.

## Verify API↔Neon

```bash
curl -s https://<render-host>/api/v1/ready
# {"status":"ready","database":"ok","pgvector":true,...}
```

If `database: unavailable`, check that the role password and `sslmode` survive
the URL rewrite (`ssl=require`), and that `wm_app` has `CONNECT`.
