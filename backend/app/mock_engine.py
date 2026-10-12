"""
mock_engine.py - contract-faithful stand-in for win_recovery_engine.exe.

Lets Track B and Track C develop, test and demo on any OS (macOS / Linux / a
non-elevated Windows laptop) on Day 1. It implements the same Engine interface
as EngineClient and produces:

  * a drive inventory shaped like contracts/schemas/drives.schema.json
  * NDJSON-equivalent events (progress, log, source_done, done, restore_item)
  * a candidates.json that validates against contracts/schemas/candidates.schema.json
  * a *real* simulated disk image per source, so "restore" reads actual bytes
    through the candidates' extents exactly like the C++ engine does.

Payloads are realistic documents (PDF/DOCX/TXT with themed text for the NLP
auto-namer, JPEG photos with EXIF, PNG screenshots) and a share of them are
deliberately damaged (zeroed headers, reused clusters, truncation) so that the
confidence scorer produces High / Medium / Low results.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import shutil
import time
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Optional

EventCallback = Callable[[dict], None]
SECTOR = 512
CLUSTER = 4096


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ============================================================================= payload factories
PDF_THEMES = [
    ("Quarterly Financial Report 2026", "Quarterly revenue grew twelve percent. The financial statements show strong operating margin, "
     "healthy cash flow and lower financial risk. Revenue forecasts for 2026 remain positive across every region."),
    ("Employee Handbook 2025", "This employee handbook explains leave policy, remote work policy, code of conduct, payroll schedule "
     "and employee benefits. Every employee must acknowledge the handbook policies."),
    ("Invoice 4411 Acme Corporation", "Invoice for consulting services delivered to Acme Corporation. Payment terms net thirty days. "
     "Invoice total includes tax. Please reference the invoice number with payment."),
    ("Thesis Draft Disk Forensics", "This thesis studies disk forensics on NTFS volumes. Master file table records, data runs and "
     "signature carving are evaluated for forensic recovery of deleted evidence."),
    ("Medical Lab Results March 2026", "Laboratory results: blood panel within normal range, cholesterol slightly elevated. "
     "The physician recommends diet changes and a follow-up laboratory test in six months."),
]
DOCX_THEMES = [
    ("Board Meeting Minutes October 2025", "The board approved the annual budget. Meeting minutes record votes on the budget, "
     "the hiring plan and the new office lease. The board meeting adjourned at five."),
    ("Project Proposal Solar Irrigation Pilot", "The solar irrigation pilot reduces water usage for farmers. Irrigation pumps run "
     "on solar panels and soil moisture sensors schedule irrigation automatically."),
    ("Lease Agreement Apartment 12B", "This lease agreement between landlord and tenant covers apartment 12B. Rent is due monthly; "
     "the security deposit is refundable at the end of the lease term."),
]
TXT_THEMES = [
    "Wi-Fi setup notes\nRouter admin password changed. Guest network enabled. Firmware updated to version 3.2.\n",
    "Grocery list\nmilk, eggs, bread, coffee, tomatoes, basil, olive oil, rice, lentils\n",
    "Backup checklist\n1. Copy photos to external drive\n2. Verify backup integrity\n3. Store drive offsite\n",
]
FOLDERS = ["Users\\Alice\\Documents", "Users\\Alice\\Documents\\Finance", "Users\\Alice\\Pictures\\Holiday 2025",
           "Users\\Alice\\Desktop", "Projects\\Thesis", "Shared\\Scans"]


def _pdf(title: str, body: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(60, 780, title)
    c.setFont("Helvetica", 11)
    y = 750
    for sentence in (body + " ") .split(". ") * 3:
        if sentence.strip():
            c.drawString(60, y, sentence.strip()[:95])
            y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


def _docx(title: str, body: str) -> bytes:
    def p(text: str) -> str:
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<w:p><w:r><w:t xml:space=\"preserve\">{esc}</w:t></w:r></w:p>"

    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + p(title) + "".join(p(body) for _ in range(4)) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
        z.writestr("docProps/core.xml", f'<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dc="y"><dc:title>{title}'
                                        f'</dc:title></cp:coreProperties>')
    return buf.getvalue()


def _jpeg(rng: random.Random, idx: int) -> bytes:
    from PIL import Image, ImageDraw

    w, h = 640, 427
    base = (rng.randrange(40, 200), rng.randrange(40, 200), rng.randrange(40, 200))
    im = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = rng.randrange(w), rng.randrange(h)
        r = rng.randrange(10, 80)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    d.rectangle([0, h - 60, w, h], fill=(20, 60, 120))
    d.text((12, h - 40), f"Holiday photo #{idx}", fill=(255, 255, 255))
    exif = Image.Exif()
    exif[0x0110] = rng.choice(["Canon EOS 250D", "NIKON D3500", "iPhone 13"])  # Model
    exif[0x0132] = f"2025:{rng.randrange(6, 9):02d}:{rng.randrange(1, 28):02d} {rng.randrange(8, 20):02d}:15:00"  # DateTime
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, exif=exif)
    return buf.getvalue()


def _png(rng: random.Random) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (480, 300), (245, 245, 245))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 480, 28], fill=(30, 58, 138))
    d.text((10, 8), "Screenshot - dashboard", fill=(255, 255, 255))
    for i in range(6):
        hgt = rng.randrange(30, 220)
        d.rectangle([40 + i * 70, 280 - hgt, 90 + i * 70, 280], fill=(37, 99, 235))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ============================================================================= mock engine
class MockEngine:
    is_mock = True

    def __init__(self, state_dir: Path, scan_seconds: Optional[float] = None) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.scan_seconds = float(os.environ.get("MOCK_SCAN_SECONDS", "6")) if scan_seconds is None else scan_seconds
        self.elevated = os.environ.get("MOCK_ELEVATED", "true").lower() != "false"

    # ------------------------------------------------------------------ inventory
    def version(self) -> str:
        return "1.0.0-mock"

    def available(self) -> bool:
        return True

    def check_elevation(self) -> dict:
        return {"elevated": self.elevated, "elevation_type": "full" if self.elevated else "limited",
                "in_administrators_group": True, "error": None}

    _VOLUMES = [
        # letter, label, fs, disk, offset, size, free, type, system
        ("C:", "Windows", "NTFS", 0, 1048576, 511_000_000_000, 120_000_000_000, "fixed", True),
        ("D:", "Data", "NTFS", 1, 1048576, 1_000_202_000_000, 410_000_000_000, "fixed", False),
        ("E:", "USB_BACKUP", "FAT32", 2, 1048576, 31_000_000_000, 9_000_000_000, "removable", False),
        ("F:", "Archive", "NTFS", 1, 1_000_204_000_000, 999_000_000_000, 700_000_000_000, "fixed", False),
    ]
    _DISKS = [
        (0, "Samsung SSD 980 PRO 512GB", "NVMe", "SSD", False, 512_110_190_592),
        (1, "WDC WD20EZRZ-00Z5HB0", "SATA", "HDD", False, 2_000_398_934_016),
        (2, "Seagate Expansion Desk", "USB", "HDD", True, 32_017_047_552),
    ]

    def list_drives(self) -> dict:
        drives = []
        for idx, model, bus, media, removable, size in self._DISKS:
            parts = []
            for n, v in enumerate([v for v in self._VOLUMES if v[3] == idx], 1):
                parts.append({"number": n, "offset_bytes": v[4], "length_bytes": v[5], "style": "GPT", "type_label": "Basic data partition",
                              "mounted_letter": v[0], "source_spec": f"\\\\.\\PhysicalDrive{idx}@p{n}"})
            if idx == 1:  # an unmounted, damaged partition for the "Corrupted Partition" scenario
                parts.append({"number": len(parts) + 1, "offset_bytes": 1_999_300_000_000, "length_bytes": 1_000_000_000,
                              "style": "GPT", "type_label": "Basic data partition", "mounted_letter": None,
                              "source_spec": f"\\\\.\\PhysicalDrive{idx}@p{len(parts) + 1}"})
            drives.append({"index": idx, "device_path": f"\\\\.\\PhysicalDrive{idx}", "model": model, "serial": f"MOCK{idx:04d}",
                           "bus_type": bus, "media_type": media, "removable": removable, "size_bytes": size, "bytes_per_sector": 512,
                           "physical_bytes_per_sector": 4096 if media == "HDD" else 512, "total_sectors": size // 512,
                           "partition_style": "GPT", "accessible": True, "error": None, "partitions": parts})
        vols = []
        for letter, label, fs, disk, off, size, free, dtype, system in self._VOLUMES:
            vols.append({"letter": letter, "root_path": letter + "\\", "device_path": f"\\\\.\\{letter}",
                         "volume_guid_path": f"\\\\?\\Volume{{0000000{disk}-0000-0000-0000-{ord(letter[0]):012d}}}\\",
                         "nt_device_name": f"\\Device\\HarddiskVolume{ord(letter[0]) - 64}", "label": label, "filesystem": fs,
                         "drive_type": dtype, "total_bytes": size, "free_bytes": free, "bytes_per_sector": 512,
                         "cluster_size": 4096 if fs == "NTFS" else 32768, "total_sectors": size // 512, "physical_drive_index": disk,
                         "partition_offset_bytes": off, "is_system_volume": system, "accessible": True, "error": None,
                         "source_spec": letter})
        return {"schema_version": "1.0", "generated_at": _now(), "engine_version": self.version(),
                "elevation": self.check_elevation(), "physical_drives": drives, "volumes": vols}

    # ------------------------------------------------------------------ guard
    def _disk_of_letter(self, letter: str) -> Optional[int]:
        for v in self._VOLUMES:
            if v[0].upper() == letter.upper():
                return v[3]
        return None

    @staticmethod
    def _letter(path: str) -> Optional[str]:
        m = re.match(r"^(?:\\\\[.?]\\)?([A-Za-z]):", path or "")
        return (m.group(1).upper() + ":") if m else None

    def validate_destination(self, destination: str, sources: list[str], required_bytes: int = 0) -> dict:
        conflicts, warnings, code = [], [], "OK"
        dest_letter = self._letter(destination)
        dest_disk = self._disk_of_letter(dest_letter) if dest_letter else None
        if not destination:
            conflicts.append("Destination path is empty.")
            code = "INVALID_PATH"
        for s in sources:
            src_letter = self._letter(s)
            m = re.search(r"PhysicalDrive(\d+)(@p\d+)?", s, re.I)
            if src_letter and dest_letter and src_letter == dest_letter:
                code = "SAME_VOLUME"
                conflicts.append(f"Destination {dest_letter}\\ is the same volume as source {src_letter}\\. Writing there would "
                                 "overwrite the deleted data you are recovering.")
            elif m and dest_disk is not None and int(m.group(1)) == dest_disk and not m.group(2):
                code = "SAME_PHYSICAL_DISK"
                conflicts.append(f"Destination lives on PhysicalDrive{dest_disk}, which is being scanned sector-by-sector.")
            elif src_letter and dest_disk is not None and self._disk_of_letter(src_letter) == dest_disk:
                warnings.append(f"Destination is a different partition on the same physical disk as {src_letter}\\. "
                                "A physically separate drive is recommended.")
        free = 0
        for v in self._VOLUMES:
            if dest_letter and v[0] == dest_letter:
                free = v[6]
        if not dest_letter:
            try:
                free = shutil.disk_usage(Path(destination).anchor or "/").free
            except OSError:
                free = 0
        if not conflicts and required_bytes and free and free < required_bytes:
            code = "INSUFFICIENT_SPACE"
            conflicts.append(f"Destination has {free} bytes free but {required_bytes} are required.")
        return {"safe": not conflicts, "reason_code": "OK" if not conflicts else code, "destination": destination,
                "destination_volume_root": (dest_letter + "\\") if dest_letter else (Path(destination).anchor or "/"),
                "destination_volume_guid": None, "destination_physical_drives": [dest_disk] if dest_disk is not None else [],
                "free_bytes": free, "required_bytes": int(required_bytes), "conflicts": conflicts, "warnings": warnings}

    # ------------------------------------------------------------------ scan
    def _resolve_sources(self, mode: str, scope: str, sources: list[str], folder: Optional[str]) -> list[dict]:
        specs: list[str]
        if scope == "all":
            specs = ["\\\\.\\PhysicalDrive1", "\\\\.\\PhysicalDrive2"] if mode == "deep" else ["D:", "E:", "F:"]
        elif scope == "folder":
            specs = [(folder or "D:")[:2]]
        else:
            specs = sources or ["D:"]
        out = []
        for spec in specs:
            letter = self._letter(spec)
            m = re.search(r"PhysicalDrive(\d+)", spec, re.I)
            if letter:
                v = next((v for v in self._VOLUMES if v[0] == letter), None)
                if v is None:
                    raise ValueError(f"Volume not found: {spec}")
                out.append({"source_id": f"vol-{letter[0]}", "kind": "volume", "letter": letter, "fs": v[2], "disk": v[3],
                            "display": f"{letter}\\ ({v[1]}, {v[2]})", "offset": v[4], "size": v[5],
                            "media": next(d[3] for d in self._DISKS if d[0] == v[3])})
            elif m:
                idx = int(m.group(1))
                d = next((d for d in self._DISKS if d[0] == idx), None)
                if d is None:
                    raise ValueError(f"Physical drive not found: {spec}")
                out.append({"source_id": f"pd{idx}", "kind": "physical", "letter": None, "fs": "Unknown", "disk": idx,
                            "display": f"Disk {idx} ({d[1]})", "offset": 0, "size": d[5], "media": d[3]})
            else:
                raise ValueError(f"Unrecognised source specification: {spec}")
        return out

    def _payload_pool(self, rng: random.Random) -> list[tuple[str, str, bytes]]:
        """(extension, preferred name, bytes)"""
        pool: list[tuple[str, str, bytes]] = []
        for title, body in PDF_THEMES:
            pool.append(("pdf", re.sub(r"\W+", "_", title.split(" 20")[0]).strip("_") + ".pdf", _pdf(title, body)))
        for title, body in DOCX_THEMES:
            pool.append(("docx", re.sub(r"\W+", "_", title)[:40] + ".docx", _docx(title, body)))
        for i, t in enumerate(TXT_THEMES):
            pool.append(("txt", ["wifi_notes.txt", "groceries.txt", "backup_checklist.txt"][i], t.encode()))
        for i in range(8):
            pool.append(("jpg", f"IMG_{2040 + i}.JPG", _jpeg(rng, i)))
        for i in range(2):
            pool.append(("png", f"Screenshot {i + 1}.png", _png(rng)))
        return pool

    def scan(self, *, scan_id: str, mode: str, scope: str, sources: list[str], folder: Optional[str], out_file: Path,
             sample_dir: Optional[Path], cancel_file: Path, types: list[str], allow_non_hdd: bool, unallocated_only: bool,
             on_event: EventCallback) -> int:
        started = _now()
        if not self.elevated:
            on_event({"event": "error", "ts": _now(), "code": "NOT_ELEVATED", "fatal": True,
                      "message": "Administrator rights are required to open raw disk handles. Restart the backend from an "
                                 "elevated (Run as administrator) terminal."})
            return 2
        try:
            srcs = self._resolve_sources(mode, scope, sources, folder)
        except ValueError as exc:
            on_event({"event": "error", "ts": _now(), "code": "SOURCE_NOT_FOUND", "message": str(exc), "fatal": True})
            return 4
        if scope != "all" and not allow_non_hdd:
            for s in srcs:
                if s["media"] == "SSD":
                    on_event({"event": "error", "ts": _now(), "code": "NON_HDD_SOURCE", "fatal": True,
                              "message": f"{s['display']} is on a solid-state drive. TRIM erases deleted blocks, so recovery is "
                                         "unreliable. Re-run with --allow-non-hdd to scan anyway."})
                    return 4
        on_event({"event": "scan_started", "ts": started, "scan_id": scan_id, "mode": mode, "target_scope": scope, "elevated": True,
                  "sources": [{"source_id": s["source_id"], "device_path": s.get("letter") and f"\\\\.\\{s['letter']}" or
                               f"\\\\.\\PhysicalDrive{s['disk']}", "kind": s["kind"], "display_name": s["display"],
                               "media_type": s["media"]} for s in srcs]})

        rng = random.Random(zlib.crc32(scan_id.encode()))
        pool = self._payload_pool(rng)
        wanted = {t.lower().lstrip(".") for t in types or []}
        if wanted:
            pool = [p for p in pool if p[0] in wanted or (p[0] == "jpg" and "jpeg" in wanted)] or pool[:0]
        if sample_dir:
            Path(sample_dir).mkdir(parents=True, exist_ok=True)

        candidates: list[dict] = []
        source_docs: list[dict] = []
        cancelled = False
        steps = 20
        for si, s in enumerate(srcs):
            image = self.state_dir / f"{scan_id}_{s['source_id']}.img"
            img = bytearray()
            n_files = 0 if not pool else (rng.randint(18, 26) if mode == "quick" else rng.randint(14, 20))
            t_src = time.time()
            local: list[dict] = []
            phase = "carve" if mode == "deep" else ("fat_directories" if s["fs"] == "FAT32" else "mft")
            method = "carve" if mode == "deep" else ("fat" if s["fs"] == "FAT32" else "mft")
            src_pool = [p for p in pool if p[0] != "txt"] if method == "carve" else pool
            # Every document theme appears at least once per source (so demos and tests can always find e.g. the
            # invoice); the remaining slots are filled at random, as before.
            picks = [p for p in src_pool if p[0] not in ("jpg", "png")]
            rng.shuffle(picks)
            picks += [rng.choice(src_pool) for _ in range(max(0, n_files - len(picks)))] if src_pool else []
            for k, (ext, name, data) in enumerate(picks):
                local.append(self._place(rng, img, s, method, ext, name, data, image, k, folder if scope == "folder" else None))
            # pad the image so it looks like a disk region
            img += bytes(CLUSTER * 4)
            image.write_bytes(bytes(img))
            for step in range(1, steps + 1):
                if cancel_file.exists():
                    cancelled = True
                    on_event({"event": "log", "ts": _now(), "level": "warning", "message": "Cancel file detected - stopping scan.",
                              "source_id": s["source_id"]})
                    break
                time.sleep(self.scan_seconds / steps / max(1, len(srcs)))
                total = s["size"] if mode == "deep" else 250_000
                done = int(total * step / steps)
                on_event({"event": "progress", "ts": _now(), "scan_id": scan_id, "source_id": s["source_id"], "source_index": si,
                          "source_count": len(srcs), "phase": phase, "unit": "bytes" if mode == "deep" else "records",
                          "bytes_done": done, "bytes_total": total, "percent": round(step * 100 / steps, 2),
                          "overall_percent": round((si + step / steps) * 100 / len(srcs), 2),
                          "candidates_found": len(candidates) + int(len(local) * step / steps),
                          "rate_mb_s": 142.5 if mode == "deep" else 0.0, "eta_seconds": round((steps - step) * self.scan_seconds / steps),
                          "read_errors": 1 if (mode == "deep" and s["source_id"] == "pd2" and step > 10) else 0})
            if sample_dir:
                for c in local:
                    raw = self._read_extents(image, c)
                    p = Path(sample_dir) / f"{c['id']}.{c['extension'] or 'bin'}"
                    p.write_bytes(raw[: 2 * 1024 * 1024])
                    c["sample_path"], c["sample_bytes"] = str(p), min(len(raw), 2 * 1024 * 1024)
            candidates.extend(local)
            status = "cancelled" if cancelled else "completed"
            read_errors = 1 if (mode == "deep" and s["source_id"] == "pd2") else 0
            source_docs.append({
                "source_id": s["source_id"], "device_path": str(image), "kind": "image", "display_name": s["display"] + " [simulated]",
                "letter": s["letter"], "volume_guid_path": None, "filesystem": s["fs"] if mode == "quick" else "Unknown",
                "media_type": s["media"], "sector_size": SECTOR, "cluster_size": CLUSTER, "total_bytes": s["size"],
                "bytes_scanned": s["size"] if not cancelled else s["size"] // 2, "partition_offset_bytes": s["offset"],
                "volume_offset_in_device": 0, "physical_drive_index": s["disk"],
                "mft_records_scanned": 250_000 if method == "mft" else 0, "deleted_entries_found": len(local) + 7,
                "candidates": len(local), "read_errors": read_errors, "status": status, "error": None,
                "elapsed_seconds": round(time.time() - t_src, 2)})
            on_event({"event": "source_done", "ts": _now(), "source_id": s["source_id"], "status": status, "error": None,
                      "candidates": len(local), "read_errors": read_errors, "elapsed_seconds": round(time.time() - t_src, 2)})
            if cancelled:
                break

        by_method: dict[str, int] = {}
        by_ext: dict[str, int] = {}
        for c in candidates:
            by_method[c["method"]] = by_method.get(c["method"], 0) + 1
            by_ext[c["extension"] or "(none)"] = by_ext.get(c["extension"] or "(none)", 0) + 1
        doc = {
            "schema_version": "1.0",
            "engine": {"name": "win_recovery_engine", "version": self.version(), "platform": "mock"},
            "scan": {"scan_id": scan_id, "mode": mode, "target_scope": scope, "folder_path": folder, "started_at": started,
                     "finished_at": _now(), "status": "cancelled" if cancelled else "completed", "elevated": True,
                     "source_specs": sources, "type_filter": sorted(wanted),
                     "options": {"sector_aligned": True, "unallocated_only": bool(unallocated_only or (scope == "folder" and mode == "deep")),
                                 "verify_headers": True, "sample_dir": str(sample_dir) if sample_dir else None,
                                 "sample_bytes": 2 * 1024 * 1024, "max_candidates": 500000}},
            "sources": source_docs,
            "statistics": {"total_candidates": len(candidates), "total_bytes": sum(c["size_bytes"] for c in candidates),
                           "read_errors": sum(s["read_errors"] for s in source_docs),
                           "samples_bytes_written": sum(c["sample_bytes"] for c in candidates), "by_method": by_method, "by_extension": by_ext},
            "candidates": candidates,
        }
        tmp = Path(str(out_file) + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, out_file)
        on_event({"event": "done", "ts": _now(), "command": "scan", "scan_id": scan_id, "status": doc["scan"]["status"],
                  "candidates_file": str(out_file), "candidates": len(candidates), "elapsed_seconds": 0})
        return 5 if cancelled else 0

    def _place(self, rng: random.Random, img: bytearray, s: dict, method: str, ext: str, name: str, data: bytes, image: Path,
               k: int, folder: Optional[str]) -> dict:
        """Write `data` into the simulated disk (possibly fragmented / damaged) and build a contract candidate."""
        roll = rng.random()
        damage = "none"
        if roll < 0.12:
            damage = "header_overwritten"
        elif roll < 0.22:
            damage = "partial_overwrite"
        elif roll < 0.30 and method == "carve":
            damage = "truncated"
        if method == "carve" and damage == "header_overwritten":
            damage = "partial_overwrite"  # a carved hit by definition still has its header
        payload = bytearray(data)
        size = len(payload)
        if damage == "truncated":
            payload = payload[: max(600, size // 3)]
        if damage == "header_overwritten":
            payload[: min(len(payload), 4096)] = bytes(min(len(payload), 4096))
        if damage == "partial_overwrite":
            start = len(payload) // 2
            payload[start:] = bytes(rng.getrandbits(8) for _ in range(len(payload) - start))

        # gap of free space between files (sector aligned)
        img += bytes(CLUSTER * rng.randint(1, 6))
        fragments = 1
        if method == "mft" and size > 3 * CLUSTER and rng.random() < 0.3:
            fragments = rng.randint(2, 3)
        extents = []
        written = 0
        per = -(-len(payload) // fragments)
        per = -(-per // CLUSTER) * CLUSTER
        for f in range(fragments):
            chunk = payload[written: written + per]
            if not chunk:
                break
            off = len(img)
            img += chunk + bytes((-len(chunk)) % CLUSTER)
            extents.append({"offset": off, "length": len(chunk), "sparse": False})
            written += len(chunk)
            if f < fragments - 1:
                img += bytes(CLUSTER * rng.randint(2, 8))
        if method == "carve":
            size = len(payload) if damage != "truncated" else min(size, max(len(payload), 2 * 1024 * 1024))
            extents = [{"offset": extents[0]["offset"], "length": size, "sparse": False}]
        first = extents[0]["offset"]
        lba = first // SECTOR
        resident = None
        if method == "mft" and size < 700 and ext == "txt":
            resident = base64.b64encode(bytes(payload)).decode()
            extents = []
        folder_path = (folder.rstrip("\\") if folder else f"{s['letter'] or ''}\\{rng.choice(FOLDERS)}") if method != "carve" else None
        file_name = None if method == "carve" else (name if method == "mft" or rng.random() < 0.7 else "_" + name[1:])
        cid = {"mft": f"{s['source_id']}-mft-{40000 + k * 7 + rng.randint(0, 6)}",
               "fat": f"{s['source_id']}-fat-{first + 0x400000:x}",
               "carve": f"{s['source_id']}-carve-{first:x}"}[method]
        ratio: Optional[float] = None
        if method in ("mft", "fat"):
            ratio = {"none": 0.0, "header_overwritten": 1.0 if rng.random() < 0.5 else 0.25, "partial_overwrite": 0.5,
                     "truncated": 0.0}[damage]
        else:
            ratio = 0.0
        notes = []
        if method == "carve":
            notes.append("carved: original file name and folder are not stored in file content")
        if damage == "header_overwritten":
            notes.append("header_zeroed: first sector is all zeros (likely overwritten or wiped)")
        if method == "fat" and size > CLUSTER:
            notes.append("fat_contiguity_assumed: FAT chain is cleared on delete; fragmented files will be corrupt")
        if resident:
            notes.append("resident_data: payload stored inside the MFT record")
        sig = {"jpg": "jpg", "png": "png", "pdf": "pdf", "docx": "zip"}.get(ext)
        header_check = "unknown" if sig is None else ("mismatch" if damage == "header_overwritten" else "match")
        if sig is None and damage == "header_overwritten":
            header_check = "mismatch"
        day = rng.randint(1, 28)
        ts = {"created": f"2025-0{rng.randint(1, 9)}-{day:02d}T09:12:00Z", "modified": f"2025-10-{day:02d}T17:45:00Z",
              "mft_modified": f"2026-0{rng.randint(1, 3)}-{day:02d}T11:0{rng.randint(0, 9)}:00Z" if method == "mft" else None,
              "accessed": f"2026-01-{day:02d}T08:00:00Z"}
        if method == "carve":
            ts = {"created": None, "modified": None, "mft_modified": None, "accessed": None}
        mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png", "txt": "text/plain",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[ext]
        return {
            "id": cid, "source_id": s["source_id"], "source_device": str(image), "source_kind": "image", "method": method,
            "file_name": file_name, "original_path": f"{folder_path}\\{file_name}" if file_name else None, "extension": ext,
            "signature": None if header_check == "mismatch" else sig, "mime_type": mime, "size_bytes": size, "lba": lba,
            "absolute_lba": lba + s["offset"] // SECTOR, "sector_size": SECTOR, "extents": extents,
            "fragment_count": len(extents), "resident_data_b64": resident, "timestamps": ts,
            "mft": ({"record_number": int(cid.rsplit("-", 1)[1]), "sequence": 3, "parent_record": 1234, "record_flags": 0,
                     "file_attributes": 32, "is_compressed": False, "is_encrypted": False, "is_sparse": False, "fixup_error": False}
                    if method == "mft" else None),
            "integrity": {"header_check": header_check if method != "carve" else "match",
                          "footer_found": None if method != "carve" else damage != "truncated",
                          "size_estimated": method == "carve" and damage == "truncated",
                          "size_source": "metadata" if method != "carve" else ("estimated" if damage == "truncated" else "footer"),
                          "truncated": method == "carve" and damage == "truncated", "allocated_ratio": ratio,
                          "path_orphaned": False},
            "partition_hint": s["letter"] if method == "carve" else None, "notes": notes, "sample_path": None, "sample_bytes": 0,
        }

    @staticmethod
    def _read_extents(image: Path, c: dict) -> bytes:
        if c.get("resident_data_b64"):
            return base64.b64decode(c["resident_data_b64"])[: c["size_bytes"]]
        out = bytearray()
        with open(image, "rb") as fh:
            for e in c["extents"]:
                if len(out) >= c["size_bytes"]:
                    break
                n = min(e["length"], c["size_bytes"] - len(out))
                if e.get("sparse"):
                    out += bytes(n)
                else:
                    fh.seek(e["offset"])
                    chunk = fh.read(n)
                    out += chunk + bytes(n - len(chunk))
        return bytes(out)

    # ------------------------------------------------------------------ recover
    def recover(self, *, manifest_path: Path, cancel_file: Path, on_event: EventCallback) -> int:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        dest = Path(manifest["destination"])
        items = manifest.get("items", [])
        required = sum(int(i.get("size_bytes", 0)) for i in items)
        letters = sorted({ln for i in items for ln in [self._letter(i.get("original_path") or "")] if ln})
        chk = self.validate_destination(str(dest), letters, required)
        on_event({"event": "destination_check", "result": chk})
        if not chk["safe"]:
            on_event({"event": "error", "ts": _now(), "code": "UNSAFE_DESTINATION", "message": chk["conflicts"][0], "fatal": True})
            return 3
        dest.mkdir(parents=True, exist_ok=True)
        restored = failed = written_total = 0
        for idx, it in enumerate(items, 1):
            if cancel_file.exists():
                on_event({"event": "done", "ts": _now(), "command": "recover", "status": "cancelled", "restored": restored,
                          "failed": failed, "bytes_written": written_total, "destination": str(dest)})
                return 5
            rel = Path(*[re.sub(r'[<>:"|?*\x00-\x1f]', "_", p) for p in re.split(r"[\\/]+", it.get("relative_dir") or "")
                         if p and p not in (".", "..")]) if it.get("relative_dir") else Path()
            out_dir = dest / rel
            out_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", it.get("output_name") or f"{it['id']}.{it.get('extension') or 'bin'}")
            target = out_dir / name
            n = 1
            while target.exists():
                target = out_dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            try:
                data = self._read_extents(Path(it["source_device"]), it)
                target.write_bytes(data)
                restored += 1
                written_total += len(data)
                on_event({"event": "restore_item", "ts": _now(), "id": it["id"], "status": "ok", "output_path": str(target),
                          "bytes_written": len(data), "read_errors": 0, "error": None})
            except OSError as exc:
                failed += 1
                on_event({"event": "restore_item", "ts": _now(), "id": it["id"], "status": "failed", "output_path": None,
                          "bytes_written": 0, "read_errors": 0, "error": str(exc)})
            on_event({"event": "progress", "ts": _now(), "phase": "restore", "bytes_done": written_total, "bytes_total": required,
                      "percent": round(100 * written_total / max(1, required), 2), "items_done": idx, "items_total": len(items)})
            time.sleep(0.02)
        on_event({"event": "done", "ts": _now(), "command": "recover",
                  "status": "completed" if not failed else ("partial" if restored else "failed"), "restored": restored,
                  "failed": failed, "bytes_written": written_total, "destination": str(dest)})
        return 0 if restored or not failed else 10
