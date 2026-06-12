"""ClickHouse scratch store — the ONLY write path in cerebro-mcp.

Every other ClickHouse client in the codebase is pinned ``readonly: 1``
(clients/clickhouse.py). ``ScratchStore`` holds the one write-capable
client, pinned to a single validated database, with table names locked to
an internal regex and DDL built only from templates in schemas.py — no
caller SQL ever reaches this class.

Required grant for the deployment user:

    GRANT CREATE DATABASE, CREATE TABLE, INSERT, DROP TABLE, SELECT
    ON <scratch db>.* TO <cerebro user>
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
import time
from typing import Any, Callable

import clickhouse_connect

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(
    r"^rpc_(logs|calls|storage|code|traces|blocks|scan_jobs)_?[0-9a-f]{0,8}$"
)
_DB_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

REGISTRY_TABLE = "rpc_scan_jobs"

_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table} (
    job_id        String,
    kind          LowCardinality(String),
    label         String,
    table_name    String,
    spec_json     String,
    status        LowCardinality(String),
    cursor_json   String,
    rows_written  UInt64,
    note          String,
    created_at    DateTime,
    updated_at    DateTime
) ENGINE = ReplacingMergeTree(updated_at) ORDER BY job_id
"""

_REGISTRY_COLUMNS = [
    "job_id", "kind", "label", "table_name", "spec_json",
    "status", "cursor_json", "rows_written", "note",
    "created_at", "updated_at",
]


def _grant_hint(exc: Exception) -> str:
    return (
        f"ClickHouse user '{settings.CLICKHOUSE_USER}' lacks write access to "
        f"the RPC-scan scratch database '{settings.RPC_SCAN_SCRATCH_DATABASE}'. "
        f"Run: GRANT CREATE DATABASE, CREATE TABLE, INSERT, DROP TABLE, SELECT "
        f"ON {settings.RPC_SCAN_SCRATCH_DATABASE}.* TO {settings.CLICKHOUSE_USER}. "
        f"Original error: {exc}"
    )


class ScratchStore:
    """Write-capable ClickHouse access, confined to one scratch database."""

    def __init__(self, database: str | None = None,
                 client_factory: Callable[[], Any] | None = None):
        self._db = database or settings.RPC_SCAN_SCRATCH_DATABASE
        if not _DB_RE.fullmatch(self._db):
            raise ValueError(f"invalid scratch database name: {self._db!r}")
        self._factory = client_factory or self._default_client
        self._client: Any | None = None
        # clickhouse_connect clients are not thread-safe; inserts are batched
        # and infrequent so serializing on one lock is fine.
        self._lock = threading.Lock()
        self._u256: str | None = None

    @property
    def database(self) -> str:
        return self._db

    # -- client plumbing ----------------------------------------------------

    def _default_client(self):
        return clickhouse_connect.get_client(
            host=settings.CLICKHOUSE_HOST,
            port=settings.CLICKHOUSE_PORT,
            username=settings.CLICKHOUSE_USER,
            password=settings.CLICKHOUSE_PASSWORD,
            secure=settings.CLICKHOUSE_SECURE,
            verify=settings.CLICKHOUSE_VERIFY,
            connect_timeout=settings.CLICKHOUSE_CONNECT_TIMEOUT,
            send_receive_timeout=120,
        )

    def _ensure_client_locked(self):
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _cmd(self, sql: str) -> Any:
        with self._lock:
            return self._ensure_client_locked().command(sql)

    def _query_rows(self, sql: str) -> list[list[Any]]:
        with self._lock:
            result = self._ensure_client_locked().query(sql)
        return [list(r) for r in result.result_rows]

    @staticmethod
    def _validate_table(table: str) -> None:
        if not _TABLE_RE.fullmatch(table):
            raise ValueError(f"illegal scratch table name: {table!r}")

    # -- lifecycle -----------------------------------------------------------

    def ensure_ready(self) -> None:
        try:
            self._cmd(f"CREATE DATABASE IF NOT EXISTS {self._db}")
            self._cmd(_REGISTRY_DDL.format(db=self._db, table=REGISTRY_TABLE))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(_grant_hint(exc)) from exc

    def uint256_type(self) -> str:
        if self._u256 is None:
            try:
                self._cmd("SELECT toUInt256(1)")
                self._u256 = "UInt256"
            except Exception:  # noqa: BLE001
                self._u256 = "Decimal(76, 0)"
        return self._u256

    # -- scan tables ----------------------------------------------------------

    def create_scan_table(self, table: str, columns_ddl: list[str], order_by: str) -> None:
        self._validate_table(table)
        self._cmd(
            f"CREATE TABLE IF NOT EXISTS {self._db}.{table} "
            f"({', '.join(columns_ddl)}) "
            f"ENGINE = ReplacingMergeTree(_scanned_at) {order_by}"
        )

    def insert_rows(self, table: str, columns: list[str], rows: list[list[Any]]) -> None:
        self._validate_table(table)
        if not rows:
            return
        last: Exception | None = None
        for attempt in range(max(1, settings.RPC_SCAN_INSERT_MAX_RETRIES)):
            try:
                with self._lock:
                    client = self._ensure_client_locked()
                    client.insert(f"{self._db}.{table}", rows, column_names=columns)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                with self._lock:
                    self._client = None  # force reconnect on next attempt
                if attempt < settings.RPC_SCAN_INSERT_MAX_RETRIES - 1:
                    time.sleep(0.5 * (2 ** attempt))
        raise last or RuntimeError("scratch insert failed")

    def drop_table(self, table: str) -> None:
        self._validate_table(table)
        self._cmd(f"DROP TABLE IF EXISTS {self._db}.{table}")

    # -- job registry ----------------------------------------------------------

    def upsert_job_row(self, row: dict[str, Any]) -> None:
        """Insert one registry version row; ReplacingMergeTree(updated_at) dedups."""
        values = [[
            row["job_id"], row["kind"], row.get("label", ""),
            row["table_name"], row.get("spec_json", "{}"),
            row["status"], row.get("cursor_json", "{}"),
            int(row.get("rows_written", 0)), row.get("note", ""),
            _to_dt(row.get("created_at")), _to_dt(row.get("updated_at")),
        ]]
        self.insert_rows(REGISTRY_TABLE, _REGISTRY_COLUMNS, values)

    def load_job_row(self, job_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{8}", job_id):
            return None
        rows = self._query_rows(
            f"SELECT {', '.join(_REGISTRY_COLUMNS)} "
            f"FROM {self._db}.{REGISTRY_TABLE} FINAL "
            f"WHERE job_id = '{job_id}' LIMIT 1"
        )
        if not rows:
            return None
        return dict(zip(_REGISTRY_COLUMNS, rows[0]))

    def list_job_rows(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query_rows(
            f"SELECT {', '.join(_REGISTRY_COLUMNS)} "
            f"FROM {self._db}.{REGISTRY_TABLE} FINAL "
            f"WHERE status != 'expired' "
            f"ORDER BY updated_at DESC LIMIT {max(1, int(limit))}"
        )
        return [dict(zip(_REGISTRY_COLUMNS, r)) for r in rows]

    def mark_orphans_on_startup(self) -> int:
        """Jobs left 'running' by a dead process become resumable 'partial'."""
        orphans = self._query_rows(
            f"SELECT {', '.join(_REGISTRY_COLUMNS)} "
            f"FROM {self._db}.{REGISTRY_TABLE} FINAL WHERE status = 'running'"
        )
        for raw in orphans:
            row = dict(zip(_REGISTRY_COLUMNS, raw))
            row["status"] = "partial"
            row["note"] = "server_restart"
            row["updated_at"] = None  # stamp now
            self.upsert_job_row(row)
        return len(orphans)

    # -- cleanup ---------------------------------------------------------------

    def sweep_expired(self) -> int:
        """Registry-driven DROP of scan tables older than the TTL.

        Registry rows are kept but flipped to 'expired' (grant-light; no
        ALTER DELETE needed). Orphan tables with no registry row are dropped
        on table metadata age.
        """
        cutoff_days = settings.RPC_SCAN_SCRATCH_TTL_DAYS
        dropped = 0
        try:
            expired = self._query_rows(
                f"SELECT {', '.join(_REGISTRY_COLUMNS)} "
                f"FROM {self._db}.{REGISTRY_TABLE} FINAL "
                f"WHERE status != 'expired' "
                f"AND created_at < now() - INTERVAL {int(cutoff_days)} DAY"
            )
            registered: set[str] = set()
            for raw in expired:
                row = dict(zip(_REGISTRY_COLUMNS, raw))
                registered.add(row["table_name"])
                self.drop_table(row["table_name"])
                row["status"] = "expired"
                row["updated_at"] = None
                self.upsert_job_row(row)
                dropped += 1
            orphan_rows = self._query_rows(
                f"SELECT name FROM system.tables "
                f"WHERE database = '{self._db}' AND name LIKE 'rpc_%' "
                f"AND name != '{REGISTRY_TABLE}' "
                f"AND metadata_modification_time < now() - INTERVAL {int(cutoff_days)} DAY"
            )
            known = {
                r[0] for r in self._query_rows(
                    f"SELECT table_name FROM {self._db}.{REGISTRY_TABLE} FINAL"
                )
            }
            for (name,) in orphan_rows:
                if _TABLE_RE.fullmatch(name) and name not in known:
                    self.drop_table(name)
                    dropped += 1
        except Exception:  # noqa: BLE001
            logger.exception("rpc_scan scratch sweep failed")
        return dropped

    # -- summaries ---------------------------------------------------------------

    def table_summary(
        self,
        table: str,
        *,
        dedup_key: str,
        stat_exprs: dict[str, str] | None = None,
        top_column: str = "",
        top_extra_expr: str = "",
        sample_columns: list[str] | None = None,
        sample_rows: int = 5,
    ) -> dict[str, Any]:
        """Counts-first summary. All counts use uniqExact over the dedup key
        (pre-merge ReplacingMergeTree count() overcounts after a resume)."""
        self._validate_table(table)
        out: dict[str, Any] = {"table": f"{self._db}.{table}"}

        exprs = {"row_count": f"uniqExact{dedup_key}"}
        exprs.update(stat_exprs or {})
        select = ", ".join(f"{expr} AS {name}" for name, expr in exprs.items())
        stats = self._query_rows(f"SELECT {select} FROM {self._db}.{table}")
        if stats:
            out.update(dict(zip(exprs.keys(), stats[0])))

        if top_column and _COLUMN_RE.fullmatch(top_column):
            extra = f", {top_extra_expr}" if top_extra_expr else ""
            out["top_values"] = self._query_rows(
                f"SELECT `{top_column}`, uniqExact{dedup_key} AS rows{extra} "
                f"FROM {self._db}.{table} "
                f"GROUP BY `{top_column}` ORDER BY rows DESC LIMIT 10"
            )
            out["top_column"] = top_column

        cols = [c for c in (sample_columns or []) if _COLUMN_RE.fullmatch(c)][:12]
        col_sql = ", ".join(f"`{c}`" for c in cols) if cols else "*"
        out["sample_columns"] = cols
        out["sample"] = self._query_rows(
            f"SELECT {col_sql} FROM {self._db}.{table} LIMIT {max(1, int(sample_rows))}"
        )
        return out


def _to_dt(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, (int, float)) and value > 0:
        return _dt.datetime.utcfromtimestamp(value)
    return _dt.datetime.utcnow()


class BatchInserter:
    """Bounded buffer between scanner threads and ClickHouse.

    ``flush()`` is the durability point. The job cursor is NOT advanced from
    ``on_flush`` — durability is unit-based (jobs.commit_unit). ``on_flush``
    only reports how many rows became durable (progress accounting).
    On failure the rows stay in the buffer and the exception propagates, so
    the caller's cursor never moves past unflushed data.
    """

    def __init__(self, store: ScratchStore, table: str, columns: list[str],
                 on_flush: Callable[[int], None] | None = None):
        self._store = store
        self._table = table
        self._columns = columns
        self._on_flush = on_flush
        self._buf: list[list[Any]] = []
        self._last_flush = time.time()
        self._lock = threading.Lock()

    def add(self, row: list[Any]) -> None:
        with self._lock:
            self._buf.append(row)
            if (
                len(self._buf) >= settings.RPC_SCAN_INSERT_BATCH_ROWS
                or time.time() - self._last_flush > settings.RPC_SCAN_INSERT_FLUSH_SECONDS
            ):
                self._flush_locked()

    def flush(self) -> int:
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> int:
        if not self._buf:
            self._last_flush = time.time()
            return 0
        rows, self._buf = self._buf, []
        try:
            self._store.insert_rows(self._table, self._columns, rows)
        except Exception:
            self._buf = rows + self._buf  # keep rows; cursor must not advance
            raise
        self._last_flush = time.time()
        if self._on_flush:
            self._on_flush(len(rows))
        return len(rows)

    def close(self) -> int:
        return self.flush()


def spec_to_json(spec: dict[str, Any]) -> str:
    return json.dumps(spec, default=str, separators=(",", ":"))
