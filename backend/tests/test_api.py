"""
HTTP-level contract tests (FastAPI TestClient + mock engine + SQLite).
Every response is validated against contracts/schemas/rest-api.schema.json.

    pip install -r backend/requirements.txt
    pytest -q backend/tests/test_api.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("api")
    os.environ.update({"DATA_DIR": str(data), "ENGINE_MODE": "mock", "MOCK_SCAN_SECONDS": "0.3", "DATABASE_URL": "",
                       "JWT_SECRET": "test-secret"})
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "contracts"))
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/api/auth/register", json={"email": "ops@example.com", "password": "Passw0rd!", "full_name": "Ops"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def check(obj, definition):
    from validate_contracts import validate

    errors = validate("rest", obj, definition)
    assert errors == [], errors


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    check(r.json(), "health")


def test_auth_flow(client, auth):
    assert client.get("/api/auth/me").status_code == 401
    me = client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200 and me.json()["role"] == "admin"
    check(me.json(), "user")
    bad = client.post("/api/auth/login", json={"email": "ops@example.com", "password": "nope12345"})
    assert bad.status_code == 401
    weak = client.post("/api/auth/register", json={"email": "x@example.com", "password": "short"})
    assert weak.status_code == 422


def test_drives_and_guard(client, auth):
    r = client.get("/api/drives", headers=auth)
    assert r.status_code == 200 and r.json()["volumes"]
    g = client.post("/api/destinations/validate", headers=auth, json={"destination": "D:\\Rec", "sources": ["D:"]})
    assert g.json()["safe"] is False and g.json()["reason_code"] == "SAME_VOLUME"


def test_scan_results_restore_report(client, auth, tmp_path):
    r = client.post("/api/scans", headers=auth, json={"scenario": "accidental_delete", "target_scope": "drive", "sources": ["D:"]})
    assert r.status_code == 202, r.text
    session = r.json()
    check(session, "scan_session")
    for _ in range(200):
        p = client.get(f"/api/scans/{session['id']}/progress", headers=auth).json()
        if p["status"] in ("completed", "partial", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert p["status"] == "completed"
    files = client.get(f"/api/scans/{session['id']}/files?page_size=100&confidence=high,medium", headers=auth).json()
    check(files, "page")
    assert files["items"]
    for f in files["items"]:
        check(f, "recovered_file")
    f = files["items"][0]
    prev = client.get(f"/api/scans/{session['id']}/files/{f['id']}/preview", headers=auth).json()
    check(prev, "file_preview")
    if prev["thumbnail_url"]:
        img = client.get(prev["thumbnail_url"], headers=auth)
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    unsafe = client.post(f"/api/scans/{session['id']}/restore", headers=auth, json={"file_ids": [f["id"]], "destination": "D:\\x"})
    assert unsafe.status_code == 400
    job = client.post(f"/api/scans/{session['id']}/restore", headers=auth,
                      json={"file_ids": [x["id"] for x in files["items"][:5]], "destination": str(tmp_path / "out")}).json()
    check(job, "restore_job")
    for _ in range(100):
        j = client.get(f"/api/restores/{job['id']}", headers=auth).json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert j["status"] == "completed"
    rep = client.post(f"/api/scans/{session['id']}/reports", headers=auth, json={}).json()
    check(rep, "report")
    pdf = client.get(rep["download_url"], headers=auth)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    hist = client.get("/api/scans", headers=auth).json()
    assert hist["total"] >= 1
