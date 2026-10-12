<#
.SYNOPSIS
  Builds and starts the Next.js web interface on http://localhost:3000.
  Does NOT need Administrator rights. Use -Mock to run without a backend, -Dev for hot reload.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-web.ps1
#>
param([switch]$Mock, [switch]$Dev)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root 'web')

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js 18+ was not found. Install the LTS version from nodejs.org.' }
if (-not (Test-Path 'node_modules')) {
  npm install
  if ($LASTEXITCODE -ne 0) { throw 'npm install failed - check your internet connection and run this script again.' }
}
# NEXT_PUBLIC_* values are baked in at build time, so set the mode before building.
if ($Mock) { $env:NEXT_PUBLIC_USE_MOCKS = 'true' } else { $env:NEXT_PUBLIC_USE_MOCKS = 'false' }
if ($Dev) {
  npm run dev
} else {
  npm run build
  if ($LASTEXITCODE -ne 0) { throw 'npm run build failed - see the errors above.' }
  npm start
}
