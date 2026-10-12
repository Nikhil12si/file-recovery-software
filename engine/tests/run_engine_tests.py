#!/usr/bin/env python3
"""
run_engine_tests.py - black-box regression tests for win_recovery_engine.

Usage:
    python make_test_images.py  <imgdir>
    python run_engine_tests.py  <engine-binary> <imgdir>

On Windows the same tests run against win_recovery_engine.exe because every
source is passed as `image:<path>`. Exit code 0 == all tests passed.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

ENGINE = os.path.abspath(sys.argv[1])
IMG = os.path.abspath(sys.argv[2])
EXP = json.load(open(os.path.join(IMG, "expected.json")))
WORK = os.path.join(IMG, "work")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

failures = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(args):
    p = subprocess.run([ENGINE] + args, capture_output=True, text=True, encoding="utf-8")
    events = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            events.append(json.loads(line))  # every stdout line must be valid JSON
    return p.returncode, events, p.stderr


def scan(name, img, mode, extra=()):
    out = os.path.join(WORK, f"{name}.json")
    rc, ev, err = run(["scan", "--mode", mode, "--scope", "drive", "--source", "image:" + os.path.join(IMG, img),
                       "--out", out, "--sample-dir", os.path.join(WORK, f"{name}_samples"), *extra])
    check(rc == 0, f"[{name}] engine exit code 0 (got {rc}) {err.strip()}")
    check(any(e["event"] == "done" for e in ev), f"[{name}] 'done' event emitted")
    check(any(e["event"] == "progress" for e in ev), f"[{name}] progress events emitted")
    return json.load(open(out, encoding="utf-8")), ev


def sample_sha(c):
    return sha(open(c["sample_path"], "rb").read()) if c.get("sample_path") else None


# ----------------------------------------------------------------------------- NTFS
print("NTFS quick scan")
doc, _ = scan("ntfs", "ntfs.img", "quick")
by_name = {c["file_name"]: c for c in doc["candidates"]}
for live in EXP["ntfs"]["live"]:
    check(live not in by_name, f"live file {live} not reported")
for name, e in EXP["ntfs"]["deleted"].items():
    c = by_name.get(name)
    check(c is not None, f"deleted {name} found")
    if not c:
        continue
    if "path" in e:
        check(c["original_path"] == e["path"], f"{name} path {c['original_path']}")
    if "data" in e:
        check(c["size_bytes"] == e["data"]["len"], f"{name} size {c['size_bytes']}")
        check(sample_sha(c) == e["data"]["sha256"], f"{name} content hash matches")
    if e.get("fragments"):
        check(c["fragment_count"] == e["fragments"], f"{name} fragment_count == {e['fragments']}")
    if e.get("resident"):
        check(c["resident_data_b64"] is not None, f"{name} resident payload present")
    if "allocated_ratio" in e:
        check(abs((c["integrity"]["allocated_ratio"] or 0) - e["allocated_ratio"]) < 1e-6, f"{name} allocated_ratio")
        check(c["integrity"]["header_check"] == "mismatch", f"{name} header mismatch detected")
    if e.get("orphaned"):
        check(c["integrity"]["path_orphaned"] is True, f"{name} orphan detected")

print("NTFS folder scope")
out = os.path.join(WORK, "ntfs_folder.json")
rc, ev, err = run(["scan", "--mode", "quick", "--scope", "folder", "--folder", "\\Users\\OldStuff",
                   "--source", "image:" + os.path.join(IMG, "ntfs.img"), "--out", out])
fd = json.load(open(out))
names = sorted(c["file_name"] for c in fd["candidates"])
check(rc == 0 and names == ["inside_old.png"], f"folder filter returns only OldStuff contents: {names}")

# ----------------------------------------------------------------------------- FAT32
print("FAT32 quick scan")
doc, _ = scan("fat", "fat32.img", "quick")
by_name = {c["file_name"]: c for c in doc["candidates"]}
for live in EXP["fat32"]["live"]:
    check(live not in by_name, f"live file {live} not reported")
for e in EXP["fat32"]["deleted"]:
    c = by_name.get(e["name"])
    check(c is not None, f"deleted {e['name']} found (LFN reconstructed)")
    if not c:
        continue
    check(c["original_path"].endswith(e["path"]), f"{e['name']} path {c['original_path']}")
    check(c["size_bytes"] == e["data"]["len"], f"{e['name']} size")
    check(sample_sha(c) == e["data"]["sha256"], f"{e['name']} content hash matches")
    check(c["integrity"]["allocated_ratio"] == 0, f"{e['name']} clusters free")

print("MBR partition discovery")
doc, _ = scan("mbr", "mbr.img", "quick")
check(len(doc["candidates"]) == len(EXP["fat32"]["deleted"]), f"partitioned image yields {len(doc['candidates'])} candidates")
check(all(c["source_id"].endswith("-p1") for c in doc["candidates"]), "candidate ids carry partition suffix")
check(all(c["absolute_lba"] >= 2048 for c in doc["candidates"]), "absolute LBA includes partition offset")

# ----------------------------------------------------------------------------- Carving
print("Deep scan carving")
doc, _ = scan("carve", "carve.img", "deep")
cands = {c["extents"][0]["offset"]: c for c in doc["candidates"]}
for f in EXP["carve"]["files"]:
    c = cands.get(f["offset"])
    check(c is not None, f"carved {f['extension']} at offset {f['offset']}")
    if not c:
        continue
    check(c["extension"] == f["extension"], f"  extension {c['extension']} == {f['extension']}")
    if f.get("estimated"):
        check(c["integrity"]["size_estimated"] is True, "  truncated JPEG flagged as estimated")
    else:
        check(c["size_bytes"] == f["size"], f"  exact size {c['size_bytes']} == {f['size']}")
        check(c["integrity"]["footer_found"] is True, "  end structure found")
extra = [c for c in doc["candidates"] if c["extents"][0]["offset"] not in {f["offset"] for f in EXP["carve"]["files"]}]
check(not extra, f"no false positives ({[(c['extension'], c['extents'][0]['offset']) for c in extra]})")

print("Type filter")
out = os.path.join(WORK, "carve_pdf.json")
rc, _, _ = run(["scan", "--mode", "deep", "--scope", "drive", "--source", "image:" + os.path.join(IMG, "carve.img"),
                "--types", "pdf,docx", "--out", out])
exts = sorted({c["extension"] for c in json.load(open(out))["candidates"]})
check(exts == ["docx", "pdf"], f"type filter -> {exts}")

print("Deep scan of NTFS (unallocated only)")
doc, _ = scan("ntfs_deep", "ntfs.img", "deep", ["--unallocated-only"])
check(all((c["integrity"]["allocated_ratio"] or 0) < 1 for c in doc["candidates"]), "no carving inside allocated clusters")
check(any(c["extension"] == "pdf" for c in doc["candidates"]), "deleted PDF carved from free space")

# ----------------------------------------------------------------------------- Recover
print("Recover")
fat = json.load(open(os.path.join(WORK, "fat.json")))
dest = os.path.join(WORK, "restored")
manifest = {
    "schema_version": "1.0",
    "destination": dest,
    "preserve_paths": True,
    "items": [dict(c, output_name=c["file_name"], relative_dir=os.path.dirname(c["original_path"].split("\\", 1)[1].replace("\\", "/")))
              for c in fat["candidates"]] +
             [dict(c, output_name="Resident_Todo.txt", relative_dir="") for c in
              json.load(open(os.path.join(WORK, "ntfs.json")))["candidates"] if c["file_name"] == "todo.txt"],
}
mpath = os.path.join(WORK, "manifest.json")
json.dump(manifest, open(mpath, "w"))
rc, ev, err = run(["recover", "--manifest", mpath])
check(rc == 0, f"recover exit 0 ({rc}) {err}")
items = [e for e in ev if e["event"] == "restore_item"]
check(len(items) == len(manifest["items"]) and all(i["status"] == "ok" for i in items), f"{len(items)} items restored ok")
for e in EXP["fat32"]["deleted"]:
    rel = e["path"].lstrip("\\").replace("\\", os.sep)
    p = os.path.join(dest, rel)
    check(os.path.exists(p) and sha(open(p, "rb").read()) == e["data"]["sha256"], f"restored {rel} byte-identical")
check(open(os.path.join(dest, "Resident_Todo.txt"), "rb").read().startswith(b"Remember"), "resident file restored")
# Second run must not overwrite: expect "name (1).ext"
rc, ev, _ = run(["recover", "--manifest", mpath])
check(os.path.exists(os.path.join(dest, "Resident_Todo (1).txt")), "existing files are never overwritten")

print("Destination guard")
bad = dict(manifest, destination=os.path.join(IMG, "fat32.img"))
json.dump(bad, open(mpath, "w"))
rc, ev, _ = run(["recover", "--manifest", mpath])
check(rc == 3 and any(e["event"] == "error" and e["code"] == "UNSAFE_DESTINATION" for e in ev), "destination == source rejected")

print("CLI contract")
rc, ev, _ = run(["scan", "--mode", "bogus", "--scope", "drive", "--source", "x"])
check(rc == 1, "bad mode -> exit 1")
rc, ev, _ = run(["scan", "--mode", "quick", "--scope", "drive", "--source", "image:/nonexistent.img"])
check(rc == 4 and ev and ev[0]["code"] == "SOURCE_NOT_FOUND", "missing source -> exit 4")
p = subprocess.run([ENGINE, "signatures"], capture_output=True, text=True)
check(len(json.loads(p.stdout)["signatures"]) >= 12, "signatures listed")

print()
print(f"{'ALL TESTS PASSED' if not failures else str(len(failures)) + ' FAILURE(S)'}")
sys.exit(1 if failures else 0)
