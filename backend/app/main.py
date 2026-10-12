"""
main.py - FastAPI application (Track B: recovery_service).

Run (elevated terminal on Windows - raw disk access needs Administrator):
    uvicorn recovery_service:app --host 127.0.0.1 --port 8000

Interactive API docs: http://127.0.0.1:8000/api/docs   (OpenAPI: /api/openapi.json)
Route table & contracts: contracts/README.md
"""
from __future__ import annotations

import json
import logging
import platform
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from . import schemas as S
from .analysis import AnalysisEngine, CandidatesFormatError, validate_candidates
from .auth import get_current_user, require_role
from .auth import router as auth_router
from .config import settings
from .db import Database, Repository
from .engine_bridge import EngineError, create_engine
from .jobs import JobManager, initial_progress
from .report_gen import ReportGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("recovery_service")


# ============================================================================= lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings.database_url, settings.schema_sql_path)
    db.init_schema()
    repo = Repository(db)
    engine = create_engine(settings)
    analysis = AnalysisEngine(settings.data_dir / "thumbnails", workers=settings.analysis_workers)
    jobs = JobManager(settings, repo, engine, analysis)
    jobs.recover_interrupted()
    app.state.settings = settings
    app.state.db = db
    app.state.repo = repo
    app.state.engine = engine
    app.state.jobs = jobs
    app.state.reports = ReportGenerator()
    app.state.cache = {}
    log.info("database=%s engine=%s data_dir=%s", db.dialect, "mock" if engine.is_mock else settings.engine_path, settings.data_dir)
    yield
    jobs.shutdown()
    db.close()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"],
                   allow_headers=["*"])


class PrivateNetworkAccess:
    """The hosted website (e.g. https://<project>.web.app) talks to this agent on 127.0.0.1. Chromium asks first
    with a preflight carrying `Access-Control-Request-Private-Network: true`. For origins listed in CORS_ORIGINS /
    web_origins.txt only, answer `Access-Control-Allow-Private-Network: true`; every other origin is still refused
    by the CORS middleware. Implemented as plain ASGI so it works with every Starlette version."""

    _REQ = b"access-control-request-private-network"

    def __init__(self, app, origins: list[str]) -> None:  # noqa: ANN001
        self.app = app
        self.origins = {o.rstrip("/") for o in origins}

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers") or []
        origin = next((v.decode("latin-1") for k, v in headers if k == b"origin"), "").rstrip("/")
        if origin not in self.origins or not any(k == self._REQ for k, _ in headers):
            return await self.app(scope, receive, send)
        scope = dict(scope, headers=[(k, v) for k, v in headers if k != self._REQ])

        async def send_with_pna(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                message = dict(message, headers=[*message.get("headers", []), (b"access-control-allow-private-network", b"true")])
            await send(message)

        await self.app(scope, receive, send_with_pna)


app.add_middleware(PrivateNetworkAccess, origins=settings.cors_origins)  # outermost: runs before CORS
app.include_router(auth_router)


# ============================================================================= error mapping
@app.exception_handler(EngineError)
async def engine_error_handler(_: Request, exc: EngineError) -> JSONResponse:
    http = {"ENGINE_MISSING": 503, "NOT_ELEVATED": 403, "SOURCE_NOT_FOUND": 404}.get(exc.code, 502)
    return JSONResponse(status_code=http, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", []) if p != "body")
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs).replace("Value error, ", ""), "code": "VALIDATION_ERROR",
                                                  "errors": json.loads(json.dumps(exc.errors(), default=str))})


def _state(request: Request) -> Any:
    return request.app.state


def _session_or_404(request: Request, session_id: str, user: dict) -> dict:
    row = _state(request).repo.get_scan_session(session_id, user["id"])
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scan session not found")
    return row


def _cached(request: Request, key: str, ttl: float, fn):
    cache = _state(request).cache
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = fn()
    cache[key] = (time.monotonic(), value)
    return value


# ============================================================================= system
@app.get("/api/health", tags=["system"])
def health(request: Request) -> dict:
    st = _state(request)
    db_ok = st.db.ping()
    engine_ok = st.engine.available()
    return {"status": "ok" if db_ok and engine_ok else "degraded", "version": settings.version,
            "engine": {"available": engine_ok, "mock": st.engine.is_mock, "path": None if st.engine.is_mock else settings.engine_path,
                       "version": st.engine.version()},
            "database": {"ok": db_ok, "dialect": st.db.dialect}}


@app.get("/api/system/status", tags=["system"])
def system_status(request: Request, user: dict = Depends(get_current_user)) -> dict:
    st = _state(request)
    warnings: list[str] = []
    try:
        elev = _cached(request, "elevation", 30, st.engine.check_elevation)
    except EngineError as exc:
        elev = {"elevated": False, "elevation_type": "unknown"}
        warnings.append(exc.message)
    if st.engine.is_mock:
        warnings.append("Simulation mode: the recovery engine binary was not found (or ENGINE_MODE=mock). Drives and results are "
                        "simulated so the interface can be explored safely.")
    elif not settings.is_windows:
        warnings.append("Raw disk access is only available on Windows; only disk images can be scanned on this host.")
    if not elev.get("elevated"):
        warnings.append("The backend is not running as Administrator, so raw drive handles cannot be opened. Close it and start it "
                        "again from an elevated terminal (right-click > Run as administrator).")
    return {"platform": f"{platform.system()} {platform.release()}", "elevated": bool(elev.get("elevated")),
            "elevation_type": elev.get("elevation_type", "unknown"), "engine_available": st.engine.available(),
            "mock_mode": st.engine.is_mock, "warnings": warnings}


@app.get("/api/drives", tags=["system"])
def drives(request: Request, refresh: bool = False, user: dict = Depends(get_current_user)) -> dict:
    if refresh:
        _state(request).cache.pop("drives", None)
    return _cached(request, "drives", 15, _state(request).engine.list_drives)


@app.post("/api/destinations/validate", tags=["system"])
def validate_destination(body: S.ValidateDestinationRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    return _state(request).engine.validate_destination(body.destination, body.sources, body.required_bytes)


# ============================================================================= scans
@app.post("/api/scans", status_code=status.HTTP_202_ACCEPTED, tags=["scans"])
def create_scan(body: S.ScanCreateRequest, request: Request, user: dict = Depends(require_role("admin", "operator"))) -> dict:
    st = _state(request)
    data = body.model_dump()
    data["label"] = body.label or S.default_label(body)
    data["options"] = {"allow_non_hdd": body.allow_non_hdd, "unallocated_only": body.unallocated_only}
    data["progress"] = initial_progress()
    session = st.repo.create_scan_session(user["id"], data)
    st.repo.audit(user["id"], "scan.created", {"session_id": session["id"], "mode": body.mode, "scope": body.target_scope,
                                               "sources": body.sources, "folder": body.folder_path})
    st.jobs.start_scan(session)
    return S.session_out(session, st.jobs.progress(session))


@app.post("/api/scans/import", status_code=status.HTTP_202_ACCEPTED, tags=["scans"])
async def import_scan(request: Request, file: UploadFile = File(...), label: Optional[str] = None,
                      user: dict = Depends(require_role("admin", "operator"))) -> dict:
    st = _state(request)
    raw = await file.read()
    try:
        doc = validate_candidates(json.loads(raw.decode("utf-8")))
    except (ValueError, CandidatesFormatError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Not a valid candidates.json: {exc}")
    scan = doc["scan"]
    session = st.repo.create_scan_session(user["id"], {
        "label": label or f"Imported {file.filename} ({scan.get('mode', 'quick')})", "mode": scan.get("mode", "quick"),
        "target_scope": scan.get("target_scope", "drive"), "sources": scan.get("source_specs", []),
        "folder_path": scan.get("folder_path"), "scenario": "custom", "file_types": scan.get("type_filter", []),
        "progress": initial_progress("Importing"),
    })
    path = settings.data_dir / "scans" / session["id"] / "candidates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    st.jobs.start_import(session, path)
    return S.session_out(session)


@app.get("/api/scans", tags=["scans"])
def list_scans(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: Optional[str] = None,
               user: dict = Depends(get_current_user)) -> dict:
    st = _state(request)
    rows, total = st.repo.list_scan_sessions(user["id"], page, page_size, q)
    return S.page([S.session_out(r, st.jobs.progress(r)) for r in rows], page, page_size, total)


@app.get("/api/scans/{session_id}", tags=["scans"])
def get_scan(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    row = _session_or_404(request, session_id, user)
    return S.session_out(row, _state(request).jobs.progress(row))


@app.get("/api/scans/{session_id}/progress", tags=["scans"])
def scan_progress(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    row = _session_or_404(request, session_id, user)
    return {"status": row["status"], "progress": _state(request).jobs.progress(row), "totals": S.session_out(row)["totals"],
            "error": row.get("error")}


@app.post("/api/scans/{session_id}/cancel", tags=["scans"])
def cancel_scan(session_id: str, request: Request, user: dict = Depends(require_role("admin", "operator"))) -> dict:
    row = _session_or_404(request, session_id, user)
    if not _state(request).jobs.cancel_scan(session_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Scan is not running (status: {row['status']})")
    return S.session_out(row, _state(request).jobs.progress(row))


@app.delete("/api/scans/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["scans"])
def delete_scan(session_id: str, request: Request, user: dict = Depends(require_role("admin", "operator"))) -> Response:
    st = _state(request)
    _session_or_404(request, session_id, user)
    if st.jobs.is_active(session_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cancel the scan before deleting it")
    st.repo.delete_scan_session(session_id, user["id"])
    for p in (settings.data_dir / "scans" / session_id, settings.data_dir / "thumbnails" / session_id, Path(settings.sample_dir) / session_id):
        shutil.rmtree(p, ignore_errors=True)
    st.repo.audit(user["id"], "scan.deleted", {"session_id": session_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/scans/{session_id}/files", tags=["results"])
def list_files(session_id: str, request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
               extension: Optional[str] = Query(None, description="comma separated, e.g. jpg,pdf"),
               confidence: Optional[str] = Query(None, description="comma separated: high,medium,low"),
               q: Optional[str] = None, cluster_id: Optional[str] = None, method: Optional[str] = None,
               sort: str = Query("confidence", pattern="^(name|size|confidence|lba|extension|modified)$"),
               order: str = Query("desc", pattern="^(asc|desc)$"), user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(request, session_id, user)
    exts = [e for e in (extension or "").split(",") if e.strip()]
    confs = [c for c in (confidence or "").split(",") if c.strip()]
    rows, total = _state(request).repo.list_files(session_id, page, page_size, exts or None, confs or None, q, cluster_id, method, sort, order)
    return S.page([S.file_out(r) for r in rows], page, page_size, total)


@app.get("/api/scans/{session_id}/files/{file_id}", tags=["results"])
def get_file(session_id: str, file_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(request, session_id, user)
    row = _state(request).repo.get_file(session_id, file_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    return S.file_detail_out(row)


@app.get("/api/scans/{session_id}/files/{file_id}/preview", tags=["results"])
def file_preview(session_id: str, file_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(request, session_id, user)
    row = _state(request).repo.get_file(session_id, file_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    kind = row.get("preview_kind") or "none"
    msg = None
    if kind == "none":
        msg = ("No preview: the content could not be decoded (it may be overwritten) or this file type has no previewer."
               if row["confidence"] == "low" else "No preview is available for this file type.")
    return {"file_id": file_id, "kind": kind,
            "thumbnail_url": f"/api/scans/{session_id}/files/{file_id}/thumbnail" if row.get("thumbnail_path") else None,
            "text_excerpt": row.get("text_excerpt"), "keywords": row.get("keywords") or [], "message": msg}


@app.get("/api/scans/{session_id}/files/{file_id}/thumbnail", tags=["results"])
def file_thumbnail(session_id: str, file_id: str, request: Request, user: dict = Depends(get_current_user)) -> FileResponse:
    _session_or_404(request, session_id, user)
    row = _state(request).repo.get_file(session_id, file_id)
    if not row or not row.get("thumbnail_path") or not Path(row["thumbnail_path"]).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No thumbnail")
    return FileResponse(row["thumbnail_path"], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/scans/{session_id}/clusters", tags=["results"])
def clusters(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> list[dict]:
    _session_or_404(request, session_id, user)
    return [S.cluster_out(c) for c in _state(request).repo.list_clusters(session_id)]


@app.get("/api/scans/{session_id}/stats", tags=["results"])
def stats(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(request, session_id, user)
    return _state(request).repo.stats(session_id)


# ============================================================================= restore
@app.post("/api/scans/{session_id}/restore", status_code=status.HTTP_202_ACCEPTED, tags=["restore"])
def restore(session_id: str, body: S.RestoreRequest, request: Request, user: dict = Depends(require_role("admin", "operator"))) -> dict:
    st = _state(request)
    session = _session_or_404(request, session_id, user)
    if session["status"] not in ("completed", "partial", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Results are not ready yet")
    files = st.repo.get_files(session_id, list(dict.fromkeys(body.file_ids)))
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="None of the selected files belong to this session")
    required = sum(int(f["size_bytes"]) for f in files)
    specs = set(session.get("sources") or [])
    for f in files:
        cand = f["candidate"]
        if cand.get("source_kind") != "image":
            specs.add(cand.get("source_device", ""))
        path = f.get("original_path") or ""
        if len(path) >= 2 and path[1] == ":":
            specs.add(path[:2])
    check = st.engine.validate_destination(body.destination, sorted(s for s in specs if s), required)
    if not check.get("safe"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"message": "Unsafe destination", "destination_check": check})
    job = st.jobs.start_restore(session, user, files, body.destination, body.use_smart_names, body.preserve_paths)
    out = S.restore_out(job, 0.0)
    out["destination_check"] = check
    return out


@app.get("/api/restores/{job_id}", tags=["restore"])
def restore_status(job_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    st = _state(request)
    row = st.repo.get_restore_job(job_id, user["id"])
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restore job not found")
    return S.restore_out(row, st.jobs.restore_percent(job_id) if row["status"] in ("queued", "running") else None)


@app.post("/api/restores/{job_id}/cancel", tags=["restore"])
def restore_cancel(job_id: str, request: Request, user: dict = Depends(require_role("admin", "operator"))) -> dict:
    st = _state(request)
    row = st.repo.get_restore_job(job_id, user["id"])
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restore job not found")
    st.jobs.cancel_restore(job_id)
    return S.restore_out(row)


@app.get("/api/scans/{session_id}/restores", tags=["restore"])
def list_restores(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> list[dict]:
    _session_or_404(request, session_id, user)
    return [S.restore_out(r) for r in _state(request).repo.list_restore_jobs(session_id)]


# ============================================================================= reports (Module 7)
@app.post("/api/scans/{session_id}/reports", status_code=status.HTTP_201_CREATED, tags=["reports"])
def create_report(session_id: str, request: Request, body: Optional[S.ReportRequest] = None,
                  user: dict = Depends(get_current_user)) -> dict:
    st = _state(request)
    session = _session_or_404(request, session_id, user)
    if session["status"] in ("queued", "running", "analyzing"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Wait for the scan to finish before generating a report")
    files = st.repo.all_files(session_id)
    title = (body.title if body and body.title else None) or f"Recovery Report - {session['label']}"
    fname = f"recovery_report_{session_id[:8]}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = settings.data_dir / "reports" / fname
    summary = st.reports.build(path, session, files, st.repo.list_clusters(session_id), st.repo.list_restore_jobs(session_id),
                               user=user, title=title)
    row = st.repo.create_report(session_id, user["id"], title, fname, str(path), path.stat().st_size, summary)
    st.repo.audit(user["id"], "report.created", {"session_id": session_id, "report_id": row["id"]})
    return S.report_out(row)


@app.get("/api/reports", tags=["reports"])
def list_reports(request: Request, scan_id: Optional[str] = None, user: dict = Depends(get_current_user)) -> list[dict]:
    return [S.report_out(r) for r in _state(request).repo.list_reports(user["id"], scan_id)]


@app.get("/api/reports/{report_id}/download", tags=["reports"])
def download_report(report_id: str, request: Request, user: dict = Depends(get_current_user)) -> FileResponse:
    row = _state(request).repo.get_report(report_id, user["id"])
    if not row or not Path(row["file_path"]).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report not found")
    return FileResponse(row["file_path"], media_type="application/pdf", filename=row["file_name"])


# ============================================================================= semantic search
@app.get("/api/search", tags=["results"])
def search(request: Request, q: str = Query(..., min_length=2, max_length=200), limit: int = Query(50, ge=1, le=200),
           user: dict = Depends(get_current_user)) -> list[dict]:
    rows = _state(request).repo.search(user["id"], q, limit)
    terms = {t for t in q.lower().split() if t}
    hits = []
    for r in rows:
        kws = r.get("keywords") or []
        hits.append({"file": S.file_out(r), "scan_label": r.get("scan_label", ""), "rank": float(r.get("rank") or 0),
                     "matched_keywords": [k for k in kws if any(t in k for t in terms)]})
    return hits
