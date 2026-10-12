<#
.SYNOPSIS
  Builds win_recovery_engine.exe (Release, x64) with CMake + Visual Studio 2022.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build-engine.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Native tools do not raise PowerShell errors on failure, so check every exit code.
function Invoke-Checked([string]$what, [scriptblock]$cmd) {
  & $cmd
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
  throw 'cmake was not found. Install Visual Studio 2022 with the "Desktop development with C++" workload (it includes CMake), or use engine\build_msvc.bat from an "x64 Native Tools Command Prompt for VS 2022".'
}

Set-Location (Join-Path $root 'engine')
Invoke-Checked 'CMake configure' { cmake -S . -B build -G "Visual Studio 17 2022" -A x64 }
Invoke-Checked 'CMake build' { cmake --build build --config Release }
Write-Host "Built engine\build\Release\win_recovery_engine.exe" -ForegroundColor Green
Invoke-Checked 'Engine self-check' { & .\build\Release\win_recovery_engine.exe version }
