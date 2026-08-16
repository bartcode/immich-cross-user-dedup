"""Session state for the web UI: config, client, scan result, background jobs."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from immich_dedup.core.api import ImmichClient
from immich_dedup.core.config import DedupConfig, save_env
from immich_dedup.core.models import ScanResult
from immich_dedup.core.preflight import PreflightReport, run_preflight


@dataclass
class JobStatus:
    kind: str | None = None  # 'scan' | 'apply' | 'undo' | None
    running: bool = False
    stage: str = ""
    current: int = 0
    total: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "running": self.running,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Session:
    config: DedupConfig
    client: ImmichClient
    reports_dir: Path
    preflight: PreflightReport | None = None
    scan_result: ScanResult | None = None
    last_result: dict[str, Any] | None = None  # summary of the last finished job
    job: JobStatus = field(default_factory=JobStatus)
    env_file: Path = field(default_factory=lambda: Path(".env"))

    def __post_init__(self) -> None:
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()

    # -- job runner ---------------------------------------------------------

    def run_job(self, kind: str, fn: Callable[[JobStatus], dict[str, Any]]) -> dict[str, Any]:
        """Run fn in the background, one job at a time. Returns immediately."""
        with self._state_lock:
            if self.job.running:
                raise JobBusyError(f"a {self.job.kind} job is already running")
            self.job = JobStatus(kind=kind, running=True, started_at=_now())
        thread = threading.Thread(target=self._execute, args=(kind, fn), daemon=True)
        thread.start()
        return self.job.as_dict()

    def _execute(self, kind: str, fn: Callable[[JobStatus], dict[str, Any]]) -> None:
        status = self.job

        def progress(stage: str, current: int, total: int | None) -> None:
            with self._state_lock:
                status.stage = stage
                status.current = current
                status.total = total

        try:
            result = fn(progress)
            with self._state_lock:
                status.running = False
                status.finished_at = _now()
                status.stage = "done"
                self.last_result = {"kind": kind, **result}
        except BaseException as error:  # noqa: BLE001 - surfaced to the UI
            with self._state_lock:
                status.running = False
                status.finished_at = _now()
                status.error = f"{type(error).__name__}: {error}"

    # -- helpers ------------------------------------------------------------

    def is_configured(self) -> bool:
        return all(
            (
                self.config.immich_url,
                self.config.primary_email,
                self.config.secondary_email,
                self.config.primary_api_key,
                self.config.secondary_api_key,
            )
        )

    def reconfigure(
        self,
        *,
        immich_url: str,
        primary_email: str,
        secondary_email: str,
        primary_api_key: str = "",
        secondary_api_key: str = "",
        persist: bool = True,
        client_factory: Callable[[DedupConfig], ImmichClient] | None = None,
    ) -> DedupConfig:
        """Swap the connection details, reset session state, and optionally
        persist to the .env file. Blank API keys keep the current ones."""
        if self.job.running:
            raise JobBusyError("cannot change the connection while a job is running")

        config = DedupConfig(
            immich_url=immich_url.strip().rstrip("/"),
            primary_email=primary_email.strip().lower(),
            secondary_email=secondary_email.strip().lower(),
            primary_api_key=primary_api_key.strip() or self.config.primary_api_key,
            secondary_api_key=secondary_api_key.strip() or self.config.secondary_api_key,
            reports_dir=self.reports_dir,
        )
        if not config.immich_url or not config.primary_email or not config.secondary_email:
            raise ValueError("Immich URL and both email addresses are required")

        factory = client_factory or (lambda c: ImmichClient(c.immich_url, c.primary_api_key, c.secondary_api_key))
        new_client = factory(config)
        previous = self.client
        with self._state_lock:
            self.config = config
            self.client = new_client
            self.preflight = None
            self.scan_result = None
            self.last_result = None
        previous.close()

        if persist:
            save_env(self.env_file, {
                "IMMICH_URL": config.immich_url,
                "PRIMARY_EMAIL": config.primary_email,
                "SECONDARY_EMAIL": config.secondary_email,
                "PRIMARY_API_KEY": config.primary_api_key,
                "SECONDARY_API_KEY": config.secondary_api_key,
            })
        return config

    def ensure_preflight(self) -> PreflightReport:
        if self.preflight is None:
            self.preflight = run_preflight(self.client, self.config)
        return self.preflight

    def journals(self) -> list[dict[str, Any]]:
        files = sorted(self.reports_dir.glob("dedup_apply_*.jsonl"), reverse=True)
        return [
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
            for path in files
        ]

    def journal_path(self, name: str) -> Path:
        """Resolve a journal file name inside reports_dir, rejecting traversal."""
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("invalid journal name")
        path = (self.reports_dir / name).resolve()
        if path.parent != self.reports_dir.resolve():
            raise ValueError("invalid journal name")
        return path

    def new_journal_path(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:6]
        return self.reports_dir / f"dedup_apply_{stamp}-{unique}.jsonl"


class JobBusyError(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()
