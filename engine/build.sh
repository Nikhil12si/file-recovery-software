#!/usr/bin/env bash
# Builds the POSIX *test harness* of the engine (scans disk images only; the
# product build is Windows/MSVC via CMake or build_msvc.bat) and runs the
# regression suite against synthetic NTFS / FAT32 / carving images.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
g++ -std=c++20 -O2 -Wall -Wextra -Iinclude src/*.cpp -o build/win_recovery_engine
python3 tests/make_test_images.py build/test_images
python3 tests/run_engine_tests.py build/win_recovery_engine build/test_images
