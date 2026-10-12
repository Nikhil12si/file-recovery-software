#!/usr/bin/env bash
# Builds the Windows engine (build/Release/win_recovery_engine.exe) WITHOUT Visual Studio, from Linux or macOS,
# with llvm-mingw (https://github.com/mstorsjo/llvm-mingw/releases): put its bin/ folder on PATH, then run this.
# The result is a static x64 Windows 10/11 console program that needs only DLLs built into Windows.
set -euo pipefail
cd "$(dirname "$0")"
CXX=${CXX:-x86_64-w64-mingw32-clang++}
command -v "$CXX" >/dev/null || { echo "$CXX not found: install llvm-mingw and add its bin/ to PATH" >&2; exit 1; }
mkdir -p build/Release
"$CXX" -std=c++20 -O2 -Wall -Wextra -Wshadow -municode -static \
  -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00 -DUNICODE -D_UNICODE -DNOMINMAX -DWIN32_LEAN_AND_MEAN -D_CRT_SECURE_NO_WARNINGS \
  -Iinclude src/main.cpp src/win_drive_manager.cpp src/win_security_guard.cpp src/win_recovery_engine.cpp \
  -ladvapi32 -o build/Release/win_recovery_engine.exe
echo "Built build/Release/win_recovery_engine.exe"
