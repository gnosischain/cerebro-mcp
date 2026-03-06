import time
from typing import Any

import clickhouse_connect

from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, validate_identifier, ensure_limit


class ClickHouseManager:
    """Manages per-database ClickHouse client connections."""

    def __init__(self):
        self._clients: dict[str, Any] = {}

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

    def execute_raw(self, sql: str, database: str = "dbt") -> dict:
        """Execute a system/metadata query (DESCRIBE, SHOW, etc.)."""
        self._validate_database(database)
        client = self.get_client(database)
        result = client.query(sql)
        return {
            "columns": result.column_names,
            "rows": result.result_rows,
        }
