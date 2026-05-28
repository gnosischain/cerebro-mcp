from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

from cerebro_mcp.models.tool import TableListPage
from cerebro_mcp.tools.analytics.schema import register_schema_tools


def test_list_tables_returns_paginated_structured_page():
    mcp = FastMCP("schema-pagination")
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {
        "columns": ["name", "engine", "total_rows", "size"],
        "rows": [
            [f"table_{idx:02d}", "MergeTree", idx, f"{idx} B"]
            for idx in range(4)
        ],
    }
    register_schema_tools(mcp, ch)

    fn = mcp._tool_manager._tools["list_tables"].fn
    result = fn(database="dbt", page_size=3)

    assert isinstance(result, TableListPage)
    assert len(result.tables) == 3
    assert result.next_page_token is not None
    assert "More tables are available" in result.summary_markdown


def test_list_tables_rejects_mismatched_page_token():
    mcp = FastMCP("schema-pagination-mismatch")
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {
        "columns": ["name", "engine", "total_rows", "size"],
        "rows": [
            ["table_a", "MergeTree", 1, "1 B"],
            ["table_b", "MergeTree", 2, "2 B"],
        ],
    }
    register_schema_tools(mcp, ch)

    fn = mcp._tool_manager._tools["list_tables"].fn
    first_page = fn(database="dbt", page_size=1)
    assert isinstance(first_page, TableListPage)

    result = fn(
        database="execution",
        page_size=1,
        page_token=first_page.next_page_token,
    )

    assert isinstance(result, str)
    assert "page_token" in result
