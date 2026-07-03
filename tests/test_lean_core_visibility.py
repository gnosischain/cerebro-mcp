"""Tests for the Phase-3 lean-core visibility filter + `load_tools` spike.

Mirrors `test_semantic_find.py` / `test_tool_meta.py` style. Covers:
- with LEAN_CORE_ENABLED on, `list_tools()` returns the core set and drops a
  sample advanced tool;
- with it off, all (non-app) tools are present;
- a core tool is never hidden;
- app-only tools stay hidden regardless of the flag;
- `load_tools([advanced])` makes it reappear in the filtered list;
- `classify_tool` tier assignment for a few known tools.
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp import config
from cerebro_mcp.tools.tool_meta import classify_tool
from cerebro_mcp.tools.visualization.mini_apps import (
    APP_ONLY_META,
    clear_force_visible_tool_names,
    install_app_only_filter,
    mark_app_only,
    register_load_tools_tool,
)


def _make_mcp(name: str) -> FastMCP:
    """A FastMCP with one core tool, one advanced tool, and one app-only tool,
    with the visibility filter + `load_tools` installed (as in server.py)."""
    mcp = FastMCP(name)

    @mcp.tool()
    def query_metrics(metrics: list[str]) -> str:  # core
        """Answer a metric question over the semantic layer."""
        return ""

    @mcp.tool()
    def contract_explore(address: str) -> str:  # advanced
        """Explore a contract's ABI and functions by address."""
        return ""

    @mcp.tool(meta=APP_ONLY_META)
    def get_mini_app_rows(view_id: str) -> str:
        """[App-only] Hydration tool for the mini-app frontend."""
        return ""

    mark_app_only("get_mini_app_rows")
    install_app_only_filter(mcp)
    register_load_tools_tool(mcp)
    return mcp


def _list_names(mcp) -> list[str]:
    tools = asyncio.run(mcp.list_tools())
    return [t.name for t in tools]


@pytest.fixture(autouse=True)
def _reset_force_visible():
    clear_force_visible_tool_names()
    yield
    clear_force_visible_tool_names()


def test_flag_off_all_non_app_tools_visible(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", False)
    mcp = _make_mcp("lean-off")
    names = _list_names(mcp)
    assert "query_metrics" in names          # core
    assert "contract_explore" in names       # advanced still visible
    assert "load_tools" in names
    # app-only ALWAYS hidden, flag or not
    assert "get_mini_app_rows" not in names


def test_flag_on_hides_advanced_keeps_core(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", True)
    mcp = _make_mcp("lean-on")
    names = _list_names(mcp)
    assert "query_metrics" in names          # core never hidden
    assert "load_tools" in names             # escape hatch stays visible
    assert "contract_explore" not in names   # advanced dropped
    assert "get_mini_app_rows" not in names  # app-only still hidden


def test_core_tool_never_hidden(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", True)
    mcp = _make_mcp("lean-core-visible")
    assert "query_metrics" in _list_names(mcp)


def test_load_tools_unhides_advanced(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", True)
    mcp = _make_mcp("lean-load")
    assert "contract_explore" not in _list_names(mcp)

    load_tools = mcp._tool_manager._tools["load_tools"].fn
    result = asyncio.run(load_tools(names=["contract_explore"]))
    assert result["unhidden"] == ["contract_explore"]
    assert result["unknown"] == []
    # After un-hiding it survives the lean-core drop.
    assert "contract_explore" in _list_names(mcp)


def test_load_tools_reports_unknown_and_already_core(monkeypatch):
    monkeypatch.setattr(config.settings, "LEAN_CORE_ENABLED", True)
    mcp = _make_mcp("lean-load-edge")
    load_tools = mcp._tool_manager._tools["load_tools"].fn
    result = asyncio.run(load_tools(names=["query_metrics", "no_such_tool_xyz"]))
    # core tool needs no un-hide; unknown reported, not fatal
    assert "query_metrics" in result["already_core"]
    assert "no_such_tool_xyz" in result["unknown"]
    assert result["unhidden"] == []
    # list_changed emission is best-effort; outside a live request it is False
    assert result["list_changed_emitted"] is False


def test_classify_tier_assignments():
    assert classify_tool("query_metrics")["tier"] == "core"
    assert classify_tool("find")["tier"] == "core"
    assert classify_tool("explain_metric_query")["tier"] == "core"
    assert classify_tool("verify_numbers")["tier"] == "core"
    assert classify_tool("contract_explore")["tier"] == "advanced"
    assert classify_tool("discover_models")["tier"] == "advanced"
    # unknown tool defaults to advanced (never accidentally core)
    assert classify_tool("totally_unknown_tool_xyz")["tier"] == "advanced"
