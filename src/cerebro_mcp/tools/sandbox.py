"""Phase 2 MCP tools: simulation sandboxes (DuckDB + Parquet).

Three tools, all delegating to `SandboxManager`:

- `create_simulation_sandbox` — fork a CH SELECT into a private DuckDB.
- `query_sandbox` — run any SQL (read or write) inside that DuckDB.
- `destroy_sandbox` — close + remove the sandbox.
- `list_sandboxes` — diagnostic.

Production CH stays read-only; mutations only ever touch DuckDB.

Example agent flow (`mmm_simulator`):

    create_simulation_sandbox(
        sandbox_id="gpay_q3_baseline",
        source_query='''
            SELECT day, sum(volume_usd) AS volume, sum(reward_usd) AS reward
            FROM dbt.fct_execution_gpay_kpi_daily
            WHERE day >= today() - 90
            GROUP BY day
        ''',
        table_name="baseline",
    )

    # Apply a +30% reward shift in the sandbox:
    query_sandbox(
        sandbox_id="gpay_q3_baseline",
        sql="UPDATE baseline SET reward = reward * 1.3",
    )

    # Re-aggregate against the modified slice:
    query_sandbox(
        sandbox_id="gpay_q3_baseline",
        sql="SELECT sum(reward) AS new_reward FROM baseline",
    )

    destroy_sandbox(sandbox_id="gpay_q3_baseline")
"""

from __future__ import annotations

import asyncio
import json
import logging

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.sandbox_manager import default_sandbox_manager
from cerebro_mcp.tool_output import format_results_table, truncate_response

logger = logging.getLogger(__name__)


def _ensure_sweeper_installed() -> None:
    """Install the periodic TTL sweeper on the running event loop, idempotently.

    Lazy install (vs at server boot) lets us grab the loop FastMCP actually
    runs on. No-op when called outside an async context — the atexit hook
    in `bootstrap.install_sandbox_atexit` is the safety net.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from cerebro_mcp.bootstrap import install_sandbox_sweeper
    install_sandbox_sweeper(loop)


def register_sandbox_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def create_simulation_sandbox(
        sandbox_id: str,
        source_query: str,
        table_name: str = "data",
        database: str = "dbt",
    ) -> str:
        """Fork ClickHouse data into a private DuckDB sandbox for what-if analysis.

        The source SELECT runs against ClickHouse (read-only, validated by the
        same safety rules as `execute_query`). The result is materialized as a
        local parquet file and mounted as a real table inside an in-memory
        DuckDB instance keyed by `sandbox_id`. Subsequent calls to
        `query_sandbox(sandbox_id, ...)` can run arbitrary SQL — including
        UPDATE, INSERT, DELETE — against that table without ever touching
        ClickHouse.

        Use this when an agent needs to test a counterfactual ("+30% reward",
        "remove the top 5 wallets", "what if cohort X had not been deposited")
        before recommending an action.

        Args:
            sandbox_id: caller-chosen identifier (URL-safe, ≤64 chars). Reused
                in subsequent `query_sandbox` / `destroy_sandbox` calls.
            source_query: ClickHouse SELECT to materialize. Wrap aggregations
                or joins as you would for `execute_query`.
            table_name: name to mount the parquet as inside DuckDB. Default `data`.
            database: ClickHouse database (default `dbt`).

        Returns:
            JSON-formatted summary: sandbox_id, table, row_count, parquet bytes.
        """
        try:
            _ensure_sweeper_installed()
            mgr = default_sandbox_manager()
            result = mgr.create(
                sandbox_id=sandbox_id,
                source_query=source_query,
                ch_manager=ch,
                table_name=table_name,
                database=database,
            )
            return (
                f"Sandbox `{result['sandbox_id']}` created.\n"
                f"- table: `{result['table']}`  (rows: {result['row_count']:,})\n"
                f"- bytes on disk: {result['bytes']:,}\n"
                f"\nUse `query_sandbox(sandbox_id=\"{result['sandbox_id']}\", "
                f"sql=...)` to read or mutate. Call `destroy_sandbox` when done."
            )
        except Exception as e:
            logger.exception("create_simulation_sandbox failed")
            return f"Error: {e}"

    @mcp.tool()
    def query_sandbox(sandbox_id: str, sql: str, max_rows: int = 200) -> str:
        """Run any SQL against a sandbox. Reads, UPDATEs, INSERTs, DELETEs allowed.

        The query executes against the DuckDB instance backing this sandbox.
        Production ClickHouse is **never** touched. Use this to apply
        counterfactuals (UPDATE/INSERT/DELETE) and then SELECT to compare.

        Args:
            sandbox_id: id returned by `create_simulation_sandbox`.
            sql: any DuckDB SQL. DuckDB largely supports ANSI SQL plus its
                own extensions (window functions, CTEs, UNNEST, etc.).
            max_rows: cap rows returned in the response payload. Default 200.

        Returns:
            For SELECTs: a markdown table of results, or a row count if empty.
            For DML: a confirmation with `rows_affected` from DuckDB.
        """
        try:
            result = default_sandbox_manager().query(sandbox_id, sql)
            cols = result["columns"]
            rows = result["rows"][:max_rows]
            if not cols:
                rows_aff = result.get("rows_affected", -1)
                return (
                    f"OK. Sandbox `{sandbox_id}`: {rows_aff} rows affected "
                    "(SELECT to inspect the sandbox state)."
                )
            table = format_results_table(cols, rows)
            footer = ""
            if len(result["rows"]) > max_rows:
                footer = (
                    f"\n\n_(showing first {max_rows} of "
                    f"{len(result['rows'])} rows)_"
                )
            return truncate_response(
                f"Sandbox `{sandbox_id}` — {len(result['rows'])} row(s)\n\n"
                + table + footer
            )
        except Exception as e:
            logger.exception("query_sandbox failed")
            return f"Error: {e}"

    @mcp.tool()
    def destroy_sandbox(sandbox_id: str) -> str:
        """Close and remove a simulation sandbox. Idempotent."""
        try:
            existed = default_sandbox_manager().destroy(sandbox_id)
            if existed:
                return f"Sandbox `{sandbox_id}` destroyed."
            return f"Sandbox `{sandbox_id}` not found (already destroyed?)."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_sandboxes() -> str:
        """List active simulation sandboxes (id, table, rows, bytes, idle time)."""
        sandboxes = default_sandbox_manager().list_sandboxes()
        if not sandboxes:
            return "No active sandboxes."
        return "Active sandboxes:\n" + json.dumps(sandboxes, indent=2, default=str)
