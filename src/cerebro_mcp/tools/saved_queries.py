import json
import os
from datetime import datetime, timezone

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, validate_identifier
from cerebro_mcp.tools.query import format_results_table, truncate_response


SAVED_QUERIES_DIR = os.environ.get("CEREBRO_SAVED_QUERIES_DIR", os.path.expanduser("~/.cerebro-mcp"))
SAVED_QUERIES_FILE = os.path.join(SAVED_QUERIES_DIR, "saved_queries.json")


def _load_saved_queries() -> dict:
    """Load saved queries from JSON file."""
    if not os.path.exists(SAVED_QUERIES_FILE):
        return {"queries": {}}
    with open(SAVED_QUERIES_FILE) as f:
        return json.load(f)


def _save_queries(data: dict) -> None:
    """Save queries to JSON file."""
    os.makedirs(SAVED_QUERIES_DIR, exist_ok=True)
    with open(SAVED_QUERIES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def register_saved_query_tools(mcp, ch: ClickHouseManager):
    """Register saved query management tools."""

    @mcp.tool()
    def save_query(
        name: str,
        sql: str,
        database: str = "dbt",
        description: str = "",
        overwrite: bool = False,
    ) -> str:
        """Save a query for later reuse. Validates SQL before saving.

        Args:
            name: Unique name for the query (alphanumeric + underscores only).
            sql: The SQL query to save (must be a valid SELECT statement).
            database: Target database for execution. Default: dbt.
            description: Optional description of what the query does.
            overwrite: Set to True to replace an existing query with the same name.

        Returns:
            Confirmation or error message.
        """
        try:
            valid, err = validate_identifier(name)
            if not valid:
                return f"Error: Invalid query name: {err}"

            is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
            if not is_valid:
                return f"Error: Query rejected: {error}"

            if database not in settings.ALLOWED_DATABASES:
                return (
                    f"Error: Database '{database}' not allowed. "
                    f"Allowed: {', '.join(settings.ALLOWED_DATABASES)}"
                )

            data = _load_saved_queries()
            now = datetime.now(timezone.utc).isoformat()

            if name in data["queries"] and not overwrite:
                existing = data["queries"][name]
                return (
                    f"Query '{name}' already exists "
                    f"(saved {existing.get('updated_at', 'unknown')}). "
                    f"Set `overwrite=True` to replace it."
                )

            data["queries"][name] = {
                "sql": sql,
                "database": database,
                "description": description,
                "created_at": data["queries"].get(name, {}).get("created_at", now),
                "updated_at": now,
            }
            _save_queries(data)

            action = "Updated" if name in data["queries"] and overwrite else "Saved"
            return f"{action} query '{name}' (database: {database})."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_saved_queries() -> str:
        """List all saved queries with their names, databases, and descriptions.

        Returns:
            Table of saved queries or message if none exist.
        """
        try:
            data = _load_saved_queries()
            queries = data.get("queries", {})

            if not queries:
                return "No saved queries found. Use `save_query` to save one."

            lines = ["# Saved Queries\n"]
            lines.append("| Name | Database | Description | Updated |")
            lines.append("|------|----------|-------------|---------|")

            for name, q in sorted(queries.items()):
                desc = q.get("description", "")[:60]
                updated = q.get("updated_at", "")[:10]
                lines.append(
                    f"| {name} | {q.get('database', 'dbt')} | {desc} | {updated} |"
                )

            lines.append(f"\nTotal: {len(queries)} saved queries")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def run_saved_query(name: str, max_rows: int = 100) -> str:
        """Execute a previously saved query by name.

        Args:
            name: The name of the saved query to execute.
            max_rows: Maximum rows to return (1-10000). Default: 100.

        Returns:
            Query results formatted as a markdown table.
        """
        try:
            data = _load_saved_queries()
            queries = data.get("queries", {})

            if name not in queries:
                available = ", ".join(sorted(queries.keys())) if queries else "none"
                return f"Error: Query '{name}' not found. Available: {available}"

            q = queries[name]
            sql = q["sql"]
            database = q.get("database", "dbt")

            result = ch.execute_query(sql, database, max_rows)
            table = format_results_table(result["columns"], result["rows"])

            header = (
                f"**Query:** {name}\n"
                f"**Database:** {database}\n"
                f"**Rows:** {result['row_count']} | "
                f"**Time:** {result['elapsed_seconds']}s\n\n"
            )
            if q.get("description"):
                header = f"**Description:** {q['description']}\n" + header

            return truncate_response(header + table)
        except Exception as e:
            return f"Error: {e}"
