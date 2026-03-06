from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.safety import validate_query


def format_results_table(columns: list, rows: list, max_col_width: int = 60) -> str:
    """Format query results as a markdown table."""
    if not rows:
        return "No rows returned."

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

    # Build table
    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    separator = "-|-".join("-" * w for w in widths)

    lines = [header, separator]
    for row in str_rows:
        lines.append(" | ".join(val.ljust(widths[i]) for i, val in enumerate(row)))

    return "\n".join(lines)


def register_query_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def execute_query(
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
    ) -> str:
        """Execute a read-only SQL query against a Gnosis Chain ClickHouse database.

        Args:
            sql: The SQL query to execute. Must be a SELECT statement.
                 Use ClickHouse SQL syntax.
            database: Target database. One of: execution, consensus,
                      crawlers_data, nebula, dbt. Default: dbt.
            max_rows: Maximum rows to return (1-10000). Default: 100.

        Returns:
            Query results as a formatted table with metadata.
        """
        try:
            result = ch.execute_query(sql, database, max_rows)
            table = format_results_table(result["columns"], result["rows"])
            meta = (
                f"\n\n---\n"
                f"Rows: {result['row_count']} | "
                f"Time: {result['elapsed_seconds']}s | "
                f"Database: {database}"
            )
            return table + meta
        except Exception as e:
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
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
