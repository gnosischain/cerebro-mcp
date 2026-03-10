from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query

# Session query counter and nudge state for report workflow
_query_count = 0
_last_nudge_time: float = 0.0
_NUDGE_COOLDOWN = 300  # seconds between nudges (5 min)


def format_results_table(
    columns: list, rows: list, max_col_width: int = 60, max_chars: int = 0
) -> str:
    """Format query results as a markdown table with row-aware truncation."""
    if not rows:
        return "No rows returned."

    if max_chars <= 0:
        max_chars = settings.TOOL_RESPONSE_MAX_CHARS

    # Convert all values to strings, truncate wide columns
    str_rows = []
    for row in rows:
        str_row = []
        for val in row:
            s = str(val) if val is not None else "NULL"
            if len(s) > max_col_width:
                s = s[: max_col_width - 3] + "..."
            str_row.append(s)
        str_rows.append(str_row)

    # Calculate column widths
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    # Build table with row-aware size budget
    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    separator = "-|-".join("-" * w for w in widths)

    lines = [header, separator]
    current_chars = len(header) + len(separator) + 2

    for row in str_rows:
        row_str = " | ".join(val.ljust(widths[i]) for i, val in enumerate(row))
        if current_chars + len(row_str) + 1 > max_chars:
            lines.append(
                f"\n[Table truncated at ~{current_chars:,} chars. "
                f"Showing {len(lines) - 2} of {len(str_rows)} rows. "
                "Use more specific filters or add LIMIT to reduce output.]"
            )
            break
        lines.append(row_str)
        current_chars += len(row_str) + 1

    return "\n".join(lines)


def truncate_response(text: str, max_chars: int = 0) -> str:
    """Truncate free-text responses that exceed the size budget."""
    if max_chars <= 0:
        max_chars = settings.TOOL_RESPONSE_MAX_CHARS
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n[Response truncated at {max_chars:,} chars. "
        "Use more specific filters or add LIMIT to reduce output.]"
    )


def register_query_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def execute_query(
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
    ) -> str:
        """Execute a read-only SQL query against a Gnosis Chain ClickHouse database.

        IMPORTANT: Before calling this tool, you MUST first call `describe_table`
        or `get_model_details` to verify exact column names. Column names are
        non-obvious (e.g., `value` not `staked_gno`, `cnt` not `count`,
        `txs` not `transactions`). Never guess column names.

        Args:
            sql: The SQL query to execute. Must be a SELECT statement.
                 Use ClickHouse SQL syntax. Only use column names verified
                 via `describe_table` or `get_model_details`.
            database: Target database. One of: execution, consensus,
                      crawlers_data, nebula, dbt. Default: dbt.
            max_rows: Maximum rows to return (1-10000). Default: 100.

        Returns:
            Query results as a formatted table with metadata.
        """
        try:
            global _query_count
            _query_count += 1

            result = ch.execute_query(sql, database, max_rows)
            table = format_results_table(result["columns"], result["rows"])
            meta = (
                f"\n\n---\n"
                f"Rows: {result['row_count']} | "
                f"Time: {result['elapsed_seconds']}s | "
                f"Database: {database}"
            )
            response = table + meta

            # Nudge toward generate_chart / generate_report during multi-query workflows
            if _query_count >= 3:
                import time as _time

                global _last_nudge_time
                now = _time.monotonic()
                if now - _last_nudge_time > _NUDGE_COOLDOWN:
                    from cerebro_mcp.tools.visualization import _chart_registry

                    if _chart_registry:
                        response += (
                            f"\n\n> **Reminder:** You have "
                            f"{len(_chart_registry)} chart(s) registered. "
                            "Call `generate_report(title, content_markdown)` "
                            "with `{{chart:CHART_ID}}` placeholders to "
                            "produce the interactive report."
                        )
                    else:
                        response += (
                            "\n\n> **Tip:** To create charts and a visual "
                            "report, use `generate_chart(sql, chart_type, "
                            "x_field, y_field, title)` for each metric, then "
                            "`generate_report(title, content_markdown)`."
                        )
                    _last_nudge_time = now

            return response
        except Exception as e:
            error_msg = str(e)
            # Add actionable hint for column-name errors
            if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                # Extract table name from the query for the hint
                import re

                table_match = re.search(
                    r"\bFROM\s+(\w+)", sql, re.IGNORECASE
                )
                table_hint = (
                    f" Use `describe_table` on '{table_match.group(1)}' "
                    "to see exact column names."
                    if table_match
                    else " Use `describe_table` to check exact column names."
                )
                return (
                    f"Error: {error_msg}\n\n"
                    f"**Hint**: Wrong column name.{table_hint} "
                    "Do NOT guess — verify the schema first."
                )
            return f"Error: {e}"

    @mcp.tool()
    def explain_query(
        sql: str,
        database: str = "dbt",
    ) -> str:
        """Show the execution plan for a SQL query without running it.

        Args:
            sql: The SQL query to explain.
            database: Target database. Default: dbt.

        Returns:
            The EXPLAIN output from ClickHouse.
        """
        try:
            is_valid, error = validate_query(sql)
            if not is_valid:
                return f"Query rejected: {error}"

            explain_sql = f"EXPLAIN {sql}"
            result = ch.execute_raw(explain_sql, database)
            lines = [str(row[0]) if row else "" for row in result["rows"]]
            return truncate_response("\n".join(lines))
        except Exception as e:
            return f"Error: {e}"
