#!/bin/bash
# WorkMeta AI — PostgreSQL initialization (runs once, on a fresh data volume).
#
# Creates:
#   - extensions: vector (pgvector), citext, pgcrypto
#   - restricted runtime role (wm_app): LOGIN, NO SUPERUSER, NO BYPASSRLS
#
# The owner role (POSTGRES_USER) owns the database and runs migrations.
# The application must always connect as the restricted role.
set -euo pipefail

: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

# Role names are interpolated into SQL, so allow only safe identifiers.
if ! [[ "$APP_DB_USER" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "ERROR: APP_DB_USER must match ^[a-z_][a-z0-9_]*$" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS citext;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;

    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
        CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}';
      END IF;
    END
    \$\$;

    ALTER ROLE ${APP_DB_USER} WITH LOGIN PASSWORD '${APP_DB_PASSWORD}';
    ALTER ROLE ${APP_DB_USER} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${APP_DB_USER};
    GRANT USAGE ON SCHEMA public TO ${APP_DB_USER};
EOSQL

echo "WorkMeta AI database initialized: extensions enabled, '${APP_DB_USER}' role ready."
