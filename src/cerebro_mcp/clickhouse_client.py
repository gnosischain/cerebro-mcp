from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import clickhouse_connect

from cerebro_mcp.config import settings
from cerebro_mcp.observability import (
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
from cerebro_mcp.tool_output import fit_rows_to_budget, normalize_rows
from cerebro_mcp.tool_models import QueryResult

logger = logging.getLogger(__name__)


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


class ClickHouseManager:
    """Manages per-database ClickHouse client connections and query execution."""

    SCHEMA_CACHE_TTL = 300
    SCHEMA_CACHE_MAX_ENTRIES = 256
    TABLE_PAGE_CACHE_TTL = 60
    TABLE_PAGE_CACHE_MAX_ENTRIES = 64

    def __init__(self):
        self._clients: dict[str, Any] = {}
        self._schema_cache: dict[str, tuple[float, dict]] = {}
        self._table_page_cache: dict[str, tuple[float, dict]] = {}

    def get_client(self, database: str):
        if database not in self._clients:
            self._clients[database] = clickhouse_connect.get_client(
                host=settings.CLICKHOUSE_HOST,
                port=settings.CLICKHOUSE_PORT,
                username=settings.CLICKHOUSE_USER,
                password=settings.CLICKHOUSE_PASSWORD,
                database=database,
                secure=settings.CLICKHOUSE_SECURE,
                verify=settings.CLICKHOUSE_VERIFY,
                connect_timeout=settings.CLICKHOUSE_CONNECT_TIMEOUT,
                send_receive_timeout=settings.CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
                settings={
                    "readonly": 1,
                    "max_execution_time": settings.effective_query_timeout_seconds,
                },
            )
        return self._clients[database]

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

    def execute_raw(
        self, sql: str, database: str = "dbt", parameters: dict | None = None
    ) -> dict:
        """Execute a metadata query (DESCRIBE, SHOW, system tables)."""
        self._validate_database(database)
        client = self.get_client(database)
        result = client.query(sql, parameters=parameters)
        return {
            "columns": list(result.column_names),
            "rows": [list(row) for row in result.result_rows],
        }

    def _rows_from_arrow(self, arrow_table) -> tuple[list[str], list[list[Any]]]:
        col_dict = arrow_table.to_pydict()
        columns = list(col_dict.keys())
        if not columns:
            return columns, []
        row_count = len(col_dict[columns[0]])
        rows = [
            [col_dict[column][i] for column in columns]
            for i in range(row_count)
        ]
        return columns, rows

    def _fetch_rows_arrow(self, client, sql: str) -> tuple[list[str], list[list[Any]]]:
        table = client.query_arrow(sql)
        return self._rows_from_arrow(table)

    def _fetch_rows_native(self, client, sql: str, parameters: dict | None = None) -> tuple[list[str], list[list[Any]]]:
        result = client.query(sql, parameters=parameters)
        return list(result.column_names), [list(row) for row in result.result_rows]

    def _fetch_rows(
        self, client, sql: str, fetch_mode: Literal["auto", "rows", "arrow"],
        parameters: dict | None = None,
    ) -> tuple[list[str], list[list[Any]], Literal["rows", "arrow"], list[str]]:
        warnings: list[str] = []
        if fetch_mode in {"auto", "arrow"} and parameters is None:
            try:
                columns, rows = self._fetch_rows_arrow(client, sql)
                return columns, rows, "arrow", warnings
            except Exception:
                if fetch_mode == "arrow":
                    raise
                warnings.append("arrow_fallback_to_row_fetch")
        columns, rows = self._fetch_rows_native(client, sql, parameters=parameters)
        return columns, rows, "rows", warnings

    def run_query(
        self,
        sql: str,
        database: str = "dbt",
        requested_max_rows: int = 100,
        audience: Literal["tool", "internal"] = "tool",
        fetch_mode: Literal["auto", "rows", "arrow"] = "auto",
        parameters: dict | None = None,
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
                client, executed_sql, fetch_mode, parameters=parameters
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
        cache = self._table_page_cache if page_cache else self._schema_cache
        ttl = self.TABLE_PAGE_CACHE_TTL if page_cache else self.SCHEMA_CACHE_TTL
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
    ) -> dict:
        """Execute a metadata query with TTL caching."""
        cached = self._cache_get(cache_key, page_cache=page_cache)
        if cached is not None:
            return cached
        result = self.execute_raw(sql, database, parameters=parameters)
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
