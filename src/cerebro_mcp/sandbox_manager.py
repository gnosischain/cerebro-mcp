"""Phase 2: DuckDB + Parquet simulation sandbox.

Why this exists
===============

ClickHouse runs in `readonly=1` mode. That's correct for production safety,
but it leaves simulator-style agents (`mmm_simulator`, `forecasting_analyst`)
with no way to test counterfactuals — "what if Gnosis Pay cashback was
+30%?" — except by hand-waving in prose.

The sandbox lets these agents:

  1. Pick a CH SELECT that materializes the slice they care about.
  2. Cerebro forks the result to a parquet file on local disk.
  3. A private DuckDB connection mounts that parquet as a real table.
  4. The agent runs UPDATE / INSERT / DELETE against the DuckDB table —
     standard SQL — to apply the counterfactual.
  5. The agent re-aggregates and reports a delta vs the original slice.
  6. Cerebro destroys the sandbox; the parquet is unlinked, the DuckDB
     connection is closed. ClickHouse never saw a write.

Lifecycle
=========

- `create(sandbox_id, source_query, table_name)` exports CH data to
  `<root>/<sandbox_id>/snapshot.parquet` and returns metadata.
- `query(sandbox_id, sql)` runs ANY SQL (read or write) against the
  sandbox's DuckDB.
- `destroy(sandbox_id)` closes the connection, removes the parquet, and
  deletes the workspace dir.
- `sweep_expired()` drops sandboxes idle longer than `SANDBOX_TTL_SECONDS`.
- LRU eviction triggers when the manager has more than
  `SANDBOX_MAX_CONCURRENT` live sandboxes.

Safety
======

- `sandbox_id` is regex-validated to prevent path traversal.
- The CH-side export reuses `ClickHouseClient.export_to_parquet`, which
  validates SQL via `safety.validate_query` and aborts streams that exceed
  `SANDBOX_MAX_BYTES_PER_EXPORT`.
- DuckDB queries run inside an `:memory:` connection — there is no path to
  the production CH cluster from inside a sandbox.
- Concurrency is `RLock`-protected; multiple agents can hit different
  sandboxes simultaneously, and the same sandbox serializes access.

Determinism
===========

DuckDB connections are not picklable, so this manager is a singleton in
the main process. If Phase 4 later moves CPU work to a worker pool, the
pool tasks must NOT touch this manager — only the main event loop does.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)


_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_sandbox_id(sandbox_id: str) -> None:
    """Sandbox IDs become directory names — reject anything that could
    escape the workspace root or break the file system."""
    if not isinstance(sandbox_id, str) or not _SANDBOX_ID_RE.match(sandbox_id):
        raise ValueError(
            f"Invalid sandbox_id {sandbox_id!r}: must match "
            f"[a-zA-Z0-9_-]{{1,64}}"
        )


def _validate_table_name(table_name: str) -> None:
    """The table name is interpolated into a CREATE TABLE statement, so it
    must be a plain identifier."""
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", table_name or ""):
        raise ValueError(
            f"Invalid table name {table_name!r}: must be a plain SQL identifier."
        )


@dataclass
class Sandbox:
    """Per-sandbox state. `conn` is opened lazily on first query if needed
    (currently always opened during create)."""

    sandbox_id: str
    workspace: Path
    parquet_path: Path
    table_name: str
    source_query: str
    conn: duckdb.DuckDBPyConnection
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    bytes_written: int = 0
    row_count: int = 0


class SandboxManager:
    """Process-wide registry of active DuckDB sandboxes.

    Use `default_sandbox_manager()` for the singleton shared by MCP tools.
    Tests construct fresh instances with overridden roots / clients.
    """

    def __init__(
        self,
        root: Path | None = None,
        max_concurrent: int | None = None,
        ttl_seconds: int | None = None,
        max_bytes_per_export: int | None = None,
    ) -> None:
        self._root = Path(root or settings.SANDBOX_ROOT)
        self._max_concurrent = (
            max_concurrent
            if max_concurrent is not None
            else settings.SANDBOX_MAX_CONCURRENT
        )
        self._ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else settings.SANDBOX_TTL_SECONDS
        )
        self._max_bytes = (
            max_bytes_per_export
            if max_bytes_per_export is not None
            else settings.SANDBOX_MAX_BYTES_PER_EXPORT
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sandboxes: dict[str, Sandbox] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        sandbox_id: str,
        source_query: str,
        ch_manager,
        table_name: str = "data",
        database: str = "dbt",
    ) -> dict:
        """Fork a CH query into a new sandbox.

        Args:
            sandbox_id: caller-chosen identifier (URL-safe, ≤64 chars).
            source_query: CH SELECT to materialize. Goes through
                `validate_query` and the parquet sanitizer before export.
            ch_manager: a `ClickHouseManager` (passed by the MCP tool).
                Kept as a parameter rather than imported here so tests can
                inject a fake.
            table_name: name to mount the parquet as inside DuckDB.
            database: CH database the source query targets.

        Returns:
            `{"sandbox_id", "table", "row_count", "bytes", "parquet_path"}`.
        """
        _validate_sandbox_id(sandbox_id)
        _validate_table_name(table_name)

        with self._lock:
            if sandbox_id in self._sandboxes:
                raise ValueError(f"Sandbox {sandbox_id!r} already exists.")

            # LRU eviction BEFORE we do the export work so we don't hold a
            # half-built sandbox if eviction fails.
            while len(self._sandboxes) >= self._max_concurrent:
                self._evict_oldest_locked()

            workspace = self._root / sandbox_id
            workspace.mkdir(parents=True, exist_ok=True)
            parquet_path = workspace / "snapshot.parquet"

            try:
                bytes_written = ch_manager.export_to_parquet(
                    source_query,
                    parquet_path,
                    self._max_bytes,
                    database=database,
                )
            except Exception:
                # Clean up the empty workspace; otherwise repeated failed
                # exports leak directories.
                shutil.rmtree(workspace, ignore_errors=True)
                raise

            try:
                conn = duckdb.connect(database=":memory:")
                conn.execute(
                    f"CREATE TABLE {table_name} AS "
                    "SELECT * FROM read_parquet(?)",
                    [str(parquet_path)],
                )
                row_count = conn.execute(
                    f"SELECT count(*) FROM {table_name}"
                ).fetchone()[0]
            except Exception:
                # Mount failed — destroy on-disk state so a retry starts clean.
                parquet_path.unlink(missing_ok=True)
                shutil.rmtree(workspace, ignore_errors=True)
                raise

            sandbox = Sandbox(
                sandbox_id=sandbox_id,
                workspace=workspace,
                parquet_path=parquet_path,
                table_name=table_name,
                source_query=source_query,
                conn=conn,
                bytes_written=int(bytes_written),
                row_count=int(row_count),
            )
            self._sandboxes[sandbox_id] = sandbox

            logger.info(
                "sandbox_created id=%s table=%s rows=%d bytes=%d",
                sandbox_id, table_name, row_count, bytes_written,
            )

            return {
                "sandbox_id": sandbox_id,
                "table": table_name,
                "row_count": int(row_count),
                "bytes": int(bytes_written),
                "parquet_path": str(parquet_path),
            }

    def query(self, sandbox_id: str, sql: str) -> dict:
        """Run any SQL (incl. UPDATE/INSERT/DELETE) against the sandbox.

        Returns:
            `{"columns", "rows", "row_count", "rows_affected"}`. For DML
            queries `columns` and `rows` will be empty; `rows_affected` is
            DuckDB's `cursor.rowcount` (may be -1 if not reported).
        """
        _validate_sandbox_id(sandbox_id)
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is None:
                raise KeyError(f"Sandbox {sandbox_id!r} not found.")
            sandbox.last_used_at = time.time()
            cursor = sandbox.conn.execute(sql)
            try:
                rowcount = cursor.rowcount  # -1 for SELECTs in DuckDB
            except Exception:
                rowcount = -1
            cols = (
                [d[0] for d in cursor.description] if cursor.description else []
            )
            rows = [list(r) for r in cursor.fetchall()] if cols else []
            return {
                "columns": cols,
                "rows": rows,
                "row_count": len(rows),
                "rows_affected": rowcount,
            }

    def destroy(self, sandbox_id: str) -> bool:
        """Close + remove the sandbox. Returns True if it existed, False if not."""
        _validate_sandbox_id(sandbox_id)
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            if sandbox is None:
                return False
            self._teardown_locked(sandbox)
            return True

    def list_sandboxes(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "sandbox_id": s.sandbox_id,
                    "table": s.table_name,
                    "row_count": s.row_count,
                    "bytes": s.bytes_written,
                    "created_at": s.created_at,
                    "last_used_at": s.last_used_at,
                    "idle_seconds": int(time.time() - s.last_used_at),
                }
                for s in self._sandboxes.values()
            ]

    def sweep_expired(self) -> int:
        """Drop sandboxes idle longer than `ttl_seconds`. Returns the count
        evicted. Safe to call from a periodic asyncio task."""
        now = time.time()
        with self._lock:
            expired_ids = [
                sid
                for sid, sb in self._sandboxes.items()
                if now - sb.last_used_at > self._ttl_seconds
            ]
            for sid in expired_ids:
                sandbox = self._sandboxes.pop(sid)
                self._teardown_locked(sandbox)
                logger.info("sandbox_expired id=%s", sid)
            return len(expired_ids)

    def shutdown(self) -> None:
        """Tear down every sandbox (atexit). Best-effort — logs and continues
        on individual failures so we don't leave a dangling DuckDB instance
        because of one bad workspace."""
        with self._lock:
            ids = list(self._sandboxes.keys())
            for sid in ids:
                sandbox = self._sandboxes.pop(sid)
                try:
                    self._teardown_locked(sandbox)
                except Exception as exc:
                    logger.warning(
                        "sandbox_shutdown_failed id=%s err=%s", sid, exc
                    )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict_oldest_locked(self) -> None:
        """LRU eviction: drop the sandbox with the oldest `last_used_at`."""
        oldest_id = min(
            self._sandboxes,
            key=lambda sid: self._sandboxes[sid].last_used_at,
        )
        sandbox = self._sandboxes.pop(oldest_id)
        self._teardown_locked(sandbox)
        logger.info("sandbox_evicted id=%s reason=lru", oldest_id)

    def _teardown_locked(self, sandbox: Sandbox) -> None:
        try:
            sandbox.conn.close()
        except Exception as exc:
            logger.warning(
                "sandbox_conn_close_failed id=%s err=%s",
                sandbox.sandbox_id, exc,
            )
        sandbox.parquet_path.unlink(missing_ok=True)
        # Remove the workspace dir if empty (keeps disk tidy).
        try:
            shutil.rmtree(sandbox.workspace, ignore_errors=True)
        except Exception as exc:
            logger.warning(
                "sandbox_workspace_cleanup_failed id=%s err=%s",
                sandbox.sandbox_id, exc,
            )


_default_manager: SandboxManager | None = None


def default_sandbox_manager() -> SandboxManager:
    """Return the process-wide singleton, lazily constructed."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SandboxManager()
    return _default_manager


def reset_default_sandbox_manager() -> None:
    """Tests use this to wipe state between runs."""
    global _default_manager
    if _default_manager is not None:
        _default_manager.shutdown()
    _default_manager = None
