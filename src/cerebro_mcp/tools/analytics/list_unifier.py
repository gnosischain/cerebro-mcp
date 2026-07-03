"""`list` — one read-only listing front door for the whole listing family.

Phase 4 consolidation. A single ``list(kind=..., ...)`` tool dispatches to the
same module-level helper functions the individual ``list_*`` tools call, so the
output is byte-identical to the legacy tool for a given ``kind``:

    kind="tables"        -> list_tables_impl(ch, database=..., ...)
    kind="databases"     -> list_databases_impl(ch)
    kind="charts"        -> list_charts_impl()
    kind="reports"       -> list_reports_impl(limit=...)
    kind="saved_queries" -> list_saved_queries_impl()

No behaviour is duplicated: each ``list_*`` tool is now a thin shim over the
same helper the unifier calls. All five legacy tools stay registered and
callable (deprecation window, not a breaking change); they and this ``list``
tool are ``tier="advanced"`` in ``tools/tool_meta.py`` so they vanish under
``LEAN_CORE_ENABLED`` while remaining invocable.

Registered LAST in ``server.py`` (like ``find``) so the helper imports resolve
against fully-initialised modules.
"""

from __future__ import annotations

from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.tools.analytics.metadata import list_databases_impl
from cerebro_mcp.tools.analytics.saved_queries import list_saved_queries_impl
from cerebro_mcp.tools.analytics.schema import list_tables_impl
from cerebro_mcp.tools.visualization.charts import list_charts_impl, list_reports_impl

# The kinds the unifier understands, in a stable order (also used to render the
# error message for an unknown kind).
LIST_KINDS: tuple[str, ...] = (
    "tables",
    "databases",
    "charts",
    "reports",
    "saved_queries",
)


def register_list_tool(mcp, ch: ClickHouseManager) -> None:
    """Register the unified ``list(kind=...)`` tool.

    Dispatches to the shared ``list_*_impl`` helpers so output matches the
    legacy per-kind tools exactly. Register this LAST (after the tools whose
    helpers it imports are defined).
    """

    @mcp.tool()
    def list(
        kind: str,
        database: str = "dbt",
        name_pattern: str = "",
        like: str = "",
        page_size: int = 50,
        page_token: str | None = None,
        include_detailed_columns: bool = False,
        limit: int = 20,
    ) -> Any:
        """List things of a given `kind` — one front door for the listing family.

        `kind` selects what to list and which extra args apply:

        - `"tables"` — tables in a ClickHouse database (paginated). Uses
          `database` (default "dbt"), `name_pattern`/`like`, `page_size`,
          `page_token`, `include_detailed_columns`.
        - `"databases"` — all allowed ClickHouse databases with table counts.
        - `"charts"` — charts currently in the in-memory registry.
        - `"reports"` — previously generated reports saved on disk. Uses `limit`
          (default 20).
        - `"saved_queries"` — saved SQL queries.

        Output for each `kind` is identical to the legacy `list_<kind>` tool
        (`list_tables`, `list_databases`, `list_charts`, `list_reports`,
        `list_saved_queries`), which remain callable during the deprecation
        window.
        """
        if kind == "tables":
            return list_tables_impl(
                ch,
                database=database,
                name_pattern=name_pattern,
                like=like,
                page_size=page_size,
                page_token=page_token,
                include_detailed_columns=include_detailed_columns,
            )
        if kind == "databases":
            return list_databases_impl(ch)
        if kind == "charts":
            return list_charts_impl()
        if kind == "reports":
            return list_reports_impl(limit)
        if kind == "saved_queries":
            return list_saved_queries_impl()
        return (
            f"Error: unknown kind '{kind}'. "
            f"Expected one of: {', '.join(LIST_KINDS)}."
        )
