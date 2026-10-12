<#
.SYNOPSIS
  Starts the FastAPI recovery service with Administrator rights (required for
  raw \\.\X: and \\.\PhysicalDriveN access). Re-launches itself elevated via UAC.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
#>
param(
  [string]$DatabaseUrl = $env:DATABASE_URL,
  [string]$DataDir = $env:DATA_DIR,
  [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host 'Not elevated - requesting Administrator rights (UAC)...' -ForegroundColor Yellow
  $argList = "-NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
  if ($DatabaseUrl) { $argList += " -DatabaseUrl `"$DatabaseUrl`"" }
  if ($DataDir) { $argList += " -DataDir `"$DataDir`"" }
  Start-Process powershell -Verb RunAs -ArgumentList $argList
  exit
}

Set-Location (Join-Path $root 'backend')
$venvPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
  if (-not $python) { throw 'Python 3.10+ was not found. Install it from python.org and tick "Add python.exe to PATH".' }
  Write-Host 'Creating Python virtual environment...' -ForegroundColor Cyan
  & $python.Source -m venv .venv
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { throw 'Creating the virtual environment failed. Install Python 3.10+ from python.org (tick "Add python.exe to PATH"; the Microsoft Store shortcut is not enough) and try again.' }
}

# (Re)install dependencies when requirements.txt changed or a previous install did not finish.
$marker = '.venv\.requirements.sha256'
$reqHash = (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash
if (-not (Test-Path $marker) -or (Get-Content $marker -Raw).Trim() -ne $reqHash) {
  Write-Host 'Installing Python dependencies...' -ForegroundColor Cyan
  & $venvPython -m pip install --upgrade pip
  & $venvPython -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) { throw 'pip install -r requirements.txt failed - check your internet connection and run this script again.' }
  Set-Content -Path $marker -Value $reqHash
}

# The engine can come from CMake (build\Release\) or from build_msvc.bat (build\).
$engine = @('engine\build\Release\win_recovery_engine.exe', 'engine\build\win_recovery_engine.exe') |
  ForEach-Object { Join-Path $root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($engine) { $env:ENGINE_PATH = $engine; Write-Host "Engine: $engine" -ForegroundColor Green }
else { Write-Host 'Engine not found (run scripts\build-engine.ps1) - running in SIMULATION mode.' -ForegroundColor Yellow }
if ($DatabaseUrl) { $env:DATABASE_URL = $DatabaseUrl }
if ($DataDir) { $env:DATA_DIR = $DataDir }
$env:PORT = "$Port"
Write-Host "Recovery service (elevated) on http://127.0.0.1:$Port  - docs: http://127.0.0.1:$Port/api/docs" -ForegroundColor Green
& $venvPython recovery_service.py
