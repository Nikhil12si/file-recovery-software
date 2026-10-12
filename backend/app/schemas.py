"""
schemas.py - REST request models and response serializers.

Responses are plain dicts shaped exactly like contracts/schemas/rest-api.schema.json
($defs: scan_session, recovered_file, deletion_cluster, restore_job, report, ...).
Keeping serialisation here means DB column changes never leak into the API.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Scenario = Literal["accidental_delete", "formatted_drive", "corrupted_partition", "custom"]

SCENARIO_DEFAULT_MODE = {"accidental_delete": "quick", "formatted_drive": "deep", "corrupted_partition": "deep", "custom": "quick"}
SCENARIO_LABEL = {"accidental_delete": "Accidental delete", "formatted_drive": "Formatted drive",
                  "corrupted_partition": "Corrupted partition", "custom": "Custom scan"}


# ============================================================================= requests
class ScanCreateRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    mode: Optional[Literal["quick", "deep"]] = None
    target_scope: Literal["all", "drive", "folder"] = "drive"
    sources: list[str] = Field(default_factory=list, max_length=32)
    folder_path: Optional[str] = Field(default=None, max_length=1024)
    scenario: Scenario = "custom"
    file_types: list[str] = Field(default_factory=list, max_length=64)
    allow_non_hdd: bool = False
    unallocated_only: bool = False

    @field_validator("file_types")
    @classmethod
    def _types(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = t.strip().lower().lstrip(".")
            if t and t.isalnum() and len(t) <= 10 and t not in out:
                out.append(t)
        return out

    @field_validator("sources")
    @classmethod
    def _sources(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @model_validator(mode="after")
    def _check(self) -> "ScanCreateRequest":
        if self.mode is None:
            self.mode = SCENARIO_DEFAULT_MODE[self.scenario]  # type: ignore[assignment]
        if self.target_scope == "drive" and not self.sources:
            raise ValueError("Select at least one drive or partition for a single-drive scan")
        if self.target_scope == "folder":
            if not self.folder_path or len(self.folder_path) < 3 or self.folder_path[1] != ":":
                raise ValueError("Folder scans need an absolute folder path such as D:\\Users\\me\\Documents")
        return self


class ValidateDestinationRequest(BaseModel):
    destination: str = Field(..., min_length=1, max_length=1024)
    sources: list[str] = Field(default_factory=list)
    required_bytes: int = Field(default=0, ge=0)


class RestoreRequest(BaseModel):
    file_ids: list[str] = Field(..., min_length=1, max_length=100000)
    destination: str = Field(..., min_length=2, max_length=1024)
    use_smart_names: bool = True
    preserve_paths: bool = True


class ReportRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)


# ============================================================================= serializers
def page(items: list[Any], page_no: int, page_size: int, total: int) -> dict:
    return {"items": items, "page": page_no, "page_size": page_size, "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0}


def default_label(req: ScanCreateRequest) -> str:
    where = {"all": "All drives", "folder": req.folder_path or "Folder"}.get(req.target_scope, ", ".join(req.sources) or "Drive")
    mode = "Quick" if req.mode == "quick" else "Deep"
    return f"{SCENARIO_LABEL[req.scenario]} - {where} ({mode}) {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"


def session_out(row: dict, progress: Optional[dict] = None) -> dict:
    totals = {"files": 0, "bytes": 0, "high": 0, "medium": 0, "low": 0, "clusters": 0, **(row.get("totals") or {})}
    return {
        "id": row["id"], "label": row["label"], "mode": row["mode"], "target_scope": row["target_scope"],
        "sources": row.get("sources") or [], "folder_path": row.get("folder_path"), "scenario": row.get("scenario") or "custom",
        "file_types": row.get("file_types") or [], "status": row["status"], "progress": progress or row.get("progress") or {},
        "totals": totals, "health_warnings": row.get("health_warnings") or [], "error": row.get("error"),
        "created_at": row["created_at"], "started_at": row.get("started_at"), "finished_at": row.get("finished_at"),
    }


def display_name(row: dict) -> str:
    if row.get("file_name"):
        return row["file_name"]
    if row.get("suggested_name"):
        return row["suggested_name"]
    return f"carved_{row.get('absolute_lba', 0)}.{row.get('extension') or 'bin'}"


def file_out(row: dict) -> dict:
    return {
        "id": row["id"], "scan_id": row["session_id"], "candidate_id": row["candidate_id"], "method": row["method"],
        "file_name": row.get("file_name"), "suggested_name": row.get("suggested_name"), "display_name": display_name(row),
        "original_path": row.get("original_path"), "extension": row.get("extension") or "", "mime_type": row.get("mime_type"),
        "size_bytes": int(row["size_bytes"]), "confidence": row["confidence"], "confidence_score": int(row["confidence_score"]),
        "confidence_reasons": row.get("confidence_reasons") or [], "cluster_id": row.get("cluster_id"), "lba": int(row.get("lba") or 0),
        "absolute_lba": int(row.get("absolute_lba") or 0), "fragment_count": int(row.get("fragment_count") or 0),
        "timestamps": row.get("timestamps") or {}, "keywords": row.get("keywords") or [], "has_thumbnail": bool(row.get("thumbnail_path")),
        "preview_kind": row.get("preview_kind") or "none", "restored": bool(row.get("restored")), "restored_path": row.get("restored_path"),
        "source_id": row.get("source_id"),
    }


def file_detail_out(row: dict) -> dict:
    out = file_out(row)
    cand = row.get("candidate") or {}
    out.update({
        "sha256": row.get("sha256"),
        "integrity": cand.get("integrity"),
        "extents": cand.get("extents", [])[:64],
        "notes": cand.get("notes", []),
        "mft": cand.get("mft"),
        "partition_hint": cand.get("partition_hint"),
        "source_device": cand.get("source_device"),
    })
    return out


def cluster_out(row: dict) -> dict:
    return {"id": row["id"], "scan_id": row["session_id"], "label": row["label"], "file_count": int(row["file_count"]),
            "total_bytes": int(row["total_bytes"]), "lba_start": int(row["lba_start"]), "lba_end": int(row["lba_end"]),
            "dominant_extension": row["dominant_extension"], "time_start": row.get("time_start"), "time_end": row.get("time_end")}


def restore_out(row: dict, live_percent: Optional[float] = None) -> dict:
    total = int(row.get("total_files") or 0)
    done = int(row.get("restored_files") or 0) + int(row.get("failed_files") or 0)
    pct = live_percent if live_percent is not None else (100.0 if row["status"] in ("completed", "partial", "failed", "cancelled")
                                                         else round(100 * done / max(1, total), 2))
    return {"id": row["id"], "scan_id": row["session_id"], "status": row["status"], "destination": row["destination"],
            "total_files": total, "restored_files": int(row.get("restored_files") or 0), "failed_files": int(row.get("failed_files") or 0),
            "bytes_written": int(row.get("bytes_written") or 0), "percent": pct, "items": row.get("items") or [],
            "destination_check": row.get("destination_check"), "error": row.get("error"), "created_at": row["created_at"],
            "finished_at": row.get("finished_at")}


def report_out(row: dict) -> dict:
    return {"id": row["id"], "scan_id": row["session_id"], "title": row["title"], "file_name": row["file_name"],
            "size_bytes": int(row.get("size_bytes") or 0), "created_at": row["created_at"],
            "download_url": f"/api/reports/{row['id']}/download"}
