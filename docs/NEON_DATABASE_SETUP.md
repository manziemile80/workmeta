# Neon Database Setup — WorkMeta AI (Free Tier)

> RLS, tenant isolation and least-privilege grants are preserved on Neon.
> Never paste connection strings into git, logs, or chat.

## Where to get the strings (Neon dashboard)

1. Neon → project `workmeta` (us-east-2 / Ohio) → **Connection string**.
2. Toggle **Pooled** ON for the API runtime string; OFF (direct) for the one-off migration string.
3. Both strings end with `?sslmode=require&channel_binding=require`. Keep `sslmode`, the app strips `channel_binding` automatically (asyncpg).

## DATABASE_URL vs DATABASE_MIGRATION_URL

| Variable | Use that endpoint | Why |
|---|---|---|
| `DATABASE_URL` (API/workers) | **Pooled** | Long-lived asyncpg pool (5 + 10 overflow) — pooler handles Render cold starts |
| `DATABASE_MIGRATION_URL` (Alembic) | **Direct** (one-off) | Migrations are transactional DDL + extensions — direct is safest for the single run |

Both must be `postgresql+asyncpg://` (app normalizes for you, but enter it that way). `?sslmode=require` → normalized to `?ssl=require`. `channel_binding` → stripped.

## pgvector

After creating the project, in Neon's SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Check `alembic` output or `SELECT extversion FROM pg_extension WHERE extname='vector';` — the API `/api/v1/ready` reports `pgvector: true`.

## Windows one-off migration (no Render Shell needed)

From the repo root (never prints the URL):

```powershell
cd apps/api
$env:DATABASE_MIGRATION_URL = (Get-Content "$HOME\Desktop\neon-direct.txt").Trim()
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
Remove-Item Env:23
```

Or paste the direct string into `$env:DATABASE_MIGRATION_URL` in the terminal prompt (masked in Render Dashboard if you prefer), run the same two commands, then close the shell. Expect `20261112_0007 (head)`.

## Verify API↔Neon

```bash
curl -s https://<render-host>/api/v1/ready
# {"status":"ready","database":"ok","pgvector":true,...}
```

If `database: unavailable`, the pooled string or `channel_binding` handling is the first thing to check (fixed in config normalization).
