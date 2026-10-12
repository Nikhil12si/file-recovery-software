"""
jobs.py - background orchestration of scans and restores (Track B).

    scan:    queued -> running (engine NDJSON progress) -> analyzing (Module 6)
             -> completed | partial | cancelled | failed
    restore: queued -> running (engine `recover` NDJSON) -> completed | partial | failed | cancelled

Live progress is kept in memory for cheap polling (GET /api/scans/{id}/progress)
and persisted to PostgreSQL every couple of seconds so history survives restarts.
Scans run one at a time by default (MAX_CONCURRENT_SCANS) because concurrent
sector reads on one HDD make every scan slower.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .analysis import AnalysisEngine, CandidatesFormatError, load_candidates
from .db import Repository, utcnow
from .engine_bridge import EXIT_CODES, Engine, EngineError

log = logging.getLogger("jobs")


def initial_progress(message: str = "Waiting for a free scan slot") -> dict:
    return {"phase": "queued", "percent": 0.0, "overall_percent": 0.0, "bytes_done": 0, "bytes_total": 0, "unit": "bytes",
            "rate_mb_s": 0.0, "eta_seconds": 0, "candidates_found": 0, "source_index": 0, "source_count": 0, "current_source": None,
            "read_errors": 0, "message": message, "updated_at": utcnow(), "log": []}


class ScanRuntime:
    def __init__(self, session: dict) -> None:
        self.session_id: str = session["id"]
        self.user_id: str = session["user_id"]
        self.progress = initial_progress()
        self.log: collections.deque = collections.deque(maxlen=200)
        self.cancel_requested = False
        self.cancel_file: Optional[Path] = None
        self.thread: Optional[threading.Thread] = None
        self.last_persist = 0.0
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        with self.lock:
            p = dict(self.progress)
            p["log"] = list(self.log)[-50:]
            return p


def _sha256(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def output_name(f: dict, use_smart_names: bool) -> str:
    ext = f.get("extension") or "bin"
    if f.get("file_name"):
        name = f["file_name"]
        # FAT deleted short names lose their first character ("_EPORT.PDF"): prefer the smart name.
        if use_smart_names and f.get("method") == "fat" and name.startswith("_") and f.get("suggested_name"):
            name = f["suggested_name"]
        return name
    if use_smart_names and f.get("suggested_name"):
        return f["suggested_name"]
    return f"carved_{f.get('absolute_lba', 0)}.{ext}"


def relative_dir(f: dict, preserve_paths: bool) -> str:
    """Directory below the destination, mirroring the original tree when known."""
    if not preserve_paths:
        return ""
    path = f.get("original_path")
    if path:
        parts = [p for p in re.split(r"[\\/]+", path) if p]
        root = parts[0] if parts else ""
        letter = root[0] if re.match(r"^[A-Za-z]:$", root) else re.sub(r"[\[\]]", "", root) or "source"
        middle = parts[1:-1]
        return "/".join([f"{letter}_drive" if len(letter) == 1 else letter, *middle])
    return f"Carved/{(f.get('extension') or 'other').upper()}"


class JobManager:
    def __init__(self, settings: Any, repo: Repository, engine: Engine, analysis: AnalysisEngine) -> None:
        self.settings = settings
        self.repo = repo
        self.engine = engine
        self.analysis = analysis
        self._scans: dict[str, ScanRuntime] = {}
        self._restores: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._scan_slots = threading.Semaphore(max(1, int(os.environ.get("MAX_CONCURRENT_SCANS", "1"))))

    # ================================================================== lifecycle
    def recover_interrupted(self) -> None:
        for s in self.repo.interrupted_sessions():
            self.repo.update_scan_session(s["id"], status="failed", error="The service was restarted while this scan was running.",
                                          finished_at=utcnow())

    def shutdown(self) -> None:
        for rt in list(self._scans.values()):
            if rt.cancel_file:
                rt.cancel_file.parent.mkdir(parents=True, exist_ok=True)
                rt.cancel_file.touch()

    # ================================================================== scans
    def start_scan(self, session: dict) -> None:
        rt = ScanRuntime(session)
        scan_dir = Path(self.settings.data_dir) / "scans" / session["id"]
        scan_dir.mkdir(parents=True, exist_ok=True)
        rt.cancel_file = scan_dir / "cancel.flag"
        with self._lock:
            self._scans[session["id"]] = rt
        rt.thread = threading.Thread(target=self._run_scan, args=(rt, session), name=f"scan-{session['id'][:8]}", daemon=True)
        rt.thread.start()

    def start_import(self, session: dict, candidates_path: Path) -> None:
        """Analyse a candidates.json produced elsewhere (e.g. engine run by hand on another PC)."""
        rt = ScanRuntime(session)
        with self._lock:
            self._scans[session["id"]] = rt

        def run() -> None:
            self.repo.update_scan_session(session["id"], status="running", started_at=utcnow())
            self._log(rt, "info", f"Imported {candidates_path.name}")
            self._analyze_and_store(rt, session["id"], candidates_path, 0)

        rt.thread = threading.Thread(target=run, name=f"import-{session['id'][:8]}", daemon=True)
        rt.thread.start()

    def is_active(self, session_id: str) -> bool:
        rt = self._scans.get(session_id)
        return bool(rt and rt.thread and rt.thread.is_alive())

    def progress(self, session: dict) -> dict:
        rt = self._scans.get(session["id"])
        if rt and rt.thread and rt.thread.is_alive():
            return rt.snapshot()
        p = dict(session.get("progress") or {})
        return {**initial_progress(""), **p}

    def cancel_scan(self, session_id: str) -> bool:
        rt = self._scans.get(session_id)
        if not rt or not rt.thread or not rt.thread.is_alive():
            return False
        rt.cancel_requested = True
        if rt.cancel_file:
            rt.cancel_file.touch()
        self._log(rt, "warning", "Cancellation requested by user")
        return True

    def _log(self, rt: ScanRuntime, level: str, message: str) -> None:
        with rt.lock:
            rt.log.append({"ts": utcnow(), "level": level, "message": message})
            rt.progress["message"] = message
            rt.progress["updated_at"] = utcnow()

    def _persist_progress(self, rt: ScanRuntime, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - rt.last_persist < 2.0:
            return
        rt.last_persist = now
        try:
            self.repo.update_scan_session(rt.session_id, progress=rt.snapshot())
        except Exception:  # noqa: BLE001
            log.exception("failed to persist progress")

    def _on_scan_event(self, rt: ScanRuntime, errors: list[dict]):
        def handle(ev: dict) -> None:
            kind = ev.get("event")
            if kind == "progress":
                with rt.lock:
                    p = rt.progress
                    for k in ("phase", "percent", "overall_percent", "bytes_done", "bytes_total", "unit", "rate_mb_s", "eta_seconds",
                              "candidates_found", "source_index", "source_count", "read_errors"):
                        if k in ev:
                            p[k] = ev[k]
                    p["current_source"] = ev.get("source_id", p.get("current_source"))
                    # Reserve the last 10 % of the bar for the analysis phase.
                    p["overall_percent"] = round(float(ev.get("overall_percent", p["overall_percent"])) * 0.9, 2)
                    p["message"] = f"{str(ev.get('phase', '')).replace('_', ' ').title()} - {ev.get('source_id', '')}"
                    p["updated_at"] = utcnow()
                self._persist_progress(rt)
            elif kind == "scan_started":
                with rt.lock:
                    rt.progress["source_count"] = len(ev.get("sources", []))
                    rt.progress["phase"] = "starting"
                self._log(rt, "info", "Scanning " + ", ".join(s.get("display_name", s.get("source_id", "")) for s in ev.get("sources", [])))
            elif kind == "log":
                self._log(rt, ev.get("level", "info"), ev.get("message", ""))
            elif kind == "error":
                errors.append(ev)
                self._log(rt, "error", f"{ev.get('code')}: {ev.get('message')}")
            elif kind == "source_done":
                self._log(rt, "info", f"{ev.get('source_id')}: {ev.get('status')} ({ev.get('candidates', 0)} candidates)")
            elif kind == "done":
                self._log(rt, "info", f"Engine finished: {ev.get('status')} - {ev.get('candidates', 0)} candidates")

        return handle

    def _run_scan(self, rt: ScanRuntime, session: dict) -> None:
        sid = session["id"]
        self.repo.update_scan_session(sid, progress=rt.snapshot())
        with self._scan_slots:
            if rt.cancel_requested:
                self.repo.update_scan_session(sid, status="cancelled", finished_at=utcnow(), progress=rt.snapshot())
                return
            started = utcnow()
            self.repo.update_scan_session(sid, status="running", started_at=started)
            with rt.lock:
                rt.progress.update(phase="starting", message="Starting engine")
            scan_dir = Path(self.settings.data_dir) / "scans" / sid
            out_file = scan_dir / "candidates.json"
            sample_dir = Path(self.settings.sample_dir) / sid
            opts = session.get("options") or {}
            errors: list[dict] = []
            code: Optional[int] = None
            try:
                code = self.engine.scan(
                    scan_id=sid, mode=session["mode"], scope=session["target_scope"], sources=session.get("sources") or [],
                    folder=session.get("folder_path"), out_file=out_file, sample_dir=sample_dir, cancel_file=rt.cancel_file,  # type: ignore[arg-type]
                    types=session.get("file_types") or [], allow_non_hdd=bool(opts.get("allow_non_hdd")),
                    unallocated_only=bool(opts.get("unallocated_only")), on_event=self._on_scan_event(rt, errors),
                )
            except EngineError as exc:
                errors.append({"code": exc.code, "message": exc.message})
            except Exception as exc:  # noqa: BLE001
                log.exception("engine crashed")
                errors.append({"code": "INTERNAL", "message": str(exc)})

            if not out_file.exists():
                msg = errors[0]["message"] if errors else (
                    f"Engine exited with code {code} ({EXIT_CODES.get(code, 'unknown')}) without writing {out_file.name}"
                    if code is not None else "Engine did not start")
                self._log(rt, "error", msg)
                with rt.lock:
                    rt.progress["phase"] = "done"
                self.repo.update_scan_session(sid, status="cancelled" if rt.cancel_requested else "failed", error=msg,
                                              engine_exit_code=code, finished_at=utcnow(), progress=rt.snapshot())
                return

            self._analyze_and_store(rt, sid, out_file, code)

    def _analyze_and_store(self, rt: ScanRuntime, sid: str, out_file: Path, code: Optional[int]) -> None:
        """Module 6 + persistence; shared by engine scans and imported candidates.json files."""
        self.repo.update_scan_session(sid, status="analyzing", engine_exit_code=code, candidates_path=str(out_file))
        with rt.lock:
            rt.progress.update(phase="analysis", percent=0.0, overall_percent=90.0, unit="items", message="Analysing results")

        def analysis_progress(done: int, total: int, message: str) -> None:
            with rt.lock:
                rt.progress.update(percent=round(100 * done / max(1, total), 2),
                                   overall_percent=round(90 + 10 * done / max(1, total), 2),
                                   bytes_done=done, bytes_total=total, message=message, updated_at=utcnow())
            self._persist_progress(rt)

        try:
            doc = load_candidates(out_file)
            result = self.analysis.analyze(doc, sid, analysis_progress)
            self.repo.replace_analysis_results(sid, rt.user_id, result.files, result.clusters)
        except (CandidatesFormatError, Exception) as exc:  # noqa: BLE001
            log.exception("analysis failed")
            self._log(rt, "error", f"Analysis failed: {exc}")
            self.repo.update_scan_session(sid, status="failed", error=f"Analysis failed: {exc}", finished_at=utcnow(),
                                          progress=rt.snapshot())
            return

        engine_status = doc["scan"].get("status", "completed")
        final = {"completed": "completed", "partial": "partial", "cancelled": "cancelled", "failed": "failed"}.get(engine_status, "completed")
        if rt.cancel_requested:
            final = "cancelled"
        error = None
        if final == "failed":
            error = "; ".join(s.get("error") or s.get("status") for s in doc.get("sources", []) if s.get("status") != "completed") or None
        with rt.lock:
            rt.progress.update(phase="done", percent=100.0, overall_percent=100.0, candidates_found=result.totals["files"],
                               message=f"{result.totals['files']} files found ({result.totals['high']} high confidence)",
                               updated_at=utcnow())
        self.repo.update_scan_session(sid, status=final, totals=result.totals, health_warnings=result.health_warnings,
                                      scan_summary=result.scan_summary, engine_scan_id=doc["scan"].get("scan_id"),
                                      finished_at=utcnow(), error=error, progress=rt.snapshot())
        self.repo.audit(rt.user_id, "scan.finished", {"session_id": sid, "status": final, "files": result.totals["files"]})
        if rt.cancel_file and rt.cancel_file.exists():
            rt.cancel_file.unlink(missing_ok=True)


    # ================================================================== restores
    def start_restore(self, session: dict, user: dict, files: list[dict], destination: str, use_smart_names: bool,
                      preserve_paths: bool) -> dict:
        items = [{"file_id": f["id"], "status": "pending", "output_path": None, "bytes_written": 0, "sha256": None, "error": None}
                 for f in files]
        job = self.repo.create_restore_job(session["id"], user["id"], destination,
                                           {"use_smart_names": use_smart_names, "preserve_paths": preserve_paths}, items)
        state = {"job": job, "cancel_file": Path(self.settings.data_dir) / "restore_manifests" / f"{job['id']}.cancel", "percent": 0.0}
        with self._lock:
            self._restores[job["id"]] = state
        threading.Thread(target=self._run_restore_safe, args=(state, files, use_smart_names, preserve_paths),
                         name=f"restore-{job['id'][:8]}", daemon=True).start()
        self.repo.audit(user["id"], "restore.started", {"session_id": session["id"], "job_id": job["id"], "files": len(files),
                                                        "destination": destination})
        return job

    def restore_percent(self, job_id: str) -> Optional[float]:
        st = self._restores.get(job_id)
        return st["percent"] if st else None

    def cancel_restore(self, job_id: str) -> bool:
        st = self._restores.get(job_id)
        if not st:
            return False
        st["cancel_file"].touch()
        return True

    def _run_restore_safe(self, state: dict, files: list[dict], use_smart_names: bool, preserve_paths: bool) -> None:
        """Never leave a job stuck in queued/running because of an unexpected exception."""
        try:
            self._run_restore(state, files, use_smart_names, preserve_paths)
        except Exception as exc:  # noqa: BLE001
            log.exception("restore job failed")
            state["percent"] = 100.0
            try:
                self.repo.update_restore_job(state["job"]["id"], status="failed", error=f"Restore failed: {exc}", finished_at=utcnow())
            except Exception:  # noqa: BLE001
                log.exception("could not record restore failure")

    def _run_restore(self, state: dict, files: list[dict], use_smart_names: bool, preserve_paths: bool) -> None:
        job = state["job"]
        jid = job["id"]
        by_candidate = {f["candidate"]["id"]: f for f in files}
        items = {i["file_id"]: i for i in job["items"]}
        manifest = {
            "schema_version": "1.0",
            "destination": job["destination"],
            "preserve_paths": preserve_paths,
            "items": [dict(f["candidate"], output_name=output_name(f, use_smart_names), relative_dir=relative_dir(f, preserve_paths))
                      for f in files],
        }
        mpath = Path(self.settings.data_dir) / "restore_manifests" / f"{jid}.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        self.repo.update_restore_job(jid, status="running")
        counters = {"ok": 0, "failed": 0, "bytes": 0}
        errors: list[str] = []
        dest_check: dict = {}
        last = [0.0]

        def persist(force: bool = False) -> None:
            if force or time.monotonic() - last[0] > 1.5:
                last[0] = time.monotonic()
                self.repo.update_restore_job(jid, items=list(items.values()), restored_files=counters["ok"],
                                             failed_files=counters["failed"], bytes_written=counters["bytes"])

        def on_event(ev: dict) -> None:
            kind = ev.get("event")
            if kind == "restore_item":
                f = by_candidate.get(ev.get("id"))
                if not f:
                    return
                item = items[f["id"]]
                ok = ev.get("status") in ("ok", "partial")
                item.update(status=ev.get("status", "failed"), output_path=ev.get("output_path"),
                            bytes_written=int(ev.get("bytes_written") or 0), error=ev.get("error"))
                if ok and ev.get("output_path"):
                    item["sha256"] = _sha256(ev["output_path"])
                    self.repo.mark_restored(f["id"], ev["output_path"], item["sha256"])
                    counters["ok"] += 1
                else:
                    counters["failed"] += 1
                counters["bytes"] += item["bytes_written"]
                state["percent"] = round(100 * (counters["ok"] + counters["failed"]) / max(1, len(files)), 2)
                persist()
            elif kind == "destination_check":
                dest_check.update(ev.get("result") or {})
            elif kind == "error":
                errors.append(f"{ev.get('code')}: {ev.get('message')}")

        code: Optional[int] = None
        try:
            code = self.engine.recover(manifest_path=mpath, cancel_file=state["cancel_file"], on_event=on_event)
        except EngineError as exc:
            errors.append(f"{exc.code}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            log.exception("restore crashed")
            errors.append(str(exc))

        if code == 5:
            status = "cancelled"
        elif counters["ok"] == len(files) and not counters["failed"]:
            status = "completed"
        elif counters["ok"] > 0:
            status = "partial"
        else:
            status = "failed"
        for item in items.values():
            if item["status"] == "pending" and status in ("failed", "cancelled"):
                item["error"] = errors[0] if errors else status
        state["percent"] = 100.0
        self.repo.update_restore_job(jid, status=status, items=list(items.values()), restored_files=counters["ok"],
                                     failed_files=counters["failed"], bytes_written=counters["bytes"],
                                     destination_check=dest_check or None, error="; ".join(errors) or None, finished_at=utcnow())
        # A machine-readable log next to the recovered files helps users verify integrity later.
        if counters["ok"]:
            try:
                log_path = Path(job["destination"]) / f"_recovery_log_{jid[:8]}.json"
                log_path.write_text(json.dumps({"job_id": jid, "finished_at": utcnow(), "items": list(items.values())}, indent=2),
                                    encoding="utf-8")
            except OSError:
                pass
        state["cancel_file"].unlink(missing_ok=True)
