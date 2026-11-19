<#
.SYNOPSIS
  Safely apply pending Alembic migrations to the PRODUCTION Neon database.

.DESCRIPTION
  Prompts for DATABASE_MIGRATION_URL (owner role) without echoing it.
  Temporarily sets it for this process only; never writes it to disk or .env.
  Verifies the target before migrating and clears the variable on exit.

  Usage (from repository root):
      powershell -ExecutionPolicy Bypass -File tools/scripts/migrate_production.ps1

  You will be prompted to paste the production DATABASE_MIGRATION_URL.
#>

param()

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$apiDir   = Join-Path $repoRoot "apps\api"

# Candidate Python locations, in preference order.
$candidates = @(
    (Join-Path $apiDir ".venv\Scripts\python.exe"),
    (Join-Path $apiDir "venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)

$python = $null
foreach ($c in $candidates) {
    if (Test-Path $c) {
        # Verify it has alembic + sqlalchemy
        $check = & $c -c "import alembic, sqlalchemy; print('ok')" 2>$null
        if ($check -eq 'ok') { $python = $c; break }
    }
}

# Fall back to a system Python that has the deps
if (-not $python) {
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) {
        $check = & $sys.Source -c "import alembic, sqlalchemy; print('ok')" 2>$null
        if ($check -eq 'ok') { $python = $sys.Source }
    }
}

# If nothing suitable exists, create apps/api/.venv and install deps
if (-not $python) {
    Write-Host "No suitable Python environment found. Creating apps/api/.venv..." -ForegroundColor Yellow
    $basePython = (Get-Command python).Source
    & $basePython -m venv (Join-Path $apiDir ".venv")
    $python = Join-Path $apiDir ".venv\Scripts\python.exe"
    & $python -m pip install --upgrade pip
    & $python -m pip install -e $apiDir
    $check = & $python -c "import alembic, sqlalchemy; print('ok')" 2>$null
    if ($check -ne 'ok') {
        Write-Host "Failed to install dependencies." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Using Python: $python" -ForegroundColor DarkGray

# --- Prompt for the production migration URL (hidden input) ---
$secure = Read-Host -Prompt "Paste production DATABASE_MIGRATION_URL" -AsSecureString
$migrationUrl = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
)

if ([string]::IsNullOrWhiteSpace($migrationUrl)) {
    Write-Host "No URL provided. Aborting." -ForegroundColor Red
    exit 1
}

# --- Masked target verification (never print the secret) ---
try {
    $uri = [System.Uri]$migrationUrl
    $hostName = $uri.Host
    $dbName = $uri.AbsolutePath.TrimStart('/')
    Write-Host "Target host : $hostName"
    Write-Host "Target db   : $dbName"
    if ($dbName -eq 'neondb') {
        Write-Host "WARNING: this looks like the local dev database name. Confirm this is production." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Could not parse URL. Aborting." -ForegroundColor Red
    exit 1
}

$confirmation = Read-Host -Prompt "Proceed with migration on host '$hostName' / db '$dbName'? (yes/no)"
if ($confirmation -ne 'yes') {
    Write-Host "Aborted by user." -ForegroundColor Yellow
    exit 0
}

# --- Set env vars for this process only ---
$env:PYTHONPATH = $apiDir
$env:DATABASE_MIGRATION_URL = $migrationUrl

# Clear the secret from the shell variable immediately
$migrationUrl = $null
[GC]::Collect()

try {
    Push-Location $apiDir

    Write-Host "`n=== Current revision ===" -ForegroundColor Cyan
    & $python -m alembic current
    if ($LASTEXITCODE -ne 0) { throw "alembic current failed" }

    Write-Host "`n=== Upgrade to head ===" -ForegroundColor Cyan
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }

    Write-Host "`n=== Verify revision ===" -ForegroundColor Cyan
    & $python -m alembic current
    if ($LASTEXITCODE -ne 0) { throw "alembic current failed" }

    Write-Host "`n=== Verify training_error column ===" -ForegroundColor Cyan
    $check = @"
import asyncio
from app.core.config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def main():
    s = get_settings()
    eng = create_async_engine(s.database_migration_url)
    async with eng.connect() as c:
        r = await c.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name='submodels' AND column_name='training_error'"))
        print('training_error exists:', r.scalar_one_or_none() is not None)
    await eng.dispose()
asyncio.run(main())
"@
    $check | Out-File (Join-Path $env:TEMP "check_col.py") -Encoding utf8
    & $python (Join-Path $env:TEMP "check_col.py")
    if ($LASTEXITCODE -ne 0) { throw "column check failed" }

    Write-Host "`nMigration completed successfully." -ForegroundColor Green
} catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    # Always clear the secret
    Remove-Item Env:\DATABASE_MIGRATION_URL -ErrorAction SilentlyContinue
    $env:PYTHONPATH = $null
    Pop-Location -ErrorAction SilentlyContinue
    Write-Host "Environment cleared." -ForegroundColor DarkGray
}