"""Phase 3: SQLite-backed event log for resumable workflows.

Schema:
    workflows(id, kind, status, created_at, updated_at, metadata_json)
    events(workflow_id, seq, kind, payload_json, ts, payload_compressed)
    gates(workflow_id, gate_name, status, payload_json, updated_at)

Why SQLite (and not DuckDB):
    Append-only single-row writes + low-latency replay reads is exactly what
    SQLite WAL is built for. DuckDB is columnar/OLAP — single-row inserts
    are slow under concurrent writers. We already use DuckDB for the Phase 2
    sandboxes (analytical workload); SQLite is the right tool for the event
    log (transactional workload).

Why aiosqlite (and not sqlite3):
    FastMCP runs an asyncio event loop. Synchronous SQLite calls would
    block it during compaction or contention. aiosqlite proxies through a
    thread, freeing the loop.

Crash safety:
    PRAGMA journal_mode=WAL with synchronous=NORMAL gives ms-class commits
    that survive a process kill; the next process opens the db, replays the
    WAL, and finds every committed event. We do NOT use synchronous=FULL
    (slower, marginal benefit when the OS hasn't crashed).

Replay model:
    Events are append-only with monotonic per-workflow `seq`. Replay reads
    `events WHERE workflow_id = ? ORDER BY seq` and folds them into the
    in-memory state of whatever component owns the workflow (research_store,
    storyteller_state, etc.). The store itself does not interpret event
    kinds — it's a durable log, callers do the semantics.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from cerebro_mcp.config import settings
from cerebro_mcp.workflow_payloads import (
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


# `cerebro_state.db` is purely local observability state. If the schema
# changes, the operational answer is `rm .cerebro/cerebro_state.db*` and
# let it recreate on next boot — no in-place migrations, no backfill.
_SCHEMA = """
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


_VALID_WORKFLOW_STATUSES = frozenset(
    {WORKFLOW_RUNNING, WORKFLOW_WAITING_GATE, WORKFLOW_COMPLETED,
     WORKFLOW_FAILED, WORKFLOW_ORPHANED}
)
_VALID_GATE_STATUSES = frozenset(
    {GATE_PENDING, GATE_READY, GATE_PASSED, GATE_FAILED}
)


class EventStore:
    """aiosqlite-backed event log. Construct one per process; the
    `default_event_store()` accessor returns a lazily-built singleton.

    All public methods are async — they're called from the FastMCP
    asyncio loop. The connection is opened on first use; we don't hold
    a long-lived connection because aiosqlite recycles them per call,
    which keeps WAL truncation healthy.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        compression_threshold: int | None = None,
    ) -> None:
        self._path = Path(
            db_path
            or settings.EVENT_STORE_PATH
        )
        self._compression_threshold = (
            compression_threshold
            if compression_threshold is not None
            else settings.EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES
        )
        self._init_lock = asyncio.Lock()
        self._initialized = False
        # Per-workflow append serialization. SQLite handles concurrent
        # connections fine, but our `seq` is computed by SELECT MAX(...) +
        # INSERT, which is racy under concurrent writers (gather() of N
        # subtasks all reading MAX simultaneously then inserting MAX+1
        # collide on the UNIQUE constraint). A per-workflow asyncio.Lock
        # serializes appends for a single workflow while still allowing
        # different workflows to write concurrently — which matches the
        # natural shape of the workload.
        self._append_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Init / connection plumbing
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Idempotent. Creates the parent dir + schema; sets WAL mode +
        synchronous=NORMAL once, then sticky for every subsequent connection."""
        async with self._init_lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=NORMAL")
                await db.execute("PRAGMA foreign_keys=ON")
                await db.executescript(_SCHEMA)
                await db.commit()
            self._initialized = True

    @contextlib.asynccontextmanager
    async def _connect(self):
        """Async context manager yielding an aiosqlite Connection with
        per-connection PRAGMAs already applied.

        Why a CM and not a plain `aiosqlite.connect()` proxy:

        SQLite's `synchronous` pragma is **connection-scoped** (unlike
        `journal_mode=WAL`, which persists at the database level). If we
        only set it during `init()`, every other connection falls back to
        the SQLite default (FULL=2), which fsyncs both the WAL and the
        main DB on every commit — roughly halves append throughput vs
        NORMAL=1. Found via filesystem audit after the 2026-04-27 live
        session. Setting it inside the CM guarantees every code path
        gets the intended commit semantics.

        Callers use it as:
            async with self._connect() as db:
                await db.execute(...)
                await db.commit()
        """
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA synchronous=NORMAL")
            yield db

    # ------------------------------------------------------------------
    # Workflow lifecycle
    # ------------------------------------------------------------------

    async def create_workflow(
        self,
        workflow_id: str,
        kind: str,
        metadata: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> None:
        """Insert a new workflow row with status=running. Raises if the
        workflow_id already exists — callers should generate UUIDs or
        domain-keyed ids deterministic enough not to collide.

        `owner` is the (already-hashed) caller identity from
        `identity.get_current_owner()`. Pass it explicitly here rather
        than reading the contextvar inside the method so this layer
        stays pure data — callers in MCP tool wrappers fetch identity
        once at request entry and thread it through.
        """
        now = time.time()
        meta_json = json.dumps(metadata or {}, default=str)
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO workflows (id, kind, status, created_at, "
                "updated_at, metadata_json, owner) VALUES (?,?,?,?,?,?,?)",
                (workflow_id, kind, WORKFLOW_RUNNING, now, now,
                 meta_json, owner),
            )
            await db.commit()

    async def mark_workflow_status(
        self, workflow_id: str, status: str
    ) -> None:
        if status not in _VALID_WORKFLOW_STATUSES:
            raise ValueError(f"Invalid workflow status: {status!r}")
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            await db.execute(
                "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), workflow_id),
            )
            await db.commit()

    async def get_workflow(
        self,
        workflow_id: str,
        requesting_owner: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the workflow row, or None if not found.

        If `requesting_owner` is set, returns None when the row's owner
        is set to a different value (NULL-owned rows always pass through
        — they're the single-tenant / legacy fallback). This is the
        per-caller isolation filter; pass `None` to bypass (admin / boot
        sweep).
        """
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT id, kind, status, created_at, updated_at, "
                "metadata_json, owner FROM workflows WHERE id = ?",
                (workflow_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            owner = row[6]
            if (
                requesting_owner is not None
                and owner is not None
                and owner != requesting_owner
            ):
                # Row exists but belongs to someone else — return None
                # rather than raise, so callers don't have to distinguish
                # "not found" from "not yours".
                return None
            return {
                "id": row[0], "kind": row[1], "status": row[2],
                "created_at": row[3], "updated_at": row[4],
                "metadata": json.loads(row[5] or "{}"),
                "owner": owner,
            }

    async def list_workflows(
        self,
        statuses: list[str] | None = None,
        older_than_seconds: float | None = None,
        owner: str | None = None,
        include_unowned: bool = True,
    ) -> list[dict[str, Any]]:
        """List workflows with optional filters.

        Filters:
            statuses             — return only rows in this status set.
            older_than_seconds   — used by the bootstrap orphan sweep.
            owner                — when set, return rows whose owner
                                   matches OR (when `include_unowned`)
                                   rows whose owner is NULL.
            include_unowned      — gates whether NULL-owned rows are
                                   visible to a given `owner`. True
                                   (default) = legacy NULL rows are
                                   visible to everyone (single-tenant
                                   fallback). Set False for strict
                                   isolation.

        `owner=None` matches the historical behavior: returns every row.
        Use `None` from boot sweeps and admin paths; pass the caller's
        identity (from `identity.get_current_owner()`) from MCP tool
        wrappers.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if older_than_seconds is not None:
            clauses.append("updated_at < ?")
            params.append(time.time() - older_than_seconds)
        if owner is not None:
            if include_unowned:
                clauses.append("(owner = ? OR owner IS NULL)")
            else:
                clauses.append("owner = ?")
            params.append(owner)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                f"SELECT id, kind, status, created_at, updated_at, owner "
                f"FROM workflows {where} ORDER BY updated_at DESC",
                params,
            )
            rows = await cur.fetchall()
            return [
                {"id": r[0], "kind": r[1], "status": r[2],
                 "created_at": r[3], "updated_at": r[4],
                 "owner": r[5]}
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def append_event(
        self,
        workflow_id: str,
        kind: str,
        payload: Any,
    ) -> int:
        """Append an event with monotonic per-workflow `seq`. Returns the
        new seq. Compresses the payload with gzip if its serialized size
        exceeds `compression_threshold` (LLM message-history payloads can
        be 10-100 KB per turn; compression keeps the db file lean).

        Touches `updated_at` on the parent workflow so the orphan sweep
        knows the workflow is alive.
        """
        body = serialize_payload(payload).encode("utf-8")
        compressed = 0
        if len(body) > self._compression_threshold:
            body = gzip.compress(body, compresslevel=6)
            compressed = 1
        ts = time.time()
        if not self._initialized:
            await self.init()
        # Serialize per-workflow appends so the SELECT MAX + INSERT pair
        # is atomic. asyncio.Lock is per-event-loop; since FastMCP runs a
        # single loop, one lock per workflow is correct.
        lock = self._append_locks.setdefault(workflow_id, asyncio.Lock())
        async with lock, self._connect() as db:
            cur = await db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE workflow_id = ?",
                (workflow_id,),
            )
            row = await cur.fetchone()
            seq = (row[0] if row and row[0] is not None else 0) + 1
            await db.execute(
                "INSERT INTO events (workflow_id, seq, kind, payload_json, "
                "ts, payload_compressed) VALUES (?,?,?,?,?,?)",
                (workflow_id, seq, kind, body, ts, compressed),
            )
            await db.execute(
                "UPDATE workflows SET updated_at = ? WHERE id = ?",
                (ts, workflow_id),
            )
            await db.commit()
            return seq

    async def replay(
        self, workflow_id: str
    ) -> list[dict[str, Any]]:
        """Return every event for a workflow in seq order, with payloads
        already decompressed and JSON-decoded. Caller folds these into
        whatever in-memory state the workflow owns."""
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT seq, kind, payload_json, ts, payload_compressed "
                "FROM events WHERE workflow_id = ? ORDER BY seq",
                (workflow_id,),
            )
            rows = await cur.fetchall()
        events: list[dict[str, Any]] = []
        for seq, kind, body, ts, compressed in rows:
            if compressed:
                body = gzip.decompress(body)
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"_raw_bytes_len": len(body)}
            events.append(
                {"seq": seq, "kind": kind, "payload": payload, "ts": ts}
            )
        return events

    async def event_count(self, workflow_id: str) -> int:
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM events WHERE workflow_id = ?",
                (workflow_id,),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    async def set_gate(
        self,
        workflow_id: str,
        gate_name: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if status not in _VALID_GATE_STATUSES:
            raise ValueError(f"Invalid gate status: {status!r}")
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO gates (workflow_id, gate_name, status, "
                "payload_json, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(workflow_id, gate_name) DO UPDATE SET "
                "status = excluded.status, "
                "payload_json = excluded.payload_json, "
                "updated_at = excluded.updated_at",
                (workflow_id, gate_name, status,
                 json.dumps(payload or {}, default=str), time.time()),
            )
            await db.commit()

    async def get_gate(
        self, workflow_id: str, gate_name: str
    ) -> dict[str, Any] | None:
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT status, payload_json, updated_at FROM gates "
                "WHERE workflow_id = ? AND gate_name = ?",
                (workflow_id, gate_name),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return {
                "status": row[0],
                "payload": json.loads(row[1] or "{}"),
                "updated_at": row[2],
            }

    async def list_gates(self, workflow_id: str) -> list[dict[str, Any]]:
        if not self._initialized:
            await self.init()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT gate_name, status, payload_json, updated_at "
                "FROM gates WHERE workflow_id = ? ORDER BY updated_at",
                (workflow_id,),
            )
            rows = await cur.fetchall()
        return [
            {"gate_name": r[0], "status": r[1],
             "payload": json.loads(r[2] or "{}"), "updated_at": r[3]}
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


_default_store: EventStore | None = None


def default_event_store() -> EventStore:
    """Return the process-wide singleton. Caller is responsible for
    awaiting `store.init()` once before first use."""
    global _default_store
    if _default_store is None:
        _default_store = EventStore()
    return _default_store


def reset_default_event_store() -> None:
    """Tests use this to wipe state between runs."""
    global _default_store
    _default_store = None
