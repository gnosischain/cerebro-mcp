import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, ensure_limit
from cerebro_mcp.tools.query import format_results_table, truncate_response, _truncate_sql


@dataclass
class QueryJob:
    id: str
    sql: str
    database: str
    max_rows: int
    status: str = "pending"  # pending, running, completed, failed
    result: dict | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.time)
    completed_at: float | None = None


# Module-level state
_pending_queries: dict[str, QueryJob] = {}
_executor = ThreadPoolExecutor(max_workers=3)

# Auto-cleanup threshold (10 minutes)
_CLEANUP_AFTER_SECONDS = 600


def _cleanup_old_jobs():
    """Remove completed/failed jobs older than cleanup threshold."""
    now = time.time()
    expired = [
        qid
        for qid, job in _pending_queries.items()
        if job.completed_at and (now - job.completed_at) > _CLEANUP_AFTER_SECONDS
    ]
    for qid in expired:
        del _pending_queries[qid]


def _run_query(job: QueryJob, ch: ClickHouseManager):
    """Execute a query in a background thread using Arrow path."""
    try:
        job.status = "running"
        result = ch.execute_query_arrow(job.sql, job.database, job.max_rows)
        job.result = result
        job.status = "completed"
    except Exception as e:
        job.error = str(e)
        job.status = "failed"
    finally:
        job.completed_at = time.time()


def register_async_query_tools(mcp, ch: ClickHouseManager):
    """Register async query execution tools."""

    @mcp.tool()
    def start_query(
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
    ) -> str:
        """Submit a long-running query for async execution. Returns a query ID to poll.

        Use this instead of execute_query when the query might take longer than
        30 seconds (e.g., large aggregations over raw execution/consensus tables).

        IMPORTANT: Before calling this tool, verify column names using
        `describe_table` or `get_model_details`. Never guess column names.

        Args:
            sql: The SQL query to execute (SELECT only). Only use column names
                 verified via `describe_table` or `get_model_details`.
            database: Target database. Default: dbt.
            max_rows: Maximum rows to return (1-10000). Default: 100.

        Returns:
            Query ID to use with get_query_results.
        """
        try:
            _cleanup_old_jobs()

            is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
            if not is_valid:
                return f"Error: Query rejected: {error}"

            capped_max = min(max_rows, settings.MAX_ROWS)
            safe_sql = ensure_limit(sql, capped_max)

            from cerebro_mcp.tools.session_state import state

            state.record_execute_query(sql)

            query_id = str(uuid.uuid4())[:8]
            job = QueryJob(
                id=query_id,
                sql=safe_sql,
                database=database,
                max_rows=capped_max,
            )
            _pending_queries[query_id] = job

            _executor.submit(_run_query, job, ch)

            return (
                f"Query submitted successfully.\n\n"
                f"- **Query ID:** `{query_id}`\n"
                f"- **Database:** {database}\n"
                f"- **Max rows:** {capped_max}\n\n"
                f"Use `get_query_results('{query_id}')` to check status and retrieve results."
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_query_results(query_id: str) -> str:
        """Check status and retrieve results of an async query submitted via start_query.

        Args:
            query_id: The query ID returned by start_query.

        Returns:
            Query status and results (if completed).
        """
        try:
            job = _pending_queries.get(query_id)
            if job is None:
                return (
                    f"Query ID `{query_id}` not found. "
                    "The server may have restarted, or the query expired. "
                    "Please submit again via `start_query`."
                )

            if job.status == "pending":
                elapsed = time.time() - job.submitted_at
                return (
                    f"**Status:** Pending (queued)\n"
                    f"**Elapsed:** {elapsed:.1f}s\n\n"
                    f"The query is waiting to execute. Try again in a few seconds."
                )

            if job.status == "running":
                elapsed = time.time() - job.submitted_at
                return (
                    f"**Status:** Running\n"
                    f"**Elapsed:** {elapsed:.1f}s\n\n"
                    f"The query is still executing. Try again in a few seconds."
                )

            if job.status == "failed":
                error_msg = job.error or ""
                hint = ""
                if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                    import re as _re
                    table_match = _re.search(
                        r"\bFROM\s+(\w+)", job.sql, _re.IGNORECASE
                    )
                    table_hint = (
                        f" Use `describe_table` on '{table_match.group(1)}' "
                        "to see exact column names."
                        if table_match
                        else " Use `describe_table` to check exact column names."
                    )
                    hint = (
                        f"\n\n**Hint**: Wrong column name.{table_hint} "
                        "Do NOT guess — verify the schema first."
                    )
                return (
                    f"**Status:** Failed\n"
                    f"**Error:** {error_msg}{hint}\n\n"
                    f"**SQL:**\n```sql\n{job.sql}\n```"
                )

            # Completed
            result = job.result
            elapsed = (job.completed_at or 0) - job.submitted_at
            table = format_results_table(
                result["columns"],
                result["rows"],
            )

            sql_block = f"\n\n### SQL\n```sql\n{_truncate_sql(job.sql)}\n```"
            output = (
                f"**Status:** Completed\n"
                f"**Rows:** {result['row_count']} | "
                f"**Time:** {elapsed:.1f}s | "
                f"**Database:** {job.database}\n\n"
                f"{table}"
                f"{sql_block}"
            )
            return truncate_response(output)

        except Exception as e:
            return f"Error: {e}"
