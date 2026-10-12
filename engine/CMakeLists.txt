# =============================================================================
# win_recovery_engine — Track A (Member 1) build
#   Windows (primary target):
#     cmake -S . -B build -G "Visual Studio 17 2022" -A x64
#     cmake --build build --config Release
#   Linux/macOS (CI only: scans disk *images*, no raw device access):
#     cmake -S . -B build && cmake --build build && ctest --test-dir build
# =============================================================================
cmake_minimum_required(VERSION 3.20)
project(win_recovery_engine VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(win_recovery_engine
    src/main.cpp
    src/win_drive_manager.cpp
    src/win_security_guard.cpp
    src/win_recovery_engine.cpp)
target_include_directories(win_recovery_engine PRIVATE include)

if(WIN32)
    # Windows 10+ APIs (seek-penalty / NVMe bus type in winioctl.h).
    target_compile_definitions(win_recovery_engine PRIVATE
        _WIN32_WINNT=0x0A00 WINVER=0x0A00 UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)
    target_link_libraries(win_recovery_engine PRIVATE advapi32 kernel32)
    if(MSVC)
        target_compile_options(win_recovery_engine PRIVATE /W4 /permissive- /utf-8 /EHsc)
        # asInvoker on purpose: inventory/elevation checks work unelevated and
        # scan/recover report NOT_ELEVATED (exit 2) instead of failing to start.
        target_link_options(win_recovery_engine PRIVATE "/MANIFESTUAC:level='asInvoker' uiAccess='false'")
        set_property(TARGET win_recovery_engine PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    else()
        target_compile_options(win_recovery_engine PRIVATE -Wall -Wextra -municode)
        target_link_options(win_recovery_engine PRIVATE -municode -static)
    endif()
else()
    message(STATUS "Non-Windows host: building the image-only test harness (no raw device access).")
    target_compile_options(win_recovery_engine PRIVATE -Wall -Wextra)
endif()

# ---- Regression tests (synthetic NTFS / FAT32 / carving images) -------------
find_package(Python3 COMPONENTS Interpreter)
if(Python3_FOUND)
    enable_testing()
    add_test(NAME make_images
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/make_test_images.py ${CMAKE_BINARY_DIR}/test_images)
    add_test(NAME engine_regression
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_engine_tests.py
                     $<TARGET_FILE:win_recovery_engine> ${CMAKE_BINARY_DIR}/test_images)
    set_tests_properties(engine_regression PROPERTIES DEPENDS make_images)
endif()
