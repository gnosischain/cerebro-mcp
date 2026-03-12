import time
from typing import Any

import clickhouse_connect

from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, validate_identifier, ensure_limit


class ClickHouseManager:
    """Manages per-database ClickHouse client connections."""

    SCHEMA_CACHE_TTL = 300  # 5 minutes
    SCHEMA_CACHE_MAX_ENTRIES = 256

    def __init__(self):
        self._clients: dict[str, Any] = {}
        self._schema_cache: dict[str, tuple[float, dict]] = {}

    def get_client(self, database: str):
        if database not in self._clients:
            self._clients[database] = clickhouse_connect.get_client(
                host=settings.CLICKHOUSE_HOST,
                port=settings.CLICKHOUSE_PORT,
                username=settings.CLICKHOUSE_USER,
                password=settings.CLICKHOUSE_PASSWORD,
                database=database,
                secure=settings.CLICKHOUSE_SECURE,
                send_receive_timeout=settings.QUERY_TIMEOUT_SECONDS,
                settings={"readonly": 1},
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

    def execute_query(
        self,
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
    ) -> dict:
        """Execute a validated read-only query and return results."""
        self._validate_database(database)

        is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
        if not is_valid:
            raise ValueError(f"Query rejected: {error}")

        capped_max = min(max_rows, settings.MAX_ROWS)
        safe_sql = ensure_limit(sql, capped_max)

        client = self.get_client(database)
        start = time.time()
        result = client.query(safe_sql)
        elapsed = time.time() - start

        columns = result.column_names
        rows = result.result_rows[:capped_max]

        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "elapsed_seconds": round(elapsed, 3),
        }

    def execute_raw(
        self, sql: str, database: str = "dbt", parameters: dict | None = None
    ) -> dict:
        """Execute a system/metadata query (DESCRIBE, SHOW, etc.)."""
        self._validate_database(database)
        client = self.get_client(database)
        result = client.query(sql, parameters=parameters)
        return {
            "columns": result.column_names,
            "rows": result.result_rows,
        }

    def execute_query_arrow(
        self,
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
    ) -> dict:
        """Execute a query using Arrow for efficient columnar processing.

        Falls back to execute_query() if Arrow conversion fails.
        """
        self._validate_database(database)

        is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
        if not is_valid:
            raise ValueError(f"Query rejected: {error}")

        capped_max = min(max_rows, settings.MAX_ROWS)
        safe_sql = ensure_limit(sql, capped_max)

        client = self.get_client(database)
        start = time.time()

        try:
            arrow_table = client.query_arrow(safe_sql)
            elapsed = time.time() - start

            col_dict = arrow_table.to_pydict()
            columns = list(col_dict.keys())
            if columns:
                num_rows = min(len(col_dict[columns[0]]), capped_max)
                rows = [
                    [col_dict[c][i] for c in columns]
                    for i in range(num_rows)
                ]
            else:
                rows = []

            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "elapsed_seconds": round(elapsed, 3),
            }
        except Exception:
            # Fallback to standard row-based path
            return self.execute_query(sql, database, max_rows)

    # --- Schema metadata cache ---

    def _cache_get(self, key: str) -> dict | None:
        if key in self._schema_cache:
            ts, result = self._schema_cache[key]
            if time.time() - ts < self.SCHEMA_CACHE_TTL:
                return result
            del self._schema_cache[key]
        return None

    def _cache_set(self, key: str, result: dict) -> None:
        if len(self._schema_cache) >= self.SCHEMA_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(self._schema_cache))
            del self._schema_cache[oldest_key]
        self._schema_cache[key] = (time.time(), result)

    @property
    def schema_cache_size(self) -> int:
        return len(self._schema_cache)

    def execute_raw_cached(
        self,
        sql: str,
        database: str,
        cache_key: str,
        parameters: dict | None = None,
    ) -> dict:
        """Execute a metadata query with TTL caching."""
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        result = self.execute_raw(sql, database, parameters=parameters)
        self._cache_set(cache_key, result)
        return result
