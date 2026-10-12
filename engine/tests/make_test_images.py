#!/usr/bin/env python3
"""
make_test_images.py - builds deterministic disk images for engine regression tests.

Produces (in the output directory):
  carve.img   raw "formatted" disk with real JPEG/PNG/GIF/BMP/PDF/DOCX/XLSX/ZIP/SQLite/WAV/MP4
              files placed on sector boundaries between random noise
  fat32.img   FAT32 volume with live + deleted files (LFN entries, deleted sub-directory)
  ntfs.img    minimal but structurally valid NTFS volume: $MFT (with fixups), $Bitmap,
              live + deleted files, fragmented runs, resident data, $ATTRIBUTE_LIST,
              deleted parent directory and a reused (orphaned) parent
  mbr.img     MBR-partitioned disk containing the FAT32 volume as partition 1
  expected.json  ground truth consumed by run_engine_tests.py

Only the Python standard library plus Pillow / reportlab / python-docx / openpyxl are used.
"""
import io
import json
import os
import random
import sqlite3
import struct
import sys
import tempfile
import wave
import zipfile

OUT = sys.argv[1] if len(sys.argv) > 1 else "test_images"
os.makedirs(OUT, exist_ok=True)
rng = random.Random(1234)
SECTOR = 512


# --------------------------------------------------------------------------- sample files
def make_jpeg(color=(200, 30, 30), size=(320, 200), text=None):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    for i in range(0, size[0], 16):
        d.line([(i, 0), (size[0] - i, size[1])], fill=(i % 255, 120, 255 - i % 255), width=2)
    if text:
        d.text((10, 10), text, fill=(255, 255, 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def make_png():
    from PIL import Image
    im = Image.new("RGBA", (128, 96), (10, 120, 200, 255))
    for x in range(128):
        im.putpixel((x, x % 96), (255, 255, 0, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make_gif():
    from PIL import Image
    frames = [Image.new("P", (64, 64), i * 40) for i in range(3)]
    b = io.BytesIO()
    frames[0].save(b, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return b.getvalue()


def make_bmp():
    from PIL import Image
    im = Image.new("RGB", (50, 40), (0, 200, 0))
    b = io.BytesIO()
    im.save(b, "BMP")
    return b.getvalue()


def make_pdf(title="Quarterly Financial Report 2026"):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 760, title)
    c.setFont("Helvetica", 11)
    y = 730
    for line in [
        "Financial report summary for fiscal year 2026.",
        "Revenue grew in every region; the financial outlook remains strong.",
        "Quarterly revenue, operating margin and financial statements are attached.",
    ] * 3:
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.save()
    return b.getvalue()


def make_docx():
    import docx
    d = docx.Document()
    d.add_heading("Project Proposal: Solar Irrigation Pilot", 0)
    for _ in range(5):
        d.add_paragraph("The solar irrigation pilot reduces water usage for farmers. "
                        "Irrigation scheduling uses soil moisture sensors and solar pumps.")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def make_xlsx():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Cost"])
    for i in range(20):
        ws.append([f"Line {i}", i * 10.5])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def make_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("readme.txt", "hello archive " * 50)
        z.writestr("data/values.csv", "a,b\n1,2\n" * 100)
    return b.getvalue()


def make_sqlite():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    con = sqlite3.connect(p)
    con.execute("create table t(id integer primary key, v text)")
    con.executemany("insert into t(v) values (?)", [("row %d" % i,) for i in range(500)])
    con.commit()
    con.close()
    data = open(p, "rb").read()
    os.unlink(p)
    return data


def make_wav():
    b = io.BytesIO()
    w = wave.open(b, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(bytes(rng.getrandbits(8) for _ in range(16000)))
    w.close()
    return b.getvalue()


def make_mp4():
    def box(t, payload):
        return struct.pack(">I", 8 + len(payload)) + t + payload
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    moov = box(b"moov", box(b"mvhd", bytes(100)))
    mdat = box(b"mdat", bytes(rng.getrandbits(8) for _ in range(5000)))
    return ftyp + moov + mdat


def noise(n):
    # Random bytes that cannot accidentally contain magic numbers at sector starts.
    data = bytearray(rng.getrandbits(8) for _ in range(n))
    for i in range(0, n, SECTOR):
        data[i] = 0x00
    return bytes(data)


def align(n, a=SECTOR):
    return (n + a - 1) // a * a


# --------------------------------------------------------------------------- carve image
def build_carve_image():
    files = [
        ("jpg", make_jpeg(text="Holiday 2026")), ("png", make_png()), ("gif", make_gif()), ("bmp", make_bmp()),
        ("pdf", make_pdf()), ("docx", make_docx()), ("xlsx", make_xlsx()), ("zip", make_zip()),
        ("sqlite", make_sqlite()), ("wav", make_wav()), ("mp4", make_mp4()), ("jpg", make_jpeg((20, 20, 220))),
    ]
    img = bytearray()
    img += noise(64 * 1024)
    expected = []
    for ext, data in files:
        off = len(img)
        img += data
        img += b"\x00" * (align(len(img)) - len(img))
        expected.append({"offset": off, "size": len(data), "extension": ext})
        img += noise(rng.randint(2, 40) * SECTOR)
    # A truncated JPEG (header + some data, no EOI) - must be reported as estimated.
    off = len(img)
    trunc = make_jpeg((50, 50, 50))[:3000]
    img += trunc + b"\x00" * (align(len(trunc)) - len(trunc))
    expected.append({"offset": off, "size": None, "extension": "jpg", "estimated": True})
    img += b"\x00" * (4 * 1024 * 1024 - len(img) % (1024 * 1024))
    open(os.path.join(OUT, "carve.img"), "wb").write(img)
    return {"files": expected, "payloads": {f"{e['offset']}": None for e in expected}}


# --------------------------------------------------------------------------- FAT32 image
def lfn_checksum(name11):
    s = 0
    for b in name11:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


def build_fat32(total_mb=64):
    bps, spc, reserved, nfats = 512, 1, 32, 2
    total_sectors = total_mb * 1024 * 1024 // bps
    clusters = total_sectors  # upper bound for FAT size computation
    fatsz = (clusters * 4 + bps - 1) // bps
    first_data = reserved + nfats * fatsz
    nclusters = (total_sectors - first_data) // spc
    cs = bps * spc
    img = bytearray(total_sectors * bps)
    fat = [0] * (nclusters + 2)
    fat[0], fat[1] = 0x0FFFFFF8, 0x0FFFFFFF

    bs = bytearray(512)
    bs[0:3] = b"\xEB\x58\x90"
    bs[3:11] = b"MSWIN4.1"
    struct.pack_into("<HBHBHHBHHHII", bs, 0x0B, bps, spc, reserved, nfats, 0, 0, 0xF8, 0, 63, 255, 0, total_sectors)
    struct.pack_into("<IHHIHH", bs, 0x24, fatsz, 0, 0, 2, 1, 6)
    bs[0x40] = 0x80
    bs[0x42] = 0x29
    struct.pack_into("<I", bs, 0x43, 0x1234ABCD)
    bs[0x47:0x52] = b"TESTVOL    "
    bs[0x52:0x5A] = b"FAT32   "
    bs[510:512] = b"\x55\xAA"
    img[0:512] = bs

    next_free = [3]  # cluster 2 = root

    def alloc(n):
        start = next_free[0]
        for i in range(n):
            fat[start + i] = start + i + 1 if i < n - 1 else 0x0FFFFFFF
        next_free[0] += n
        return start

    def coff(c):
        return (first_data + (c - 2) * spc) * bps

    def write_data(data):
        n = max(1, (len(data) + cs - 1) // cs)
        c = alloc(n)
        img[coff(c):coff(c) + len(data)] = data
        return c

    def sfn(name):
        base, _, ext = name.upper().partition(".")
        base = "".join(ch for ch in base if ch.isalnum())[:6] + "~1" if len(base) > 8 else base
        return (base.ljust(8)[:8] + ext.ljust(3)[:3]).encode("ascii")

    def entries_for(name, attr, cluster, size):
        name11 = sfn(name)
        out = []
        units = name.encode("utf-16-le")
        chars = [units[i:i + 2] for i in range(0, len(units), 2)] + [b"\x00\x00"]
        while len(chars) % 13:
            chars.append(b"\xFF\xFF")
        parts = [chars[i:i + 13] for i in range(0, len(chars), 13)]
        chk = lfn_checksum(name11)
        for idx in range(len(parts), 0, -1):
            p = parts[idx - 1]
            e = bytearray(32)
            e[0] = idx | (0x40 if idx == len(parts) else 0)
            e[1:11] = b"".join(p[0:5])
            e[11] = 0x0F
            e[13] = chk
            e[14:26] = b"".join(p[5:11])
            e[28:32] = b"".join(p[11:13])
            out.append(e)
        s = bytearray(32)
        s[0:11] = name11
        s[11] = attr
        struct.pack_into("<HHHHHHHI", s, 14, 0x6000, 0x5A21, 0x5A21, cluster >> 16, 0x6000, 0x5A21, cluster & 0xFFFF, size)
        out.append(s)
        return out

    def write_dir(cluster, entries):
        raw = b"".join(bytes(e) for e in entries)
        img[coff(cluster):coff(cluster) + len(raw)] = raw

    fat[2] = 0x0FFFFFFF
    payload = {}
    # Sub-directory (live) and a sub-directory that will be deleted.
    docs_c = alloc(1)
    old_c = alloc(1)
    f_report = make_pdf("Annual Budget Planning 2025")
    f_photo = make_jpeg((30, 160, 90), text="Beach")
    f_notes = ("Meeting notes about the migration plan. " * 40).encode()
    f_keep = b"this file is still alive" * 10
    f_old = make_png()
    c_report = write_data(f_report)
    c_photo = write_data(f_photo)
    c_notes = write_data(f_notes)
    c_keep = write_data(f_keep)
    c_old = write_data(f_old)

    root = []
    vol = bytearray(32)
    vol[0:11] = b"TESTVOL    "
    vol[11] = 0x08
    root.append(vol)
    root += entries_for("Documents", 0x10, docs_c, 0)
    root += entries_for("OldProjects", 0x10, old_c, 0)
    root += entries_for("Holiday Photo.jpg", 0x20, c_photo, len(f_photo))
    root += entries_for("keep_me.txt", 0x20, c_keep, len(f_keep))
    docs = []
    dot = bytearray(32); dot[0:11] = b".          "; dot[11] = 0x10
    dotdot = bytearray(32); dotdot[0:11] = b"..         "; dotdot[11] = 0x10
    docs += [dot, dotdot]
    docs += entries_for("Budget Report 2025.pdf", 0x20, c_report, len(f_report))
    docs += entries_for("migration-notes.txt", 0x20, c_notes, len(f_notes))
    old = [bytearray(dot), bytearray(dotdot)]
    old += entries_for("diagram.png", 0x20, c_old, len(f_old))

    # ---- delete: photo (root), report (Documents), notes (Documents), whole OldProjects dir
    def delete(entries, name11_prefix_match):
        # mark the SFN and all its preceding LFN entries as deleted
        for i, e in enumerate(entries):
            if e[11] != 0x0F and bytes(e[0:11]) == name11_prefix_match:
                j = i
                e[0] = 0xE5
                j -= 1
                while j >= 0 and entries[j][11] == 0x0F:
                    entries[j][0] = 0xE5
                    j -= 1
                return
        raise RuntimeError("entry not found")

    def free_chain(c):
        while 2 <= c < 0x0FFFFFF8:
            n = fat[c]
            fat[c] = 0
            c = n

    delete(root, sfn("Holiday Photo.jpg")); free_chain(c_photo)
    delete(docs, sfn("Budget Report 2025.pdf")); free_chain(c_report)
    delete(docs, sfn("migration-notes.txt")); free_chain(c_notes)
    # Delete the diagram inside OldProjects and then the directory itself.
    delete(old, sfn("diagram.png")); free_chain(c_old)
    delete(root, sfn("OldProjects")); free_chain(old_c)
    write_dir(2, root)
    write_dir(docs_c, docs)
    write_dir(old_c, old)

    raw_fat = b"".join(struct.pack("<I", v) for v in fat)
    raw_fat += b"\x00" * (fatsz * bps - len(raw_fat))
    for i in range(nfats):
        start = (reserved + i * fatsz) * bps
        img[start:start + len(raw_fat)] = raw_fat
    open(os.path.join(OUT, "fat32.img"), "wb").write(img)

    # MBR-wrapped copy (partition 1 at LBA 2048) for partition-table parsing tests.
    mbr = bytearray(512)
    struct.pack_into("<BBBBBBBBII", mbr, 446, 0x00, 0, 0, 0, 0x0C, 0, 0, 0, 2048, total_sectors)
    mbr[510:512] = b"\x55\xAA"
    open(os.path.join(OUT, "mbr.img"), "wb").write(bytes(mbr) + b"\x00" * (2048 * 512 - 512) + bytes(img))

    return {
        "deleted": [
            {"name": "Holiday Photo.jpg", "path": "\\Holiday Photo.jpg", "data": f_photo},
            {"name": "Budget Report 2025.pdf", "path": "\\Documents\\Budget Report 2025.pdf", "data": f_report},
            {"name": "migration-notes.txt", "path": "\\Documents\\migration-notes.txt", "data": f_notes},
            {"name": "diagram.png", "path": "\\OldProjects\\diagram.png", "data": f_old},
        ],
        "live": ["keep_me.txt"],
    }


# --------------------------------------------------------------------------- NTFS image
CS = 4096
REC = 1024
NREC = 64
MFT_LCN = 4
MIRR_LCN = 2
TOTAL_CLUSTERS = 4096  # 16 MiB volume


def ft(year=2026):
    # FILETIME for Jan 1st of year
    import calendar
    secs = calendar.timegm((year, 1, 15, 10, 30, 0, 0, 0, 0))
    return (secs + 11644473600) * 10_000_000


def attr_resident(atype, value, attr_id):
    hdr_len = 0x18
    total = (hdr_len + len(value) + 7) // 8 * 8
    a = bytearray(total)
    struct.pack_into("<IIBBHHH", a, 0, atype, total, 0, 0, 0x18, 0, attr_id)
    struct.pack_into("<IHBB", a, 0x10, len(value), hdr_len, 0, 0)
    a[hdr_len:hdr_len + len(value)] = value
    return bytes(a)


def encode_runs(runs):
    """runs: list of (lcn or None, length)."""
    out = bytearray()
    prev = 0
    for lcn, length in runs:
        lb = length.to_bytes(8, "little").rstrip(b"\x00") or b"\x00"
        if lcn is None:
            out.append(len(lb))
            out += lb
            continue
        delta = lcn - prev
        prev = lcn
        n = 1
        while not (-(1 << (8 * n - 1)) <= delta < (1 << (8 * n - 1))):
            n += 1
        ob = delta.to_bytes(n, "little", signed=True)
        out.append(len(lb) | (len(ob) << 4))
        out += lb + ob
    out.append(0)
    return bytes(out)


def attr_nonresident(atype, runs, real_size, attr_id, start_vcn=0, flags=0):
    rl = encode_runs(runs)
    total_clusters = sum(l for _, l in runs)
    total = (0x40 + len(rl) + 7) // 8 * 8
    a = bytearray(total)
    struct.pack_into("<IIBBHHH", a, 0, atype, total, 1, 0, 0x40, flags, attr_id)
    struct.pack_into("<QQHH", a, 0x10, start_vcn, start_vcn + total_clusters - 1, 0x40, 0)
    struct.pack_into("<QQQ", a, 0x28, total_clusters * CS, real_size, real_size)
    a[0x40:0x40 + len(rl)] = rl
    return bytes(a)


def std_info(attrs=0x20):
    v = struct.pack("<QQQQI", ft(2025), ft(2026), ft(2026), ft(2026), attrs) + bytes(12)
    return attr_resident(0x10, v, 0)


def file_name(parent_rec, parent_seq, name, size=0, ns=3, is_dir=False):
    enc = name.encode("utf-16-le")
    v = struct.pack("<QQQQQQQII", (parent_seq << 48) | parent_rec, ft(2025), ft(2026), ft(2026), ft(2026),
                    (size + CS - 1) // CS * CS, size, 0x10000000 if is_dir else 0x20, 0)
    v += struct.pack("<BB", len(name), ns) + enc
    return attr_resident(0x30, v, 1)


def mft_record(rec_no, seq, flags, attrs, base_ref=0):
    r = bytearray(REC)
    r[0:4] = b"FILE"
    usa_ofs, usa_cnt = 0x30, REC // 512 + 1
    struct.pack_into("<HHQHHHH", r, 4, usa_ofs, usa_cnt, 0, seq, 1, 0x38, flags)
    body = b"".join(attrs) + struct.pack("<I", 0xFFFFFFFF) + bytes(4)
    struct.pack_into("<II", r, 0x18, 0x38 + len(body), REC)
    struct.pack_into("<Q", r, 0x20, base_ref)
    struct.pack_into("<H", r, 0x28, 7)
    struct.pack_into("<I", r, 0x2C, rec_no)
    r[0x38:0x38 + len(body)] = body
    # fixups: update sequence number 0x0007
    usn = 0x0007
    struct.pack_into("<H", r, usa_ofs, usn)
    for i in range(1, usa_cnt):
        pos = i * 512 - 2
        r[usa_ofs + 2 * i:usa_ofs + 2 * i + 2] = r[pos:pos + 2]
        struct.pack_into("<H", r, pos, usn)
    return bytes(r)


def build_ntfs():
    img = bytearray(TOTAL_CLUSTERS * CS)
    bitmap = bytearray(TOTAL_CLUSTERS // 8)

    def mark(lcn, n, used=True):
        for c in range(lcn, lcn + n):
            if used:
                bitmap[c // 8] |= 1 << (c % 8)
            else:
                bitmap[c // 8] &= ~(1 << (c % 8))

    mark(0, 4)                        # boot + mirror area
    mft_clusters = NREC * REC // CS
    mark(MFT_LCN, mft_clusters)
    next_lcn = [64]

    def place(data, fragments=1):
        """Write data into `fragments` non-contiguous runs; returns run list."""
        nclus = max(1, (len(data) + CS - 1) // CS)
        runs = []
        per = max(1, nclus // fragments)
        remaining, pos = nclus, 0
        while remaining > 0:
            n = per if len(runs) < fragments - 1 else remaining
            lcn = next_lcn[0]
            chunk = data[pos * CS:(pos + n) * CS]
            img[lcn * CS:lcn * CS + len(chunk)] = chunk
            runs.append((lcn, n))
            next_lcn[0] += n + 7   # leave gaps so runs are fragmented
            pos += n
            remaining -= n
        return runs

    records = {}
    # ---- system records
    bitmap_lcn = 40
    records[0] = mft_record(0, 1, 0x01, [std_info(0x06), file_name(5, 5, "$MFT"),
                                          attr_nonresident(0x80, [(MFT_LCN, mft_clusters)], NREC * REC, 2)])
    records[5] = mft_record(5, 5, 0x03, [std_info(0x06), file_name(5, 5, ".", is_dir=True)])
    mark(bitmap_lcn, 1)
    records[6] = mft_record(6, 6, 0x01, [std_info(0x06), file_name(5, 5, "$Bitmap"),
                                          attr_nonresident(0x80, [(bitmap_lcn, 1)], len(bitmap), 2)])
    # ---- directories
    records[16] = mft_record(16, 1, 0x03, [std_info(0x10), file_name(5, 5, "Users", is_dir=True)])
    records[17] = mft_record(17, 1, 0x03, [std_info(0x10), file_name(16, 1, "Docs", is_dir=True)])
    records[18] = mft_record(18, 3, 0x02, [std_info(0x10), file_name(16, 1, "OldStuff", is_dir=True)])  # deleted dir (seq 2->3)
    records[19] = mft_record(19, 9, 0x01, [std_info(), file_name(5, 5, "reused-record.txt")])           # was a dir (seq 4), now a live file

    payload = {}
    # ---- deleted file, contiguous
    pdf = make_pdf("Board Meeting Minutes Strategy 2026")
    runs = place(pdf)
    records[20] = mft_record(20, 2, 0x00, [std_info(), file_name(17, 1, "minutes.pdf", len(pdf)),
                                            attr_nonresident(0x80, runs, len(pdf), 2)])
    payload["minutes.pdf"] = pdf
    # ---- deleted file, fragmented (3 runs)
    jpg = make_jpeg((90, 10, 140), (640, 480), "Fragmented")
    runs = place(jpg, fragments=3)
    records[21] = mft_record(21, 2, 0x00, [std_info(), file_name(17, 1, "photo_fragmented.jpg", len(jpg)),
                                            attr_nonresident(0x80, runs, len(jpg), 2)])
    payload["photo_fragmented.jpg"] = jpg
    # ---- deleted file, resident data
    small = b"Remember to back up the drive before upgrading Windows.\r\n"
    records[22] = mft_record(22, 2, 0x00, [std_info(), file_name(17, 1, "todo.txt", len(small)),
                                            attr_resident(0x80, small, 2)])
    payload["todo.txt"] = small
    # ---- live file (must NOT be reported)
    live = b"live data" * 100
    runs = place(live)
    mark(runs[0][0], runs[0][1])
    records[23] = mft_record(23, 1, 0x01, [std_info(), file_name(17, 1, "keep.txt", len(live)),
                                            attr_nonresident(0x80, runs, len(live), 2)])
    # ---- deleted file inside deleted dir (parent ref seq 2, dir now seq 3)
    png = make_png()
    runs = place(png)
    records[24] = mft_record(24, 2, 0x00, [std_info(), file_name(18, 2, "inside_old.png", len(png)),
                                            attr_nonresident(0x80, runs, len(png), 2)])
    payload["inside_old.png"] = png
    # ---- deleted file whose clusters were re-allocated (overwritten)
    over = make_jpeg((1, 2, 3))
    runs = place(over)
    mark(runs[0][0], runs[0][1])
    img[runs[0][0] * CS:runs[0][0] * CS + 1024] = b"\x00" * 1024   # header wiped by new owner
    records[25] = mft_record(25, 2, 0x00, [std_info(), file_name(17, 1, "overwritten.jpg", len(over)),
                                            attr_nonresident(0x80, runs, len(over), 2)])
    # ---- deleted file with $ATTRIBUTE_LIST, $DATA in extension record 27
    docx_data = make_docx()
    runs = place(docx_data, fragments=2)
    al_entry = lambda t, rec, aid: struct.pack("<IHBBQQH", t, 0x20, 0, 0x1A, 0, (2 << 48) | rec, aid) + bytes(6)
    attr_list = al_entry(0x10, 26, 0) + al_entry(0x30, 26, 1) + al_entry(0x80, 27, 0)
    records[26] = mft_record(26, 2, 0x00, [std_info(), attr_resident(0x20, attr_list, 3),
                                            file_name(17, 1, "proposal.docx", len(docx_data))])
    records[27] = mft_record(27, 2, 0x00, [attr_nonresident(0x80, runs, len(docx_data), 0)], base_ref=(2 << 48) | 26)
    payload["proposal.docx"] = docx_data
    # ---- deleted file whose parent (record 19, seq 4) was reused by a file (seq 9) -> orphan
    orphan = b"orphaned content " * 300
    runs = place(orphan)
    records[28] = mft_record(28, 2, 0x00, [std_info(), file_name(19, 4, "lost.txt", len(orphan)),
                                            attr_nonresident(0x80, runs, len(orphan), 2)])
    payload["lost.txt"] = orphan

    for no in range(NREC):
        rec = records.get(no)
        if rec is None:
            continue
        off = MFT_LCN * CS + no * REC
        img[off:off + REC] = rec
    # $MFTMirr (first 4 records)
    for no in range(4):
        if no in records:
            off = MIRR_LCN * CS + no * REC
            img[off:off + REC] = records[no]
    img[bitmap_lcn * CS:bitmap_lcn * CS + len(bitmap)] = bitmap

    bs = bytearray(512)
    bs[0:3] = b"\xEB\x52\x90"
    bs[3:11] = b"NTFS    "
    struct.pack_into("<HB", bs, 0x0B, 512, CS // 512)
    bs[0x15] = 0xF8
    struct.pack_into("<QQQ", bs, 0x28, TOTAL_CLUSTERS * CS // 512 - 1, MFT_LCN, MIRR_LCN)
    bs[0x40] = 0xF6  # 2^10 = 1024-byte records
    bs[0x44] = 1
    bs[510:512] = b"\x55\xAA"
    img[0:512] = bs
    open(os.path.join(OUT, "ntfs.img"), "wb").write(img)
    return {
        "deleted": {
            "minutes.pdf": {"path": "[img-ntfs.img]\\Users\\Docs\\minutes.pdf", "data": pdf},
            "photo_fragmented.jpg": {"path": "[img-ntfs.img]\\Users\\Docs\\photo_fragmented.jpg", "data": jpg, "fragments": 3},
            "todo.txt": {"path": "[img-ntfs.img]\\Users\\Docs\\todo.txt", "data": small, "resident": True},
            "inside_old.png": {"path": "[img-ntfs.img]\\Users\\OldStuff\\inside_old.png", "data": png},
            "overwritten.jpg": {"path": "[img-ntfs.img]\\Users\\Docs\\overwritten.jpg", "allocated_ratio": 1.0},
            "proposal.docx": {"path": "[img-ntfs.img]\\Users\\Docs\\proposal.docx", "data": docx_data},
            "lost.txt": {"orphaned": True, "data": orphan},
        },
        "live": ["keep.txt", "reused-record.txt"],
    }


def to_jsonable(obj):
    if isinstance(obj, bytes):
        import hashlib
        return {"sha256": hashlib.sha256(obj).hexdigest(), "len": len(obj)}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    return obj


if __name__ == "__main__":
    expected = {"carve": build_carve_image(), "fat32": build_fat32(), "ntfs": build_ntfs()}
    json.dump(to_jsonable(expected), open(os.path.join(OUT, "expected.json"), "w"), indent=2)
    print("images written to", OUT)
