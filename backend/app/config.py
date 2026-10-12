"""
config.py - runtime settings for the recovery service (Track B).

All values come from environment variables (or a `.env` file next to
recovery_service.py) so that the three tracks can run with different
configurations without code changes:

    DATABASE_URL        postgresql://user:pass@localhost:5432/file_recovery
                        (falls back to sqlite:///<DATA_DIR>/recovery.db when unset)
    ENGINE_PATH         path to win_recovery_engine.exe
    ENGINE_MODE         auto | real | mock   (auto = real if ENGINE_PATH exists, else mock)
    DATA_DIR            working directory for candidates.json, samples, thumbnails, reports
    SAMPLE_DIR          where the engine writes preview samples (must NOT be a scanned drive)
    JWT_SECRET          HMAC secret for access tokens (REQUIRED in production)
    JWT_TTL_MINUTES     access-token lifetime (default 480)
    CORS_ORIGINS        comma separated list (default http://localhost:3000)
    SCHEMA_SQL_PATH     path to database/schema.sql (auto-applied to an empty PostgreSQL DB)
"""
from __future__ import annotations

import os
import platform
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE lines) so python-dotenv is not required."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


_load_dotenv()

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


def _path_env(name: str, default: str) -> str:
    """Relative paths in .env (e.g. ENGINE_PATH=..\\engine\\build\\...) are relative to the backend folder,
    not to whatever directory the service happened to be started from."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    p = Path(value).expanduser()
    return str(p if p.is_absolute() else (BACKEND_ROOT / p).resolve())


def _default_engine_path() -> str:
    exe = "win_recovery_engine.exe" if platform.system() == "Windows" else "win_recovery_engine"
    for p in (REPO_ROOT / "engine" / "build" / "Release" / exe, REPO_ROOT / "engine" / "build" / exe, BACKEND_ROOT / "bin" / exe):
        if p.is_file():
            return str(p)
    return str(REPO_ROOT / "engine" / "build" / "Release" / exe)


@dataclass
class Settings:
    app_name: str = "Windows HDD File Recovery Service"
    version: str = "1.0.0"
    data_dir: Path = field(default_factory=lambda: Path(_path_env("DATA_DIR", str(BACKEND_ROOT / "data"))).resolve())
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    engine_path: str = field(default_factory=lambda: _path_env("ENGINE_PATH", "") or _default_engine_path())
    engine_mode: str = field(default_factory=lambda: os.environ.get("ENGINE_MODE", "auto").lower())
    sample_dir: str = field(default_factory=lambda: _path_env("SAMPLE_DIR", ""))
    sample_bytes: int = field(default_factory=lambda: int(os.environ.get("SAMPLE_BYTES", str(2 * 1024 * 1024))))
    sample_budget_mb: int = field(default_factory=lambda: int(os.environ.get("SAMPLE_BUDGET_MB", "2048")))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", ""))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.environ.get("JWT_TTL_MINUTES", "480")))
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
    )
    schema_sql_path: Path = field(default_factory=lambda: Path(_path_env("SCHEMA_SQL_PATH", str(REPO_ROOT / "database" / "schema.sql"))))
    analysis_workers: int = field(default_factory=lambda: int(os.environ.get("ANALYSIS_WORKERS", str(min(8, (os.cpu_count() or 2))))))
    max_candidates: int = field(default_factory=lambda: int(os.environ.get("MAX_CANDIDATES", "500000")))
    allow_registration: bool = field(default_factory=lambda: os.environ.get("ALLOW_REGISTRATION", "true").lower() == "true")
    # Hosted website(s) allowed to use this local agent: one origin per line in backend/web_origins.txt
    # (written by scripts/deploy-firebase.ps1, e.g. https://my-project.web.app). Only listed sites may call it.
    web_origins_file: Path = field(default_factory=lambda: Path(_path_env("WEB_ORIGINS_FILE", str(BACKEND_ROOT / "web_origins.txt"))))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("scans", "thumbnails", "reports", "restore_manifests", "mock_disk"):
            (self.data_dir / sub).mkdir(exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'recovery.db').as_posix()}"
        if self.jwt_secret.lower().startswith("change-me"):
            self.jwt_secret = ""  # never sign tokens with the published placeholder from .env.example
        if not self.jwt_secret:
            # Development fallback: persisted random secret so tokens survive restarts.
            secret_file = self.data_dir / ".jwt_secret"
            if secret_file.is_file():
                self.jwt_secret = secret_file.read_text().strip()
            else:
                self.jwt_secret = secrets.token_urlsafe(48)
                secret_file.write_text(self.jwt_secret)
        if self.web_origins_file.is_file():
            for line in self.web_origins_file.read_text(encoding="utf-8").splitlines():
                origin = line.strip().rstrip("/")
                if origin and not origin.startswith("#") and origin not in self.cors_origins:
                    self.cors_origins.append(origin)
        if not self.sample_dir:
            self.sample_dir = str(self.data_dir / "samples")

    @property
    def use_mock_engine(self) -> bool:
        if self.engine_mode == "mock":
            return True
        if self.engine_mode == "real":
            return False
        return not Path(self.engine_path).is_file()

    @property
    def is_windows(self) -> bool:
        return platform.system() == "Windows"


settings = Settings()
