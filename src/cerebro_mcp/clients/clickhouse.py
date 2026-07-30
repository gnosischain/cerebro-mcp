from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import clickhouse_connect

from cerebro_mcp.config import settings
from cerebro_mcp.runtime.observability import (
    log_event,
    observe_clickhouse_query,
    observe_clickhouse_table_access,
)
from cerebro_mcp.safety import (
    enforce_result_limit,
    extract_table_names,
    validate_identifier,
    validate_query,
)
from cerebro_mcp.runtime.tool_output import fit_rows_to_budget, normalize_rows
from cerebro_mcp.models.tool import QueryResult

logger = logging.getLogger(__name__)


def _arrow_temporal_columns(schema) -> list[str]:
    """Names of arrow fields whose type is a date (date32/date64) or any
    timestamp (any unit, any timezone). Read from the schema so the decision is
    type-based, never a numeric-range guess."""
    try:
        import pyarrow as pa
    except Exception:  # pragma: no cover - pyarrow is a hard dependency
        return []
    out: list[str] = []
    for field_ in schema:
        ftype = field_.type
        if pa.types.is_date(ftype) or pa.types.is_timestamp(ftype):
            out.append(field_.name)
    return out


def _iso_temporal(value: Any) -> Any:
    """Render a temporal cell as an ISO string.

    ``to_pydict()`` yields ``datetime.date`` for date32/date64 and
    ``datetime.datetime`` for timestamps (tz-aware when the arrow type carried a
    timezone) — ``.isoformat()`` gives ``YYYY-MM-DD`` / ``YYYY-MM-DDTHH:MM:SS``.
    Ints (some pyarrow versions surface date32 as epoch-days) are converted by
    magnitude. ``None`` passes through.
    """
    if value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        import datetime as _dt

        av = abs(value)
        try:
            if av < 100_000:  # epoch-days
                return (_dt.date(1970, 1, 1) + _dt.timedelta(days=int(value))).isoformat()
            if av < 1e11:  # epoch-seconds
                return _dt.datetime.fromtimestamp(int(value), tz=_dt.timezone.utc).isoformat()
            if av < 1e14:  # epoch-milliseconds
                return _dt.datetime.fromtimestamp(int(value) / 1000, tz=_dt.timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return value
    return value


# ---------------------------------------------------------------------------
# Phase 2: Parquet sanitization
#
# ClickHouse → Arrow type mapping is not 1:1. Exporting certain CH types
# (Enum*, IPv4/v6, UUID, big Decimals, deeply nested arrays, DateTime64 with
# nanosecond precision) into a parquet file that DuckDB can mount cleanly
# requires casting them in the SELECT clause first. We DESCRIBE the inner
# query to learn its column types, build a sanitizing outer SELECT, and
# stream THAT through query_arrow_stream.
# ---------------------------------------------------------------------------

_DECIMAL_RE = re.compile(r"^Decimal\d*\((\d+)\s*,\s*\d+\)$", re.IGNORECASE)
_DATETIME64_PRECISION_RE = re.compile(r"^DateTime64\((\d+)", re.IGNORECASE)


def _decimal_precision(t: str) -> int:
    """Return precision P from `Decimal(P,S)` / `Decimal128(P,S)` / etc."""
    m = _DECIMAL_RE.match(t.strip())
    return int(m.group(1)) if m else 0


def _datetime64_precision(t: str) -> int:
    """Return the precision N from `DateTime64(N, ...)` (0 if unparseable)."""
    m = _DATETIME64_PRECISION_RE.match(t.strip())
    return int(m.group(1)) if m else 0


def _sanitize_column_for_parquet(name: str, ch_type: str) -> str:
    """Emit a SELECT-clause expression for `name` that's safe to write into
    parquet and read back via DuckDB.

    Returns either the bare column name (no cast needed) or
    `<expr> AS <name>`. Backtick-quoted column names are produced so reserved
    words / unusual chars don't break the wrapping SELECT.
    """
    quoted = f"`{name}`"
    bare_type = ch_type.strip()

    # Strip Nullable() / LowCardinality() wrappers for type pattern matching;
    # the cast still produces nullable output where applicable because
    # CAST(NULL AS T) = NULL.
    inner = bare_type
    while True:
        m = re.match(r"^(?:Nullable|LowCardinality)\((.*)\)$", inner, re.IGNORECASE)
        if not m:
            break
        inner = m.group(1).strip()

    upper = inner.upper()

    if upper.startswith("ENUM"):
        return f"CAST({quoted} AS String) AS {quoted}"
    if upper == "UUID":
        return f"toString({quoted}) AS {quoted}"
    if upper.startswith("IPV4") or upper.startswith("IPV6"):
        return f"toString({quoted}) AS {quoted}"
    if upper == "DATE":
        # CH `Date` is 16-bit unsigned days-since-epoch. clickhouse-connect
        # surfaces it as Arrow `uint16` in some configurations, which DuckDB
        # then reads as `USMALLINT` — not as a real DATE — and date functions
        # like `strftime` fail at the binder. `Date32` is wider (Int32) and
        # consistently exports as Arrow `date32[day]`, which DuckDB infers
        # as DATE. Always upcast for parquet roundtrips.
        return f"toDate32({quoted}) AS {quoted}"
    if upper.startswith("DATETIME64") and _datetime64_precision(inner) > 6:
        # Arrow handles us-precision (DateTime64(6)); ns can lose precision
        # in some pyarrow builds. Downcast.
        return f"toDateTime64({quoted}, 6) AS {quoted}"
    if upper.startswith("DECIMAL") and _decimal_precision(inner) > 38:
        # Arrow's decimal128 caps at precision 38.
        return f"CAST({quoted} AS Float64) AS {quoted}"
    if "ARRAY(TUPLE" in upper or upper.count("ARRAY(") > 1:
        # Deeply-nested arrays read back as opaque BLOBs in DuckDB. Fall
        # back to a stringified rendering so the sandbox at least sees
        # something — agent can JSON_PARSE in DuckDB if needed.
        return f"toString({quoted}) AS {quoted}"
    return quoted


@dataclass
class ExecutedQuery:
    sql: str
    executed_sql: str
    database: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_seconds: float
    fetch_mode: Literal["rows", "arrow"]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueryBudget:
    """Typed, internal-only ClickHouse query budget.

    Callers cannot pass arbitrary ClickHouse settings through this contract.
    The four supported guards are always clamped to the process-wide session
    ceilings, and result overflow is always configured to fail closed.
    """

    max_execution_time: int | None = None
    max_memory_usage: int | None = None
    max_result_rows: int | None = None
    max_threads: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_execution_time",
            "max_memory_usage",
            "max_result_rows",
            "max_threads",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


# Deliberately conservative presets for Graph Explorer/internal callers.  The
# manager clamps these again against stricter deployment-wide limits.
CONTRACT_PROBE_QUERY_BUDGET = QueryBudget(
    max_execution_time=5,
    max_memory_usage=256 * 2**20,
    max_result_rows=1_000,
    max_threads=1,
)
DISCOVERY_QUERY_BUDGET = QueryBudget(
    max_execution_time=10,
    max_memory_usage=1536 * 2**20,
    max_result_rows=10_000,
    max_threads=2,
)
INTERACTIVE_QUERY_BUDGET = QueryBudget(
    max_execution_time=20,
    max_memory_usage=2 * 2**30,
    max_result_rows=10_000,
    max_threads=4,
)


class ClickHouseManager:
    """Manages per-database ClickHouse client connections and query execution."""

    SCHEMA_CACHE_TTL = 300
    SCHEMA_CACHE_MAX_ENTRIES = 256
    TABLE_PAGE_CACHE_TTL = 60
    TABLE_PAGE_CACHE_MAX_ENTRIES = 64
    MAX_INTERNAL_QUERY_THREADS = 4

    def __init__(self):
        # Per-thread clients: clickhouse_connect Client objects are NOT thread
        # safe, and the web-app dispatch now runs tool calls in a thread pool
        # (so a slow/unreachable ClickHouse can't freeze the async event loop).
        # Each worker thread lazily gets its own client.
        self._local = threading.local()
        self._schema_cache: dict[str, tuple[float, dict]] = {}
        self._table_page_cache: dict[str, tuple[float, dict]] = {}
        # Unlike the clients above, these caches are SHARED across threads.
        self._cache_lock = threading.Lock()

    def get_client(self, database: str):
        clients = getattr(self._local, "clients", None)
        if clients is None:
            clients = {}
            self._local.clients = clients
        if database not in clients:
            clients[database] = clickhouse_connect.get_client(
                host=settings.CLICKHOUSE_HOST,
                port=settings.CLICKHOUSE_PORT,
                username=settings.CLICKHOUSE_USER,
                password=settings.CLICKHOUSE_PASSWORD,
                database=database,
                secure=settings.CLICKHOUSE_SECURE,
                verify=settings.CLICKHOUSE_VERIFY,
                connect_timeout=settings.CLICKHOUSE_CONNECT_TIMEOUT,
                send_receive_timeout=settings.CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
                # One connection attempt (no internal retry storm): a genuinely
                # unreachable host used to burn ~180s of retries per query and,
                # via the old synchronous dispatch, freeze the whole server.
                query_retries=0,
                settings=self._session_settings(),
            )
        return clients[database]

    @staticmethod
    def _session_settings() -> dict:
        """Session-level query guards applied to every connection.

        ``max_memory_usage`` fails one greedy query fast instead of letting
        it exhaust the shared instance — most dbt models are VIEWS, so a
        single SELECT can execute an aggregation over a huge table
        (observed: 10.8 GiB MEMORY_LIMIT_EXCEEDED on ClickHouse Cloud).
        """
        out: dict = {
            "readonly": 1,
            "max_execution_time": settings.effective_query_timeout_seconds,
        }
        if settings.CLICKHOUSE_MAX_QUERY_MEMORY_GB > 0:
            out["max_memory_usage"] = int(
                settings.CLICKHOUSE_MAX_QUERY_MEMORY_GB * 2**30
            )
        return out

    @classmethod
    def _query_budget_settings(
        cls, query_budget: QueryBudget | None
    ) -> dict[str, Any] | None:
        """Translate a typed budget into safe per-query ClickHouse settings.

        A budget may only make a query *stricter* than the connection's
        session guards. Unknown ClickHouse settings are unrepresentable, so a
        caller cannot disable readonly mode or change an overflow policy.
        """
        if query_budget is None:
            return None

        session = cls._session_settings()
        out: dict[str, Any] = {}
        if query_budget.max_execution_time is not None:
            session_timeout = int(session.get("max_execution_time") or 0)
            out["max_execution_time"] = (
                min(query_budget.max_execution_time, session_timeout)
                if session_timeout > 0
                else query_budget.max_execution_time
            )
        if query_budget.max_memory_usage is not None:
            session_memory = int(session.get("max_memory_usage") or 0)
            out["max_memory_usage"] = (
                min(query_budget.max_memory_usage, session_memory)
                if session_memory > 0
                else query_budget.max_memory_usage
            )
        if query_budget.max_result_rows is not None:
            out["max_result_rows"] = min(
                query_budget.max_result_rows, int(settings.MAX_ROWS)
            )
            out["result_overflow_mode"] = "throw"
        if query_budget.max_threads is not None:
            out["max_threads"] = min(
                query_budget.max_threads, cls.MAX_INTERNAL_QUERY_THREADS
            )
        return out or None

    def _validate_database(self, database: str) -> None:
        valid, err = validate_identifier(database)
        if not valid:
            raise ValueError(err)
        if database not in settings.ALLOWED_DATABASES:
            raise ValueError(
                f"Database '{database}' is not allowed. "
                f"Allowed: {', '.join(settings.ALLOWED_DATABASES)}"
            )

    def ping(self, database: str = "dbt") -> bool:
        self._validate_database(database)
        client = self.get_client(database)
        result = client.query("SELECT 1")
        return bool(result.result_rows and result.result_rows[0][0] == 1)

    def get_server_info(self, database: str = "dbt") -> dict[str, str]:
        self._validate_database(database)
        client = self.get_client(database)
        result = client.query("SELECT version()")
        version = str(result.result_rows[0][0]) if result.result_rows else "unknown"
        return {"version": version}

    def export_to_parquet(
        self,
        sql: str,
        output_path: Path,
        max_bytes: int,
        database: str = "dbt",
        query_budget: QueryBudget | None = None,
    ) -> int:
        """Phase 2: stream a SELECT result to a parquet file.

        Used by the sandbox manager to fork CH data into a private DuckDB
        sandbox for "what-if" simulations. The flow is:

            DESCRIBE (sql)  →  build sanitized outer SELECT  →  stream Arrow
                            →  write parquet (zstd) batch by batch
                            →  enforce max_bytes mid-stream

        Returns:
            Total bytes written to `output_path`.

        Raises:
            ValueError: if the SQL fails safety validation, or if the
                streamed parquet exceeds `max_bytes` (in which case the
                partial file is removed).
        """
        # Defer pyarrow import: only sandbox callers pay this cost.
        import pyarrow.parquet as pq

        self._validate_database(database)
        is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
        if not is_valid:
            raise ValueError(f"Query rejected: {error}")

        client = self.get_client(database)

        # Step 1: introspect the inner-query schema. CH supports
        # `DESCRIBE (subquery)` natively; it returns rows of (name, type, ...).
        query_settings = self._query_budget_settings(query_budget)
        describe_kwargs = {"settings": query_settings} if query_settings else {}
        describe_rows = client.query(
            f"DESCRIBE ({sql})", **describe_kwargs
        ).result_rows
        casts = [
            _sanitize_column_for_parquet(str(r[0]), str(r[1]))
            for r in describe_rows
            if r and r[0] is not None
        ]
        if not casts:
            raise ValueError("Source query returned no columns; nothing to export.")

        safe_sql = f"SELECT {', '.join(casts)} FROM ({sql})"

        # Step 2: stream-write parquet, abort if max_bytes exceeded.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = 0
        writer: pq.ParquetWriter | None = None
        try:
            stream_kwargs = {"settings": query_settings} if query_settings else {}
            with client.query_arrow_stream(safe_sql, **stream_kwargs) as stream:
                for batch in stream:
                    if writer is None:
                        writer = pq.ParquetWriter(
                            str(output_path), batch.schema, compression="zstd"
                        )
                    writer.write_batch(batch)
                    bytes_written = output_path.stat().st_size
                    if bytes_written > max_bytes:
                        raise ValueError(
                            f"Sandbox export exceeded {max_bytes} bytes "
                            f"(written: {bytes_written}). Aborting."
                        )
        except Exception:
            if writer is not None:
                writer.close()
                writer = None
            output_path.unlink(missing_ok=True)
            raise
        finally:
            if writer is not None:
                writer.close()

        return bytes_written

    def execute_raw(
        self,
        sql: str,
        database: str = "dbt",
        parameters: dict | None = None,
        *,
        query_budget: QueryBudget | None = None,
    ) -> dict:
        """Execute a metadata query (DESCRIBE, SHOW, system tables)."""
        self._validate_database(database)
        client = self.get_client(database)
        query_settings = self._query_budget_settings(query_budget)
        kwargs: dict[str, Any] = {"parameters": parameters}
        if query_settings:
            kwargs["settings"] = query_settings
        result = client.query(sql, **kwargs)
        return {
            "columns": list(result.column_names),
            "rows": [list(row) for row in result.result_rows],
        }

    def _rows_from_arrow(self, arrow_table) -> tuple[list[str], list[list[Any]]]:
        # Read the arrow SCHEMA field types BEFORE to_pydict() so temporal
        # columns are converted from authoritative types — date32 / date64 and
        # ALL timestamp units/timezones become ISO strings. The type survives
        # here; converting on type (not on a numeric-range guess) means a real
        # metric near an epoch-day magnitude (e.g. 17000-25000) is never
        # misread as a date.
        temporal_cols = _arrow_temporal_columns(arrow_table.schema)
        col_dict = arrow_table.to_pydict()
        columns = list(col_dict.keys())
        if not columns:
            return columns, []
        if temporal_cols:
            for name in temporal_cols:
                series = col_dict.get(name)
                if series is None:
                    continue
                col_dict[name] = [_iso_temporal(v) for v in series]
        row_count = len(col_dict[columns[0]])
        rows = [
            [col_dict[column][i] for column in columns]
            for i in range(row_count)
        ]
        return columns, rows

    def _fetch_rows_arrow(
        self,
        client,
        sql: str,
        query_budget: QueryBudget | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        query_settings = self._query_budget_settings(query_budget)
        kwargs = {"settings": query_settings} if query_settings else {}
        table = client.query_arrow(sql, **kwargs)
        return self._rows_from_arrow(table)

    def _fetch_rows_native(
        self,
        client,
        sql: str,
        parameters: dict | None = None,
        query_budget: QueryBudget | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        query_settings = self._query_budget_settings(query_budget)
        kwargs: dict[str, Any] = {"parameters": parameters}
        if query_settings:
            kwargs["settings"] = query_settings
        result = client.query(sql, **kwargs)
        return list(result.column_names), [list(row) for row in result.result_rows]

    @staticmethod
    def _is_server_query_error(exc: Exception) -> bool:
        """True when retrying the same SQL through native rows is unsafe.

        Arrow fallback is for client-side Arrow incompatibility. A ClickHouse
        timeout/memory/query error would execute the same expensive query a
        second time, which is both misleading and operationally dangerous.
        """
        text = str(exc).lower()
        markers = (
            "clickhouse error code",
            "httpdriver for",
            "memory_limit_exceeded",
            "timeout_exceeded",
            "query_was_cancelled",
            "too_many_rows_or_bytes",
            "code: 241",
        )
        return any(marker in text for marker in markers)

    def _fetch_rows(
        self, client, sql: str, fetch_mode: Literal["auto", "rows", "arrow"],
        parameters: dict | None = None,
        query_budget: QueryBudget | None = None,
    ) -> tuple[list[str], list[list[Any]], Literal["rows", "arrow"], list[str]]:
        warnings: list[str] = []
        if fetch_mode in {"auto", "arrow"} and parameters is None:
            try:
                columns, rows = self._fetch_rows_arrow(
                    client, sql, query_budget=query_budget
                )
                return columns, rows, "arrow", warnings
            except Exception as exc:
                if fetch_mode == "arrow" or self._is_server_query_error(exc):
                    raise
                warnings.append("arrow_fallback_to_row_fetch")
        columns, rows = self._fetch_rows_native(
            client,
            sql,
            parameters=parameters,
            query_budget=query_budget,
        )
        return columns, rows, "rows", warnings

    def run_query(
        self,
        sql: str,
        database: str = "dbt",
        requested_max_rows: int = 100,
        audience: Literal["tool", "internal"] = "tool",
        fetch_mode: Literal["auto", "rows", "arrow"] = "auto",
        parameters: dict | None = None,
        query_budget: QueryBudget | None = None,
    ) -> ExecutedQuery:
        """Execute a validated read-only query through one shared pipeline."""
        self._validate_database(database)

        is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
        if not is_valid:
            raise ValueError(f"Query rejected: {error}")

        tables_accessed = extract_table_names(sql)

        if requested_max_rows < 1:
            raise ValueError("max_rows must be at least 1")

        effective_fetch_cap = min(requested_max_rows, settings.MAX_ROWS)
        if audience == "tool":
            effective_fetch_cap = min(
                effective_fetch_cap,
                settings.TOOL_RESULT_MAX_ROWS,
            )

        warnings: list[str] = []
        if requested_max_rows > effective_fetch_cap:
            if audience == "tool":
                warnings.append("tool_row_cap_applied")
            else:
                warnings.append("max_rows_capped")

        executed_sql = enforce_result_limit(sql, effective_fetch_cap)
        if executed_sql.rstrip() != sql.rstrip().rstrip(";"):
            warnings.append("limit_applied")

        client = self.get_client(database)
        start = time.time()
        try:
            columns, rows, actual_fetch_mode, fetch_warnings = self._fetch_rows(
                client,
                executed_sql,
                fetch_mode,
                parameters=parameters,
                query_budget=query_budget,
            )
        except Exception:
            elapsed = time.time() - start
            observe_clickhouse_query(
                database=database,
                audience=audience,
                fetch_mode=fetch_mode,
                status="error",
                elapsed_seconds=elapsed,
            )
            log_event(
                logger,
                "clickhouse_query",
                database=database,
                audience=audience,
                fetch_mode=fetch_mode,
                elapsed_seconds=round(elapsed, 3),
                success=False,
                tables=",".join(tables_accessed) if tables_accessed else "",
            )
            raise

        rows = rows[:effective_fetch_cap]
        elapsed = time.time() - start
        warnings.extend(fetch_warnings)
        observe_clickhouse_query(
            database=database,
            audience=audience,
            fetch_mode=actual_fetch_mode,
            status="success",
            elapsed_seconds=elapsed,
            row_count=len(rows),
        )
        for tbl in tables_accessed:
            observe_clickhouse_table_access(database=database, table_name=tbl)
        log_event(
            logger,
            "clickhouse_query",
            database=database,
            audience=audience,
            fetch_mode=actual_fetch_mode,
            elapsed_seconds=round(elapsed, 3),
            row_count=len(rows),
            success=True,
            tables=",".join(tables_accessed) if tables_accessed else "",
        )

        return ExecutedQuery(
            sql=sql,
            executed_sql=executed_sql,
            database=database,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_seconds=round(elapsed, 3),
            fetch_mode=actual_fetch_mode,
            warnings=self._dedupe_warnings(warnings),
        )

    def build_query_result(
        self,
        executed: ExecutedQuery,
        *,
        max_rows: int | None = None,
    ) -> QueryResult:
        """Convert an executed query into a JSON-safe tool payload."""
        normalized_rows = normalize_rows(executed.rows)
        preview_limit = max_rows or settings.TOOL_RESULT_MAX_ROWS
        preview_limit = min(preview_limit, settings.TOOL_RESULT_MAX_ROWS)
        preview_rows, payload_truncated = fit_rows_to_budget(
            executed.columns,
            normalized_rows,
            preview_limit,
            settings.effective_tool_result_max_chars,
        )
        warnings = list(executed.warnings)
        if payload_truncated:
            warnings.append("response_truncated")

        return QueryResult(
            sql=executed.sql,
            database=executed.database,
            columns=executed.columns,
            rows=preview_rows,
            row_count=executed.row_count,
            rows_returned=len(preview_rows),
            truncated=payload_truncated,
            fetch_mode=executed.fetch_mode,
            elapsed_seconds=executed.elapsed_seconds,
            warnings=self._dedupe_warnings(warnings),
        )

    def _cache_get(self, key: str, *, page_cache: bool = False) -> dict | None:
        # Locked: the clients are thread-local but these two caches are
        # per-MANAGER and shared, and tool bodies now run concurrently on
        # worker threads (runtime/offload.py). The contains-then-read and
        # len-then-del sequences are not atomic, so an interleaved eviction
        # otherwise raises KeyError / StopIteration.
        cache = self._table_page_cache if page_cache else self._schema_cache
        ttl = self.TABLE_PAGE_CACHE_TTL if page_cache else self.SCHEMA_CACHE_TTL
        with self._cache_lock:
            if key in cache:
                ts, result = cache[key]
                if time.time() - ts < ttl:
                    return result
                del cache[key]
            return None

    def _cache_set(self, key: str, result: dict, *, page_cache: bool = False) -> None:
        cache = self._table_page_cache if page_cache else self._schema_cache
        max_entries = (
            self.TABLE_PAGE_CACHE_MAX_ENTRIES
            if page_cache
            else self.SCHEMA_CACHE_MAX_ENTRIES
        )
        with self._cache_lock:
            if len(cache) >= max_entries:
                oldest_key = next(iter(cache))
                del cache[oldest_key]
            cache[key] = (time.time(), result)

    @property
    def schema_cache_size(self) -> int:
        return len(self._schema_cache)

    @property
    def table_page_cache_size(self) -> int:
        return len(self._table_page_cache)

    def execute_raw_cached(
        self,
        sql: str,
        database: str,
        cache_key: str,
        parameters: dict | None = None,
        *,
        page_cache: bool = False,
        query_budget: QueryBudget | None = None,
    ) -> dict:
        """Execute a metadata query with TTL caching."""
        cached = self._cache_get(cache_key, page_cache=page_cache)
        if cached is not None:
            return cached
        kwargs: dict[str, Any] = {"parameters": parameters}
        if query_budget is not None:
            kwargs["query_budget"] = query_budget
        result = self.execute_raw(sql, database, **kwargs)
        self._cache_set(cache_key, result, page_cache=page_cache)
        return result

    @staticmethod
    def _dedupe_warnings(warnings: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for warning in warnings:
            if warning not in seen:
                seen.add(warning)
                ordered.append(warning)
        return ordered
