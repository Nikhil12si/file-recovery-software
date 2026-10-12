"""
End-to-end pipeline tests for Track B (no HTTP layer, no PostgreSQL needed):

    engine (mock or real) -> candidates.json -> analysis -> SQLite repository
    -> restore via engine -> report PDF -> semantic search

Run:  pytest -q backend/tests            (from the repository root)
Set   ENGINE_PATH=<built engine> TEST_IMAGE=<ntfs.img> to also exercise the real C++ engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "contracts"))

from app.analysis import AnalysisEngine, load_candidates  # noqa: E402
from app.db import Database, Repository  # noqa: E402
from app.engine_bridge import EngineClient  # noqa: E402
from app.jobs import JobManager, output_name, relative_dir  # noqa: E402
from app.mock_engine import MockEngine  # noqa: E402
from app.report_gen import ReportGenerator  # noqa: E402

try:
    from validate_contracts import validate  # type: ignore
except ImportError:  # jsonschema not installed
    validate = None


@pytest.fixture()
def env(tmp_path):
    settings = types.SimpleNamespace(data_dir=tmp_path / "data", sample_dir=str(tmp_path / "samples"))
    for sub in ("scans", "thumbnails", "reports", "restore_manifests"):
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_schema()
    repo = Repository(db)
    user = repo.create_user("tester@example.com", "x", "Tester", "admin")
    yield settings, repo, user, tmp_path
    db.close()


def _wait(repo, sid, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        s = repo.get_scan_session(sid)
        if s["status"] in ("completed", "partial", "failed", "cancelled"):
            return s
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def _wait_restore(repo, jid, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        j = repo.get_restore_job(jid)
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("restore did not finish")


def _run(settings, repo, user, engine, **req):
    jm = JobManager(settings, repo, engine, AnalysisEngine(settings.data_dir / "thumbnails", workers=2))
    data = {"label": "t", "mode": "quick", "target_scope": "drive", "sources": ["D:"], "folder_path": None, "scenario": "custom",
            "file_types": [], "options": {}}
    data.update(req)
    session = repo.create_scan_session(user["id"], data)
    jm.start_scan(session)
    return jm, _wait(repo, session["id"])


@pytest.mark.parametrize("mode,scope", [("quick", "drive"), ("deep", "all"), ("quick", "all")])
def test_mock_scan_analysis_restore_report(env, mode, scope):
    settings, repo, user, tmp = env
    engine = MockEngine(tmp / "mockdisk", scan_seconds=0.2)
    jm, s = _run(settings, repo, user, engine, mode=mode, target_scope=scope, sources=["D:"] if scope == "drive" else [])
    assert s["status"] == "completed", s.get("error")
    assert s["totals"]["files"] > 5
    assert s["totals"]["high"] + s["totals"]["medium"] + s["totals"]["low"] == s["totals"]["files"]

    # contract check of the engine output
    if validate:
        doc = json.loads(Path(s["candidates_path"]).read_text())
        assert validate("candidates", doc) == []

    files, total = repo.list_files(s["id"], 1, 500)
    assert total == s["totals"]["files"]
    assert any(f["suggested_name"] for f in files), "smart naming produced nothing"
    assert any(f["thumbnail_path"] for f in files), "no thumbnails generated"
    if mode == "quick":
        assert all(f["file_name"] for f in files)
    # filters & sorting
    highs, n_high = repo.list_files(s["id"], 1, 500, confidence=["high"])
    assert n_high == s["totals"]["high"] and all(f["confidence"] == "high" for f in highs)
    jpgs, _ = repo.list_files(s["id"], 1, 500, extension=["jpg"], sort="size", order="asc")
    assert [f["size_bytes"] for f in jpgs] == sorted(f["size_bytes"] for f in jpgs)

    # restore the high-confidence files and verify bytes against the simulated disk
    chosen = highs[:6]
    job = jm.start_restore(s, user, chosen, str(tmp / "restored"), True, True)
    j = _wait_restore(repo, job["id"])
    assert j["status"] == "completed", j
    for item in j["items"]:
        f = next(x for x in chosen if x["id"] == item["file_id"])
        expected = MockEngine._read_extents(Path(f["candidate"]["source_device"]), f["candidate"])
        assert hashlib.sha256(expected).hexdigest() == item["sha256"]
    assert repo.get_file(s["id"], chosen[0]["id"])["restored"] is True

    # report
    out = tmp / "report.pdf"
    summary = ReportGenerator().build(out, repo.get_scan_session(s["id"]), repo.all_files(s["id"]), repo.list_clusters(s["id"]),
                                      repo.list_restore_jobs(s["id"]), user=user)
    assert out.read_bytes()[:5] == b"%PDF-" and out.stat().st_size > 3000
    assert summary["files"] == s["totals"]["files"]


def test_semantic_search_and_clusters(env):
    settings, repo, user, tmp = env
    _, s = _run(settings, repo, user, MockEngine(tmp / "mockdisk", scan_seconds=0.1))
    hits = repo.search(user["id"], "invoice")
    assert hits and all("invoice" in (h["keywords_text"] + (h.get("suggested_name") or "") + (h.get("file_name") or "")).lower()
                        for h in hits)
    assert repo.list_clusters(s["id"]), "proximity clustering found no deletion events"


def test_folder_scope_and_type_filter(env):
    settings, repo, user, tmp = env
    _, s = _run(settings, repo, user, MockEngine(tmp / "mockdisk", scan_seconds=0.1), target_scope="folder",
                folder_path="D:\\Users\\Bob\\Docs", sources=[], file_types=["pdf"])
    files, _ = repo.list_files(s["id"], 1, 500)
    assert files and all(f["extension"] == "pdf" for f in files)
    assert all(f["original_path"].startswith("D:\\Users\\Bob\\Docs\\") for f in files)


def test_cancel(env):
    settings, repo, user, tmp = env
    engine = MockEngine(tmp / "mockdisk", scan_seconds=5)
    jm = JobManager(settings, repo, engine, AnalysisEngine(settings.data_dir / "thumbnails"))
    session = repo.create_scan_session(user["id"], {"label": "c", "mode": "deep", "target_scope": "drive", "sources": ["D:"]})
    jm.start_scan(session)
    time.sleep(0.6)
    assert jm.cancel_scan(session["id"])
    s = _wait(repo, session["id"])
    assert s["status"] == "cancelled"


def test_not_elevated_and_ssd_errors(env, monkeypatch):
    settings, repo, user, tmp = env
    monkeypatch.setenv("MOCK_ELEVATED", "false")
    _, s = _run(settings, repo, user, MockEngine(tmp / "mockdisk", scan_seconds=0.1))
    assert s["status"] == "failed" and "Administrator" in s["error"]
    monkeypatch.setenv("MOCK_ELEVATED", "true")
    _, s = _run(settings, repo, user, MockEngine(tmp / "mockdisk", scan_seconds=0.1), sources=["C:"])
    assert s["status"] == "failed" and "solid-state" in s["error"]


def test_destination_guard_mock():
    e = MockEngine(Path("/tmp/frs-guard"), scan_seconds=0)
    assert e.validate_destination("D:\\Recovered", ["D:"])["reason_code"] == "SAME_VOLUME"
    assert e.validate_destination("F:\\Recovered", ["\\\\.\\PhysicalDrive1"])["reason_code"] == "SAME_PHYSICAL_DISK"
    ok = e.validate_destination("E:\\Recovered", ["D:"])
    assert ok["safe"] and ok["reason_code"] == "OK"


def test_naming_helpers():
    f = {"file_name": None, "suggested_name": "Financial_Report_2026.pdf", "extension": "pdf", "absolute_lba": 77, "method": "carve",
         "original_path": None}
    assert output_name(f, True) == "Financial_Report_2026.pdf"
    assert output_name(f, False) == "carved_77.pdf"
    assert relative_dir(f, True) == "Carved/PDF"
    g = {"file_name": "a.txt", "original_path": "D:\\Users\\me\\a.txt", "extension": "txt", "method": "mft"}
    assert relative_dir(g, True) == "D_drive/Users/me"
    assert relative_dir(g, False) == ""


@pytest.mark.skipif(not (os.environ.get("ENGINE_PATH") and os.environ.get("TEST_IMAGE")), reason="real engine not configured")
def test_real_engine_image(env):
    settings, repo, user, tmp = env
    engine = EngineClient(os.environ["ENGINE_PATH"])
    jm, s = _run(settings, repo, user, engine, sources=["image:" + os.environ["TEST_IMAGE"]])
    assert s["status"] == "completed", s.get("error")
    files, _ = repo.list_files(s["id"], 1, 500)
    assert files
    job = jm.start_restore(s, user, files, str(tmp / "restored_real"), True, True)
    j = _wait_restore(repo, job["id"])
    assert j["restored_files"] == len(files)
