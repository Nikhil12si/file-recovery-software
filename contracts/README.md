# API Contracts

These contracts are the only coupling between the three tracks. Each track codes
against the schema and the mock files on Day 1; nobody waits for anybody.

```
 Track A (C++ engine)  ──CLI + NDJSON + candidates.json──▶  Track B (FastAPI)  ──REST/JSON──▶  Track C (Next.js)
      ▲                                                        │
      └──────────────── recover-manifest.json ◀────────────────┘
```

| Boundary | Schema | Mocks (valid examples) |
|---|---|---|
| Engine → Python: scan results | `schemas/candidates.schema.json` | `mocks/candidates.quick.json`, `mocks/candidates.deep.json` |
| Engine → Python: live stdout stream | `schemas/engine-events.schema.json` | `mocks/engine-events.*.ndjson` |
| Engine → Python: inventory / guard | `schemas/drives.schema.json` | `mocks/drives.json` |
| Python → Engine: restore request | `schemas/recover-manifest.schema.json` | `mocks/recover-manifest.json` |
| Python → Web: REST | `schemas/rest-api.schema.json` (`$defs`) | `mocks/rest/*.json` |

Validate anything against them:

```bash
pip install jsonschema referencing
python contracts/validate_contracts.py candidates path/to/candidates.json
python contracts/validate_contracts.py events     engine_stdout.ndjson
python contracts/validate_contracts.py rest scan_session response.json
```

## Versioning rules

* `schema_version` is `MAJOR.MINOR` (currently `1.0`). Consumers reject a different MAJOR.
* Adding optional fields or new event types is a MINOR change. Consumers ignore unknown keys and unknown events.
* Removing or renaming a field, or changing its meaning, is a MAJOR change and needs all three members to agree.
* Any contract change ships together with updated mocks, and `validate_contracts.py` must pass on them.

---

## 1. Engine CLI contract (`win_recovery_engine.exe`)

| Command | stdout | Exit codes |
|---|---|---|
| `version` | `{"name","version","schema_version"}` | 0 |
| `list-drives` | one JSON document (`drives.schema.json`) | 0 |
| `check-elevation` | `drives.schema.json#/$defs/elevation` | 0 |
| `validate-dest --dest P --source S [...] [--required-bytes N]` | `drives.schema.json#/$defs/destination_check` | 0 safe, 3 unsafe |
| `scan …` | NDJSON events; `--out` file written at the end | 0, 1, 2, 4, 5, 10 |
| `recover --manifest F [--cancel-file C]` | NDJSON events | 0, 1, 2, 3, 5, 10 |
| `signatures` | list of carving signatures | 0 |

**Exit codes:** `0` ok · `1` usage · `2` not elevated · `3` unsafe destination · `4` source error (missing, SSD without `--allow-non-hdd`, nothing scannable) · `5` cancelled (partial `candidates.json` is still written) · `10` internal error.

**Scan arguments**

```
scan --mode quick|deep --scope all|drive|folder
     [--source D: | --source \\.\PhysicalDrive1 | --source \\.\PhysicalDrive1@p2 | --source image:C:\disk.dd]...
     [--folder "D:\Users\me\Documents"]  --out C:\work\candidates.json
     [--sample-dir E:\staging] [--sample-bytes 2097152] [--sample-budget-mb 2048]
     [--types jpg,pdf,docx] [--max-candidates 500000] [--cancel-file C:\work\cancel.flag]
     [--byte-aligned] [--unallocated-only] [--allow-non-hdd] [--no-verify] [--include-empty]
     [--stream-candidates] [--scan-id <uuid>]
```

* **Scope `all`:** in quick mode, every mounted NTFS or FAT32 volume plus unmounted NTFS or FAT32 partitions on HDDs. In deep mode, every physical HDD.
* **Scope `drive`:** the given volume, physical disk, partition or image.
* **Scope `folder`:**
  * Quick: only deleted entries whose parent chain reaches the folder's MFT record (this also works when the folder itself was deleted). FAT32 filters by path prefix.
  * Deep: carving is restricted to unallocated clusters of that folder's partition.
* **Cancellation:** create the `--cancel-file`. The engine polls it every second, stops, writes partial results and exits with `5`. Ctrl+C does the same.
* **Safety:** the source is opened with `GENERIC_READ` only. When the sample directory sits on a scanned volume, sampling is disabled (with a warning) and the scan continues.

**Events** (one JSON object per line, `engine-events.schema.json`):

| event | when | key fields |
|---|---|---|
| `scan_started` | after sources are resolved | `sources[]` |
| `progress` | at most every 0.5 s | `phase` (`mft`, `verify`, `fat_directories`, `carve`, `restore`), `unit`, `bytes_done`, `bytes_total`, `percent`, `overall_percent`, `rate_mb_s`, `eta_seconds`, `candidates_found`, `read_errors` |
| `log` | informational | `level`, `message`, `source_id` |
| `error` | fatal or per-step failure | `code` (`NOT_ELEVATED`, `SOURCE_NOT_FOUND`, `NON_HDD_SOURCE`, `UNSAFE_DESTINATION`, …), `message`, `fatal` |
| `candidate` | only with `--stream-candidates` | full candidate object |
| `source_done` | after each source | `status`, `candidates`, `read_errors` |
| `destination_check` | at the start of `recover` | guard result |
| `restore_item` | per restored file | `id`, `status` (`ok`, `partial`, `failed`), `output_path`, `bytes_written` |
| `done` | last line | `command`, `status`, counts |

### candidates.json essentials

* `extents[]` are byte ranges inside `source_device`, so any consumer can rebuild a file with nothing but `open(source_device).seek(offset).read(length)`. Sparse runs are zero-filled.
* `resident_data_b64` holds small NTFS files that live inside the MFT record.
* `integrity.header_check` compares the first bytes with the signature expected for the extension. A `mismatch` usually means the first clusters were overwritten.
* `integrity.allocated_ratio` means different things by method:
  * `mft` and `fat`: the share of the file's clusters that now belong to other files (overwrite risk).
  * `carve`: the share of the hit that lies in allocated space (it may be a live file).
* `absolute_lba` is relative to the physical disk. Track B uses it for sector-proximity clustering.
* `sample_path` points to the first `sample_bytes` of content, written to a guarded staging directory. Track B never needs raw disk access.

---

## 2. REST API (FastAPI, Track B → Next.js, Track C)

**Conventions**

* Base URL: `http://127.0.0.1:8000`. The Next.js dev server proxies `/api/*` to it.
* Authentication: `Authorization: Bearer <JWT>`. Image and PDF downloads also accept `?access_token=`.
* Errors use the HTTP status with `{"detail": string | object, "code"?: string}`. Validation errors return 422 with a readable `detail`.
* Paginated responses use `{"items", "page", "page_size", "total", "total_pages"}`.
* Generated OpenAPI is served at `/api/openapi.json` and interactive docs at `/api/docs`.

| Method | Path | Body / query | Response (`rest-api.schema.json#/$defs/…`) |
|---|---|---|---|
| POST | `/api/auth/register` | `register_request` | 201 `auth_response` (the first user becomes `admin`) |
| POST | `/api/auth/login` | `login_request` | `auth_response`; 401 bad credentials, 423 locked, 429 rate-limited |
| POST | `/api/auth/logout` | – | 204 (revokes the token's `jti`) |
| GET | `/api/auth/me` | – | `user` |
| GET | `/api/health` | – (public) | `health` |
| GET | `/api/system/status` | – | `system_status` (elevation, mock mode, warnings) |
| GET | `/api/drives?refresh=true` | – | `drives.schema.json` |
| POST | `/api/destinations/validate` | `validate_destination_request` | `drives.schema.json#/$defs/destination_check` |
| POST | `/api/scans` | `scan_create_request` | 202 `scan_session` |
| POST | `/api/scans/import` | multipart `file` = candidates.json | 202 `scan_session` |
| GET | `/api/scans?page&page_size&q` | – | `page` of `scan_session` (history) |
| GET | `/api/scans/{id}` | – | `scan_session` |
| GET | `/api/scans/{id}/progress` | – | `{status, progress: scan_progress, totals, error}` (poll every 1 s) |
| POST | `/api/scans/{id}/cancel` | – | `scan_session`; 409 if not running |
| DELETE | `/api/scans/{id}` | – | 204 |
| GET | `/api/scans/{id}/files` | `page, page_size≤500, extension=jpg,pdf, confidence=high,medium, q, cluster_id, method, sort=name\|size\|confidence\|lba\|extension\|modified, order=asc\|desc` | `page` of `recovered_file` |
| GET | `/api/scans/{id}/files/{fid}` | – | `recovered_file` plus `integrity`, `extents`, `notes`, `mft`, `sha256` |
| GET | `/api/scans/{id}/files/{fid}/preview` | – | `file_preview` |
| GET | `/api/scans/{id}/files/{fid}/thumbnail` | – | `image/jpeg` |
| GET | `/api/scans/{id}/clusters` | – | `deletion_cluster[]` |
| GET | `/api/scans/{id}/stats` | – | `scan_stats` |
| POST | `/api/scans/{id}/restore` | `restore_request` | 202 `restore_job`; 400 with `detail.destination_check` if unsafe |
| GET | `/api/restores/{job}` | – | `restore_job` (poll) |
| POST | `/api/restores/{job}/cancel` | – | `restore_job` |
| GET | `/api/scans/{id}/restores` | – | `restore_job[]` |
| POST | `/api/scans/{id}/reports` | `{title?}` | 201 `report` |
| GET | `/api/reports?scan_id=` | – | `report[]` |
| GET | `/api/reports/{rid}/download` | – | `application/pdf` |
| GET | `/api/search?q=` | – | `search_hit[]` (full-text over keywords, titles and extracted text) |

**Scan lifecycle:** `queued → running → analyzing → completed | partial | cancelled | failed`

**Confidence bands:** `high ≥ 75`, `medium 45–74`, `low < 45`. `confidence_reasons[]` explains each score in plain language for the UI.

---

## 3. Parallel development recipes

| Member | Works against | How |
|---|---|---|
| 1 – C++ | `candidates.schema.json`, `engine-events.schema.json` | `python engine/tests/run_engine_tests.py` builds synthetic NTFS, FAT32 and carving images and validates every output |
| 2 – Python | `mocks/candidates.*.json` and the mock engine | `ENGINE_MODE=mock` (default when no engine binary exists) runs the whole service on macOS or Linux |
| 3 – Web | `mocks/rest/*.json` | `NEXT_PUBLIC_USE_MOCKS=true npm run dev` serves the UI from `web/lib/mockApi.js` with no backend at all |
