# Windows HDD File Recovery Suite

A local, web-based recovery tool for deleted files on **Windows 10/11 x64** hard disk drives formatted with **NTFS or FAT32**. It has two scan engines:

* **Quick Scan** parses the NTFS `$MFT` (or FAT32 directories) and recovers deleted files with their original names, folders and timestamps.
* **Deep Scan** carves signatures sector by sector and works after formatting or file-system corruption.

The system is built as seven cohesive, loosely coupled modules split across three parallel tracks. The tracks connect only through versioned JSON contracts.

```
┌──────────────────────────── Track A · Member 1 · C++20 / Win32 ─────────────────────────────┐
│ win_recovery_engine.exe                                                                     │
│  (1) Storage Source Manager      win_drive_manager.cpp   GetLogicalDriveStringsW,           │
│                                                          QueryDosDeviceW, IOCTL_DISK_*      │
│  (2) Security & Destination Guard win_security_guard.cpp TokenElevation, volume GUID / disk │
│  (3) Dual Scan Recovery Core     win_recovery_engine.cpp CreateFileW(NO_BUFFERING),         │
│                                                          VirtualAlloc, $MFT, FAT32, carving │
└────────────────────────────────────────────┬────────────────────────────────────────────────┘
          CLI args ▲ │ NDJSON stdout events + candidates.json   (contracts/schemas/*.json)
   recover-manifest │ ▼
┌─────────────────────────── Track B · Member 2 · Python / FastAPI ───────────────────────────┐
│ recovery_service.py                                                                         │
│  (6) Analysis, Verification & Smart Recovery  analysis.py   confidence scoring, PyMuPDF/    │
│                                                             YAKE/TF-IDF auto-naming,        │
│                                                             thumbnails, LBA clustering      │
│  (7) Reporting & Session History              report_gen.py ReportLab PDF; db.py PostgreSQL │
└────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                             │ REST / JSON (contracts/schemas/rest-api.schema.json)
┌─────────────────────────── Track C · Member 3 · Next.js / PostgreSQL ───────────────────────┐
│  (1) Authentication & User Management   database/schema.sql, backend/app/auth.py (JWT)      │
│  (3) Scenario Config & User Guide UI    RecoveryDashboard.jsx, UserManual.jsx (/manual)     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Repository layout

| Path | Owner | Contents |
|---|---|---|
| `contracts/` | all three | JSON Schemas, a validator, valid mock files for every boundary, and the API reference (`contracts/README.md`) |
| `engine/` | Member 1 | C++20 engine: `include/`, `src/`, CMake and MSVC build files, synthetic-image regression tests |
| `backend/` | Member 2 (and Member 3 for `auth.py`) | FastAPI service, analysis, reporting, persistence, mock engine, tests |
| `database/schema.sql` | Member 3 | PostgreSQL schema: users, auth_sessions, recovery_sessions, recovered_files, deletion_clusters, semantic_index, restore_jobs, reports, audit_log |
| `web/` | Member 3 | Next.js 14 (React 18, Tailwind CSS): wizard, live dashboard, history, embedded manual |
| `scripts/` | – | PowerShell helpers: build the engine, set up the DB, start the backend elevated, start the web app |

## Quick start (Windows 10/11)

```powershell
# 0. Prerequisites: Visual Studio 2022 (C++ workload) + CMake, Python 3.10+, Node 18+, PostgreSQL 13+ (optional)
scripts\build-engine.ps1                   # -> engine\build\Release\win_recovery_engine.exe
scripts\setup-database.ps1                 # creates file_recovery and applies schema.sql (optional; SQLite fallback otherwise)
scripts\start-backend.ps1 -DatabaseUrl "postgresql://postgres:<pw>@localhost:5432/file_recovery"
                                           # re-launches itself elevated (UAC); serves http://127.0.0.1:8000
scripts\start-web.ps1                      # http://localhost:3000  (no admin rights needed)
```

1. Open <http://localhost:3000> and create an account. The first account becomes administrator.
2. Read **User Manual → Safety rules**.
3. Use **Recover** and follow the four-step wizard.

## Day-1 parallel development (no Windows, no admin, no other team needed)

| Member | Command | What runs |
|---|---|---|
| 1 – C++ | `engine/build.sh` (Linux/macOS/WSL) or `ctest` after the CMake build | Builds the engine, generates synthetic NTFS, FAT32, MBR and carving images, and checks every result (139 assertions: names, paths, fragments, resident data, `$ATTRIBUTE_LIST`, orphans, overwrite ratio, exact carve sizes, restore SHA-256, destination guard) |
| 2 – Python | `ENGINE_MODE=mock python backend/recovery_service.py` | Full API backed by a contract-faithful mock engine that writes real simulated disk images, so restores return real bytes |
| 3 – Web | `cd web && npm install && npm run dev:mock` | UI fully on `lib/mockApi.js` (simulated scan, results, restore, report) |

Contract checks for everyone:

```bash
pip install jsonschema referencing
python contracts/validate_contracts.py candidates contracts/mocks/candidates.quick.json
```

## Hosting the website on Firebase

The website can be published on Firebase Hosting while every visitor scans and restores on their **own** PC.
The hosted site is only the user interface; it talks to a local **Recovery Agent** (this repository's backend +
engine) on the visitor's computer at `http://127.0.0.1:8000`. Disks are never read by, or uploaded to, a server.

```powershell
# once: create a project at https://console.firebase.google.com (Hosting > Get started), note its project ID
scripts\build-engine.ps1                                          # optional: real engine instead of simulation
scripts\deploy-firebase.ps1 -ProjectId <your-project-id>          # installs the CLI, signs in, packages, builds, deploys
```

The script writes `backend\web_origins.txt` (only these sites may use the agent), packages
`web\public\downloads\recovery-agent.zip`, builds the static site into `web\out` and runs `firebase deploy`.
Visitors open the site, follow **Agent setup** (install Python, download the zip, double-click `start-agent.bat`)
and allow the browser's prompt to access apps on their device.

## How the pieces work together

1. **Wizard** (`RecoveryDashboard.jsx`) sends `POST /api/scans` with the scenario, scope (`all`, `drive` or `folder`), mode (`quick` or `deep`) and file types.
2. **JobManager** (`backend/app/jobs.py`) starts `win_recovery_engine scan … --out candidates.json --sample-dir … --cancel-file …`. It reads its NDJSON progress, so the UI's `GET /progress` poll shows phase, rate, ETA and read errors.
3. **Engine**: for a quick scan it opens `\\.\D:` read-only and unbuffered, reads the VBR, follows `$MFT`'s own data runs, applies update-sequence fixups, and collects deleted records (in-use flag `0x01` cleared). From each it takes `$STANDARD_INFORMATION`, `$FILE_NAME`, `$DATA` runs and `$ATTRIBUTE_LIST`. It then rebuilds paths (detecting reused parents) and computes the overwrite ratio from `$Bitmap`. For a deep scan it streams the device through 4 MiB `VirtualAlloc` buffers, matches signatures at sector boundaries, and walks each format's structure (JPEG markers, PNG chunks, ZIP EOCD, OLE FAT, ISO-BMFF boxes and others) to get exact sizes.
4. **Analysis** (`analysis.py`):
   * verifies each preview sample with real decoders
   * extracts text and keywords (YAKE, or TF-IDF over the session)
   * auto-names files, for example `Quarterly_Financial_Report_2026.pdf`
   * renders thumbnails
   * scores confidence with readable reasons
   * clusters files by absolute LBA into deletion events
5. **Restore**: `POST /restore` validates the destination, then sends a manifest to `win_recovery_engine recover`. The engine guards the destination again and writes files without overwriting anything. Python records a SHA-256 for each file and writes `_recovery_log_*.json`.
6. **Reports**: ReportLab produces a PDF with statistics, charts, health warnings, clusters and the top files. Sessions, files and the full-text `semantic_index` are stored in PostgreSQL.

## Safety design

* Sources are opened with `GENERIC_READ` and `FILE_SHARE_READ|FILE_SHARE_WRITE` only. Nothing is ever written to a source.
* The destination guard checks every target three times: in the UI (`/destinations/validate`), in the API before the job starts, and inside the engine before the first write. It compares volume GUIDs, physical disk numbers and partition offsets, and checks free space.
* Preview samples are written only to a guarded staging directory. Sampling switches off automatically if that directory is on a scanned volume.
* The service binds to `127.0.0.1`.
* Authentication uses bcrypt-hashed passwords (cost 12) and JWTs with server-side revocation (`auth_sessions.jti`). Accounts lock after repeated failures and login is rate-limited. Roles are `admin`, `operator` and `viewer`.
* HDD-only policy: SSDs are detected through the seek-penalty property and refused unless explicitly overridden.

## Testing

```bash
engine/build.sh                                                      # C++ engine regression (synthetic images)
pip install -r backend/requirements.txt && pytest -q backend/tests   # pipeline + HTTP contract tests
ENGINE_PATH=engine/build/win_recovery_engine TEST_IMAGE=engine/build/test_images/ntfs.img pytest -q backend/tests
```

## Known limitations

* **Not yet run against physical disks on Windows hardware.** The Windows engine (`engine\build\Release\win_recovery_engine.exe`, included prebuilt) is cross-compiled with llvm-mingw (`engine/build_windows_cross.sh`, zero warnings with `-Wall -Wextra -Wshadow`) and passes all 139 regression checks when run as a Windows program (under Wine) against the synthetic NTFS, FAT32 and carving images. Raw `\\.\X:` device access, IOCTLs and UAC behaviour still need a first test on a real Windows PC: try a spare USB drive first. `scripts\build-engine.ps1` (Visual Studio) rebuilds it natively.
* **Compressed and encrypted NTFS files:** LZNT1-compressed files are reported and recovered as raw clusters (rated Low). EFS-encrypted files cannot be decrypted.
* **File systems:** exFAT and other file systems are reached only through Deep Scan (carving).
* **Carving assumes contiguous files:** fragmented files carved without metadata may be incomplete. This is reflected in their confidence rating.
