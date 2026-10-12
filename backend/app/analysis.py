"""
analysis.py - MODULE 6: Result Analysis, Verification & Smart Recovery (Track B).

Input : candidates.json produced by win_recovery_engine (contracts/schemas/candidates.schema.json)
Output: per-file analysis records + deletion-event clusters + drive health warnings,
        persisted by the job manager and served to the web UI.

Pipeline (per candidate, parallelised with a thread pool):
    1. ContentVerifier      - decodes the preview sample (Pillow / PyMuPDF / zipfile ...)
    2. TextExtractor        - PDF, DOCX, XLSX, PPTX, ODF, legacy OLE, TXT/CSV/HTML/RTF
    3. KeywordExtractor     - YAKE! when installed, otherwise session-level TF-IDF
    4. SmartNamer           - metadata title > heading line > keywords (+ year), EXIF for photos
    5. ThumbnailGenerator   - Pillow (images) / PyMuPDF or pypdfium2 (first PDF page)
    6. ConfidenceScorer     - header intactness, overwrite ratio, fragmentation, footer,
                              decoding result, zero-fill detection -> High / Medium / Low
Session level:
    7. ProximityClusterer   - groups files with adjacent absolute LBAs into deletion events
    8. Health warnings      - unreadable sectors, failed sources, overwrite pressure, SSD/TRIM

All optional third-party libraries degrade gracefully; nothing here touches the
source disk (only the samples the engine wrote to the guarded sample directory).
"""
from __future__ import annotations

import base64
import collections
import io
import json
import logging
import math
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("analysis")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ----------------------------------------------------------------------------- optional deps
try:
    from PIL import ExifTags, Image, ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = 200_000_000
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

try:
    import fitz  # PyMuPDF

    HAVE_FITZ = True
except ImportError:
    HAVE_FITZ = False

try:
    import pypdf

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # damaged PDFs are expected here
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False

try:
    import pypdfium2  # optional thumbnail fallback

    HAVE_PDFIUM = True
except ImportError:
    HAVE_PDFIUM = False

try:
    import yake

    HAVE_YAKE = True
except ImportError:
    HAVE_YAKE = False

# MuPDF and PDFium are NOT thread-safe: every call into them is serialised.
_PDF_LOCK = threading.Lock()

SUPPORTED_SCHEMA = "1.0"
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff"}
TEXT_EXTS = {"txt", "csv", "log", "md", "json", "xml", "html", "htm", "ini", "cfg", "yaml", "yml", "sql", "py", "js", "ts", "java",
             "c", "cpp", "h", "cs", "rtf", "eml", "srt"}
ZIP_EXTS = {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "apk"}
OLE_EXTS = {"doc", "xls", "ppt", "msg", "ole"}

STOPWORDS = set("""
a about above after again against all also am an and any are aren't as at be because been before being below between both but by
can cannot could couldn't did didn't do does doesn't doing don't down during each few for from further had hadn't has hasn't have
haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself let's me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over own
same shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd we'll we're
we've were weren't what what's when when's where where's which while who who's whom why why's with won't would wouldn't you you'd
you'll you're you've your yours yourself yourselves will shall may might must page pages document documents file files www http
https com org net also using use used one two three new per via etc within without upon however therefore thus yes ok
""".split())

GENERIC_TITLES = {"untitled", "document", "microsoft word", "presentation", "workbook", "sheet1", "slide 1", "title", "new document",
                  "anonymous", "(anonymous)", "none", "unknown", "pdf", "doc", "print", "scan"}


# =============================================================================
# Errors / data classes
# =============================================================================
class CandidatesFormatError(ValueError):
    """candidates.json does not satisfy the contract."""


@dataclass
class Verification:
    valid: Optional[bool] = None   # True decoded OK, False corrupt, None not checked / not applicable
    detail: str = ""
    zero_ratio: float = 0.0
    entropy: float = 0.0
    metadata_title: Optional[str] = None
    exif: dict = field(default_factory=dict)


@dataclass
class AnalysisResult:
    files: list[dict]
    clusters: list[dict]
    health_warnings: list[str]
    totals: dict
    scan_summary: dict


# =============================================================================
# 0. Contract loading
# =============================================================================
REQUIRED_TOP = ("schema_version", "engine", "scan", "sources", "statistics", "candidates")
REQUIRED_CANDIDATE = ("id", "source_id", "method", "extension", "size_bytes", "extents", "integrity")


def load_candidates(path: str | Path) -> dict:
    """Load and structurally validate a candidates.json document."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CandidatesFormatError(f"Cannot read candidates file {path}: {exc}") from exc
    return validate_candidates(doc)


def validate_candidates(doc: Any) -> dict:
    if not isinstance(doc, dict):
        raise CandidatesFormatError("candidates document must be a JSON object")
    missing = [k for k in REQUIRED_TOP if k not in doc]
    if missing:
        raise CandidatesFormatError(f"candidates document missing keys: {missing}")
    major = str(doc["schema_version"]).split(".")[0]
    if major != SUPPORTED_SCHEMA.split(".")[0]:
        raise CandidatesFormatError(f"unsupported schema_version {doc['schema_version']} (expected {SUPPORTED_SCHEMA})")
    if not isinstance(doc["candidates"], list):
        raise CandidatesFormatError("'candidates' must be an array")
    for i, c in enumerate(doc["candidates"][:1000]):  # spot-check structure; full validation lives in contracts/
        bad = [k for k in REQUIRED_CANDIDATE if k not in c]
        if bad:
            raise CandidatesFormatError(f"candidate #{i} missing keys: {bad}")
    return doc


# =============================================================================
# 1. Content verification
# =============================================================================
def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = collections.Counter(data[:65536])
    n = sum(counts.values())
    return -sum(c / n * math.log2(c / n) for c in counts.values())


class ContentVerifier:
    @staticmethod
    def verify(ext: str, data: bytes, complete: bool) -> Verification:
        v = Verification()
        if not data:
            v.detail = "no sample available"
            return v
        v.zero_ratio = data.count(0) / len(data)
        v.entropy = _entropy(data)
        if v.zero_ratio > 0.97:
            v.valid = False
            v.detail = "content is almost entirely zero bytes (wiped or never written)"
            return v
        try:
            if ext in IMAGE_EXTS and HAVE_PIL:
                ContentVerifier._image(data, complete, v)
            elif ext == "pdf":
                ContentVerifier._pdf(data, complete, v)
            elif ext in ZIP_EXTS:
                ContentVerifier._zip(data, complete, v)
            elif ext in OLE_EXTS:
                v.valid = data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
                v.detail = "OLE2 header present" if v.valid else "OLE2 header missing"
            elif ext in ("sqlite", "db"):
                v.valid = data[:16] == b"SQLite format 3\x00"
                v.detail = "SQLite header present" if v.valid else "SQLite header missing"
            elif ext in TEXT_EXTS:
                text = TextExtractor.decode_text(data)
                printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text[:20000]) / max(1, min(len(text), 20000))
                v.valid = printable > 0.85
                v.detail = f"{printable:.0%} printable characters"
        except Exception as exc:  # noqa: BLE001 - corrupted input must never crash analysis
            v.valid = False
            v.detail = f"decoder error: {type(exc).__name__}: {str(exc)[:120]}"
        return v

    @staticmethod
    def _image(data: bytes, complete: bool, v: Verification) -> None:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            size = im.size
            try:
                exif = im.getexif()
                tags = {ExifTags.TAGS.get(k, str(k)): val for k, val in exif.items()} if exif else {}
                try:
                    sub = exif.get_ifd(0x8769)  # Exif IFD (DateTimeOriginal lives here)
                    tags.update({ExifTags.TAGS.get(k, str(k)): val for k, val in sub.items()})
                except Exception:  # noqa: BLE001
                    pass
                v.exif = {k: str(val)[:80] for k, val in tags.items() if k in ("DateTimeOriginal", "DateTime", "Model", "Make")}
            except Exception:  # noqa: BLE001
                pass
        if complete:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()  # structural check of the full stream
            with Image.open(io.BytesIO(data)) as im:
                im.load()
            v.valid = True
            v.detail = f"{fmt} {size[0]}x{size[1]} decodes completely"
        else:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
            v.valid = None
            v.detail = f"{fmt} {size[0]}x{size[1]} header decodes (partial sample)"

    @staticmethod
    def _pdf(data: bytes, complete: bool, v: Verification) -> None:
        if not data.startswith(b"%PDF-"):
            v.valid = False
            v.detail = "missing %PDF- header"
            return
        if HAVE_FITZ:
            with _PDF_LOCK, fitz.open(stream=data, filetype="pdf") as doc:
                pages = doc.page_count
                title = (doc.metadata or {}).get("title")
                v.metadata_title = title or None
                if pages > 0:
                    doc.load_page(0)
            v.valid = pages > 0 if complete else None
            v.detail = f"PDF with {pages} page(s)"
        elif HAVE_PYPDF:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
            pages = len(reader.pages)
            try:
                v.metadata_title = (reader.metadata.title if reader.metadata else None) or None
            except Exception:  # noqa: BLE001
                v.metadata_title = None
            v.valid = pages > 0 if complete else None
            v.detail = f"PDF with {pages} page(s)"
        else:
            v.valid = (b"%%EOF" in data[-2048:]) if complete else None
            v.detail = "PDF trailer present" if v.valid else "PDF trailer not verified"

    @staticmethod
    def _zip(data: bytes, complete: bool, v: Verification) -> None:
        if not complete:
            v.valid = None if data[:4] == b"PK\x03\x04" else False
            v.detail = "ZIP local header present (partial sample)" if v.valid is None else "ZIP header missing"
            return
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            bad = z.testzip()
            names = z.namelist()
            if "docProps/core.xml" in names:
                m = re.search(rb"<dc:title>(.*?)</dc:title>", z.read("docProps/core.xml"), re.S)
                if m:
                    v.metadata_title = m.group(1).decode("utf-8", "replace").strip() or None
        v.valid = bad is None
        v.detail = f"archive with {len(names)} member(s)" + ("" if bad is None else f", CRC error in {bad}")


# =============================================================================
# 2. Text extraction
# =============================================================================
class TextExtractor:
    MAX_CHARS = 20000

    @staticmethod
    def decode_text(data: bytes) -> str:
        if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
            return data.decode("utf-16", "replace")
        if data.startswith(b"\xef\xbb\xbf"):
            return data[3:].decode("utf-8", "replace")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            # Heuristic UTF-16LE detection (every other byte zero)
            if len(data) > 64 and data[1:200:2].count(0) > 80:
                return data.decode("utf-16-le", "replace")
            return data.decode("cp1252", "replace")

    @staticmethod
    def _xml_text(xml: bytes, tag_pattern: str) -> str:
        parts = re.findall(tag_pattern.encode(), xml, re.S)
        text = " ".join(p.decode("utf-8", "replace") for p in parts)
        return re.sub(r"&(amp|lt|gt|quot|apos);", lambda m: {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}[m.group(1)], text)

    @classmethod
    def extract(cls, ext: str, data: bytes) -> str:
        if not data:
            return ""
        try:
            if ext == "pdf":
                text = cls._pdf(data)
            elif ext == "docx":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    xml = z.read("word/document.xml")
                xml = re.sub(rb"</w:p>", b"</w:p>\n<w:t>\n</w:t>", xml)
                text = cls._xml_text(xml, r"<w:t[^>]*>(.*?)</w:t>")
            elif ext == "xlsx":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    xml = z.read("xl/sharedStrings.xml") if "xl/sharedStrings.xml" in z.namelist() else b""
                text = cls._xml_text(xml, r"<t[^>]*>(.*?)</t>")
            elif ext == "pptx":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    slides = sorted(n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml", n))
                    text = "\n".join(cls._xml_text(z.read(s), r"<a:t>(.*?)</a:t>") for s in slides[:50])
            elif ext in ("odt", "ods", "odp"):
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    xml = z.read("content.xml")
                text = re.sub(r"<[^>]+>", " ", xml.decode("utf-8", "replace"))
            elif ext in OLE_EXTS:
                text = cls._ole_strings(data)
            elif ext in ("html", "htm", "xml"):
                raw = cls.decode_text(data)
                raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
                text = re.sub(r"<[^>]+>", " ", raw)
            elif ext == "rtf":
                raw = cls.decode_text(data)
                raw = re.sub(r"\\'[0-9a-f]{2}", " ", raw)
                raw = re.sub(r"\\[a-z]+-?\d* ?", " ", raw)
                text = re.sub(r"[{}]", " ", raw)
            elif ext in TEXT_EXTS:
                text = cls.decode_text(data)
            else:
                return ""
        except Exception as exc:  # noqa: BLE001
            log.debug("text extraction failed for .%s: %s", ext, exc)
            return ""
        text = re.sub(r"[ \t\u00a0]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()[: cls.MAX_CHARS]

    @staticmethod
    def _pdf(data: bytes) -> str:
        if HAVE_FITZ:
            with _PDF_LOCK, fitz.open(stream=data, filetype="pdf") as doc:
                return "\n".join(doc.load_page(i).get_text() for i in range(min(doc.page_count, 10)))
        if HAVE_PYPDF:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
            return "\n".join((p.extract_text() or "") for p in reader.pages[:10])
        return ""

    @staticmethod
    def _ole_strings(data: bytes) -> str:
        # Legacy binary Office: harvest UTF-16LE and ASCII runs (good enough for keywords).
        u16 = re.findall(rb"(?:[\x20-\x7e]\x00){5,}", data)
        ascii_runs = re.findall(rb"[\x20-\x7e]{6,}", data)
        words = [r.decode("utf-16-le", "ignore") for r in u16] + [r.decode("ascii", "ignore") for r in ascii_runs]
        return "\n".join(w for w in words if sum(c.isalpha() for c in w) > len(w) * 0.6)[:20000]


# =============================================================================
# 3. Keyword extraction (YAKE! or session-level TF-IDF)
# =============================================================================
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")


def tokenize(text: str) -> list[str]:
    return [w.lower().strip("-'") for w in _WORD_RE.findall(text) if w.lower() not in STOPWORDS and len(w) <= 30]


class KeywordExtractor:
    """YAKE! (unsupervised, single document) when available; otherwise TF-IDF
    where document frequencies come from every document recovered in the same
    session (fit()), which rewards words that make a file distinctive."""

    def __init__(self, top: int = 8) -> None:
        self.top = top
        self.df: collections.Counter[str] = collections.Counter()
        self.n_docs = 0
        self._yake = yake.KeywordExtractor(lan="en", n=2, dedupLim=0.7, top=top * 2) if HAVE_YAKE else None
        self._lock = threading.Lock()  # YAKE extractor instances are not documented as thread-safe

    def fit(self, texts: list[str]) -> None:
        for t in texts:
            self.df.update(set(tokenize(t)))
        self.n_docs = len(texts)

    def extract(self, text: str) -> list[str]:
        if not text or len(text) < 20:
            return []
        if self._yake is not None:
            try:
                with self._lock:
                    kws = [k for k, _ in self._yake.extract_keywords(text[:15000])]
                cleaned, seen = [], set()
                for k in kws:
                    k = k.strip().lower()
                    if not k or k in seen or all(w in STOPWORDS for w in k.split()) or re.search(r"\d{4,}", k):
                        continue
                    seen.add(k)
                    cleaned.append(k)
                if cleaned:
                    return cleaned[: self.top]
            except Exception as exc:  # noqa: BLE001
                log.debug("yake failed: %s", exc)
        return self._tfidf(text)

    def _tfidf(self, text: str) -> list[str]:
        tokens = tokenize(text[:50000])
        if not tokens:
            return []
        tf = collections.Counter(tokens)
        # Words appearing in the first 300 characters (titles/headings) get a boost.
        head = set(tokenize(text[:300]))
        n = max(self.n_docs, 1)
        scores = {}
        for w, c in tf.items():
            idf = math.log((1 + n) / (1 + self.df.get(w, 0))) + 1.0
            scores[w] = (c / len(tokens)) * idf * (1.5 if w in head else 1.0)
        # Promote frequent bigrams of top words.
        bigrams = collections.Counter(zip(tokens, tokens[1:]))
        for (a, b), c in bigrams.most_common(5):
            if c >= 2 and a != b:
                scores[f"{a} {b}"] = (scores.get(a, 0) + scores.get(b, 0)) * 0.75
        return [w for w, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[: self.top]]


# =============================================================================
# 4. Smart naming
# =============================================================================
_YEAR_RE = re.compile(r"(?<!\d)(199\d|20[0-3]\d)(?!\d)")


def _sanitize_name(stem: str) -> str:
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_")
    stem = re.sub(r"_+", "_", stem)
    return stem[:80].rstrip("_")


def _title_case(words: list[str]) -> str:
    return "_".join(w[:1].upper() + w[1:] if not w.isupper() else w for w in words)


class SmartNamer:
    @staticmethod
    def looks_like_title(line: str) -> bool:
        words = line.split()
        if not 2 <= len(words) <= 12 or len(line) > 90:
            return False
        letters = sum(ch.isalpha() for ch in line)
        return letters / max(1, len(line.replace(" ", ""))) > 0.7 and not line.endswith((".", ",", ";"))

    @classmethod
    def suggest(cls, ext: str, text: str, keywords: list[str], verification: Verification, timestamps: dict) -> Optional[str]:
        ext = ext or "bin"
        # Photos: EXIF capture date + camera model.
        if verification.exif:
            date = verification.exif.get("DateTimeOriginal") or verification.exif.get("DateTime")
            model = verification.exif.get("Model")
            if date:
                d = re.sub(r"[^0-9]", "", date)[:8]
                stem = f"Photo_{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "Photo"
                if model:
                    stem += "_" + _sanitize_name(model)[:24]
                return f"{_sanitize_name(stem)}.{ext}"

        stem = None
        title = (verification.metadata_title or "").strip()
        if title and title.lower() not in GENERIC_TITLES and len(title) >= 4 and not title.lower().startswith("microsoft "):
            stem = _title_case(re.findall(r"[A-Za-z0-9]+", title)[:8])
        if not stem and text:
            for line in text.splitlines()[:8]:
                line = line.strip()
                if cls.looks_like_title(line):
                    stem = _title_case(re.findall(r"[A-Za-z0-9]+", line)[:8])
                    break
        if not stem and keywords:
            words: list[str] = []
            for k in keywords:
                for w in k.split():
                    if w not in words and w not in STOPWORDS:
                        words.append(w)
                if len(words) >= 3:
                    break
            stem = _title_case(words[:3]) if words else None
        if not stem:
            return None
        if not _YEAR_RE.search(stem):
            years = collections.Counter(_YEAR_RE.findall(text or ""))
            year = years.most_common(1)[0][0] if years else None
            if not year:
                ts = timestamps.get("modified") or timestamps.get("created")
                year = ts[:4] if ts and ts[:4].isdigit() else None
            if year:
                stem = f"{stem}_{year}"
        stem = _sanitize_name(stem)
        return f"{stem}.{ext}" if stem else None


# =============================================================================
# 5. Thumbnails
# =============================================================================
class ThumbnailGenerator:
    SIZE = (320, 320)

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def make(self, file_id: str, ext: str, data: bytes) -> Optional[str]:
        if not HAVE_PIL or not data:
            return None
        try:
            img = None
            if ext in IMAGE_EXTS:
                img = Image.open(io.BytesIO(data))
                img.load()
            elif ext == "pdf":
                img = self._pdf_page(data)
            if img is None:
                return None
            if img.mode not in ("RGB", "L"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            img.thumbnail(self.SIZE)
            path = self.out_dir / f"{file_id}.jpg"
            img.convert("RGB").save(path, "JPEG", quality=80)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("thumbnail failed for %s: %s", file_id, exc)
            return None

    @staticmethod
    def _pdf_page(data: bytes):
        with _PDF_LOCK:
            return ThumbnailGenerator._pdf_page_locked(data)

    @staticmethod
    def _pdf_page_locked(data: bytes):
        if HAVE_FITZ:
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.page_count == 0:
                    return None
                pix = doc.load_page(0).get_pixmap(dpi=60)
                return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if HAVE_PDFIUM:
            pdf = pypdfium2.PdfDocument(data)
            try:
                if len(pdf) == 0:
                    return None
                return pdf[0].render(scale=60 / 72).to_pil()
            finally:
                pdf.close()
        return None


# =============================================================================
# 6. Confidence scoring
# =============================================================================
class ConfidenceScorer:
    HIGH = 75
    MEDIUM = 45

    @classmethod
    def score(cls, c: dict, v: Verification, sample_complete: bool) -> tuple[str, int, list[str]]:
        s = 100
        reasons: list[str] = []
        integ = c.get("integrity") or {}
        method = c.get("method")
        mft = c.get("mft") or {}
        ext = c.get("extension") or ""

        if c.get("size_bytes", 0) == 0:
            return "low", 5, ["Zero-length file: no data to recover"]

        header = integ.get("header_check", "unknown")
        if header == "mismatch":
            s -= 45
            reasons.append(f"File header does not match .{ext} - the first clusters were probably overwritten")
        elif header == "match":
            reasons.append("File signature intact")
        else:
            s -= 5
            reasons.append("No signature check available for this file type")

        ratio = integ.get("allocated_ratio")
        if method in ("mft", "fat"):
            if ratio is None:
                s -= 10
                reasons.append("Overwrite status unknown (allocation bitmap unavailable)")
            elif ratio > 0:
                s -= min(60, int(round(ratio * 70)))
                reasons.append(f"{ratio:.0%} of the file's clusters are now used by other files (overwritten)")
            else:
                reasons.append("All clusters still free - not overwritten")
        elif method == "carve":
            s -= 5
            reasons.append("Recovered by signature carving (original name and folder are lost)")
            if integ.get("footer_found") is False:
                s -= 30
                reasons.append("End-of-file marker not found - file may be fragmented or truncated")
            if integ.get("size_estimated"):
                s -= 20
                reasons.append("File size was estimated")
            if ratio is not None and ratio > 0.99:
                reasons.append("Lies in allocated space - probably an existing (not deleted) file")

        frags = int(c.get("fragment_count") or 0)
        if method == "fat" and c.get("size_bytes", 0) > 0 and any("fat_contiguity_assumed" in n for n in c.get("notes", [])):
            s -= 12
            reasons.append("FAT chain was cleared on delete; contiguous allocation assumed")
        if method == "mft" and frags > 1:
            s -= min(10, 2 * (frags - 1))
            reasons.append(f"{frags} fragments (locations known from MFT data runs)")
        if integ.get("truncated") and method != "carve":
            s -= 20
            reasons.append("Run list shorter than file size - tail missing")
        if mft.get("is_compressed"):
            s -= 30
            reasons.append("NTFS-compressed: raw clusters need LZNT1 decompression")
        if mft.get("is_encrypted"):
            s -= 50
            reasons.append("EFS-encrypted: unreadable without the original user's certificate")
        if mft.get("fixup_error"):
            s -= 10
            reasons.append("MFT record had a torn write (update sequence mismatch)")
        if integ.get("path_orphaned"):
            s -= 3
            reasons.append("Parent folder no longer exists; placed under $Orphan")

        if v.valid is True:
            s += 10
            reasons.append(f"Content verified: {v.detail}")
        elif v.valid is False:
            s -= 35
            reasons.append(f"Content check failed: {v.detail}")
        elif v.detail and v.detail != "no sample available":
            reasons.append(v.detail)
        if v.zero_ratio > 0.5 and v.valid is not False:
            s -= 15
            reasons.append(f"{v.zero_ratio:.0%} of sampled bytes are zero")

        s = max(0, min(100, s))
        level = "high" if s >= cls.HIGH else "medium" if s >= cls.MEDIUM else "low"
        return level, s, reasons


# =============================================================================
# 7. Sector proximity clustering
# =============================================================================
class ProximityClusterer:
    """Files deleted together were usually written together, so they sit in
    nearby sectors. Sort by absolute LBA per physical device and split when the
    gap between consecutive files exceeds `gap_sectors`."""

    def __init__(self, gap_sectors: int = 20480, min_size: int = 2) -> None:
        self.gap = gap_sectors
        self.min_size = min_size

    def cluster(self, session_id: str, files: list[dict], device_of: dict[str, str]) -> list[dict]:
        groups: dict[str, list[dict]] = collections.defaultdict(list)
        for f in files:
            cand = f["candidate"]
            if cand.get("resident_data_b64") or f["absolute_lba"] <= 0:
                continue
            sid = str(f["source_id"])
            groups[device_of.get(sid, sid)].append(f)

        clusters: list[dict] = []
        n = 0
        for _, items in sorted(groups.items()):
            items.sort(key=lambda f: f["absolute_lba"])
            current: list[dict] = []
            last_end = None
            for f in items:
                ss = f["candidate"].get("sector_size") or 512
                end = f["absolute_lba"] + max(1, f["size_bytes"] // ss)
                if current and last_end is not None and f["absolute_lba"] - last_end > self.gap:
                    n = self._flush(session_id, current, clusters, n)
                    current = []
                current.append(f)
                last_end = end if last_end is None else max(last_end, end)
            n = self._flush(session_id, current, clusters, n)
        return clusters

    def _flush(self, session_id: str, members: list[dict], out: list[dict], n: int) -> int:
        if len(members) < self.min_size:
            return n
        n += 1
        cid = f"{session_id}:c{n}"
        exts = collections.Counter(m["extension"] or "(none)" for m in members)
        dominant = exts.most_common(1)[0][0]
        times = sorted(t for m in members
                       for t in [(m["timestamps"] or {}).get("mft_modified") or (m["timestamps"] or {}).get("modified")] if t)
        lba_start = members[0]["absolute_lba"]
        lba_end = max(m["absolute_lba"] + max(1, m["size_bytes"] // 512) for m in members)
        total = sum(m["size_bytes"] for m in members)
        label = f"Deletion event #{n}: {len(members)} files (mostly .{dominant})"
        if times:
            label += f", around {times[0][:10]}"
        for m in members:
            m["cluster_id"] = cid
        out.append({"id": cid, "label": label, "file_count": len(members), "total_bytes": total, "lba_start": lba_start,
                    "lba_end": lba_end, "dominant_extension": dominant, "time_start": times[0] if times else None,
                    "time_end": times[-1] if times else None})
        return n


# =============================================================================
# 8. Orchestrator
# =============================================================================
def _read_sample(c: dict, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    if c.get("resident_data_b64"):
        try:
            return base64.b64decode(c["resident_data_b64"])
        except (ValueError, TypeError):
            return b""
    p = c.get("sample_path")
    if not p:
        return b""
    try:
        with open(p, "rb") as fh:
            return fh.read(max_bytes)
    except OSError:
        return b""


def display_name(f: dict) -> str:
    if f.get("file_name"):
        return f["file_name"]
    if f.get("suggested_name"):
        return f["suggested_name"]
    ext = f.get("extension") or "bin"
    return f"carved_{f.get('absolute_lba', 0)}.{ext}"


class AnalysisEngine:
    def __init__(self, thumbnail_root: Path, workers: int = 4, cluster_gap_sectors: int = 20480) -> None:
        self.thumbnail_root = Path(thumbnail_root)
        self.workers = max(1, workers)
        self.clusterer = ProximityClusterer(cluster_gap_sectors)

    def analyze(self, doc: dict, session_id: str, progress_cb=None) -> AnalysisResult:
        doc = validate_candidates(doc)
        cands = doc["candidates"]
        thumbs = ThumbnailGenerator(self.thumbnail_root / session_id)
        total = len(cands)

        # Pass A: content (parallel). Text first so TF-IDF can be fitted on the session corpus.
        def stage_one(c: dict) -> dict:
            data = _read_sample(c)
            ext = (c.get("extension") or "").lower()
            complete = bool(data) and len(data) >= int(c.get("size_bytes") or 0)
            v = ContentVerifier.verify(ext, data, complete)
            text = TextExtractor.extract(ext, data) if data else ""
            return {"c": c, "data_len": len(data), "complete": complete, "v": v, "text": text, "ext": ext}

        staged: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for i, r in enumerate(pool.map(stage_one, cands, chunksize=32), 1):
                staged.append(r)
                if progress_cb and (i % 200 == 0 or i == total):
                    progress_cb(i, total * 2, "Verifying content and extracting text")

        kw = KeywordExtractor()
        kw.fit([s["text"] for s in staged if s["text"]])

        def stage_two(s: dict) -> dict:
            c, v = s["c"], s["v"]
            fid = str(uuid.uuid4())
            keywords = kw.extract(s["text"]) if s["text"] else []
            suggested = SmartNamer.suggest(s["ext"], s["text"], keywords, v, c.get("timestamps") or {}) if (s["text"] or v.exif) else None
            thumb = None
            if s["ext"] in IMAGE_EXTS or s["ext"] == "pdf":
                thumb = thumbs.make(fid, s["ext"], _read_sample(c))
            level, score, reasons = ConfidenceScorer.score(c, v, s["complete"])
            # Damaged files often yield NUL / control bytes: keep the excerpt readable (and storable in PostgreSQL).
            excerpt = _CONTROL_CHARS.sub("", s["text"][:1500]).strip() or None if s["text"] else None
            return {
                "id": fid,
                "candidate_id": c["id"],
                "source_id": c.get("source_id", ""),
                "method": c.get("method", "carve"),
                "file_name": c.get("file_name"),
                "suggested_name": suggested,
                "original_path": c.get("original_path"),
                "extension": s["ext"],
                "mime_type": c.get("mime_type") or "application/octet-stream",
                "size_bytes": int(c.get("size_bytes") or 0),
                "lba": int(c.get("lba") or 0),
                "absolute_lba": int(c.get("absolute_lba") or 0),
                "fragment_count": int(c.get("fragment_count") or 0),
                "confidence": level,
                "confidence_score": score,
                "confidence_reasons": reasons,
                "cluster_id": None,
                "timestamps": c.get("timestamps") or {},
                "keywords": keywords,
                "candidate": c,
                "sample_path": c.get("sample_path"),
                "thumbnail_path": thumb,
                "text_excerpt": excerpt,
                "preview_kind": "image" if thumb else ("text" if excerpt else "none"),
            }

        files: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for i, f in enumerate(pool.map(stage_two, staged, chunksize=32), 1):
                files.append(f)
                if progress_cb and (i % 200 == 0 or i == total):
                    progress_cb(total + i, total * 2, "Scoring confidence and generating previews")

        device_of = {}
        for s in doc.get("sources", []):
            pdi = s.get("physical_drive_index", -1)
            device_of[s["source_id"]] = f"disk{pdi}" if pdi is not None and pdi >= 0 else s["source_id"]
            # partition-derived ids (pd1-p2) share the parent's device
        for f in files:
            sid = f["source_id"]
            if sid not in device_of and "-p" in sid:
                device_of[sid] = device_of.get(sid.rsplit("-p", 1)[0], sid.rsplit("-p", 1)[0])
        clusters = self.clusterer.cluster(session_id, files, device_of)

        totals = {
            "files": len(files),
            "bytes": sum(f["size_bytes"] for f in files),
            "high": sum(f["confidence"] == "high" for f in files),
            "medium": sum(f["confidence"] == "medium" for f in files),
            "low": sum(f["confidence"] == "low" for f in files),
            "clusters": len(clusters),
        }
        summary = {"scan": doc.get("scan", {}), "sources": doc.get("sources", []), "statistics": doc.get("statistics", {}),
                   "engine": doc.get("engine", {})}
        return AnalysisResult(files=files, clusters=clusters, health_warnings=self.health_warnings(doc, files),
                              totals=totals, scan_summary=summary)

    @staticmethod
    def health_warnings(doc: dict, files: list[dict]) -> list[str]:
        warnings: list[str] = []
        for s in doc.get("sources", []):
            name = s.get("display_name") or s.get("source_id")
            if s.get("read_errors", 0) > 0:
                warnings.append(f"{name}: {s['read_errors']} unreadable sector(s) were skipped. The drive may be failing - "
                                "stop using it and create a sector-by-sector image before further recovery attempts.")
            if s.get("status") == "failed":
                warnings.append(f"{name}: scan failed - {s.get('error') or 'unknown error'}")
            if s.get("status") == "unsupported":
                warnings.append(f"{name}: {s.get('error') or 'file system not supported by Quick Scan'}")
            if s.get("media_type") == "SSD":
                warnings.append(f"{name} is a solid-state drive: TRIM usually erases deleted data within minutes, results will be limited.")
        meta = [f for f in files if f["method"] in ("mft", "fat")]
        if meta:
            over = [f for f in meta if (f["candidate"].get("integrity") or {}).get("allocated_ratio") not in (None, 0, 0.0)]
            share = len(over) / len(meta)
            if share >= 0.5:
                warnings.append(f"{share:.0%} of deleted files are partly overwritten. Stop writing to this drive immediately.")
        fixups = sum(1 for f in meta if (f["candidate"].get("mft") or {}).get("fixup_error"))
        if fixups:
            warnings.append(f"{fixups} MFT record(s) had torn writes - possible unexpected power loss or failing sectors.")
        if doc.get("scan", {}).get("status") == "cancelled":
            warnings.append("Scan was cancelled - results are partial.")
        return warnings
