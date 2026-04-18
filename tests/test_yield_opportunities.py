"""Tests for the Yield Opportunities mini app."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.yield_opportunities import register_yield_opportunities_tools


LP_KEY = "lp:uniswap v3:0x1111111111111111111111111111111111111111"
LENDING_KEY = "lending:aave:0x2222222222222222222222222222222222222222"


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


class StubCH:
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
            if "api_execution_yields_opportunities_latest" in sql:
                total = 2
            elif "fct_execution_pools_daily" in sql or "int_execution_lending_aave_daily" in sql:
                total = 3
            else:
                total = 0
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=["c"],
                rows=[[total]],
                row_count=1,
                elapsed_seconds=0.0,
                fetch_mode="rows",
                warnings=[],
            )

        if "api_execution_yields_opportunities_latest" in sql:
            columns = [
                "type",
                "token",
                "name",
                "address",
                "pool_key",
                "protocol",
                "yield_apr",
                "yield_apy",
                "borrow_apy",
                "tvl",
                "total_supplied",
                "total_borrowed",
                "fees_7d",
                "volume_usd_7d",
                "net_apr_7d",
                "utilization_rate",
                "fee_pct",
                "rate_trend_14d",
                "reserve_address",
                "opportunity_key",
                "headline_rate",
                "lvr_apr_7d",
            ]
            rows = [
                [
                    "LP",
                    "GNO",
                    "GNO / xDAI 0.3%",
                    "0x1111111111111111111111111111111111111111",
                    "GNO / xDAI",
                    "Uniswap V3",
                    5.1,
                    None,
                    None,
                    2_500_000.0,
                    None,
                    None,
                    72_000.0,
                    840_000.0,
                    8.4,
                    None,
                    0.3,
                    [5.0, 5.2, 5.4],
                    None,
                    LP_KEY,
                    8.4,
                    3.3,
                ],
                [
                    "Lending",
                    "sDAI",
                    "sDAI",
                    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    None,
                    "Aave",
                    None,
                    5.8,
                    7.1,
                    None,
                    4_800_000.0,
                    2_900_000.0,
                    None,
                    None,
                    None,
                    61.5,
                    None,
                    [5.3, 5.5, 5.8],
                    "0x2222222222222222222222222222222222222222",
                    LENDING_KEY,
                    5.8,
                    None,
                ],
            ]
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_seconds=0.0,
                fetch_mode="rows",
                warnings=[],
            )

        if "fct_execution_pools_daily" in sql:
            columns = [
                "date",
                "opportunity_key",
                "type",
                "token",
                "name",
                "address",
                "pool_key",
                "protocol",
                "fee_apr_7d",
                "lvr_apr_7d",
                "net_apr_7d",
                "tvl",
                "fees_usd_daily",
                "volume_usd_daily",
            ]
            rows = [
                ["2026-04-01", LP_KEY, "LP", "GNO", "GNO / xDAI 0.3%", "0x1111111111111111111111111111111111111111", "GNO / xDAI", "Uniswap V3", 4.8, 2.9, 7.7, 2_300_000.0, 9_800.0, 120_000.0],
                ["2026-04-02", LP_KEY, "LP", "GNO", "GNO / xDAI 0.3%", "0x1111111111111111111111111111111111111111", "GNO / xDAI", "Uniswap V3", 5.0, 3.1, 8.1, 2_450_000.0, 10_200.0, 132_000.0],
                ["2026-04-03", LP_KEY, "LP", "GNO", "GNO / xDAI 0.3%", "0x1111111111111111111111111111111111111111", "GNO / xDAI", "Uniswap V3", 5.1, 3.3, 8.4, 2_500_000.0, 10_700.0, 140_000.0],
            ]
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_seconds=0.0,
                fetch_mode="rows",
                warnings=[],
            )

        if "int_execution_lending_aave_daily" in sql:
            columns = [
                "date",
                "opportunity_key",
                "type",
                "token",
                "name",
                "address",
                "pool_key",
                "protocol",
                "yield_apy",
                "borrow_apy",
                "utilization_rate",
                "total_supplied",
                "total_borrowed",
            ]
            rows = [
                ["2026-04-01", LENDING_KEY, "Lending", "sDAI", "sDAI", "0x2222222222222222222222222222222222222222", None, "Aave", 5.4, 6.8, 59.2, 4_600_000.0, 2_700_000.0],
                ["2026-04-02", LENDING_KEY, "Lending", "sDAI", "sDAI", "0x2222222222222222222222222222222222222222", None, "Aave", 5.6, 6.9, 60.1, 4_700_000.0, 2_820_000.0],
                ["2026-04-03", LENDING_KEY, "Lending", "sDAI", "sDAI", "0x2222222222222222222222222222222222222222", None, "Aave", 5.8, 7.1, 61.5, 4_800_000.0, 2_900_000.0],
            ]
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_seconds=0.0,
                fetch_mode="rows",
                warnings=[],
            )

        return ExecutedQuery(
            sql=sql,
            executed_sql=sql,
            database=database,
            columns=[],
            rows=[],
            row_count=0,
            elapsed_seconds=0.0,
            fetch_mode="rows",
            warnings=[],
        )


def _build_server():
    server = FastMCP("yield-test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_yield_opportunities_tools(server, ch)
    return server


def _get_tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def test_open_yield_opportunities_returns_ranked_dataset():
    server = _build_server()
    fn = _get_tool(server, "open_yield_opportunities")
    result = fn()
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "yield_opportunities"
    assert "opportunities" in sc["datasets"]
    assert sc["summary_cards"][0]["label"] == "Opportunities"


def test_load_yield_opportunity_reuses_view_and_attaches_histories():
    server = _build_server()
    open_fn = _get_tool(server, "open_yield_opportunities")
    opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_yield_opportunity")
    loaded = load_fn(view_id=view_id, opportunity_key=LP_KEY, compare_with=LENDING_KEY)
    sc = loaded.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_id"] == view_id
    assert "selected_history" in sc["datasets"]
    assert "compare_history" in sc["datasets"]
    assert sc["view_state"]["selected_opportunity_key"] == LP_KEY
    assert sc["view_state"]["compare_with"] == LENDING_KEY


def test_update_focus_returns_patch_payload():
    server = _build_server()
    open_fn = _get_tool(server, "open_yield_opportunities")
    view_id = open_fn().structuredContent["view_id"]

    update_fn = _get_tool(server, "update_yield_opportunities_focus")
    result = update_fn(
        view_id=view_id,
        sort="tvl_desc",
        token="GNO",
        type="lp",
        protocol="Uniswap V3",
    )
    sc = result.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["sort"] == "tvl_desc"
    assert sc["patch"]["filters"]["token"] == "GNO"


def test_run_simulation_forward_returns_patch_with_results():
    server = _build_server()
    open_fn = _get_tool(server, "open_yield_opportunities")
    opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_yield_opportunity")
    load_fn(view_id=view_id, opportunity_key=LP_KEY)

    simulate_fn = _get_tool(server, "run_yield_simulation")
    result = simulate_fn(view_id=view_id, opportunity_key=LP_KEY, mode="forward", principal=10_000)
    sc = result.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["simulation"]["mode"] == "forward"
    assert sc["patch"]["simulation"]["ending_value_usd"] > 10_000


def test_run_simulation_historical_replay_uses_loaded_history():
    server = _build_server()
    open_fn = _get_tool(server, "open_yield_opportunities")
    view_id = open_fn().structuredContent["view_id"]

    load_fn = _get_tool(server, "load_yield_opportunity")
    load_fn(view_id=view_id, opportunity_key=LENDING_KEY)

    simulate_fn = _get_tool(server, "run_yield_simulation")
    result = simulate_fn(
        view_id=view_id,
        opportunity_key=LENDING_KEY,
        mode="historical_replay",
        principal=5_000,
    )
    sc = result.structuredContent
    assert sc["patch"]["simulation"]["mode"] == "historical_replay"
    assert len(sc["patch"]["simulation"]["series"]) >= 3


def test_invalid_opportunity_key_returns_error():
    server = _build_server()
    open_fn = _get_tool(server, "open_yield_opportunities")
    view_id = open_fn().structuredContent["view_id"]

    load_fn = _get_tool(server, "load_yield_opportunity")
    result = load_fn(view_id=view_id, opportunity_key="bogus")
    assert result.isError is True
