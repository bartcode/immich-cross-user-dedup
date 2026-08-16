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
from immich_dedup.core.config import DedupConfig, SecondaryCredentials, empty_config, save_env, secondary_env_values
from immich_dedup.core.models import ScanResult
from immich_dedup.core.preflight import PreflightReport, run_preflight
from immich_dedup.core.serialize import load_scan, save_scan


@dataclass
class JobStatus:
    kind: str | None = None  # 'scan' | 'apply' | 'undo' | None
    running: bool = False
    stage: str = ""
    current: int = 0
    total: int | None = None
    error: str | None = None
    cancelled: bool = False
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
            "cancelled": self.cancelled,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobCancelledError(Exception):
    """Raised inside a running job after cancel_job() was called."""


def _client_keys(config: DedupConfig) -> dict[str, str]:
    keys = {config.primary_email: config.primary_api_key}
    keys.update({secondary.email: secondary.api_key for secondary in config.secondaries})
    return keys


def build_client(config: DedupConfig, transport=None) -> ImmichClient:
    return ImmichClient(config.immich_url, _client_keys(config), transport=transport)


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
        self._cancel = threading.Event()
        self._restore_scan()

    # -- scan persistence ----------------------------------------------------

    @property
    def scan_path(self) -> Path:
        return self.reports_dir / "dedup_scan.json"

    def _restore_scan(self) -> None:
        """Reload the last persisted scan when it belongs to the same users."""
        if self.scan_result is not None or not self.is_configured():
            return
        stored = load_scan(self.scan_path)
        if stored is None:
            return
        same_primary = stored.primary.email == self.config.primary_email
        same_secondaries = {user.email for user in stored.secondaries} == set(self.config.secondary_emails)
        if same_primary and same_secondaries:
            self.scan_result = stored

    def persist_scan(self) -> None:
        if self.scan_result is not None:
            try:
                save_scan(self.scan_path, self.scan_result, immich_url=self.config.immich_url)
            except OSError as error:  # persistence is best-effort
                print(f"warning: could not persist scan results: {error}")

    # -- job runner ---------------------------------------------------------

    def run_job(self, kind: str, fn: Callable[[JobStatus], dict[str, Any]]) -> dict[str, Any]:
        """Run fn in the background, one job at a time. Returns immediately."""
        with self._state_lock:
            if self.job.running:
                raise JobBusyError(f"a {self.job.kind} job is already running")
            self.job = JobStatus(kind=kind, running=True, started_at=_now())
        self._cancel.clear()
        thread = threading.Thread(target=self._execute, args=(kind, fn), daemon=True)
        thread.start()
        return self.job.as_dict()

    def cancel_job(self) -> bool:
        """Request cancellation of the running job. The job aborts at its next
        progress checkpoint. Returns False when nothing is running."""
        if not self.job.running:
            return False
        self._cancel.set()
        return True

    def _execute(self, kind: str, fn: Callable[[JobStatus], dict[str, Any]]) -> None:
        status = self.job

        def progress(stage: str, current: int, total: int | None) -> None:
            if self._cancel.is_set():
                raise JobCancelledError(kind)
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
        except JobCancelledError:
            with self._state_lock:
                status.running = False
                status.finished_at = _now()
                status.cancelled = True
                status.stage = "cancelled"
                self.last_result = {
                    "kind": kind,
                    "cancelled": True,
                    "note": "cancelled — any work already done is journaled and undoable",
                }
        except BaseException as error:  # noqa: BLE001 - surfaced to the UI
            with self._state_lock:
                status.running = False
                status.finished_at = _now()
                status.error = f"{type(error).__name__}: {error}"

    # -- helpers ------------------------------------------------------------

    def is_configured(self) -> bool:
        return (
            bool(self.config.immich_url)
            and bool(self.config.primary_email)
            and bool(self.config.primary_api_key)
            and bool(self.config.secondaries)
            and all(
                secondary.email and secondary.api_key for secondary in self.config.secondaries
            )
        )

    def reconfigure(
        self,
        *,
        immich_url: str,
        primary_email: str,
        primary_api_key: str = "",
        secondaries: list[dict[str, str]] | None = None,
        persist: bool = True,
        client_factory: Callable[[DedupConfig], ImmichClient] | None = None,
    ) -> DedupConfig:
        """Swap the connection details, reset session state, and optionally
        persist to the .env file. Blank API keys keep the current ones (matched
        by email for secondaries)."""
        if self.job.running:
            raise JobBusyError("cannot change the connection while a job is running")

        email = primary_email.strip().lower()
        if not immich_url.strip() or not email:
            raise ValueError("Immich URL and the primary email are required")

        kept = {secondary.email: secondary for secondary in self.config.secondaries}
        resolved: list[SecondaryCredentials] = []
        for entry in secondaries or []:
            entry_email = entry.get("email", "").strip().lower()
            entry_key = entry.get("api_key", "").strip()
            if not entry_email:
                raise ValueError("secondary users need an email")
            api_key = entry_key or (kept[entry_email].api_key if entry_email in kept else "")
            if not api_key:
                raise ValueError(f"no API key given for secondary {entry_email!r} (and none stored)")
            resolved.append(SecondaryCredentials(email=entry_email, api_key=api_key))
        if not resolved:
            raise ValueError("at least one secondary user is required")
        if email in {secondary.email for secondary in resolved}:
            raise ValueError("the primary user must not also be listed as a secondary user")

        config = DedupConfig(
            immich_url=immich_url.strip().rstrip("/"),
            primary_email=email,
            primary_api_key=primary_api_key.strip() or self.config.primary_api_key,
            secondaries=tuple(resolved),
            reports_dir=self.reports_dir,
        )

        factory = client_factory or build_client
        new_client = factory(config)
        previous = self.client
        with self._state_lock:
            self.config = config
            self.client = new_client
            self.preflight = None
            self.scan_result = None
            self.last_result = None
        previous.close()
        # a stored scan belonging to the new connection's users is restored
        self._restore_scan()

        if persist:
            save_env(
                self.env_file,
                {
                    "IMMICH_URL": config.immich_url,
                    "PRIMARY_EMAIL": config.primary_email,
                    "PRIMARY_API_KEY": config.primary_api_key,
                    **secondary_env_values(config.secondaries),
                },
            )
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


def unconfigured_session(reports_dir: Path, env_file: Path | None = None) -> Session:
    """A session with no values — the web UI starts like this and gets its
    connection details through POST /api/config."""
    config = empty_config(reports_dir=reports_dir)
    client = ImmichClient("", {"unconfigured": "none"})
    return Session(
        config=config,
        client=client,
        reports_dir=reports_dir,
        env_file=env_file or Path(".env"),
    )
