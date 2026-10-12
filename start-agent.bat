@echo off
REM Starts the Recovery Agent (local backend + engine) used by the hosted website.
REM It asks for Administrator rights (needed to read disks) and opens in a new window.
cd /d "%~dp0"
where python >nul 2>nul || where py >nul 2>nul || (
  echo Python 3.10 or newer is not installed.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-backend.ps1"
if errorlevel 1 pause
