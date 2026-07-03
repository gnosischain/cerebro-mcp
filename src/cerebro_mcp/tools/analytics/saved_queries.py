import json
import os
from datetime import datetime, timezone

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, validate_identifier
from cerebro_mcp.runtime.tool_output import build_query_summary


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


def list_saved_queries_impl() -> str:
    """Shared implementation for the ``list_saved_queries`` tool and the ``list``
    unifier (``kind="saved_queries"``). Byte-identical output for both callers."""
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

        Deprecated: use `list(kind="saved_queries")`.

        Returns:
            Table of saved queries or message if none exist.
        """
        return list_saved_queries_impl()

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

            from cerebro_mcp.tools.governance.session_state import state

            state.record_execute_query(sql)

            executed = ch.run_query(
                sql,
                database,
                requested_max_rows=max_rows,
                audience="tool",
            )
            result = ch.build_query_result(executed, max_rows=max_rows)

            notes = []
            if q.get("description"):
                notes.append(f"**Description:** {q['description']}")
            notes.append(f"**Saved Query:** {name}")

            return build_query_summary(
                columns=result.columns,
                rows=result.rows,
                row_count=result.row_count,
                rows_returned=result.rows_returned,
                elapsed_seconds=result.elapsed_seconds,
                database=result.database,
                sql=result.sql,
                warnings=result.warnings,
                extra_notes=notes,
            )
        except Exception as e:
            return f"Error: {e}"
