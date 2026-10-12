"""
report_gen.py - MODULE 7 (part 1): ReportLab PDF generation.

Builds a self-contained recovery report for one session:
    1. Scan overview (mode, scope, sources, timings, engine)
    2. Summary statistics + confidence pie chart + top file types bar chart
    3. Drive health warnings
    4. Per-source table (file system, size, scanned, read errors, status)
    5. File type breakdown
    6. Deletion events (sector proximity clusters)
    7. Top recoverable files
    8. Restore history
    9. Confidence rating legend

Session history / semantic tags are stored by db.Repository (PostgreSQL); this
module is pure rendering so it can be unit-tested without a database.
"""
from __future__ import annotations

import collections
import datetime as dt
from pathlib import Path
from typing import Any, Optional

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

BRAND = colors.HexColor("#1e3a8a")
HIGH = colors.HexColor("#15803d")
MEDIUM = colors.HexColor("#b45309")
LOW = colors.HexColor("#b91c1c")
GRID = colors.HexColor("#cbd5e1")
ZEBRA = colors.HexColor("#f1f5f9")


def human_bytes(n: Optional[int]) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _esc(text: Any) -> str:
    return (str(text) if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ReportGenerator:
    def __init__(self) -> None:
        ss = getSampleStyleSheet()
        self.styles = {
            "title": ParagraphStyle("title", parent=ss["Title"], textColor=BRAND, fontSize=22, spaceAfter=6),
            "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], textColor=colors.HexColor("#475569"), alignment=TA_CENTER),
            "h2": ParagraphStyle("h2", parent=ss["Heading2"], textColor=BRAND, spaceBefore=10, spaceAfter=6, keepWithNext=1),
            "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13),
            "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=10),
            "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=8, leading=10),
            "warn": ParagraphStyle("warn", parent=ss["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#7f1d1d")),
        }

    # ------------------------------------------------------------------ helpers
    def _table(self, rows: list[list[Any]], widths: list[float], header: bool = True, align_right: tuple[int, ...] = ()) -> Table:
        def cell(value: Any, is_header: bool, col: int) -> Any:
            if not isinstance(value, str):
                return value  # flowables (charts) pass through
            text = _esc(value)
            if is_header:
                text = f"<font color='white'><b>{text}</b></font>"
            if col in align_right:
                text = f"<para alignment='right'>{text}</para>"
            return Paragraph(text, self.styles["cell"])

        data = [[cell(c, header and i == 0, j) for j, c in enumerate(r)] for i, r in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), BRAND))
            style += [("BACKGROUND", (0, i), (-1, i), ZEBRA) for i in range(2, len(rows), 2)]
        t.setStyle(TableStyle(style))
        return t

    @staticmethod
    def _confidence_pie(totals: dict) -> Drawing:
        d = Drawing(80 * mm, 55 * mm)
        values = [totals.get("high", 0), totals.get("medium", 0), totals.get("low", 0)]
        if sum(values) == 0:
            d.add(String(10, 70, "No files", fontSize=10))
            return d
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 15, 15, 45 * mm, 45 * mm
        pie.data = values
        pie.labels = [f"High {values[0]}", f"Medium {values[1]}", f"Low {values[2]}"]
        pie.slices.strokeColor = colors.white
        for i, col in enumerate((HIGH, MEDIUM, LOW)):
            pie.slices[i].fillColor = col
        pie.sideLabels = True
        pie.slices.fontSize = 7
        d.add(pie)
        return d

    @staticmethod
    def _types_bar(by_ext: dict[str, int]) -> Drawing:
        top = sorted(by_ext.items(), key=lambda kv: -kv[1])[:8][::-1]
        d = Drawing(90 * mm, 55 * mm)
        if not top:
            return d
        bc = HorizontalBarChart()
        bc.x, bc.y, bc.width, bc.height = 30, 10, 60 * mm, 45 * mm
        bc.data = [[v for _, v in top]]
        bc.categoryAxis.categoryNames = [f".{k}" for k, _ in top]
        bc.categoryAxis.labels.fontSize = 7
        bc.valueAxis.labels.fontSize = 7
        bc.valueAxis.valueMin = 0
        bc.bars[0].fillColor = BRAND
        d.add(bc)
        return d

    @staticmethod
    def _footer(canvas, doc) -> None:  # noqa: ANN001 - ReportLab callback signature
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(15 * mm, 10 * mm, "Windows HDD File Recovery Suite - confidential recovery report")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    # ------------------------------------------------------------------ main
    def build(self, out_path: Path, session: dict, files: list[dict], clusters: list[dict], restore_jobs: list[dict],
              user: Optional[dict] = None, title: Optional[str] = None) -> dict:
        """Render the PDF; returns a summary dict that is stored alongside the report."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        st = self.styles
        summary_block = session.get("scan_summary") or {}
        sources = summary_block.get("sources") or []
        totals = session.get("totals") or {}
        by_ext = collections.Counter(f["extension"] or "(none)" for f in files)
        bytes_by_ext: dict[str, int] = collections.defaultdict(int)
        high_by_ext: dict[str, int] = collections.defaultdict(int)
        for f in files:
            bytes_by_ext[f["extension"] or "(none)"] += int(f["size_bytes"])
            if f["confidence"] == "high":
                high_by_ext[f["extension"] or "(none)"] += 1
        title = title or f"Recovery Report - {session.get('label', 'Session')}"

        story: list[Any] = [
            Paragraph("File Recovery Report", st["title"]),
            Paragraph(_esc(title), st["subtitle"]),
            Paragraph(f"Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                      + (f" for {_esc(user.get('full_name') or user.get('email'))}" if user else ""), st["subtitle"]),
            Spacer(1, 8),
        ]

        # 1. Overview
        story.append(Paragraph("1. Scan overview", st["h2"]))
        scan = summary_block.get("scan") or {}
        engine = summary_block.get("engine") or {}
        overview = [
            ["Property", "Value"],
            ["Session ID", session.get("id", "")],
            ["Scan mode", {"quick": "Quick Scan (MFT / FAT metadata)", "deep": "Deep Scan (sector-by-sector carving)"}.get(str(session.get("mode")), str(session.get("mode", "")))],
            ["Target scope", {"all": "All drives", "drive": "Single drive", "folder": "Single folder"}.get(str(session.get("target_scope")), "")],
            ["Sources", ", ".join(session.get("sources") or []) or "All detected drives"],
            ["Folder filter", session.get("folder_path") or "-"],
            ["Scenario", (session.get("scenario") or "custom").replace("_", " ").title()],
            ["Status", (session.get("status") or "").title()],
            ["Started", session.get("started_at") or "-"],
            ["Finished", session.get("finished_at") or "-"],
            ["Engine", f"{engine.get('name', 'win_recovery_engine')} {engine.get('version', '')} ({engine.get('platform', '')})"],
            ["Administrator", "Yes" if scan.get("elevated") else "No"],
        ]
        story.append(self._table(overview, [45 * mm, 125 * mm]))

        # 2. Summary
        story.append(Paragraph("2. Summary statistics", st["h2"]))
        stats_rows = [
            ["Metric", "Value"],
            ["Recoverable files found", f"{totals.get('files', len(files)):,}"],
            ["Total size", human_bytes(totals.get("bytes", sum(int(f['size_bytes']) for f in files)))],
            ["High confidence", f"{totals.get('high', 0):,}"],
            ["Medium confidence", f"{totals.get('medium', 0):,}"],
            ["Low confidence", f"{totals.get('low', 0):,}"],
            ["Deletion events (clusters)", f"{totals.get('clusters', len(clusters)):,}"],
            ["Files already restored", f"{sum(1 for f in files if f.get('restored')):,}"],
        ]
        charts = Table([[self._confidence_pie(totals), self._types_bar(dict(by_ext))]], colWidths=[85 * mm, 90 * mm])
        story += [self._table(stats_rows, [70 * mm, 100 * mm], align_right=(1,)), Spacer(1, 4), charts]

        # 3. Health
        story.append(Paragraph("3. Drive health warnings", st["h2"]))
        warnings = session.get("health_warnings") or []
        if warnings:
            box = Table([[Paragraph("<b>Warning:</b> " + _esc(w), st["warn"])] for w in warnings], colWidths=[170 * mm])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef2f2")),
                                     ("BOX", (0, 0), (-1, -1), 0.6, LOW), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
            story.append(box)
        else:
            story.append(Paragraph("No read errors or overwrite pressure were detected during this scan.", st["body"]))

        # 4. Sources
        if sources:
            story.append(Paragraph("4. Scanned sources", st["h2"]))
            rows = [["Source", "FS", "Media", "Size", "Scanned", "Found", "Read errors", "Status"]]
            for s in sources:
                rows.append([s.get("display_name") or s.get("source_id"), s.get("filesystem", ""), s.get("media_type", ""),
                             human_bytes(s.get("total_bytes")), human_bytes(s.get("bytes_scanned")), str(s.get("candidates", 0)),
                             str(s.get("read_errors", 0)), (s.get("status") or "").title()])
            story.append(self._table(rows, [44 * mm, 18 * mm, 14 * mm, 20 * mm, 20 * mm, 14 * mm, 18 * mm, 22 * mm], align_right=(3, 4, 5, 6)))

        # 5. File types
        story.append(Paragraph("5. File types found", st["h2"]))
        rows = [["Extension", "Files", "Total size", "High confidence"]]
        for ext, n in by_ext.most_common(25):
            rows.append([f".{ext}", f"{n:,}", human_bytes(bytes_by_ext[ext]), f"{high_by_ext[ext]:,} ({high_by_ext[ext] / n:.0%})"])
        if len(rows) == 1:
            rows.append(["-", "0", "0 B", "-"])
        story.append(self._table(rows, [35 * mm, 30 * mm, 45 * mm, 60 * mm], align_right=(1, 2)))

        # 6. Clusters
        story.append(Paragraph("6. Deletion events (sector proximity clusters)", st["h2"]))
        if clusters:
            rows = [["Event", "Files", "Size", "LBA range", "Main type", "Time"]]
            for c in clusters[:40]:
                rows.append([c["label"].split(":")[0], str(c["file_count"]), human_bytes(c["total_bytes"]),
                             f"{c['lba_start']:,} - {c['lba_end']:,}", f".{c['dominant_extension']}", (c.get("time_start") or "-")[:16]])
            story.append(self._table(rows, [32 * mm, 14 * mm, 22 * mm, 50 * mm, 20 * mm, 32 * mm], align_right=(1, 2)))
        else:
            story.append(Paragraph("No groups of adjacent deleted files were detected.", st["body"]))

        # 7. Top files
        story.append(PageBreak())
        story.append(Paragraph("7. Most recoverable files", st["h2"]))
        ranked = sorted(files, key=lambda f: (-int(f["confidence_score"]), -int(f["size_bytes"])))[:60]
        rows = [["Name", "Original location", "Size", "Confidence"]]
        for f in ranked:
            name = f.get("file_name") or f.get("suggested_name") or f"carved_{f.get('absolute_lba')}.{f.get('extension')}"
            rows.append([name, f.get("original_path") or (f.get("candidate") or {}).get("partition_hint") or "(carved)",
                         human_bytes(f["size_bytes"]), f"{f['confidence'].title()} ({f['confidence_score']})"])
        if len(rows) == 1:
            rows.append(["-", "-", "-", "-"])
        story.append(self._table(rows, [68 * mm, 57 * mm, 20 * mm, 25 * mm], align_right=(2,)))

        # 8. Restore history
        story.append(Paragraph("8. Restore history", st["h2"]))
        if restore_jobs:
            rows = [["Created", "Destination", "Files", "Restored", "Failed", "Written", "Status"]]
            for j in restore_jobs:
                rows.append([(j.get("created_at") or "")[:16], j.get("destination", ""), str(j.get("total_files", 0)),
                             str(j.get("restored_files", 0)), str(j.get("failed_files", 0)), human_bytes(j.get("bytes_written")),
                             (j.get("status") or "").title()])
            story.append(self._table(rows, [26 * mm, 56 * mm, 14 * mm, 18 * mm, 16 * mm, 20 * mm, 20 * mm], align_right=(2, 3, 4, 5)))
        else:
            story.append(Paragraph("No files have been restored from this session yet.", st["body"]))

        # 9. Legend
        legend = [
            ["Rating", "Meaning"],
            ["High (75-100)", "Header intact, clusters not overwritten, content decodes. Expect a fully working file."],
            ["Medium (45-74)", "Recoverable but with risk: fragmentation, estimated size, partial overwrite or unverified content."],
            ["Low (0-44)", "Header overwritten, mostly reused clusters, encryption/compression or failed decoding. Try anyway, "
                           "but expect damage."],
        ]
        story.append(KeepTogether([Paragraph("9. Confidence rating guide", st["h2"]), self._table(legend, [30 * mm, 140 * mm])]))

        doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                                bottomMargin=18 * mm, title=title, author="Windows HDD File Recovery Suite",
                                subject="Recovery session report")
        doc.build(story, onFirstPage=self._footer, onLaterPages=self._footer)
        return {
            "files": len(files), "bytes": sum(int(f["size_bytes"]) for f in files),
            "by_extension": dict(by_ext.most_common(25)), "confidence": {k: totals.get(k, 0) for k in ("high", "medium", "low")},
            "clusters": len(clusters), "warnings": len(warnings), "pages_hint": 2 + len(files) // 60,
        }
