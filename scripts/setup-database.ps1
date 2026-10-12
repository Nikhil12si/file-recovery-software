<#
.SYNOPSIS
  Creates the PostgreSQL database and applies database\schema.sql (safe to run again).
.EXAMPLE
  scripts\setup-database.ps1 -User postgres -Database file_recovery
#>
param([string]$User = 'postgres', [string]$Database = 'file_recovery', [string]$PgHost = 'localhost', [int]$Port = 5432)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
  throw 'psql was not found. Install PostgreSQL 13+ and add its bin folder (e.g. C:\Program Files\PostgreSQL\16\bin) to PATH.'
}
if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Invalid database name '$Database' (letters, digits and _ only)." }

$exists = psql -h $PgHost -p $Port -U $User -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$Database'"
if ($LASTEXITCODE -ne 0) { throw "Could not connect to PostgreSQL at ${PgHost}:${Port} as $User." }
if (-not $exists) {
  psql -h $PgHost -p $Port -U $User -d postgres -c "CREATE DATABASE $Database"
  if ($LASTEXITCODE -ne 0) { throw "CREATE DATABASE $Database failed." }
}
psql -h $PgHost -p $Port -U $User -d $Database -v ON_ERROR_STOP=1 -q -f (Join-Path $root 'database\schema.sql')
if ($LASTEXITCODE -ne 0) { throw 'Applying database\schema.sql failed.' }
Write-Host "Database ready. Use: `$env:DATABASE_URL = 'postgresql://${User}:<password>@${PgHost}:${Port}/${Database}'" -ForegroundColor Green
