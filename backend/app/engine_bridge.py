"""
engine_bridge.py - process boundary between Track B (Python) and Track A (C++).

EngineClient wraps win_recovery_engine.exe using ONLY the documented CLI
contract (contracts/README.md): JSON documents for inventory commands and
NDJSON event streams for scan/recover. MockEngine (mock_engine.py) implements
the identical interface so the backend and the web UI can be developed and
demoed on any OS without raw disk access.

Subprocesses are driven from worker threads (not asyncio) because uvicorn on
Windows may run a SelectorEventLoop, which cannot spawn subprocesses.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

log = logging.getLogger("engine")

EventCallback = Callable[[dict], None]

EXIT_CODES = {
    0: "ok", 1: "usage error", 2: "administrator rights required", 3: "unsafe destination",
    4: "source error", 5: "cancelled", 10: "internal engine error",
}


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str, exit_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class Engine(Protocol):
    """Interface shared by EngineClient and MockEngine."""

    is_mock: bool

    def version(self) -> Optional[str]: ...
    def available(self) -> bool: ...
    def list_drives(self) -> dict: ...
    def check_elevation(self) -> dict: ...
    def validate_destination(self, destination: str, sources: list[str], required_bytes: int = 0) -> dict: ...
    def scan(self, *, scan_id: str, mode: str, scope: str, sources: list[str], folder: Optional[str], out_file: Path,
             sample_dir: Optional[Path], cancel_file: Path, types: list[str], allow_non_hdd: bool, unallocated_only: bool,
             on_event: EventCallback) -> int: ...
    def recover(self, *, manifest_path: Path, cancel_file: Path, on_event: EventCallback) -> int: ...


class EngineClient:
    is_mock = False

    def __init__(self, path: str, sample_bytes: int = 2 * 1024 * 1024, sample_budget_mb: int = 2048, max_candidates: int = 500000) -> None:
        self.path = path
        self.sample_bytes = sample_bytes
        self.sample_budget_mb = sample_budget_mb
        self.max_candidates = max_candidates
        self._version: Optional[str] = None

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _creationflags() -> int:
        return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

    def available(self) -> bool:
        return Path(self.path).is_file()

    def _run_json(self, args: list[str], timeout: float = 60.0, ok_codes: tuple[int, ...] = (0,)) -> dict:
        if not self.available():
            raise EngineError("ENGINE_MISSING", f"Engine binary not found at {self.path}")
        try:
            p = subprocess.run([self.path, *args], capture_output=True, timeout=timeout, creationflags=self._creationflags())
        except subprocess.TimeoutExpired as exc:
            raise EngineError("ENGINE_TIMEOUT", f"engine {' '.join(args)} timed out") from exc
        out = p.stdout.decode("utf-8", "replace").strip()
        doc: Optional[dict] = None
        try:
            doc = json.loads(out)  # inventory commands print exactly one JSON document
        except ValueError:
            for line in reversed(out.splitlines()):  # error paths print an NDJSON error event
                if line.strip().startswith("{"):
                    try:
                        doc = json.loads(line)
                        break
                    except ValueError:
                        continue
        if not isinstance(doc, dict):
            raise EngineError("ENGINE_BAD_OUTPUT", f"engine returned non-JSON output: {out[:200]!r}", p.returncode)
        if doc.get("event") == "error":
            raise EngineError(doc.get("code", "ENGINE_ERROR"), doc.get("message", "engine error"), p.returncode)
        if p.returncode not in ok_codes:
            raise EngineError("ENGINE_EXIT", f"engine exited with {p.returncode} ({EXIT_CODES.get(p.returncode, '?')})", p.returncode)
        return doc

    def _stream(self, args: list[str], on_event: EventCallback, cancel_file: Optional[Path] = None) -> int:
        if not self.available():
            raise EngineError("ENGINE_MISSING", f"Engine binary not found at {self.path}")
        log.info("engine %s", " ".join(args))
        proc = subprocess.Popen([self.path, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=self._creationflags())
        stderr_chunks: list[bytes] = []

        def drain_stderr() -> None:
            assert proc.stderr is not None
            for chunk in iter(lambda: proc.stderr.read(4096), b""):  # type: ignore[union-attr]
                stderr_chunks.append(chunk)

        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        killer_started = threading.Event()

        def watchdog() -> None:
            # Engine polls the cancel file itself; kill only if it ignores it for 20 s.
            while proc.poll() is None:
                if cancel_file is not None and cancel_file.exists() and not killer_started.is_set():
                    killer_started.set()
                    deadline = time.time() + 20
                    while proc.poll() is None and time.time() < deadline:
                        time.sleep(0.5)
                    if proc.poll() is None:
                        log.warning("engine ignored cancel request; terminating")
                        proc.kill()
                time.sleep(0.5)

        threading.Thread(target=watchdog, daemon=True).start()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                log.warning("unparseable engine line: %s", line[:200])
                continue
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001 - a consumer bug must not kill the stream
                log.exception("event handler failed")
        code = proc.wait()
        t.join(timeout=2)
        if stderr_chunks:
            log.info("engine stderr: %s", b"".join(stderr_chunks).decode("utf-8", "replace")[:2000])
        return code

    # ------------------------------------------------------------------ contract commands
    def version(self) -> Optional[str]:
        if self._version is None and self.available():
            try:
                self._version = self._run_json(["version"], timeout=10).get("version")
            except EngineError:
                self._version = None
        return self._version

    def list_drives(self) -> dict:
        return self._run_json(["list-drives"], timeout=90)

    def check_elevation(self) -> dict:
        return self._run_json(["check-elevation"], timeout=10)

    def validate_destination(self, destination: str, sources: list[str], required_bytes: int = 0) -> dict:
        args = ["validate-dest", "--dest", destination, "--required-bytes", str(int(required_bytes))]
        for s in sources:
            args += ["--source", s]
        return self._run_json(args, timeout=30, ok_codes=(0, 3))

    def scan(self, *, scan_id: str, mode: str, scope: str, sources: list[str], folder: Optional[str], out_file: Path,
             sample_dir: Optional[Path], cancel_file: Path, types: list[str], allow_non_hdd: bool, unallocated_only: bool,
             on_event: EventCallback) -> int:
        args = ["scan", "--mode", mode, "--scope", scope, "--out", str(out_file), "--scan-id", scan_id,
                "--cancel-file", str(cancel_file), "--max-candidates", str(self.max_candidates)]
        for s in sources:
            args += ["--source", s]
        if folder:
            args += ["--folder", folder]
        if sample_dir:
            args += ["--sample-dir", str(sample_dir), "--sample-bytes", str(self.sample_bytes),
                     "--sample-budget-mb", str(self.sample_budget_mb)]
        if types:
            args += ["--types", ",".join(t.lower().lstrip(".") for t in types)]
        if allow_non_hdd:
            args.append("--allow-non-hdd")
        if unallocated_only:
            args.append("--unallocated-only")
        return self._stream(args, on_event, cancel_file)

    def recover(self, *, manifest_path: Path, cancel_file: Path, on_event: EventCallback) -> int:
        return self._stream(["recover", "--manifest", str(manifest_path), "--cancel-file", str(cancel_file)], on_event, cancel_file)


def create_engine(settings) -> "Engine":  # noqa: ANN001
    if settings.use_mock_engine:
        from .mock_engine import MockEngine

        log.warning("Using MOCK engine (ENGINE_MODE=%s, ENGINE_PATH=%s)", settings.engine_mode, settings.engine_path)
        return MockEngine(Path(settings.data_dir) / "mock_disk")
    return EngineClient(settings.engine_path, settings.sample_bytes, settings.sample_budget_mb, settings.max_candidates)
