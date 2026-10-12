"""
Regression tests for defects found during the full-project review (October 2026):

  * PostgreSQL rejects NUL (0x00) in TEXT/JSONB: text recovered from damaged files made the whole
    scan fail with "Analysis failed" on PostgreSQL (SQLite accepted it, so it went unnoticed).
  * The semantic index stored only the smart name when one existed, so a file could no longer be
    found by its original name.
  * The mock engine placed files purely at random, so a document theme (e.g. the invoice) was missing
    in about a third of runs and test_semantic_search_and_clusters failed intermittently.
"""
from __future__ import annotations

import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database, Repository, _index_title, _pg_params, _strip_nul  # noqa: E402
from app.mock_engine import PDF_THEMES, MockEngine  # noqa: E402


def test_strip_nul_recurses_into_json_values():
    raw = {"name": "a\x00b", "list": ["x\x00", 1, None, {"deep": "\x00z"}], "n": 5}
    assert _strip_nul(raw) == {"name": "ab", "list": ["x", 1, None, {"deep": "z"}], "n": 5}
    assert _pg_params({"t": "q\x00q", "i": 3, "none": None}) == {"t": "qq", "i": 3, "none": None}


def test_index_title_keeps_original_and_smart_name():
    t = _index_title({"file_name": "Invoice_4411_Acme.pdf", "suggested_name": "Payment_Terms_2025.pdf"})
    assert "Invoice_4411_Acme.pdf" in t and "Payment_Terms_2025.pdf" in t
    assert "Invoice 4411 Acme pdf" in t  # word-split form for full-text parsers
    assert _index_title({"file_name": None, "suggested_name": None}) == ""


def test_nul_text_is_stored_and_searchable(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init_schema()
    repo = Repository(db)
    user = repo.create_user("n@example.com", "x", "N", "admin")
    s = repo.create_scan_session(user["id"], {"label": "t", "mode": "quick", "target_scope": "drive", "sources": ["D:"],
                                              "folder_path": None, "scenario": "custom", "file_types": [], "options": {}})
    f = {"id": "f1", "candidate_id": "c1", "source_id": "vol-D", "method": "mft", "file_name": "Invoice_77.txt",
         "suggested_name": "Groceries_List.txt", "original_path": "D:\\Docs\\Invoice_77.txt", "extension": "txt",
         "mime_type": "text/plain", "size_bytes": 10, "lba": 1, "absolute_lba": 1, "fragment_count": 1, "confidence": "low",
         "confidence_score": 10, "confidence_reasons": ["damaged\x00"], "cluster_id": None, "timestamps": {}, "keywords": ["milk"],
         "candidate": {"id": "c1", "note": "x\x00y"}, "sample_path": None, "thumbnail_path": None,
         "text_excerpt": "milk\x00eggs", "preview_kind": "text"}
    repo.replace_analysis_results(s["id"], user["id"], [f], [])
    hits = repo.search(user["id"], "invoice")
    assert [h["id"] for h in hits] == ["f1"], "file must stay findable by its original name"
    db.close()


def test_mock_engine_places_every_document_theme(tmp_path):
    eng = MockEngine(tmp_path / "disk", scan_seconds=0)
    for seed in range(12):  # several seeds: the old random placement missed the invoice in ~1/3 of them
        out = tmp_path / f"c{seed}.json"
        code = eng.scan(scan_id=f"seed-{seed}-{zlib.crc32(str(seed).encode())}", mode="quick", scope="drive", sources=["D:"],
                        folder=None, out_file=out, sample_dir=None, cancel_file=tmp_path / "cancel", types=[],
                        allow_non_hdd=False, unallocated_only=False, on_event=lambda ev: None)
        assert code == 0
        names = out.read_text(encoding="utf-8")
        for title, _ in PDF_THEMES:
            assert title.split(" 20")[0].replace(" ", "_") in names, f"{title} missing for seed {seed}"


def test_private_network_preflight_only_for_allowed_origin():
    """Hosted website -> local agent: Chromium's Private Network Access preflight is answered only for listed sites."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient

    from app.main import PrivateNetworkAccess

    allowed = ["https://demo-site.web.app"]
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(PrivateNetworkAccess, origins=allowed)
    client = TestClient(app)
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type",
           "Access-Control-Request-Private-Network": "true"}
    ok = client.options("/api/x", headers={"Origin": "https://demo-site.web.app", **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://demo-site.web.app"
    assert ok.headers["access-control-allow-private-network"] == "true"
    bad = client.options("/api/x", headers={"Origin": "https://other.web.app", **pre})
    assert bad.status_code == 400 and "access-control-allow-private-network" not in bad.headers
