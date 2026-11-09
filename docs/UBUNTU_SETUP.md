# Ubuntu Server Setup — WorkMeta AI Staging

Target: a single Ubuntu 24.04 LTS staging host. All commands run as root
unless stated. WorkMeta staging is localhost-only (no public ports) so all
testing can be done over SSH.

## Server specification

| Resource | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 24.04 LTS | Ubuntu 24.04 LTS |
| CPU | 2 vCPU | 2–4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB | 40 GB SSD |
| Ports open (firewall) | SSH (22) | SSH (22) only; staging edge binds 127.0.0.1:18080 |
| Docker | ≥ 24 | current stable |

PostgreSQL runs in Docker (pgvector image), so no host PostgreSQL is needed.

## 1. Base setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw ca-certificates
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw enable
TZ=UTC sudo timedatectl set-timezone UTC
```

## 2. Install Docker Engine + Compose plugin

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # re-login afterwards
docker --version
docker compose version
```

## 3. Create a deploy user and directories

```bash
sudo useradd -m -s /bin/bash workmeta
sudo usermod -aG docker workmeta
sudo mkdir -p /opt/workmeta
sudo chown workmeta:workmeta /opt/workmeta
```

The code itself is cloned as `workmeta` (below) so Docker build contexts are
writable. DB/storage volumes are managed by Compose (`workmeta_stage_*`),
never by this user.

## 4. Clone and prepare configuration

```bash
sudo -u workmeta git clone <repo-url> /opt/workmeta/checkout
cd /opt/workmeta/checkout
sudo -u workmeta cp infra/deploy/env.staging.example infra/.env.staging
# Edit infra/.env.staging: replace EVERY placeholder (see next section).
sudo -u workmeta chmod 600 infra/.env.staging
```

**Consistency rule:** `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` in
`.env.staging` must match the passwords embedded in `DATABASE_URL` and
`DATABASE_MIGRATION_URL` in the same file, because Compose builds URLs from
`${POSTGRES_USER}`/`${POSTGRES_PASSWORD}`/`${APP_DB_USER}`/`${APP_DB_PASSWORD}`
and the API reads the whole `env_file` for everything else. Mismatched pairs
are the #1 staging boot failure.

## 5. Verify the host

```bash
docker compose -f infra/docker-compose.staging.yml --env-file infra/.env.staging config --quiet
```

Exit code 0 means the file parses and every required `${VAR:?}` is present.

## Firewall / exposure checklist

- `ufw status` shows only SSH.
- `ss -tlnp | grep 5432` is empty (database never published).
- Staging edge binds `127.0.0.1:18080` only (`docker ps`).

## Next steps

Deployment commands: `docs/STAGING_DEPLOYMENT.md`.
Payment sandbox credentials: `docs/STAGING_DEPLOYMENT.md#payment-sandbox-credentials`.
