@echo off
REM ===========================================================================
REM  Builds win_recovery_engine.exe with MSVC (run from "x64 Native Tools
REM  Command Prompt for VS 2022"). Output: build\win_recovery_engine.exe
REM ===========================================================================
setlocal
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /std:c++20 /O2 /W4 /EHsc /permissive- /utf-8 /MT ^
   /D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 /DUNICODE /D_UNICODE /DNOMINMAX /DWIN32_LEAN_AND_MEAN /D_CRT_SECURE_NO_WARNINGS ^
   /Iinclude src\main.cpp src\win_drive_manager.cpp src\win_security_guard.cpp src\win_recovery_engine.cpp ^
   /Fobuild\ /Febuild\win_recovery_engine.exe ^
   /link advapi32.lib kernel32.lib /MANIFEST:EMBED /MANIFESTUAC:"level='asInvoker' uiAccess='false'"
if errorlevel 1 (
  echo BUILD FAILED
  exit /b 1
)
echo Built build\win_recovery_engine.exe
endlocal
