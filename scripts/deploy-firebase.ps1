<#
.SYNOPSIS
  Publishes the website to Firebase Hosting. Visitors use the website, and the scanning and restoring
  runs in the Recovery Agent (backend + engine) on their own PC, downloadable from the site's
  "Agent setup" page.

  Steps: install the Firebase CLI if missing -> sign in to Firebase -> allow the site in the agent
  (backend\web_origins.txt) -> package the agent as web\public\downloads\recovery-agent.zip ->
  build the static website (web\out) -> firebase deploy.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\deploy-firebase.ps1 -ProjectId my-recovery-suite
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\deploy-firebase.ps1 -ProjectId my-recovery-suite -PackageOnly
  (only rebuilds the agent zip and the website, without deploying)
#>
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [switch]$PackageOnly
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked([string]$what, [scriptblock]$cmd) {
  & $cmd
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

if ($ProjectId -notmatch '^[a-z0-9][a-z0-9-]{4,28}[a-z0-9]$') {
  throw "'$ProjectId' does not look like a Firebase project ID (lowercase letters, digits and hyphens, e.g. hdd-recovery-1234). Copy it from Firebase console > Project settings."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js 18+ was not found. Install the LTS version from nodejs.org.' }

# ---------------------------------------------------------------- 1. Firebase CLI + sign-in
$firebase = $null
if (-not $PackageOnly) {
  if (-not (Get-Command firebase -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing the Firebase CLI (npm install -g firebase-tools)...' -ForegroundColor Cyan
    Invoke-Checked 'Installing firebase-tools' { npm install -g firebase-tools }
  }
  $cmd = Get-Command firebase -ErrorAction SilentlyContinue
  $firebase = if ($cmd) { $cmd.Source } else { Join-Path $env:APPDATA 'npm\firebase.cmd' }
  if (-not (Test-Path $firebase) -and -not $cmd) { throw 'The Firebase CLI was installed but cannot be found. Close PowerShell, open a new one and run this script again.' }
  Write-Host 'Signing in to Firebase (a browser window opens the first time)...' -ForegroundColor Cyan
  Invoke-Checked 'firebase login' { & $firebase login }
}

# ---------------------------------------------------------------- 2. Allow the hosted site in the agent
$siteUrls = @("https://$ProjectId.web.app", "https://$ProjectId.firebaseapp.com")
$originsFile = Join-Path $root 'backend\web_origins.txt'
@('# Websites allowed to use this Recovery Agent (written by scripts\deploy-firebase.ps1)') + $siteUrls |
  Set-Content -Path $originsFile -Encoding UTF8
Write-Host "Agent will accept requests from: $($siteUrls -join ', ')" -ForegroundColor Green

# ---------------------------------------------------------------- 3. Package the Recovery Agent download
$stage = Join-Path ([IO.Path]::GetTempPath()) ("recovery-agent-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$agent = Join-Path $stage 'recovery-agent'
New-Item -ItemType Directory -Force -Path $agent | Out-Null

# backend\ without the local virtual environment, data, caches, tests and private settings (.env)
$skipDirs = @('.venv', 'data', '__pycache__', 'tests', '.pytest_cache')
$backendSrc = Join-Path $root 'backend'
Get-ChildItem -Path $backendSrc -Recurse -File -Force | Where-Object {
  $rel = $_.FullName.Substring($backendSrc.Length + 1)
  $parts = $rel -split '[\\/]'
  -not ($parts | Where-Object { $skipDirs -contains $_ }) -and $_.Name -ne '.env' -and $_.Extension -ne '.pyc'
} | ForEach-Object {
  $dest = Join-Path (Join-Path $agent 'backend') $_.FullName.Substring($backendSrc.Length + 1)
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
  Copy-Item -LiteralPath $_.FullName -Destination $dest
}
foreach ($rel in @('database\schema.sql', 'scripts\start-backend.ps1', 'start-agent.bat')) {
  $dest = Join-Path $agent $rel
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
  Copy-Item -LiteralPath (Join-Path $root $rel) -Destination $dest
}
$engineFound = $false
foreach ($rel in @('engine\build\Release\win_recovery_engine.exe', 'engine\build\win_recovery_engine.exe')) {
  $src = Join-Path $root $rel
  if (Test-Path $src) {
    $dest = Join-Path $agent $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest
    $engineFound = $true
  }
}
@"
HDD File Recovery Suite - Recovery Agent
=========================================
The website https://$ProjectId.web.app is the control panel; this agent does the actual
scanning and restoring on THIS computer.

1. Install Python 3.10 or newer from https://www.python.org/downloads/
   (tick "Add python.exe to PATH" on the first installer screen).
2. Double-click start-agent.bat and click Yes when Windows asks for Administrator rights.
   The first start installs its components (3-5 minutes, needs internet).
3. When the window shows "Uvicorn running on http://127.0.0.1:8000", open the website.
   If the browser asks to allow access to apps or devices on this computer, click Allow.

Keep the agent window open while you use the website. Always restore files to a different
drive than the one you are scanning.
"@ | Set-Content -Path (Join-Path $agent 'README.txt') -Encoding UTF8

$downloads = Join-Path $root 'web\public\downloads'
New-Item -ItemType Directory -Force -Path $downloads | Out-Null
$zip = Join-Path $downloads 'recovery-agent.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $agent '*') -DestinationPath $zip
Remove-Item $stage -Recurse -Force
$zipKb = [math]::Round((Get-Item $zip).Length / 1KB)
Write-Host "Packaged the Recovery Agent: web\public\downloads\recovery-agent.zip ($zipKb KB)" -ForegroundColor Green
if (-not $engineFound) {
  Write-Host 'Note: the C++ engine is not built, so the downloadable agent runs in SIMULATION mode (demo disks).' -ForegroundColor Yellow
  Write-Host '      For real recovery run scripts\build-engine.ps1 first, then this script again.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 4. Build the static website
Push-Location (Join-Path $root 'web')
try {
  if (-not (Test-Path 'node_modules')) { Invoke-Checked 'npm install' { npm install } }
  if (Test-Path 'out') { Remove-Item 'out' -Recurse -Force }
  $env:NEXT_EXPORT = 'true'                            # static export for Firebase Hosting (web\out)
  $env:NEXT_PUBLIC_USE_MOCKS = 'false'
  $env:NEXT_PUBLIC_API_BASE = 'http://127.0.0.1:8000'  # the visitor's own Recovery Agent
  Invoke-Checked 'Website build' { npm run build }
  if (-not (Test-Path 'out\dashboard.html')) { throw 'The build did not produce web\out\dashboard.html.' }
} finally {
  Remove-Item Env:NEXT_EXPORT, Env:NEXT_PUBLIC_USE_MOCKS, Env:NEXT_PUBLIC_API_BASE -ErrorAction SilentlyContinue
  Pop-Location
}
Write-Host 'Website built: web\out' -ForegroundColor Green

# ---------------------------------------------------------------- 5. Deploy
if ($PackageOnly) {
  Write-Host 'PackageOnly: skipped the Firebase deploy.' -ForegroundColor Yellow
  exit 0
}
Push-Location $root
try {
  Invoke-Checked 'firebase deploy' { & $firebase deploy --only hosting --project $ProjectId }
} finally {
  Pop-Location
}
Write-Host ''
Write-Host "Live at: https://$ProjectId.web.app" -ForegroundColor Green
Write-Host 'On this PC, restart the agent (close its window, run start-agent.bat) so it accepts the new site.' -ForegroundColor Yellow
