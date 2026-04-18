"""Tests for the Safe-aware Portfolio mini app."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.portfolio import register_portfolio_tools


SAFE = "0x1111111111111111111111111111111111111111"
OWNER = "0x2222222222222222222222222222222222222222"
OWNER2 = "0x3333333333333333333333333333333333333333"
SAFE2 = "0x4444444444444444444444444444444444444444"


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
        parameters = parameters or {}
        address = str(parameters.get("address", "")).lower()
        avatar = str(parameters.get("avatar", "")).lower()

        if "count()" in sql:
            total = 0
            if "api_execution_yields_user_lp_positions" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_yields_user_lending_positions" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_yields_user_fee_collections_daily" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_yields_user_lending_balances_daily" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_yields_user_activity" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_gpay_user_lifetime_metrics" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_gpay_user_balances_daily" in sql:
                total = 2 if address == SAFE else 0
            elif "api_execution_gpay_user_payments_daily" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_gpay_user_cashback_daily" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_gpay_user_activity" in sql:
                total = 1 if address == SAFE else 0
            elif "api_execution_circles_v2_avatar_metadata" in sql:
                total = 1 if avatar == OWNER2 else 0
            elif "api_execution_circles_v2_avatar_balances_latest" in sql:
                total = 1 if avatar == OWNER2 else 0
            elif "api_execution_circles_v2_avatar_token_distribution" in sql:
                total = 1 if avatar == OWNER2 else 0
            elif "api_execution_circles_v2_avatar_trusts_summary" in sql:
                total = 1 if avatar == OWNER2 else 0
            elif "api_execution_circles_v2_trust_relations_current" in sql:
                total = 1 if avatar == OWNER2 else 0
            elif "api_execution_circles_v2_avatar_mint_activity_daily" in sql:
                total = 1 if avatar == OWNER2 else 0
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

        if "FROM api_execution_yields_user_kpis" in sql:
            columns = [
                "wallet_address",
                "total_lp_fees_usd",
                "total_lending_balance_usd",
                "active_lp_positions",
                "in_range_positions",
                "out_of_range_positions",
                "active_lending_positions",
                "first_yield_date",
                "tenure_days",
            ]
            rows = (
                [[SAFE, 1500.0, 4200.0, 1, 1, 0, 1, "2025-02-01", 440]]
                if address == SAFE
                else []
            )
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_lifetime_metrics" in sql:
            columns = [
                "wallet_address",
                "first_activity_date",
                "last_activity_date",
                "total_payment_volume_usd",
                "total_payment_count",
                "total_cashback_usd",
            ]
            rows = (
                [[SAFE, "2025-03-01", "2026-04-16", 4200.0, 17, 24.5]]
                if address == SAFE
                else []
            )
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM int_execution_safes\n" in sql:
            columns = ["safe_address", "creation_version", "block_date", "block_timestamp"]
            rows = (
                [[SAFE, "1.4.1", "2025-01-10", "2025-01-10 08:00:00"]]
                if address == SAFE
                else [[SAFE2, "1.4.1", "2025-05-10", "2025-05-10 10:00:00"]]
                if address == SAFE2
                else []
            )
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM int_execution_safes_current_owners" in sql and "lower(safe_address)" in sql:
            columns = ["safe_address", "owner", "became_owner_at", "current_threshold"]
            if address == SAFE:
                rows = [
                    [SAFE, OWNER, "2025-01-10 08:00:00", 2],
                    [SAFE, OWNER2, "2025-01-10 08:00:00", 2],
                ]
            elif address == SAFE2:
                rows = [[SAFE2, OWNER, "2025-05-10 10:00:00", 1]]
            else:
                rows = []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM int_execution_safes_current_owners" in sql and "lower(owner)" in sql:
            columns = ["safe_address", "owner", "became_owner_at", "current_threshold"]
            rows = (
                [
                    [SAFE, OWNER, "2025-01-10 08:00:00", 2],
                    [SAFE2, OWNER, "2025-05-10 10:00:00", 1],
                ]
                if address == OWNER
                else []
            )
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM int_execution_gpay_wallets" in sql:
            columns = ["address", "activation_date", "creation_time"]
            rows = [[SAFE, "2025-03-01", "2025-01-10 08:00:00"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatars_current" in sql:
            columns = ["avatar", "avatar_type", "name", "block_timestamp", "metadata_name", "metadata_preview_image_url"]
            rows = [[OWNER2, "Human", "Owner Two", "2025-04-02 09:00:00", "Owner Two", "https://example.com/owner2.png"]] if address == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_trusts_summary" in sql:
            columns = ["avatar", "trusts_given_count", "trusts_received_count"]
            rows = [[OWNER2, 4, 7]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "count() AS holdings_count" in sql:
            columns = ["holdings_count", "balance_demurraged"]
            rows = [[3, 245.0]] if avatar == OWNER2 else [[0, 0.0]]
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_tokens_held_count" in sql:
            columns = ["avatar", "tokens_held_count"]
            rows = [[OWNER2, 3]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_balances_daily" in sql and "date = (SELECT max_date FROM latest)" in sql:
            columns = ["wallet_address", "date", "token", "label", "value_native", "value_usd"]
            rows = (
                [
                    [SAFE, "2026-04-16", "EURe", "EURe", 210.0, 210.0],
                    [SAFE, "2026-04-16", "GNO", "GNO", 1.25, 190.0],
                ]
                if address == SAFE
                else []
            )
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_yields_user_lp_positions" in sql:
            columns = ["provider", "pool_address", "protocol", "tick_lower", "tick_upper", "capital_in_usd", "capital_out_usd", "fees_collected_usd", "is_active", "is_in_range", "pool_current_tick", "entry_date", "last_action_date"]
            rows = [[SAFE, "0xpool", "Uniswap V3", -10, 10, 4_000.0, 0.0, 1_500.0, True, True, 4, "2025-02-01", "2026-04-16"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_yields_user_lending_positions" in sql:
            columns = ["user_address", "reserve_address", "symbol", "balance", "balance_usd", "supply_apy", "protocol"]
            rows = [[SAFE, "0xreserve", "sDAI", 4200.0, 4200.0, 5.8, "Aave"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_yields_user_fee_collections_daily" in sql:
            columns = ["date", "provider", "pool_address", "protocol", "fees_usd"]
            rows = [["2026-04-15", SAFE, "0xpool", "Uniswap V3", 120.0]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_yields_user_lending_balances_daily" in sql:
            columns = ["date", "user_address", "reserve_address", "symbol", "balance", "balance_usd"]
            rows = [["2026-04-15", SAFE, "0xreserve", "sDAI", 4200.0, 4200.0]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_yields_user_activity" in sql:
            columns = ["block_timestamp", "date", "transaction_hash", "protocol", "position_address", "wallet_address", "action", "token_symbol", "token_address", "amount", "amount_usd", "source"]
            rows = [["2026-04-15 12:00:00", "2026-04-15", "0xyieldtx", "Aave", "0xposition", SAFE, "Supply", "sDAI", "0xreserve", 100.0, 100.0, "Lending"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_balances_daily" in sql and "ORDER BY date DESC" in sql:
            columns = ["wallet_address", "date", "token", "label", "value_native", "value_usd"]
            rows = [
                [SAFE, "2026-04-16", "EURe", "EURe", 210.0, 210.0],
                [SAFE, "2026-04-15", "EURe", "EURe", 190.0, 190.0],
            ] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_payments_daily" in sql:
            columns = ["wallet_address", "date", "label", "value"]
            rows = [[SAFE, "2026-04-15", "EURe", 45.0]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_cashback_daily" in sql:
            columns = ["wallet_address", "date", "value"]
            rows = [[SAFE, "2026-04-15", 0.12]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_gpay_user_activity" in sql:
            columns = ["transaction_hash", "wallet_address", "timestamp", "date", "action", "symbol", "direction", "amount", "amount_usd", "counterparty"]
            rows = [["0xgpaytx1", SAFE, "2026-04-15 11:00:00", "2026-04-15", "Payment", "EURe", "out", 45.0, 45.0, "0xmerchant"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM execution.logs" in sql and "topic0 = 'ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'" in sql:
            columns = ["transaction_hash", "wallet_address", "timestamp", "date", "action", "symbol", "direction", "amount", "amount_usd", "counterparty"]
            rows = [["0xgpaytx2", SAFE, "2026-04-17 09:30:00", "2026-04-17", "Crypto Deposit", "EURe", "in", 25.0, 25.0, "0xfunder"]] if address == SAFE else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_metadata" in sql:
            columns = ["avatar", "avatar_type", "invited_by", "name", "token_id", "registered_at", "current_metadata_digest", "current_ipfs_cid_v0", "current_gateway_url", "metadata_name", "metadata_symbol", "metadata_description", "metadata_image_url", "metadata_preview_image_url", "metadata_fetched_at"]
            rows = [[OWNER2, "Human", SAFE, "Owner Two", 7, "2025-04-02", "digest", "cid", "https://example.com", "Owner Two", "OWNER2", "Profile", "https://example.com/full.png", "https://example.com/preview.png", "2026-04-16"]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_balances_latest" in sql and "ORDER BY balance_demurraged" in sql:
            columns = ["avatar", "token_address", "is_wrapped", "balance", "balance_demurraged"]
            rows = [[OWNER2, "0xtoken", False, 250.0, 245.0]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_token_distribution" in sql:
            columns = ["avatar", "holder_category", "holder_count", "balance", "balance_demurraged"]
            rows = [[OWNER2, "holders", 15, 250.0, 245.0]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_trust_relations_current" in sql:
            columns = ["truster", "trustee", "valid_from", "valid_to"]
            rows = [[OWNER2, SAFE, "2025-04-04", None]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM api_execution_circles_v2_avatar_mint_activity_daily" in sql:
            columns = ["avatar", "date", "mint_events", "amount_minted"]
            rows = [[OWNER2, "2026-04-15", 1, 24.0]] if avatar == OWNER2 else []
            return ExecutedQuery(sql, sql, database, columns, rows, len(rows), 0.0, "rows", [])

        if "FROM execution.logs" in sql and "topic0 IN (" in sql:
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=["block_timestamp", "event_kind", "owner", "threshold"],
                rows=[],
                row_count=0,
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
    server = FastMCP("portfolio-test")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_portfolio_tools(server, ch)
    return server


def _get_tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def test_open_portfolio_returns_empty_picker_view():
    server = _build_server()
    fn = _get_tool(server, "open_portfolio")
    result = fn()
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "portfolio"
    assert sc["view_state"]["current_address"] == ""


def test_load_portfolio_address_eagerly_loads_overview_and_relationships():
    server = _build_server()
    open_fn = _get_tool(server, "open_portfolio")
    view_id = open_fn().structuredContent["view_id"]

    load_fn = _get_tool(server, "load_portfolio_address")
    loaded = load_fn(view_id=view_id, address=SAFE)
    sc = loaded.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_state"]["current_address"] == SAFE
    assert sc["view_state"]["presence"]["is_safe"] is True
    assert sc["view_state"]["presence"]["has_gpay"] is True
    assert sc["view_state"]["loaded_sections"]["overview"] is True
    assert "relationships" in sc["datasets"]


def test_navigate_portfolio_relation_pushes_breadcrumbs_and_reloads_address():
    server = _build_server()
    open_fn = _get_tool(server, "open_portfolio")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_portfolio_address")
    load_fn(view_id=view_id, address=SAFE)

    navigate_fn = _get_tool(server, "navigate_portfolio_relation")
    navigated = navigate_fn(view_id=view_id, related_address=OWNER)
    sc = navigated.structuredContent
    assert sc["view_state"]["current_address"] == OWNER
    assert len(sc["view_state"]["breadcrumbs"]) == 1
    assert sc["view_state"]["breadcrumbs"][0]["address"] == SAFE
    assert sc["view_state"]["presence"]["owns_safes"] is True


def test_load_portfolio_section_gpay_merges_same_day_overlay():
    server = _build_server()
    open_fn = _get_tool(server, "open_portfolio")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_portfolio_address")
    load_fn(view_id=view_id, address=SAFE)

    section_fn = _get_tool(server, "load_portfolio_section")
    loaded = section_fn(view_id=view_id, section="gpay")
    sc = loaded.structuredContent
    assert sc["view_state"]["loaded_sections"]["gpay"] is True
    assert "gpay_activity" in sc["datasets"]
    assert sc["datasets"]["gpay_activity"]["stats"]["row_count"] == 2
    assert any("same-day Gnosis Pay activity" in warning for warning in sc["warnings"])


def test_invalid_address_returns_error():
    server = _build_server()
    open_fn = _get_tool(server, "open_portfolio")
    view_id = open_fn().structuredContent["view_id"]

    load_fn = _get_tool(server, "load_portfolio_address")
    result = load_fn(view_id=view_id, address="not-an-address")
    assert result.isError is True


def test_valid_no_match_address_returns_ready_empty_state():
    server = _build_server()
    open_fn = _get_tool(server, "open_portfolio")
    view_id = open_fn().structuredContent["view_id"]

    load_fn = _get_tool(server, "load_portfolio_address")
    loaded = load_fn(view_id=view_id, address="0x9999999999999999999999999999999999999999")
    sc = loaded.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_state"]["current_address"] == "0x9999999999999999999999999999999999999999"
    assert any("No portfolio data matched" in warning for warning in sc["warnings"])
