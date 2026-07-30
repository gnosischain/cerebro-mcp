"""Phase 3: synchronous event-log API for sync MCP tools.

The async `EventStore` (aiosqlite) is correct for parallel-fan-out
workflow code that already runs on the asyncio event loop. The research
MCP tools (`tools/research.py`) are *synchronous* — they call into
`ResearchStore` (file-based) without ever entering an event loop. We
need a thin sync interface that writes to the **same SQLite file** and
**same schema** so the two coexist.

Design choices:

- Uses stdlib `sqlite3` (no aiosqlite import). The schema bootstrap is
  still handled by `EventStore.init()` at server startup, so we can
  assume the tables exist when this module is called.
- Every method opens a fresh connection, applies WAL/synchronous
  pragmas, runs the operation in a single transaction with
  `BEGIN IMMEDIATE` so concurrent writers serialize at the SQL level
  (no Python-side lock required across processes / threads).
- Failures NEVER raise. Event-log writes are observability — a SQLite
  hiccup must not break a research-workflow MCP tool. All exceptions
  are caught and logged.
"""

from __future__ import annotations

import contextvars
import functools
import gzip
import json
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cerebro_mcp.config import settings
from cerebro_mcp.workflow.payloads import (
    GATE_FAILED,
    GATE_PASSED,
    GATE_PENDING,
    GATE_READY,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
    WORKFLOW_WAITING_GATE,
    serialize_payload,
)

logger = logging.getLogger(__name__)


_VALID_WORKFLOW_STATUSES = frozenset(
    {WORKFLOW_RUNNING, WORKFLOW_WAITING_GATE, WORKFLOW_COMPLETED,
     WORKFLOW_FAILED, WORKFLOW_ORPHANED}
)
_VALID_GATE_STATUSES = frozenset(
    {GATE_PENDING, GATE_READY, GATE_PASSED, GATE_FAILED}
)


# Schema bootstrap — duplicated from `event_store._SCHEMA` so the sync path
# can self-initialize without importing aiosqlite. Kept literally identical
# to the async version; the async path runs the same DDL via `executescript`.
# `cerebro_state.db` is purely local observability state. If the schema
# changes, operators delete the file (`rm .cerebro/cerebro_state.db*`)
# and let it recreate on next boot — no in-place migrations.
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS workflows (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    owner         TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflows_owner_status
    ON workflows(owner, status);

CREATE TABLE IF NOT EXISTS events (
    workflow_id        TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    kind               TEXT NOT NULL,
    payload_json       BLOB NOT NULL,
    ts                 REAL NOT NULL,
    payload_compressed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (workflow_id, seq),
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE INDEX IF NOT EXISTS idx_events_workflow_seq
    ON events(workflow_id, seq);

CREATE TABLE IF NOT EXISTS gates (
    workflow_id  TEXT NOT NULL,
    gate_name    TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL,
    PRIMARY KEY (workflow_id, gate_name)
);
"""

# Cache the path we last bootstrapped against so we don't run CREATE TABLE
# IF NOT EXISTS on every single connection (cheap but not free — adds ~1ms).
# When the path changes (tests / db deletion), the cache miss re-runs DDL.
_bootstrapped_for_path: str | None = None

#: Per-thread sqlite3 connection. sqlite3 connections are thread-affine, and
#: writes are funnelled through one executor thread, so this is effectively a
#: single long-lived connection rather than one per write.
_conn_cache = threading.local()


def _safe_path() -> Path:
    return Path(settings.EVENT_STORE_PATH)


def _connect() -> sqlite3.Connection:
    """Open a sync sqlite3 connection with WAL+NORMAL pragmas and the
    schema guaranteed-present.

    Self-bootstrapping is the difference vs the async `EventStore._connect`:
    the async path assumes `init()` has already run; the sync path can be
    reached at any time (e.g. when a sync MCP tool runs before the async
    server boot is complete, or after the operator deleted the db file
    mid-flight). Running `CREATE TABLE IF NOT EXISTS` on a one-time
    bootstrap-per-path cache keeps the sync path working regardless of
    init ordering at zero steady-state cost.
    """
    global _bootstrapped_for_path
    p = _safe_path()
    path_str = str(p)

    # Reuse this thread's connection. Writes are funnelled through a single
    # executor thread (see the write-deadline section), so in practice this is
    # one long-lived connection. sqlite3 connections are thread-affine, hence
    # thread-local rather than a module global.
    #
    # Reopening per call was not free: the connection was never explicitly
    # closed — `with conn:` manages a TRANSACTION, not closure — so every write
    # left a connection for the GC to finalize, and closing the last connection
    # to a WAL database triggers a checkpoint.
    cached = getattr(_conn_cache, "conn", None)
    cached_path = getattr(_conn_cache, "path", None)
    file_missing = not p.exists()
    if cached is not None and (cached_path != path_str or file_missing):
        try:
            cached.close()
        except Exception:  # pragma: no cover - defensive
            pass
        cached = None
        _conn_cache.conn = None

    # If the file was deleted out from under us, force a re-bootstrap.
    if file_missing:
        _bootstrapped_for_path = None

    if cached is not None:
        return cached

    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_str, isolation_level=None, timeout=30.0)
    # Match EventStore async pragmas: WAL journal + NORMAL synchronous.
    # journal_mode=WAL persists at the database level; synchronous is
    # connection-scoped so we set it on every open.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if _bootstrapped_for_path != path_str:
        # CREATE TABLE IF NOT EXISTS is idempotent — safe even if the
        # async init also ran.
        conn.executescript(_SCHEMA_DDL)
        _bootstrapped_for_path = path_str

    _conn_cache.conn = conn
    _conn_cache.path = path_str
    return conn


def _reset_bootstrap_cache() -> None:
    """Tests use this to force schema re-bootstrap when they swap
    `EVENT_STORE_PATH` between runs."""
    global _bootstrapped_for_path
    _bootstrapped_for_path = None
    cached = getattr(_conn_cache, "conn", None)
    if cached is not None:
        try:
            cached.close()
        except Exception:  # pragma: no cover - defensive
            pass
    _conn_cache.conn = None
    _conn_cache.path = None


# ---------------------------------------------------------------------------
# Write deadline
#
# "Failures NEVER raise" (module docstring) covers exceptions but not the
# failure mode that actually stranded a pipeline: a write that BLOCKS. See
# `EVENT_STORE_WRITE_TIMEOUT_SECONDS` in config.py for the full account of
# why nothing else in the path bounds it.
#
# Every write runs on a dedicated worker so the calling tool thread can walk
# away from it. A thread wedged in a syscall cannot be killed in Python, so on
# timeout the worker is ABANDONED and replaced: the stuck thread leaks (one
# thread, once) but a healed filesystem recovers on the next attempt rather
# than queueing behind a corpse forever.
#
# The worker is a DAEMON thread, not a ThreadPoolExecutor. `concurrent.futures`
# registers an atexit hook that JOINS its workers, so a wedged pool thread
# blocks interpreter exit even after `shutdown(wait=False)` — measured at 8s
# for an 8s stall. That would convert a tool hang into a shutdown hang: SIGTERM
# ignored until the pod's grace period expires, which with a ReadWriteOnce PVC
# and `strategy = "Recreate"` stalls the whole rollout. A daemon thread is
# abandoned at exit instead.
#
# One long-lived worker also keeps `_connect`'s thread-local connection warm,
# so the steady state is a single reused connection.
# ---------------------------------------------------------------------------

_write_state_lock = threading.Lock()
_degraded_until: float = 0.0
_last_write_error: str | None = None
_last_write_latency_ms: float | None = None
_write_timeouts: int = 0
_writes_skipped: int = 0


class _Worker:
    """Single daemon thread draining a job queue."""

    def __init__(self) -> None:
        self.queue: "queue.Queue[Any]" = queue.Queue()
        self.thread = threading.Thread(
            target=self._loop, name="cerebro-event-store", daemon=True
        )
        self.thread.start()

    def _loop(self) -> None:
        while True:
            job = self.queue.get()
            if job is None:
                return
            ctx, fn, args, kwargs, box, done = job
            try:
                # Run inside the CALLER's context. `create_workflow_safe`
                # resolves its owner from a contextvar
                # (`runtime.identity.get_current_owner`), and contextvars do
                # not cross threads — without this every workflow row would
                # silently lose its owner.
                box.append(("ok", ctx.run(fn, *args, **kwargs)))
            except BaseException as exc:  # noqa: BLE001 - relayed to caller
                box.append(("err", exc))
            finally:
                done.set()

    def submit(self, ctx, fn, args, kwargs):
        box: list[tuple[str, Any]] = []
        done = threading.Event()
        self.queue.put((ctx, fn, args, kwargs, box, done))
        return box, done


_worker: _Worker | None = None


def _get_worker_unlocked() -> _Worker:
    global _worker
    if _worker is None:
        _worker = _Worker()
    return _worker


def _abandon_executor_unlocked() -> None:
    """Drop the worker without waiting for its wedged thread.

    The thread is a daemon, so leaving it stuck costs one thread and never
    delays process exit.
    """
    global _worker
    stale = _worker
    _worker = None
    if stale is not None:
        try:
            stale.queue.put(None)
        except Exception:  # pragma: no cover - defensive
            pass


def _bounded(default: Any):
    """Run the wrapped writer on a worker thread under a hard deadline.

    Returns `default` when the store is degraded, the deadline expires, or
    the write raises. Never propagates.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            global _degraded_until, _last_write_error
            global _last_write_latency_ms, _write_timeouts, _writes_skipped

            now = time.monotonic()
            with _write_state_lock:
                if now < _degraded_until:
                    _writes_skipped += 1
                    return default
                worker = _get_worker_unlocked()

            timeout = float(
                getattr(settings, "EVENT_STORE_WRITE_TIMEOUT_SECONDS", 2.0)
            )
            started = time.monotonic()
            box, done = worker.submit(
                contextvars.copy_context(), fn, args, kwargs
            )

            if not done.wait(timeout):
                cooldown = float(
                    getattr(
                        settings,
                        "EVENT_STORE_DEGRADED_COOLDOWN_SECONDS",
                        60.0,
                    )
                )
                with _write_state_lock:
                    _write_timeouts += 1
                    _degraded_until = time.monotonic() + cooldown
                    _last_write_error = (
                        f"{fn.__name__} exceeded {timeout}s deadline "
                        f"(path={settings.EVENT_STORE_PATH}); event dropped, "
                        f"writes paused {cooldown:.0f}s"
                    )
                    _abandon_executor_unlocked()
                logger.warning(
                    "event-log write timed out after %.1fs (%s); dropping the "
                    "event and pausing writes for %.0fs. The tool call "
                    "continues — observability must not block it.",
                    timeout, fn.__name__, cooldown,
                )
                return default

            status, value = box[0]
            if status == "err":
                with _write_state_lock:
                    _last_write_error = f"{fn.__name__}: {value}"
                logger.error(
                    "event-log write failed (%s): %s", fn.__name__, value
                )
                return default

            with _write_state_lock:
                _last_write_latency_ms = (time.monotonic() - started) * 1000.0
                _last_write_error = None
            return value

        return wrapper

    return decorate


def event_store_stats() -> dict[str, Any]:
    """Health snapshot for `system_status`.

    A capability that can silently stop working must report its state —
    see the `default-off-flag-fails-silently` lesson. Before this existed,
    a wedged event store was indistinguishable from a healthy one.
    """
    with _write_state_lock:
        degraded = time.monotonic() < _degraded_until
        return {
            "path": str(_safe_path()),
            "degraded": degraded,
            "timeouts": _write_timeouts,
            "skipped_while_degraded": _writes_skipped,
            "last_write_latency_ms": _last_write_latency_ms,
            "last_error": _last_write_error,
            "write_timeout_seconds": float(
                getattr(settings, "EVENT_STORE_WRITE_TIMEOUT_SECONDS", 2.0)
            ),
        }


def _reset_write_state() -> None:
    """Tests use this to clear degraded state between cases."""
    global _degraded_until, _last_write_error, _last_write_latency_ms
    global _write_timeouts, _writes_skipped
    with _write_state_lock:
        _degraded_until = 0.0
        _last_write_error = None
        _last_write_latency_ms = None
        _write_timeouts = 0
        _writes_skipped = 0
        _abandon_executor_unlocked()


def probe_event_store_writable() -> tuple[bool, str]:
    """Boot-time writability check for `EVENT_STORE_PATH`'s directory.

    `bootstrap.ensure_writable_dir` covers only RESEARCH_DIR, so a bad
    event-store path used to surface as a hang on the first storyteller or
    research write rather than as a startup error.
    """
    try:
        p = _safe_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        probe = p.parent / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, ""
    except Exception as exc:
        return False, str(exc)


@_bounded(False)
def create_workflow_safe(
    workflow_id: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
    owner: str | None = None,
) -> bool:
    """Insert a workflow row. Returns True on success, False if the row
    already exists OR the write failed (logged). Never raises — callers
    should treat event-log writes as fire-and-forget.

    `owner` defaults to whatever `identity.get_current_owner()` returns
    at call time when the caller doesn't explicitly pass one. Domain
    helpers in this module (`record_research_started` etc.) lean on
    this default; tests can pass an explicit `owner=` to bypass.
    """
    if owner is None:
        try:
            from cerebro_mcp.runtime.identity import get_current_owner
            owner = get_current_owner()
        except Exception:
            # Identity module is optional infrastructure — never let
            # an import failure here break the workflow write.
            owner = None
    now = time.time()
    meta_json = json.dumps(metadata or {}, default=str)
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO workflows (id, kind, status, created_at, "
                    "updated_at, metadata_json, owner) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (workflow_id, kind, WORKFLOW_RUNNING, now, now,
                     meta_json, owner),
                )
                conn.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                # Row already exists — that's fine on retry / restart.
                return False
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception:
        logger.exception(
            "event-log create_workflow failed (workflow_id=%s)", workflow_id
        )
        return False


@_bounded(False)
def mark_workflow_status_safe(workflow_id: str, status: str) -> bool:
    """Update workflow status. Returns True on success, False on failure."""
    if status not in _VALID_WORKFLOW_STATUSES:
        logger.warning("event-log: rejected invalid workflow status %r", status)
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), workflow_id),
            )
            return True
    except Exception:
        logger.exception(
            "event-log mark_workflow_status failed (workflow_id=%s)",
            workflow_id,
        )
        return False


@_bounded(None)
def append_event_safe(
    workflow_id: str,
    kind: str,
    payload: Any,
) -> int | None:
    """Append an event and bump the workflow's updated_at. Returns the
    new seq, or None on failure. Compresses payload with gzip if its
    serialized size exceeds the configured threshold.

    Wrapped in `BEGIN IMMEDIATE` so the SELECT-MAX + INSERT pair is
    atomic across writers. SQLite's writer-serialization handles
    concurrent appends safely.
    """
    body = serialize_payload(payload).encode("utf-8")
    threshold = getattr(
        settings, "EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES", 4096
    )
    compressed = 0
    if len(body) > threshold:
        body = gzip.compress(body, compresslevel=6)
        compressed = 1
    ts = time.time()
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events "
                    "WHERE workflow_id = ?",
                    (workflow_id,),
                )
                row = cur.fetchone()
                seq = (row[0] if row and row[0] is not None else 0) + 1
                conn.execute(
                    "INSERT INTO events (workflow_id, seq, kind, "
                    "payload_json, ts, payload_compressed) "
                    "VALUES (?,?,?,?,?,?)",
                    (workflow_id, seq, kind, body, ts, compressed),
                )
                conn.execute(
                    "UPDATE workflows SET updated_at = ? WHERE id = ?",
                    (ts, workflow_id),
                )
                conn.execute("COMMIT")
                return seq
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception:
        logger.exception(
            "event-log append_event failed (workflow_id=%s, kind=%s)",
            workflow_id, kind,
        )
        return None


@_bounded(False)
def set_gate_safe(
    workflow_id: str,
    gate_name: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Upsert a gate. Returns True on success, False on failure or
    invalid status."""
    if status not in _VALID_GATE_STATUSES:
        logger.warning("event-log: rejected invalid gate status %r", status)
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO gates (workflow_id, gate_name, status, "
                "payload_json, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(workflow_id, gate_name) DO UPDATE SET "
                "status = excluded.status, "
                "payload_json = excluded.payload_json, "
                "updated_at = excluded.updated_at",
                (workflow_id, gate_name, status,
                 json.dumps(payload or {}, default=str), time.time()),
            )
            return True
    except Exception:
        logger.exception(
            "event-log set_gate failed (workflow_id=%s, gate=%s)",
            workflow_id, gate_name,
        )
        return False


# ---------------------------------------------------------------------------
# Research-workflow specific helpers — these are the integration points the
# research MCP tools call. They wrap the generic safe writers with the
# domain-specific event kinds and payload shapes so call-sites stay terse.
# ---------------------------------------------------------------------------


def workflow_id_for_research(project_id: str) -> str:
    """Stable mapping from `ResearchProjectState.project_id` to event-log
    `workflow_id`. Keeps the namespace unambiguous (a project_id and a
    workflow_id should never collide if both happen to use UUIDs)."""
    return f"research_{project_id}"


def record_research_started(
    project_id: str, hypothesis: str, scope: str,
) -> None:
    wid = workflow_id_for_research(project_id)
    create_workflow_safe(wid, "research_project",
                         {"project_id": project_id,
                          "hypothesis": hypothesis,
                          "scope": scope})
    append_event_safe(wid, "workflow_started",
                      {"project_id": project_id,
                       "hypothesis": hypothesis,
                       "scope": scope})


def record_research_phase_planned(
    project_id: str, phase: str, plan_markdown: str,
) -> None:
    append_event_safe(
        workflow_id_for_research(project_id),
        "phase_planned",
        {"phase": phase,
         # Cap plan size in the event payload to keep events lean — the
         # full text is already in research_store.json.
         "plan_preview": (plan_markdown or "")[:500]},
    )


def record_research_phase_completed(
    project_id: str, phase: str, advanced_to: str | None,
) -> None:
    append_event_safe(
        workflow_id_for_research(project_id),
        "phase_completed",
        {"phase": phase, "advanced_to": advanced_to},
    )


def record_research_verification(
    project_id: str, phase: str, passed: bool, summary: str,
) -> None:
    """Record verification + the matching gate flip."""
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "verification_completed",
                      {"phase": phase, "passed": passed,
                       "summary_preview": (summary or "")[:500]})
    set_gate_safe(
        wid,
        f"verification:{phase}",
        GATE_PASSED if passed else GATE_FAILED,
        {"summary_preview": (summary or "")[:500]},
    )


def record_research_peer_review(
    project_id: str, status: str, summary: str = "",
) -> None:
    """Reviewer landed a verdict (`approved` / `changes_requested` / etc).

    Maps reviewer outcome to the canonical gate status. `approved` →
    `passed`, anything else → `failed`. The verbatim verdict is in the
    event payload for replay.
    """
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "peer_review_recorded",
                      {"status": status,
                       "summary_preview": (summary or "")[:500]})
    gate_status = GATE_PASSED if status == "approved" else GATE_FAILED
    set_gate_safe(wid, "peer_review", gate_status, {"verdict": status})


def record_research_published(
    project_id: str, report_id: str, title: str,
) -> None:
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "report_published",
                      {"report_id": report_id, "title": title})
    mark_workflow_status_safe(wid, WORKFLOW_COMPLETED)


# ---------------------------------------------------------------------------
# Research-workflow work events (Step 1 expansion — beyond phase transitions)
#
# Phase 3 originally instrumented only PHASE-LEVEL transitions (start, plan,
# execute, verify, peer_review, publish). The actual analytical work — every
# `execute_query`, every `record_research_memory`, every finding, every
# evidence attachment — bypassed the event log. Result: when a Claude session
# crashed mid-research, the event log only carried `workflow_started` and
# resume hints couldn't surface the agent's actual progress (queries tried,
# observations recorded).
#
# These helpers close that gap. Each one captures a small, lossy summary of
# the work item — never the full payload, so a 100-step research session
# stays cheap to log + replay.
# ---------------------------------------------------------------------------


def record_research_query_executed(
    project_id: str,
    sql: str,
    database: str,
    row_count: int,
    elapsed_seconds: float,
    evidence_title: str = "",
    artifact_ref_id: str | None = None,
    error_class: str | None = None,
) -> None:
    """Append a `query_executed` event for an analytical query the agent
    ran inside an active research project.

    `sql` is truncated to 1.5 KB so the event log stays compact even when
    the agent runs hundreds of long queries. The full SQL is already in
    the on-disk session log JSON if a forensic replay is ever needed.
    `evidence_title` is the agent's short narrative for *why* it ran
    this query — gold for resume.

    `error_class` is set on failed queries (e.g. "ILLEGAL_AGGREGATION",
    "MEMORY_LIMIT_EXCEEDED") so resume can surface "3 queries failed
    with column hallucinations — don't retry them blindly".
    """
    append_event_safe(
        workflow_id_for_research(project_id),
        "query_executed",
        {
            "sql_preview": (sql or "")[:1500],
            "sql_full_len": len(sql or ""),
            "database": database,
            "row_count": row_count,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "evidence_title": evidence_title or None,
            "artifact_ref_id": artifact_ref_id,
            "error_class": error_class,
        },
    )


def record_research_memory_recorded(
    project_id: str, memory_id: str, kind: str,
    statement: str, confidence: float,
) -> None:
    """Append a `memory_recorded` event when an observation lands in
    `research_store.memory.json`. Statement is truncated to 800 chars
    so the event log can carry the gist without bloat — the full text
    is already on disk."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "memory_recorded",
        {
            "memory_id": memory_id,
            "kind": kind,
            "statement_preview": (statement or "")[:800],
            "statement_full_len": len(statement or ""),
            "confidence": confidence,
        },
    )


def record_research_finding_recorded(
    project_id: str, finding_id: str, title: str,
    confidence: float, evidence_count: int,
) -> None:
    """Append a `finding_recorded` event when a conclusion lands in
    `research_store.findings.json`. We don't copy the full conclusion
    text — the resume hint only needs the title + confidence to give
    the agent context."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "finding_recorded",
        {
            "finding_id": finding_id,
            "title": (title or "")[:300],
            "confidence": confidence,
            "evidence_count": evidence_count,
        },
    )


def record_research_evidence_attached(
    project_id: str, kind: str, ref_id: str, phase: str,
    title: str = "",
) -> None:
    """Append an `evidence_attached` event when a query/chart/report/
    schema artifact is wired to a research project. Phase is captured so
    the resume handler can tell, e.g., that 3 execution-phase queries
    are attached but verification is still missing evidence."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "evidence_attached",
        {
            "kind": kind,
            "ref_id": ref_id,
            "phase": phase,
            "title": (title or "")[:300],
        },
    )


# ---------------------------------------------------------------------------
# Quarterly review domain helpers (Sprint 2)
#
# QBRs share `ResearchStore` + research's `PHASE_ORDER`, but they auto-advance
# phases (no explicit plan/execute calls) and can hold analyses from multiple
# quarters in one project. We treat them as a distinct workflow `kind` so the
# resume handler can reason about them differently from research projects
# (no peer-review gate, no `failed` action — just `ready_to_resume` until
# `report_published`).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Storyteller session domain helpers (Sprint 3 — observability layer)
#
# Storyteller's state machine stays in-memory; we only record events for
# observability + resume-hint generation. The 10-phase machine has explicit
# gates and rollback semantics (clarity check failure rolls back to the
# earliest failing phase). Both `phase_advanced` and `gate_failed` are
# represented in the event log.
# ---------------------------------------------------------------------------


def workflow_id_for_storyteller(session_id: str) -> str:
    return f"storyteller_{session_id}"


#: Event kind carrying the FULL storyteller state, artifacts included.
#:
#: The `record_storyteller_*_recorded` helpers below are resume HINTS and
#: deliberately truncated — `storyboard_recorded` carries a scene count,
#: `visual_spec_recorded` a chart family, `final_story_recorded` a content
#: LENGTH. They tell a resuming agent WHERE it was, never WHAT it made, so a
#: finished pipeline could not be reconstructed after a restart.
#:
#: This kind is the artifact of record. It is written last-wins per session;
#: `load_latest_storyteller_snapshot` reads the highest seq. Payloads over
#: EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES are gzipped by `append_event_safe`,
#: which is what keeps a long story markdown cheap to store.
STORYTELLER_STATE_SNAPSHOT = "storyteller_state_snapshot"


def record_storyteller_state_snapshot(session_id: str, payload: dict) -> None:
    """Persist the full storyteller state so it survives a restart."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        STORYTELLER_STATE_SNAPSHOT,
        payload,
    )


def load_latest_storyteller_snapshot(session_id: str) -> dict | None:
    """Most recent full-state snapshot for a session, or None.

    Reads are NOT routed through the write deadline: the caller is explicitly
    asking for the data and a read that fails should say so rather than return
    a silent empty.
    """
    wid = workflow_id_for_storyteller(session_id)
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT payload_json, payload_compressed FROM events "
            "WHERE workflow_id = ? AND kind = ? ORDER BY seq DESC LIMIT 1",
            (wid, STORYTELLER_STATE_SNAPSHOT),
        )
        row = cur.fetchone()
    except Exception:
        logger.exception(
            "event-log snapshot read failed (session_id=%s)", session_id
        )
        return None
    if not row:
        return None
    body, compressed = row[0], row[1]
    try:
        if compressed:
            body = gzip.decompress(body)
        return json.loads(body)
    except Exception:
        logger.exception(
            "event-log snapshot decode failed (session_id=%s)", session_id
        )
        return None


CHART_REGISTRY_WORKFLOW = "chart_registry"
CHART_RECORDED = "chart_recorded"


def load_chart_records() -> list[dict[str, Any]]:
    """Every persisted chart record, oldest first (later entries win).

    Lives here rather than in `tools/visualization/charts.py` so the event
    schema stays behind this module — charts.py has no business knowing the
    events table, and the repo's no-SQL-in-Python guard covers that file.
    """
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT payload_json, payload_compressed FROM events "
            "WHERE workflow_id = ? AND kind = ? ORDER BY seq",
            (CHART_REGISTRY_WORKFLOW, CHART_RECORDED),
        ).fetchall()
    except Exception:
        logger.exception("event-log chart record read failed")
        return []

    out: list[dict[str, Any]] = []
    for body, compressed in rows:
        try:
            if compressed:
                body = gzip.decompress(body)
            out.append(json.loads(body))
        except Exception:
            continue
    return out


def record_chart(payload: dict[str, Any]) -> None:
    """Durable copy of one chart registry entry."""
    create_workflow_safe(CHART_REGISTRY_WORKFLOW, "chart_registry")
    append_event_safe(CHART_REGISTRY_WORKFLOW, CHART_RECORDED, payload)


def list_storyteller_sessions(limit: int = 20) -> list[dict[str, Any]]:
    """Recent storyteller sessions that have a recoverable snapshot."""
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT w.id, w.status, w.updated_at, MAX(e.seq) "
            "FROM workflows w JOIN events e ON e.workflow_id = w.id "
            "WHERE w.kind = ? AND e.kind = ? "
            "GROUP BY w.id, w.status, w.updated_at "
            "ORDER BY w.updated_at DESC LIMIT ?",
            ("storyteller_session", STORYTELLER_STATE_SNAPSHOT, int(limit)),
        )
        rows = cur.fetchall()
    except Exception:
        logger.exception("event-log session list failed")
        return []
    out = []
    for wid, status, updated_at, _seq in rows:
        out.append(
            {
                "session_id": str(wid).replace("storyteller_", "", 1),
                "status": status,
                "updated_at": updated_at,
            }
        )
    return out


def record_storyteller_session_started(session_id: str) -> None:
    wid = workflow_id_for_storyteller(session_id)
    create_workflow_safe(
        wid, "storyteller_session", {"session_id": session_id},
    )
    append_event_safe(
        wid, "workflow_started", {"session_id": session_id},
    )


def record_storyteller_phase_advanced(
    session_id: str, from_phase: str, to_phase: str,
) -> None:
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "phase_advanced",
        {"from": from_phase, "to": to_phase},
    )


def record_storyteller_gate_failed(
    session_id: str, gate: str, blocking_phase: str, reason: str = "",
) -> None:
    """A clarity / accessibility check failed; storyteller rolled back
    to `blocking_phase`. Resume handler treats this differently from a
    plain phase_advanced — multiple unresolved gate_failed events
    promote a workflow to `action: failed`."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "gate_failed",
        {"gate": gate, "blocking_phase": blocking_phase, "reason": reason},
    )


def record_storyteller_handoff_completed(
    session_id: str, report_id: str, style: str,
) -> None:
    wid = workflow_id_for_storyteller(session_id)
    append_event_safe(wid, "handoff_completed",
                      {"report_id": report_id, "style": style})
    mark_workflow_status_safe(wid, WORKFLOW_COMPLETED)


# ---------------------------------------------------------------------------
# Storyteller content events (Step 1 expansion)
#
# Phase events tell us WHICH phase the agent reached. Content events tell us
# WHAT was actually recorded so a fresh Claude session sees the story shape:
# audience, big idea sentence, scene count, visual specs, final-story title.
# Each event captures only the gist (a short preview / structured summary)
# — full payloads stay in `storyteller_state`, this is for resume hints.
# ---------------------------------------------------------------------------


def record_storyteller_context_brief_recorded(
    session_id: str,
    audience: str,
    mechanism: str,
    required_action: str,
) -> None:
    """Capture audience / mechanism / required_action when the brief is
    first stored. Resume hint surfaces the audience so the agent picks
    up the right tone."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "context_brief_recorded",
        {
            "audience": (audience or "")[:200],
            "mechanism": mechanism,
            "required_action": (required_action or "")[:300],
        },
    )


def record_storyteller_big_idea_recorded(
    session_id: str, sentence: str, stakes: str,
) -> None:
    """Capture the big-idea sentence verbatim — short enough that we
    don't truncate."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "big_idea_recorded",
        {
            "sentence": (sentence or "")[:500],
            "stakes": (stakes or "")[:300],
        },
    )


def record_storyteller_storyboard_recorded(
    session_id: str, scene_count: int, narrative_order: str,
    rationale: str = "",
) -> None:
    """Capture scene count + narrative order. Lets resume tell the agent
    'storyboard has 5 scenes; you've drafted visual specs for 3'."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "storyboard_recorded",
        {
            "scene_count": scene_count,
            "narrative_order": narrative_order,
            "rationale_preview": (rationale or "")[:300],
        },
    )


def record_storyteller_visual_spec_recorded(
    session_id: str, scene_index: int, chart_family: str,
    relationship: str, action_title: str,
) -> None:
    """One event per scene as visual specs are filled in. Lets resume
    say 'visual specs recorded for scenes [1, 2, 3] of 5; pick up at
    scene 4'."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "visual_spec_recorded",
        {
            "scene_index": scene_index,
            "chart_family": chart_family,
            "relationship": relationship,
            "action_title": (action_title or "")[:300],
        },
    )


def record_storyteller_final_story_recorded(
    session_id: str, title: str, content_length: int,
) -> None:
    """Capture title + length of the final story when the agent commits
    it. Length is a quick sanity-check signal for resume."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "final_story_recorded",
        {
            "title": (title or "")[:300],
            "content_length": content_length,
        },
    )


