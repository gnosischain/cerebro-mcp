"""Tests for the Phase-4 `list(kind=...)` unifier + the `list_*` shims.

Mirrors `test_schema_pagination.py` / `test_lean_core_visibility.py` style.
Covers, for the read-only listing family:

- `list(kind=...)` output is byte-identical to the legacy `list_<kind>` tool
  for every kind (tables, databases, charts, reports, saved_queries);
- each legacy `list_*` shim still returns its original shape;
- an unknown kind returns a helpful error (no exception);
- `list` and all five shims classify tier="advanced" (dropped under
  LEAN_CORE_ENABLED, present when off); no core tool changed tier.
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP
from unittest.mock import MagicMock

from cerebro_mcp import config
from cerebro_mcp.models.tool import TableListPage
from cerebro_mcp.tools.analytics.list_unifier import LIST_KINDS, register_list_tool
from cerebro_mcp.tools.analytics.metadata import register_metadata_tools
from cerebro_mcp.tools.analytics.saved_queries import register_saved_query_tools
from cerebro_mcp.tools.analytics.schema import register_schema_tools
from cerebro_mcp.tools.tool_meta import CORE_TOOL_NAMES, classify_tool
from cerebro_mcp.tools.visualization.charts import register_visualization_tools
from cerebro_mcp.tools.visualization.mini_apps import (
    clear_force_visible_tool_names,
    install_app_only_filter,
)

LIST_SHIMS = (
    "list_tables",
    "list_databases",
    "list_charts",
    "list_reports",
    "list_saved_queries",
)


def _make_ch() -> MagicMock:
    """A ClickHouse mock whose `execute_raw_cached` serves table/database rows.

    Both the tables and databases listers call `execute_raw_cached`; a single
    canned payload is fine because the two output shapes are driven by the
    caller, not the row content, and equality only needs determinism.
    """
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {
        "columns": ["name", "engine", "total_rows", "size"],
        "rows": [
            ["table_a", "MergeTree", 10, "10 B"],
            ["table_b", "MergeTree", 20, "20 B"],
        ],
    }
    return ch


def _make_mcp(tmp_saved_dir=None) -> FastMCP:
    """Register the five listing families + the `list` unifier on one server,
    exactly as server.py wires them (unifier last)."""
    mcp = FastMCP("list-unifier")
    ch = _make_ch()
    register_schema_tools(mcp, ch)
    register_metadata_tools(mcp, ch)
    register_saved_query_tools(mcp, ch)
    register_visualization_tools(mcp, ch)
    register_list_tool(mcp, ch)  # LAST, like in server.py
    return mcp


def _tool(mcp, name):
    return mcp._tool_manager._tools[name].fn


@pytest.fixture()
def isolated_saved_queries(tmp_path, monkeypatch):
    """Point saved-queries storage at an empty temp dir so the shim/unifier see
    the same (empty) state deterministically."""
    import cerebro_mcp.tools.analytics.saved_queries as sq

    monkeypatch.setattr(sq, "SAVED_QUERIES_FILE", str(tmp_path / "saved_queries.json"))
    yield


@pytest.fixture()
def isolated_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path / "reports"))
    yield


# ---------------------------------------------------------------------------
# Output equality: list(kind=X) == list_X()
# ---------------------------------------------------------------------------


def test_list_databases_matches_shim():
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="databases")
    legacy = _tool(mcp, "list_databases")()
    assert unified == legacy
    assert isinstance(unified, str)
    assert "# Available Databases" in unified


def test_list_tables_matches_shim():
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="tables", database="dbt")
    legacy = _tool(mcp, "list_tables")(database="dbt")
    assert isinstance(unified, TableListPage)
    assert isinstance(legacy, TableListPage)
    assert unified.model_dump() == legacy.model_dump()


def test_list_tables_passes_through_pagination_args():
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="tables", database="dbt", page_size=1)
    legacy = _tool(mcp, "list_tables")(database="dbt", page_size=1)
    assert unified.model_dump() == legacy.model_dump()
    assert unified.page_size == 1


def test_list_charts_matches_shim():
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="charts")
    legacy = _tool(mcp, "list_charts")()
    assert unified == legacy
    assert isinstance(unified, str)


def test_list_reports_matches_shim(isolated_reports):
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="reports")
    legacy = _tool(mcp, "list_reports")()
    assert unified == legacy


def test_list_reports_limit_passthrough(isolated_reports):
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="reports", limit=5)
    legacy = _tool(mcp, "list_reports")(limit=5)
    assert unified == legacy


def test_list_saved_queries_matches_shim(isolated_saved_queries):
    mcp = _make_mcp()
    unified = _tool(mcp, "list")(kind="saved_queries")
    legacy = _tool(mcp, "list_saved_queries")()
    assert unified == legacy
    assert isinstance(unified, str)


# ---------------------------------------------------------------------------
# Shims still return their original shapes
# ---------------------------------------------------------------------------


def test_shims_still_registered_and_callable():
    mcp = _make_mcp()
    for name in LIST_SHIMS:
        assert name in mcp._tool_manager._tools
    assert "list" in mcp._tool_manager._tools


def test_list_tables_shim_shape():
    mcp = _make_mcp()
    result = _tool(mcp, "list_tables")(database="dbt")
    assert isinstance(result, TableListPage)


def test_list_databases_shim_shape():
    mcp = _make_mcp()
    result = _tool(mcp, "list_databases")()
    assert isinstance(result, str) and result.startswith("# Available Databases")


# ---------------------------------------------------------------------------
# Unknown kind → helpful error, no exception
# ---------------------------------------------------------------------------


def test_unknown_kind_returns_error():
    mcp = _make_mcp()
    result = _tool(mcp, "list")(kind="widgets")
    assert isinstance(result, str)
    assert "unknown kind" in result
    for k in LIST_KINDS:
        assert k in result


# ---------------------------------------------------------------------------
# Tier classification / visibility
# ---------------------------------------------------------------------------


def test_list_and_shims_are_advanced():
    assert classify_tool("list")["tier"] == "advanced"
    for name in LIST_SHIMS:
        assert classify_tool(name)["tier"] == "advanced", name
    # None of them leaked into the authoritative core set.
    assert "list" not in CORE_TOOL_NAMES
    for name in LIST_SHIMS:
        assert name not in CORE_TOOL_NAMES


def _list_names(mcp) -> list[str]:
    return [t.name for t in asyncio.run(mcp.list_tools())]


@pytest.fixture(autouse=True)
def _reset_force_visible():
    clear_force_visible_tool_names()
    yield
    clear_force_visible_tool_names()


def test_flag_off_list_family_all_visible(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", False)
    mcp = _make_mcp()
    install_app_only_filter(mcp)
    names = _list_names(mcp)
    assert "list" in names
    for name in LIST_SHIMS:
        assert name in names


def test_flag_on_hides_list_family(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", True)
    mcp = _make_mcp()
    install_app_only_filter(mcp)
    names = _list_names(mcp)
    # unifier + all five shims are advanced → dropped
    assert "list" not in names
    for name in LIST_SHIMS:
        assert name not in names
    # still callable directly even when hidden from the advertised list
    assert _tool(mcp, "list")(kind="databases")  # no raise


def test_no_core_tool_became_advanced():
    """Sanity: the Phase-4 change did not demote any core tool."""
    for name in CORE_TOOL_NAMES:
        assert classify_tool(name)["tier"] == "core", name
