"""
db.py - persistence layer (PostgreSQL in production, SQLite for offline dev/tests).

The authoritative schema is database/schema.sql (Track C). This module:
  * opens a PostgreSQL pool (psycopg 3) or a SQLite connection,
  * applies schema.sql to an empty PostgreSQL database (or the embedded SQLite
    mirror below),
  * exposes a small repository API used by auth, the job manager and the routes.

SQL is written once with `:name` placeholders and translated per dialect, so
the repository code is identical for both back-ends.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

JSON_COLUMNS = {
    "sources", "file_types", "options", "progress", "totals", "health_warnings", "scan_summary", "confidence_reasons",
    "timestamps", "keywords", "candidate", "items", "destination_check", "summary", "details",
}
BOOL_COLUMNS = {"is_active", "restored"}

_PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def _strip_nul(value: Any) -> Any:
    """PostgreSQL TEXT and JSONB reject NUL (0x00) characters, which are common in text pulled from
    damaged or partially overwritten files. Remove them recursively before values reach the driver."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_nul(v) for v in value]
    return value


def _index_title(f: dict) -> str:
    """Searchable title: the original AND the smart name (a file must stay findable by its original
    name even when content-based naming suggested another one), plus a word-split form so that
    full-text parsers match "Invoice_4411_Acme.pdf" when the user searches for "invoice"."""
    names = list(dict.fromkeys(n for n in (f.get("file_name"), f.get("suggested_name")) if n))
    words = re.sub(r"[\W_]+", " ", " ".join(names)).strip()
    return " ".join(names + ([words] if words else []))


def _pg_params(params: Optional[dict]) -> dict:
    return {k: _strip_nul(v) if isinstance(v, str) else v for k, v in (params or {}).items()}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


# -----------------------------------------------------------------------------
# SQLite mirror of database/schema.sql (types simplified, same columns/constraints)
# -----------------------------------------------------------------------------
SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL, password_hash TEXT NOT NULL, full_name TEXT,
    role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('admin','operator','viewer')),
    is_active INTEGER NOT NULL DEFAULT 1, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uq ON users (lower(email));
CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, jti TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT, user_agent TEXT, ip_address TEXT);
CREATE TABLE IF NOT EXISTS recovery_sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, label TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('quick','deep')), target_scope TEXT NOT NULL CHECK (target_scope IN ('all','drive','folder')),
    sources TEXT NOT NULL DEFAULT '[]', folder_path TEXT, scenario TEXT NOT NULL DEFAULT 'custom', file_types TEXT NOT NULL DEFAULT '[]',
    options TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'queued', progress TEXT NOT NULL DEFAULT '{}',
    totals TEXT NOT NULL DEFAULT '{}', health_warnings TEXT NOT NULL DEFAULT '[]', scan_summary TEXT NOT NULL DEFAULT '{}',
    engine_scan_id TEXT, engine_exit_code INTEGER, candidates_path TEXT, error TEXT,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS recovery_sessions_user_idx ON recovery_sessions (user_id, created_at);
CREATE TABLE IF NOT EXISTS deletion_clusters (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE, label TEXT NOT NULL,
    file_count INTEGER NOT NULL, total_bytes INTEGER NOT NULL, lba_start INTEGER NOT NULL, lba_end INTEGER NOT NULL,
    dominant_extension TEXT NOT NULL, time_start TEXT, time_end TEXT);
CREATE TABLE IF NOT EXISTS recovered_files (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL, source_id TEXT NOT NULL, method TEXT NOT NULL, file_name TEXT, suggested_name TEXT,
    original_path TEXT, extension TEXT NOT NULL DEFAULT '', mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL, lba INTEGER NOT NULL DEFAULT 0, absolute_lba INTEGER NOT NULL DEFAULT 0,
    fragment_count INTEGER NOT NULL DEFAULT 0, confidence TEXT NOT NULL, confidence_score INTEGER NOT NULL,
    confidence_reasons TEXT NOT NULL DEFAULT '[]', cluster_id TEXT REFERENCES deletion_clusters(id) ON DELETE SET NULL,
    timestamps TEXT NOT NULL DEFAULT '{}', keywords TEXT NOT NULL DEFAULT '[]', candidate TEXT NOT NULL,
    sample_path TEXT, thumbnail_path TEXT, text_excerpt TEXT, preview_kind TEXT NOT NULL DEFAULT 'none',
    restored INTEGER NOT NULL DEFAULT 0, restored_path TEXT, sha256 TEXT, created_at TEXT NOT NULL,
    UNIQUE (session_id, candidate_id));
CREATE INDEX IF NOT EXISTS recovered_files_session_conf_idx ON recovered_files (session_id, confidence);
CREATE INDEX IF NOT EXISTS recovered_files_session_ext_idx ON recovered_files (session_id, extension);
CREATE TABLE IF NOT EXISTS semantic_index (
    file_id TEXT PRIMARY KEY REFERENCES recovered_files(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT,
    keywords_text TEXT NOT NULL DEFAULT '', content_excerpt TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'en',
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS restore_jobs (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, destination TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', options TEXT NOT NULL DEFAULT '{}', total_files INTEGER NOT NULL DEFAULT 0,
    restored_files INTEGER NOT NULL DEFAULT 0, failed_files INTEGER NOT NULL DEFAULT 0, bytes_written INTEGER NOT NULL DEFAULT 0,
    items TEXT NOT NULL DEFAULT '[]', destination_check TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, file_name TEXT NOT NULL,
    file_path TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT REFERENCES users(id) ON DELETE SET NULL, action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
"""


def _normalize_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if key in JSON_COLUMNS and isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if key in BOOL_COLUMNS:
        return bool(value)
    return value


def _normalize_row(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {k: _normalize_value(k, v) for k, v in row.items()}


class Database:
    """Thin dialect-neutral wrapper. All public methods are thread-safe."""

    def __init__(self, url: str, schema_sql_path: Optional[Path] = None) -> None:
        self.url = url
        self.schema_sql_path = schema_sql_path
        self.dialect = "postgresql" if url.startswith(("postgresql://", "postgres://")) else "sqlite"
        self._lock = threading.RLock()
        self._pool = None
        self._sqlite: Optional[sqlite3.Connection] = None
        if self.dialect == "postgresql":
            import psycopg  # noqa: F401  (import error => clear message at startup)
            try:
                from psycopg_pool import ConnectionPool

                self._pool = ConnectionPool(url, min_size=1, max_size=10, kwargs={"autocommit": False}, open=True)
            except ImportError:
                self._pool = None
        else:
            path = url.replace("sqlite:///", "", 1)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._sqlite.row_factory = sqlite3.Row
            self._sqlite.execute("PRAGMA journal_mode=WAL")
            self._sqlite.execute("PRAGMA foreign_keys=ON")
            self._sqlite.execute("PRAGMA busy_timeout=5000")

    # ------------------------------------------------------------------ plumbing
    def _sql(self, sql: str) -> str:
        if self.dialect == "postgresql":
            return _PARAM_RE.sub(lambda m: f"%({m.group(1)})s", sql)
        return sql

    def j(self, value: Any) -> Any:
        """Wrap a Python value for a JSON/JSONB column."""
        if self.dialect == "postgresql":
            from psycopg.types.json import Jsonb

            return Jsonb(_strip_nul(value))
        return json.dumps(value, default=str)

    @contextmanager
    def _pg_conn(self) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        if self._pool is not None:
            with self._pool.connection() as conn:
                conn.row_factory = dict_row
                yield conn
        else:
            with psycopg.connect(self.url, row_factory=dict_row) as conn:
                yield conn

    @contextmanager
    def transaction(self) -> Iterator["Tx"]:
        if self.dialect == "postgresql":
            with self._pg_conn() as conn:
                with conn.transaction():
                    yield Tx(self, conn)
        else:
            with self._lock:
                assert self._sqlite is not None
                self._sqlite.execute("BEGIN IMMEDIATE")
                try:
                    yield Tx(self, self._sqlite)
                    self._sqlite.execute("COMMIT")
                except BaseException:
                    self._sqlite.execute("ROLLBACK")
                    raise

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        with self.transaction() as tx:
            return tx.query(sql, params)

    def one(self, sql: str, params: Optional[dict] = None) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Optional[dict] = None) -> int:
        with self.transaction() as tx:
            return tx.execute(sql, params)

    # ------------------------------------------------------------------ schema
    def init_schema(self) -> None:
        if self.dialect == "sqlite":
            assert self._sqlite is not None
            with self._lock:
                self._sqlite.executescript(SQLITE_SCHEMA)
            return
        exists = self.one("SELECT to_regclass('public.users') IS NOT NULL AS ok")
        if exists and exists.get("ok"):
            return
        if not self.schema_sql_path or not Path(self.schema_sql_path).is_file():
            raise RuntimeError(f"Database is empty and schema.sql was not found at {self.schema_sql_path}")
        script = Path(self.schema_sql_path).read_text(encoding="utf-8")
        with self._pg_conn() as conn:
            conn.autocommit = True
            conn.execute(script)  # psycopg 3 runs multi-statement scripts without parameters

    def ping(self) -> bool:
        try:
            self.one("SELECT 1 AS ok")
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
        if self._sqlite is not None:
            self._sqlite.close()


class Tx:
    """A unit of work bound to one connection."""

    def __init__(self, db: Database, conn: Any) -> None:
        self.db = db
        self.conn = conn

    def _params(self, params: Optional[dict]) -> dict:
        return _pg_params(params) if self.db.dialect == "postgresql" else (params or {})

    def execute(self, sql: str, params: Optional[dict] = None) -> int:
        cur = self.conn.execute(self.db._sql(sql), self._params(params))
        return cur.rowcount if cur.rowcount is not None else 0

    def executemany(self, sql: str, rows: Iterable[dict]) -> None:
        rows = list(rows)
        if not rows:
            return
        if self.db.dialect == "postgresql":
            with self.conn.cursor() as cur:
                cur.executemany(self.db._sql(sql), [_pg_params(r) for r in rows])
        else:
            self.conn.executemany(sql, rows)

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        cur = self.conn.execute(self.db._sql(sql), self._params(params))
        rows = cur.fetchall()
        return [_normalize_row(dict(r)) for r in rows]  # type: ignore[misc]

    def one(self, sql: str, params: Optional[dict] = None) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None


# =============================================================================
# Repository
# =============================================================================
class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ users
    def create_user(self, email: str, password_hash: str, full_name: Optional[str], role: str = "operator") -> dict:
        uid, now = new_id(), utcnow()
        self.db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, is_active, failed_logins, created_at, updated_at) "
            "VALUES (:id, :email, :ph, :fn, :role, :active, 0, :now, :now)",
            {"id": uid, "email": email.strip(), "ph": password_hash, "fn": full_name, "role": role, "active": True, "now": now},
        )
        return self.get_user(uid)  # type: ignore[return-value]

    def count_users(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    def get_user(self, user_id: str) -> Optional[dict]:
        return self.db.one("SELECT * FROM users WHERE id = :id", {"id": user_id})

    def get_user_by_email(self, email: str) -> Optional[dict]:
        return self.db.one("SELECT * FROM users WHERE lower(email) = lower(:e)", {"e": email.strip()})

    def record_login(self, user_id: str, success: bool, lock_until: Optional[str] = None) -> None:
        if success:
            self.db.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = :now, updated_at = :now WHERE id = :id",
                            {"id": user_id, "now": utcnow()})
        else:
            self.db.execute("UPDATE users SET failed_logins = failed_logins + 1, locked_until = :lock, updated_at = :now WHERE id = :id",
                            {"id": user_id, "lock": lock_until, "now": utcnow()})

    # ------------------------------------------------------------------ auth sessions
    def create_auth_session(self, user_id: str, jti: str, expires_at: str, user_agent: Optional[str], ip: Optional[str]) -> None:
        self.db.execute(
            "INSERT INTO auth_sessions (id, user_id, jti, created_at, expires_at, user_agent, ip_address) "
            "VALUES (:id, :uid, :jti, :now, :exp, :ua, :ip)",
            {"id": new_id(), "uid": user_id, "jti": jti, "now": utcnow(), "exp": expires_at, "ua": (user_agent or "")[:300], "ip": ip},
        )

    def auth_session_active(self, jti: str) -> bool:
        row = self.db.one("SELECT revoked_at, expires_at FROM auth_sessions WHERE jti = :jti", {"jti": jti})
        return bool(row) and row["revoked_at"] is None  # type: ignore[index]

    def revoke_auth_session(self, jti: str) -> None:
        self.db.execute("UPDATE auth_sessions SET revoked_at = :now WHERE jti = :jti AND revoked_at IS NULL", {"jti": jti, "now": utcnow()})

    # ------------------------------------------------------------------ audit
    def audit(self, user_id: Optional[str], action: str, details: dict) -> None:
        self.db.execute("INSERT INTO audit_log (user_id, action, details, created_at) VALUES (:u, :a, :d, :now)",
                        {"u": user_id, "a": action, "d": self.db.j(details), "now": utcnow()})

    # ------------------------------------------------------------------ recovery sessions
    def create_scan_session(self, user_id: str, req: dict) -> dict:
        sid, now = new_id(), utcnow()
        self.db.execute(
            "INSERT INTO recovery_sessions (id, user_id, label, mode, target_scope, sources, folder_path, scenario, file_types, "
            "options, status, progress, totals, health_warnings, scan_summary, created_at) VALUES "
            "(:id, :uid, :label, :mode, :scope, :sources, :folder, :scenario, :types, :options, 'queued', :progress, :totals, "
            ":hw, :summary, :now)",
            {
                "id": sid, "uid": user_id, "label": req["label"], "mode": req["mode"], "scope": req["target_scope"],
                "sources": self.db.j(req.get("sources", [])), "folder": req.get("folder_path"), "scenario": req.get("scenario", "custom"),
                "types": self.db.j(req.get("file_types", [])), "options": self.db.j(req.get("options", {})),
                "progress": self.db.j(req.get("progress", {})), "totals": self.db.j({}), "hw": self.db.j([]),
                "summary": self.db.j({}), "now": now,
            },
        )
        return self.get_scan_session(sid)  # type: ignore[return-value]

    _SESSION_UPDATABLE = {"status", "progress", "totals", "health_warnings", "scan_summary", "engine_scan_id", "engine_exit_code",
                          "candidates_path", "error", "started_at", "finished_at", "label"}

    def update_scan_session(self, session_id: str, **fields: Any) -> None:
        sets, params = [], {"id": session_id}
        for k, v in fields.items():
            if k not in self._SESSION_UPDATABLE:
                raise ValueError(f"column {k} is not updatable")
            sets.append(f"{k} = :{k}")
            params[k] = self.db.j(v) if k in JSON_COLUMNS else v
        if sets:
            self.db.execute(f"UPDATE recovery_sessions SET {', '.join(sets)} WHERE id = :id", params)

    def get_scan_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        sql = "SELECT * FROM recovery_sessions WHERE id = :id"
        params: dict[str, Any] = {"id": session_id}
        if user_id:
            sql += " AND user_id = :uid"
            params["uid"] = user_id
        return self.db.one(sql, params)

    def list_scan_sessions(self, user_id: str, page: int, page_size: int, q: Optional[str] = None) -> tuple[list[dict], int]:
        where, params = "WHERE user_id = :uid", {"uid": user_id}
        if q:
            where += " AND (lower(label) LIKE :q OR lower(status) LIKE :q OR lower(mode) LIKE :q)"
            params["q"] = f"%{q.lower()}%"
        total = self.db.one(f"SELECT COUNT(*) AS n FROM recovery_sessions {where}", params)
        rows = self.db.query(f"SELECT * FROM recovery_sessions {where} ORDER BY created_at DESC LIMIT :lim OFFSET :off",
                             dict(params, lim=page_size, off=(page - 1) * page_size))
        return rows, int(total["n"]) if total else 0

    def delete_scan_session(self, session_id: str, user_id: str) -> int:
        return self.db.execute("DELETE FROM recovery_sessions WHERE id = :id AND user_id = :uid", {"id": session_id, "uid": user_id})

    def interrupted_sessions(self) -> list[dict]:
        return self.db.query("SELECT * FROM recovery_sessions WHERE status IN ('queued','running','analyzing')")

    # ------------------------------------------------------------------ files / clusters / semantic index
    def replace_analysis_results(self, session_id: str, user_id: str, files: list[dict], clusters: list[dict]) -> None:
        """Atomically store the analysis output of a session (idempotent re-analysis)."""
        now = utcnow()
        j = self.db.j
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM recovered_files WHERE session_id = :s", {"s": session_id})
            tx.execute("DELETE FROM deletion_clusters WHERE session_id = :s", {"s": session_id})
            tx.executemany(
                "INSERT INTO deletion_clusters (id, session_id, label, file_count, total_bytes, lba_start, lba_end, dominant_extension, "
                "time_start, time_end) VALUES (:id, :session_id, :label, :file_count, :total_bytes, :lba_start, :lba_end, "
                ":dominant_extension, :time_start, :time_end)",
                [dict(c, session_id=session_id) for c in clusters],
            )
            tx.executemany(
                "INSERT INTO recovered_files (id, session_id, candidate_id, source_id, method, file_name, suggested_name, original_path, "
                "extension, mime_type, size_bytes, lba, absolute_lba, fragment_count, confidence, confidence_score, confidence_reasons, "
                "cluster_id, timestamps, keywords, candidate, sample_path, thumbnail_path, text_excerpt, preview_kind, restored, created_at) "
                "VALUES (:id, :session_id, :candidate_id, :source_id, :method, :file_name, :suggested_name, :original_path, :extension, "
                ":mime_type, :size_bytes, :lba, :absolute_lba, :fragment_count, :confidence, :confidence_score, :confidence_reasons, "
                ":cluster_id, :timestamps, :keywords, :candidate, :sample_path, :thumbnail_path, :text_excerpt, :preview_kind, :restored, :now)",
                [
                    dict(
                        f, session_id=session_id, now=now, restored=False,
                        confidence_reasons=j(f["confidence_reasons"]), timestamps=j(f["timestamps"]),
                        keywords=j(f["keywords"]), candidate=j(f["candidate"]),
                    )
                    for f in files
                ],
            )
            tx.executemany(
                "INSERT INTO semantic_index (file_id, session_id, user_id, title, keywords_text, content_excerpt, created_at) "
                "VALUES (:file_id, :session_id, :user_id, :title, :kw, :excerpt, :now)",
                [
                    {
                        "file_id": f["id"], "session_id": session_id, "user_id": user_id,
                        "title": _index_title(f),
                        "kw": " ".join(f["keywords"]), "excerpt": (f.get("text_excerpt") or "")[:4000], "now": now,
                    }
                    for f in files
                    if f["keywords"] or f.get("text_excerpt") or f.get("file_name")
                ],
            )

    _FILE_SORTS = {
        "name": "COALESCE(file_name, suggested_name, candidate_id)", "size": "size_bytes", "confidence": "confidence_score",
        "lba": "absolute_lba", "extension": "extension", "modified": "created_at",
    }

    def list_files(self, session_id: str, page: int, page_size: int, extension: Optional[list[str]] = None,
                   confidence: Optional[list[str]] = None, q: Optional[str] = None, cluster_id: Optional[str] = None,
                   method: Optional[str] = None, sort: str = "confidence", order: str = "desc") -> tuple[list[dict], int]:
        where, params = ["session_id = :sid"], {"sid": session_id}
        if extension:
            names = []
            for i, e in enumerate(extension):
                params[f"e{i}"] = e.lower().lstrip(".")
                names.append(f":e{i}")
            where.append(f"extension IN ({', '.join(names)})")
        if confidence:
            names = []
            for i, c in enumerate(confidence):
                params[f"c{i}"] = c.lower()
                names.append(f":c{i}")
            where.append(f"confidence IN ({', '.join(names)})")
        if q:
            params["q"] = f"%{q.lower()}%"
            where.append("(lower(COALESCE(file_name,'')) LIKE :q OR lower(COALESCE(suggested_name,'')) LIKE :q "
                         "OR lower(COALESCE(original_path,'')) LIKE :q)")
        if cluster_id:
            params["cl"] = cluster_id
            where.append("cluster_id = :cl")
        if method:
            params["m"] = method
            where.append("method = :m")
        w = " AND ".join(where)
        col = self._FILE_SORTS.get(sort, "confidence_score")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        total = self.db.one(f"SELECT COUNT(*) AS n FROM recovered_files WHERE {w}", params)
        rows = self.db.query(
            f"SELECT * FROM recovered_files WHERE {w} ORDER BY {col} {direction}, id LIMIT :lim OFFSET :off",
            dict(params, lim=page_size, off=(page - 1) * page_size),
        )
        return rows, int(total["n"]) if total else 0

    def get_file(self, session_id: str, file_id: str) -> Optional[dict]:
        return self.db.one("SELECT * FROM recovered_files WHERE id = :id AND session_id = :sid", {"id": file_id, "sid": session_id})

    def get_files(self, session_id: str, file_ids: list[str]) -> list[dict]:
        if not file_ids:
            return []
        params: dict[str, Any] = {"sid": session_id}
        names = []
        for i, fid in enumerate(file_ids):
            params[f"f{i}"] = fid
            names.append(f":f{i}")
        return self.db.query(f"SELECT * FROM recovered_files WHERE session_id = :sid AND id IN ({', '.join(names)})", params)

    def all_files(self, session_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM recovered_files WHERE session_id = :sid ORDER BY absolute_lba", {"sid": session_id})

    def mark_restored(self, file_id: str, path: str, sha256: Optional[str]) -> None:
        self.db.execute("UPDATE recovered_files SET restored = :t, restored_path = :p, sha256 = :h WHERE id = :id",
                        {"t": True, "p": path, "h": sha256, "id": file_id})

    def list_clusters(self, session_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM deletion_clusters WHERE session_id = :sid ORDER BY lba_start", {"sid": session_id})

    def stats(self, session_id: str) -> dict:
        out: dict[str, Any] = {"by_extension": {}, "by_confidence": {}, "by_method": {}, "total_files": 0, "total_bytes": 0}
        for col, key in (("extension", "by_extension"), ("confidence", "by_confidence"), ("method", "by_method")):
            for r in self.db.query(f"SELECT {col} AS k, COUNT(*) AS n FROM recovered_files WHERE session_id = :sid GROUP BY {col} "
                                   f"ORDER BY n DESC", {"sid": session_id}):
                out[key][r["k"] or "(none)"] = int(r["n"])
        t = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM recovered_files WHERE session_id = :sid",
                        {"sid": session_id})
        if t:
            out["total_files"], out["total_bytes"] = int(t["n"]), int(t["b"])
        return out

    def search(self, user_id: str, q: str, limit: int = 50) -> list[dict]:
        if self.db.dialect == "postgresql":
            sql = ("SELECT f.*, s.label AS scan_label, si.keywords_text, ts_rank(si.tsv, websearch_to_tsquery('english', :q)) AS rank "
                   "FROM semantic_index si JOIN recovered_files f ON f.id = si.file_id JOIN recovery_sessions s ON s.id = si.session_id "
                   "WHERE si.user_id = :uid AND si.tsv @@ websearch_to_tsquery('english', :q) ORDER BY rank DESC LIMIT :lim")
            return self.db.query(sql, {"uid": user_id, "q": q, "lim": limit})
        terms = [t for t in re.split(r"\W+", q.lower()) if t]
        if not terms:
            return []
        conds, params = [], {"uid": user_id, "lim": limit}
        for i, t in enumerate(terms):
            params[f"t{i}"] = f"%{t}%"
            conds.append(f"(lower(si.keywords_text) LIKE :t{i} OR lower(COALESCE(si.title,'')) LIKE :t{i} "
                         f"OR lower(si.content_excerpt) LIKE :t{i})")
        sql = ("SELECT f.*, s.label AS scan_label, si.keywords_text, 1.0 AS rank FROM semantic_index si "
               "JOIN recovered_files f ON f.id = si.file_id JOIN recovery_sessions s ON s.id = si.session_id "
               f"WHERE si.user_id = :uid AND {' AND '.join(conds)} ORDER BY f.confidence_score DESC LIMIT :lim")
        return self.db.query(sql, params)

    # ------------------------------------------------------------------ restore jobs
    def create_restore_job(self, session_id: str, user_id: str, destination: str, options: dict, items: list[dict]) -> dict:
        jid = new_id()
        self.db.execute(
            "INSERT INTO restore_jobs (id, session_id, user_id, destination, status, options, total_files, items, created_at) "
            "VALUES (:id, :sid, :uid, :dest, 'queued', :opt, :n, :items, :now)",
            {"id": jid, "sid": session_id, "uid": user_id, "dest": destination, "opt": self.db.j(options), "n": len(items),
             "items": self.db.j(items), "now": utcnow()},
        )
        return self.get_restore_job(jid)  # type: ignore[return-value]

    _RESTORE_UPDATABLE = {"status", "restored_files", "failed_files", "bytes_written", "items", "destination_check", "error", "finished_at"}

    def update_restore_job(self, job_id: str, **fields: Any) -> None:
        sets, params = [], {"id": job_id}
        for k, v in fields.items():
            if k not in self._RESTORE_UPDATABLE:
                raise ValueError(k)
            sets.append(f"{k} = :{k}")
            params[k] = self.db.j(v) if k in JSON_COLUMNS else v
        if sets:
            self.db.execute(f"UPDATE restore_jobs SET {', '.join(sets)} WHERE id = :id", params)

    def get_restore_job(self, job_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        sql, params = "SELECT * FROM restore_jobs WHERE id = :id", {"id": job_id}
        if user_id:
            sql += " AND user_id = :uid"
            params["uid"] = user_id
        return self.db.one(sql, params)

    def list_restore_jobs(self, session_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM restore_jobs WHERE session_id = :sid ORDER BY created_at DESC", {"sid": session_id})

    # ------------------------------------------------------------------ reports
    def create_report(self, session_id: str, user_id: str, title: str, file_name: str, file_path: str, size: int, summary: dict) -> dict:
        rid = new_id()
        self.db.execute(
            "INSERT INTO reports (id, session_id, user_id, title, file_name, file_path, size_bytes, summary, created_at) "
            "VALUES (:id, :sid, :uid, :t, :fn, :fp, :sz, :sum, :now)",
            {"id": rid, "sid": session_id, "uid": user_id, "t": title, "fn": file_name, "fp": file_path, "sz": size,
             "sum": self.db.j(summary), "now": utcnow()},
        )
        return self.get_report(rid)  # type: ignore[return-value]

    def get_report(self, report_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        sql, params = "SELECT * FROM reports WHERE id = :id", {"id": report_id}
        if user_id:
            sql += " AND user_id = :uid"
            params["uid"] = user_id
        return self.db.one(sql, params)

    def list_reports(self, user_id: str, session_id: Optional[str] = None) -> list[dict]:
        sql, params = "SELECT * FROM reports WHERE user_id = :uid", {"uid": user_id}
        if session_id:
            sql += " AND session_id = :sid"
            params["sid"] = session_id
        return self.db.query(sql + " ORDER BY created_at DESC", params)
