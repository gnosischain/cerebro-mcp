"""Scan jobs: lifecycle, progress, and unit-based cursor durability.

Checkpoint contract: a *scan unit* is the natural progress quantum — a
completed block range for logs/traces, a completed address batch for
calls/storage/code. After each unit the scanner calls ``commit_unit``,
which (1) force-flushes the inserter so the unit's rows are durable,
(2) advances the cursor past the unit, (3) persists the registry row
(throttled). Zero-row units checkpoint too: the flush is a no-op but the
cursor still advances, so a quiet 100k-block stretch never re-scans on
resume. Mid-unit auto-flushes only write rows — the cursor never points
inside a unit.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore, spec_to_json

TERMINAL_STATUSES = {"partial", "completed", "failed", "cancelled"}
RESUMABLE_STATUSES = {"partial", "cancelled"}

_PERSIST_INTERVAL_SECONDS = 2.0


@dataclass
class ScanProgress:
    blocks_total: int = 0
    blocks_done: int = 0
    addresses_total: int = 0
    addresses_done: int = 0
    items_found: int = 0
    rows_written: int = 0
    rpc_calls: int = 0
    skipped_ranges: int = 0
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanCursor:
    next_block: int = 0       # first block of the next unit (logs/traces)
    address_index: int = 0    # next address-batch index (calls/storage/code)
    chunk_index: int = 0      # next address-chunk pass (log topic chunks)
    skipped: list[list[int]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "ScanCursor":
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
        return cls(
            next_block=int(data.get("next_block", 0)),
            address_index=int(data.get("address_index", 0)),
            chunk_index=int(data.get("chunk_index", 0)),
            skipped=[list(map(int, pair)) for pair in data.get("skipped", [])],
        )


@dataclass
class ScanJob:
    id: str
    kind: str
    label: str
    spec: dict[str, Any]
    table_name: str
    status: str = "pending"
    progress: ScanProgress = field(default_factory=ScanProgress)
    cursor: ScanCursor = field(default_factory=ScanCursor)
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    submitted_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    _last_persist: float = 0.0

    @property
    def resumable(self) -> bool:
        return self.status in RESUMABLE_STATUSES

    def elapsed_seconds(self) -> float:
        end = self.completed_at or time.time()
        return max(0.0, end - self.submitted_at)

    def registry_row(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "label": self.label,
            "table_name": self.table_name,
            "spec_json": spec_to_json(self.spec),
            "status": self.status,
            "cursor_json": self.cursor.to_json(),
            "rows_written": self.progress.rows_written,
            "note": self.error or "",
            "created_at": self.submitted_at,
            "updated_at": None,  # stamped at insert
        }


def new_job(kind: str, label: str, spec: dict[str, Any]) -> ScanJob:
    job_id = uuid.uuid4().hex[:8]
    return ScanJob(
        id=job_id, kind=kind, label=label, spec=spec,
        table_name=f"rpc_{kind}_{job_id}",
    )


def commit_unit(
    job: ScanJob,
    inserter: BatchInserter,
    store: ScratchStore,
    *,
    next_block: int | None = None,
    address_index: int | None = None,
    chunk_index: int | None = None,
    force_persist: bool = False,
) -> None:
    """Durability point: flush rows, THEN advance the cursor, then persist.

    A failed flush raises before the cursor moves, so resume re-scans the
    unflushed unit (ReplacingMergeTree absorbs the overlap).
    """
    inserter.flush()
    if next_block is not None:
        job.cursor.next_block = next_block
    if address_index is not None:
        job.cursor.address_index = address_index
    if chunk_index is not None:
        job.cursor.chunk_index = chunk_index
    now = time.time()
    if force_persist or now - job._last_persist >= _PERSIST_INTERVAL_SECONDS:
        job._last_persist = now
        try:
            store.upsert_job_row(job.registry_row())
        except Exception:  # noqa: BLE001
            pass  # registry persistence is best-effort between terminal states


class ScanJobManager:
    """In-memory job table + executor, mirroring query_async.py conventions."""

    def __init__(self, store: ScratchStore, max_concurrent: int | None = None,
                 job_ttl_seconds: int | None = None):
        self._store = store
        self._jobs: dict[str, ScanJob] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent or settings.RPC_SCAN_MAX_CONCURRENT_JOBS
        )
        self._job_ttl = job_ttl_seconds or settings.RPC_SCAN_JOB_TTL_SECONDS
        self._lock = threading.RLock()

    def submit(self, job: ScanJob, run_fn: Callable[[ScanJob], None]) -> ScanJob:
        with self._lock:
            self.cleanup_expired()
            self._jobs[job.id] = job
        self._persist(job)
        self._executor.submit(self._run, job, run_fn)
        return job

    def get(self, job_id: str) -> ScanJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[ScanJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.submitted_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status in ("pending", "running"):
            job.cancel_event.set()
            return True
        return False

    def cancel_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.status in ("pending", "running"):
                job.cancel_event.set()

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [
                jid for jid, job in self._jobs.items()
                if job.completed_at and now - job.completed_at > self._job_ttl
            ]
            for jid in expired:
                del self._jobs[jid]
        return len(expired)

    def shutdown(self) -> None:
        self.cancel_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- internals -------------------------------------------------------------

    def _run(self, job: ScanJob, run_fn: Callable[[ScanJob], None]) -> None:
        job.status = "running"
        self._persist(job)
        try:
            run_fn(job)
            if job.cancel_event.is_set():
                job.status = "cancelled"
            elif job.progress.skipped_ranges or job.cursor.skipped:
                job.status = "partial"
            else:
                job.status = "completed"
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            job.progress.last_error = str(exc)
            job.status = "partial" if job.progress.rows_written else "failed"
        job.completed_at = time.time()
        self._persist(job)

    def _persist(self, job: ScanJob) -> None:
        try:
            self._store.upsert_job_row(job.registry_row())
        except Exception:  # noqa: BLE001
            pass
