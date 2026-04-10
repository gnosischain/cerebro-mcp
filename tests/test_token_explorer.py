"""Tests for the Token Explorer mini-app launcher."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.token_explorer import register_token_explorer_tools


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


class StubCH:
    """ClickHouse stub returning canned data for the three Token Explorer tables."""

    def run_query(
        self,
        sql,
        database="dbt",
        requested_max_rows=100,
        audience="tool",
        fetch_mode="auto",
        parameters=None,
    ):
        if "count()" in sql:
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=["c"],
                rows=[[3]], row_count=1, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        if "int_bridges_flows_daily" in sql:
            cols = ["date", "bridge", "source_chain", "dest_chain", "direction", "volume_token", "volume_usd", "txs"]
            rows = [
                ["2026-04-01", "xDAI", "eth", "gnosis", "inbound", 100.0, 12345.6, 7],
                ["2026-04-02", "Arbitrary", "eth", "gnosis", "inbound", 50.0, 4321.0, 3],
                ["2026-04-03", "xDAI", "eth", "gnosis", "outbound", 25.0, 2150.0, 1],
            ]
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=cols,
                rows=rows, row_count=len(rows), elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        if "fct_execution_pools_lps_latest" in sql:
            cols = ["window", "token", "unique_lp_count", "change_pct"]
            rows = [["7D", "GNO", 312, 0.05], ["30D", "GNO", 650, 0.12]]
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=cols,
                rows=rows, row_count=len(rows), elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        if "dune_prices" in sql:
            cols = ["date", "symbol", "price_usd"]
            rows = [["2026-04-09", "GNO", 230.5]]
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=cols,
                rows=rows, row_count=len(rows), elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database, columns=[],
            rows=[], row_count=0, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )


def _open_te(symbol="GNO"):
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_token_explorer_tools(server, ch)
    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "open_token_explorer"
    )
    return server, fn(symbol_or_address=symbol)


def test_open_token_explorer_returns_initial_load_payload():
    _, result = _open_te()
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "token_explorer"
    assert sc["status"] == "ready"


def test_payload_is_lightweight():
    _, result = _open_te()
    size = len(json.dumps(result.structuredContent))
    assert size < 50_000, f"payload should stay under 50 KB, got {size}"


def test_summary_cards_are_populated():
    _, result = _open_te()
    cards = result.structuredContent["summary_cards"]
    labels = {c["label"] for c in cards}
    assert "Token" in labels
    assert "Bridge volume (USD)" in labels
    assert "Unique LPs (7D)" in labels


def test_datasets_have_descriptors_and_page_tokens_when_needed():
    _, result = _open_te()
    datasets = result.structuredContent["datasets"]
    for key in ("metadata", "bridge_flows", "lp_counts", "price_history"):
        assert key in datasets
        ds = datasets[key]
        assert "stats" in ds
        assert "columns" in ds
        # preview_rows always present (may be empty)
        assert "preview_rows" in ds


def test_unknown_token_returns_error():
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_token_explorer_tools(server, ch)
    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "open_token_explorer"
    )
    result = fn(symbol_or_address="NOSUCHTOKEN")
    assert result.isError is True


def test_update_token_explorer_focus_returns_patch_payload():
    server, opened = _open_te()
    view_id = opened.structuredContent["view_id"]
    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "update_token_explorer_focus"
    )
    result = fn(view_id=view_id, metric="lp_count", bridge="xDAI", direction="inbound")
    sc = result.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["selected_metric"] == "lp_count"
    assert sc["patch"]["bridge"] == "xDAI"
    assert sc["patch"]["direction"] == "inbound"


def test_update_focus_rejects_unknown_metric():
    server, opened = _open_te()
    view_id = opened.structuredContent["view_id"]
    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "update_token_explorer_focus"
    )
    result = fn(view_id=view_id, metric="bogus_metric")
    assert result.isError is True


def test_update_focus_rejects_unknown_view_id():
    server, _ = _open_te()
    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "update_token_explorer_focus"
    )
    result = fn(view_id="deadbeef", metric="lp_count")
    assert result.isError is True


# ---------------------------------------------------------------------------
# New flow: zero-arg launcher + load_token_explorer_token
# ---------------------------------------------------------------------------


def test_open_token_explorer_with_no_args_returns_catalog():
    """Zero-arg launch must return an empty catalog-only view without
    running any ClickHouse queries."""
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    from cerebro_mcp.tools.token_explorer import register_token_explorer_tools

    register_token_explorer_tools(server, ch)

    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "open_token_explorer"
    )
    result = fn()  # no arguments at all
    assert result.isError is None or result.isError is False
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_state"]["mode"] == "empty"
    assert sc["view_state"]["selected_token"] == ""
    catalog = sc["view_state"]["token_catalog"]
    assert len(catalog) >= 10  # TOKEN_REGISTRY has 11 tokens
    assert sc["datasets"] == {}
    # summary cards should still render meaningfully
    labels = {c["label"] for c in sc["summary_cards"]}
    assert "Tokens available" in labels


def test_load_token_explorer_token_swaps_view_in_place():
    """After opening empty, loading a token must replace datasets while
    keeping the same view_id and catalog."""
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    from cerebro_mcp.tools.token_explorer import register_token_explorer_tools

    register_token_explorer_tools(server, ch)

    open_fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "open_token_explorer"
    )
    empty = open_fn()
    view_id = empty.structuredContent["view_id"]

    load_fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "load_token_explorer_token"
    )
    loaded = load_fn(view_id=view_id, symbol_or_address="GNO")
    assert loaded.isError is None or loaded.isError is False
    sc = loaded.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_id"] == view_id  # same view
    assert sc["view_state"]["mode"] == "loaded"
    assert sc["view_state"]["selected_token"] == "GNO"
    # datasets populated
    assert "bridge_flows" in sc["datasets"]
    assert "lp_counts" in sc["datasets"]
    # catalog still there so the user can swap tokens again
    assert len(sc["view_state"]["token_catalog"]) >= 10


def test_load_token_explorer_token_rejects_unknown_view():
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    from cerebro_mcp.tools.token_explorer import register_token_explorer_tools

    register_token_explorer_tools(server, ch)

    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "load_token_explorer_token"
    )
    result = fn(view_id="nonsense", symbol_or_address="GNO")
    assert result.isError is True


def test_load_token_explorer_token_rejects_unknown_token():
    server = FastMCP("test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    from cerebro_mcp.tools.token_explorer import register_token_explorer_tools

    register_token_explorer_tools(server, ch)

    open_fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "open_token_explorer"
    )
    view_id = open_fn().structuredContent["view_id"]

    fn = next(
        t.fn for t in server._tool_manager._tools.values() if t.name == "load_token_explorer_token"
    )
    result = fn(view_id=view_id, symbol_or_address="NOSUCH")
    assert result.isError is True
