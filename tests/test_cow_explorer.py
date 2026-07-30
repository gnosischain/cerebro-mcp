"""Contract tests for the read-only CoW Data Explorer miniapp."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import INTERACTIVE_QUERY_BUDGET, ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.security import RiskClass, TOOL_RISK_REGISTRY
from cerebro_mcp.tools.visualization import coingecko, cow_explorer, mini_apps, web_apps
from tests.sql_text import sql_code


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
OWNER = "0x" + "33" * 20
TX_HASH = "0x" + "44" * 32
ORDER_UID = "0x" + "55" * 56


class StubCH:
    """ClickHouse stub used by the one-pass exact-capped dataset loader.

    Records ``(sql, database, max_rows, parameters, query_budget)`` per call
    so tests can assert every interactive query carries the shared budget.
    """

    def __init__(self, *, total: int = 2, fail_marker: str = ""):
        self.total = total
        self.fail_marker = fail_marker
        self.calls: list[tuple[str, str, int, dict | None, object]] = []

    def run_query(
        self,
        sql,
        database="dbt",
        requested_max_rows=100,
        audience="tool",
        fetch_mode="auto",
        parameters=None,
        query_budget=None,
    ):
        self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
        if self.fail_marker and self.fail_marker in sql:
            raise RuntimeError("planned dataset failure")
        n = min(self.total, requested_max_rows)
        exact_capped = "__source_rows" in sql
        rows = [[1, NOW, NOW, NOW, self.total] for _ in range(n)] if exact_capped else [[1, NOW, NOW, NOW] for _ in range(n)]
        return self._result(
            sql,
            database,
            ["chain_id", "indexed_from", "indexed_to", "source_observed_at", "__source_rows"] if exact_capped else ["chain_id", "indexed_from", "indexed_to", "source_observed_at"],
            rows,
        )

    @staticmethod
    def _result(sql, database, columns, rows):
        return ExecutedQuery(
            sql=sql,
            executed_sql=sql,
            database=database,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_seconds=0.001,
            fetch_mode="rows",
            warnings=[],
        )


class SearchCH(StubCH):
    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None, query_budget=None):
        self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
        if "token AS identifier" in sql:
            return self._result(sql, database, ["chain_id", "identifier", "entity_type", "role", "evidence_count"], [[1, TOKEN_A, "token", "token_symbol", 3]])
        if "'transaction' AS entity_type" in sql:
            return self._result(sql, database, ["chain_id", "entity_type", "role", "evidence_count"], [[1, "transaction", "transaction", 2]])
        if "'order' AS entity_type" in sql:
            return self._result(sql, database, ["chain_id", "entity_type", "role", "evidence_count"], [[1, "order", "order", 1]])
        if "'auction' AS entity_type" in sql:
            return self._result(sql, database, ["chain_id", "entity_type", "role", "evidence_count"], [[1, "auction", "auction", 1]])
        if "'interaction_target'" in sql:
            return self._result(sql, database, ["chain_id", "role", "evidence_count"], [[1, "owner", 8], [100, "competition_solver", 2]])
        return super().run_query(sql, database, requested_max_rows, audience, fetch_mode, parameters, query_budget)


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    cow_explorer.reset_failure_cache_for_tests()
    mini_apps.reset_views_for_tests()
    web_apps.WEB_APP_CONFIGS.pop(cow_explorer.COW_APP_ID, None)
    for name in (
        "open_cow_explorer", "load_cow_explorer_section", "search_cow_explorer",
        "load_cow_entity", "load_cow_explorer_datasets", "load_cow_icon_overlay",
    ):
        web_apps.MINI_APP_TOOL_REGISTRY.pop(name, None)
    # The CoinGecko cache moved to the shared visualization.coingecko module
    # when the governance Treasury tab needed the same lookups; reset it through
    # its own helper rather than reaching into another module's internals.
    coingecko.reset_caches_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


def _server(ch=None):
    server = FastMCP("cow-test")
    ch = ch or StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    cow_explorer.register_cow_explorer_tools(server, ch)
    return server, ch


def _tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def test_launcher_opens_with_zero_clickhouse_round_trips():
    server, ch = _server()
    result = _tool(server, "open_cow_explorer")()
    payload = result.structuredContent
    assert payload["type"] == "INITIAL_LOAD"
    assert payload["app_id"] == "cow_explorer"
    assert payload["view_state"]["chain_id"] == 0
    assert payload["view_state"]["environment_scope"] == "production"
    assert len(payload["view_state"]["chain_options"]) == 10
    # v2 contract: the open path never touches ClickHouse — all datasets defer.
    assert payload["datasets"] == {}
    assert ch.calls == []
    groups = payload["view_state"]["loaded_groups"]
    assert groups["overview.core"] is False
    assert groups["overview.breakdown"] is False
    # chain options carry the static CoinGecko chain icon registry
    assert all("icon_url" in option for option in payload["view_state"]["chain_options"])


def test_section_apply_loads_core_and_group_tool_streams_the_rest():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    ).structuredContent
    assert applied["type"] == "INITIAL_LOAD"
    assert set(applied["datasets"]) == {"network_summary", "coverage_matrix"}
    assert applied["view_state"]["loaded_groups"]["overview.core"] is True
    assert applied["view_state"]["loaded_groups"]["overview.breakdown"] is False
    scope_id = applied["view_state"]["scope_id"]
    assert all(call[1] == "cow_db" for call in ch.calls)
    assert all((call[3] or {}).get("env") == "production" for call in ch.calls)
    grouped = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=scope_id,
    ).structuredContent
    assert grouped["type"] == "PATCH_VIEW_STATE"
    assert set(grouped["datasets"]) == {"network_activity", "top_pairs", "fee_policy_counts"}
    assert grouped["patch"]["loaded_groups"] == {"overview.breakdown": True}
    assert set(grouped["patch"]["dataset_revisions"]) == set(grouped["datasets"])
    record = mini_apps.get_view(view_id)
    assert record is not None
    assert set(record.datasets) == {
        "network_summary", "coverage_matrix", "network_activity", "top_pairs", "fee_policy_counts"
    }


def test_group_load_with_stale_scope_id_is_a_noop():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    )
    call_count = len(ch.calls)
    stale = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id="production:0:overview:999",
    ).structuredContent
    assert stale["type"] == "PATCH_VIEW_STATE"
    assert "stale_scope" in stale["warnings"]
    assert stale.get("datasets") in (None, {})
    assert len(ch.calls) == call_count


def test_testnet_scope_contains_only_sepolia():
    server, _ = _server()
    result = _tool(server, "open_cow_explorer")(environment_scope="testnet")
    options = result.structuredContent["view_state"]["chain_options"]
    assert [(item["chain_id"], item["name"]) for item in options] == [(11155111, "Ethereum Sepolia")]


def test_all_network_sections_and_single_chain_coercion():
    server, _ = _server()
    # Trades supports the all-networks scope in v2.
    result = _tool(server, "open_cow_explorer")(section="trades", chain_id=0)
    state = result.structuredContent["view_state"]
    assert state["section"] == "trades"
    assert state["chain_id"] == 0
    assert result.structuredContent["datasets"] == {}
    assert state["loaded_groups"]["trades.core"] is False
    # Markets stays single-chain: switching to it FROM the all-networks scope
    # coerces to Ethereum with an explicit warning.
    opened = _tool(server, "open_cow_explorer")()  # all-networks overview
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="markets", chain_id=0
    ).structuredContent
    assert applied["view_state"]["chain_id"] == 1
    assert "all_networks_unsupported" in applied["view_state"]["warnings"]
    # Live joined ALL_NETWORK_SECTIONS in v3: chain 0 stays all-networks
    # (merged feeds), no coercion and no warning.
    live = _tool(server, "open_cow_explorer")(section="live", chain_id=0)
    live_state = live.structuredContent["view_state"]
    assert live_state["chain_id"] == 0
    assert "all_networks_unsupported" not in live_state["warnings"]
    # Orders joined too: status/type analytics are multi-chain.
    orders = _tool(server, "open_cow_explorer")(section="orders", chain_id=0)
    assert orders.structuredContent["view_state"]["chain_id"] == 0


def test_section_transition_retains_other_sections_and_fingerprint_short_circuits():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    )
    loaded = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=2, section="trades", chain_id=1
    ).structuredContent
    assert loaded["type"] == "INITIAL_LOAD"
    assert loaded["view_state"]["applied_request_id"] == 2
    # Trades core loads; the overview core datasets are RETAINED on the view.
    assert {"trade_activity", "trade_pair_breakdown"} <= set(loaded["datasets"])
    assert {"network_summary", "coverage_matrix"} <= set(loaded["datasets"])
    assert "trades" not in loaded["datasets"]  # tape group defers
    first = loaded["view_state"]["dataset_revisions"]["trade_activity"]
    # Tab return with an unchanged scope: zero ClickHouse round trips.
    call_count = len(ch.calls)
    restored = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=3, section="overview", chain_id=0
    ).structuredContent
    assert restored["view_state"]["section"] == "overview"
    assert len(ch.calls) == call_count
    # Force refresh re-runs the core group and bumps revisions.
    refreshed = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=4, section="trades", chain_id=1, force_refresh=True
    ).structuredContent
    assert refreshed["view_state"]["dataset_revisions"]["trade_activity"] > first
    assert refreshed["datasets"]["trade_activity"]["stats"]["fetched_at"]


def test_entity_drilldowns_accumulate_and_truncate_breadcrumbs():
    server, _ = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    first = _tool(server, "load_cow_entity")(
        view_id=view_id, request_id=1, entity_type="address", identifier=OWNER, chain_id=1
    ).structuredContent
    assert first["view_state"]["date_range"]["kind"] == "all"
    second = _tool(server, "load_cow_entity")(
        view_id=view_id, request_id=2, entity_type="token", identifier=TOKEN_A, chain_id=1
    ).structuredContent
    assert [crumb["entity_type"] for crumb in second["view_state"]["breadcrumbs"]] == ["address", "token"]
    back = _tool(server, "load_cow_entity")(
        view_id=view_id, request_id=3, entity_type="address", identifier=OWNER, chain_id=1
    ).structuredContent
    assert [crumb["entity_type"] for crumb in back["view_state"]["breadcrumbs"]] == ["address"]


def test_partial_dataset_failure_keeps_successful_datasets():
    server, _ = _server(StubCH(fail_marker="fill_count DESC"))
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    ).structuredContent
    grouped = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=applied["view_state"]["scope_id"],
    ).structuredContent
    # v2.1 failure contract: the failed dataset stays VISIBLE as a zero-row
    # stub whose provenance carries the error, and the group is "partial" —
    # dropping it silently blanked whole panels in production.
    assert "top_pairs" in grouped["datasets"]
    stub = grouped["datasets"]["top_pairs"]
    assert stub["preview_rows"] == []
    assert stub["provenance"]["coverage"]["error"]
    assert "network_activity" in grouped["datasets"]
    assert "query_failed" in grouped["warnings"]
    assert grouped["patch"]["coverage"]["top_pairs"]["warning_codes"] == ["query_failed"]
    assert grouped["patch"]["loaded_groups"]["overview.breakdown"] == "partial"


def test_unknown_view_and_invalid_entity_short_circuit_before_sql():
    server, ch = _server()
    bad_view = _tool(server, "load_cow_explorer_section")(
        view_id="missing", request_id=1, section="trades"
    )
    assert bad_view.isError
    assert ch.calls == []
    opened = _tool(server, "open_cow_explorer")()
    call_count = len(ch.calls)
    invalid = _tool(server, "load_cow_entity")(
        view_id=opened.structuredContent["view_id"], request_id=1,
        entity_type="transaction", identifier="0xdead", chain_id=1,
    )
    assert invalid.isError
    assert len(ch.calls) == call_count


def test_stale_search_is_ignored_without_sql():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=2, section="trades", chain_id=1
    )
    call_count = len(ch.calls)
    stale = _tool(server, "search_cow_explorer")(
        view_id=view_id, request_id=1, query=TX_HASH, chain_id=1
    )
    assert stale.structuredContent["view_state"]["applied_request_id"] == 2
    assert len(ch.calls) == call_count


@pytest.mark.parametrize(
    ("query", "entity_type", "identifier"),
    [
        (ORDER_UID, "order", ORDER_UID),
        (TX_HASH, "transaction", TX_HASH),
        ("42", "auction", "42"),
        ("WETH", "token", TOKEN_A),
    ],
)
def test_search_classifier_formats(query, entity_type, identifier):
    candidates = cow_explorer._search_candidates(SearchCH(), query, "production", 0)
    assert candidates[0]["entity_type"] == entity_type
    assert candidates[0]["identifier"] == identifier


def test_address_search_returns_role_and_chain_candidates():
    candidates = cow_explorer._search_candidates(SearchCH(), OWNER, "production", 0)
    assert {(c["chain_id"], c["role"], c["entity_type"]) for c in candidates} == {
        (1, "owner", "address"), (100, "competition_solver", "solver")
    }


def test_exact_capped_mode_counts_and_fetches_deterministic_newest_rows():
    ch = StubCH(total=10_050)
    dataset = mini_apps.load_exact_capped_dataset(
        ch, "SELECT id,event_time FROM cow_db.events ORDER BY event_time DESC,id DESC",
        database="cow_db", row_cap=10_000,
    )
    assert dataset.stats.mode == "exact_capped"
    assert dataset.stats.rows_returned == 10_000
    assert dataset.stats.source_rows == 10_050
    assert dataset.stats.row_cap == 10_000
    assert dataset.stats.truncated is True
    assert len(ch.calls) == 1
    assert "count() OVER () AS __source_rows" in ch.calls[0][0]
    assert "ORDER BY event_time DESC,id DESC" in ch.calls[0][0]
    assert ch.calls[0][0].rstrip().endswith("LIMIT 10000")


def test_exact_capped_mode_rejects_unordered_sql_before_querying():
    ch = StubCH()
    with pytest.raises(mini_apps.MiniAppQueryError, match="ORDER BY"):
        mini_apps.load_exact_capped_dataset(ch, "SELECT * FROM cow_db.events", database="cow_db")
    assert ch.calls == []


def test_specs_carry_interactive_query_budget():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    ).structuredContent
    _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=applied["view_state"]["scope_id"],
    )
    assert ch.calls
    assert all(call[4] is INTERACTIVE_QUERY_BUDGET for call in ch.calls)


def test_search_carries_interactive_query_budget():
    ch = SearchCH()
    cow_explorer._search_candidates(ch, TOKEN_A, "production", 0)
    assert ch.calls
    assert all(call[4] is INTERACTIVE_QUERY_BUDGET for call in ch.calls)


def test_generated_spec_sql_never_glues_tokens_to_set_operators():
    """Multi-arm UNION assembly must keep whitespace around set-op keywords.

    Regression: f-string expressions cannot contain ``\\n`` on py<3.12, so
    ``"UNION ALL".join(parts)`` inline in an f-string silently produced
    ``GROUP BY token0,token1UNION ALL`` — a live-only ClickHouse syntax
    error (code 62) on every all-networks arm (single-chain has one arm and
    never joins). Sweep every section's generated SQL for glued keywords.
    """
    import re

    glued = re.compile(r"\S(?:UNION|EXCEPT|INTERSECT)\b|\b(?:UNION ALL|EXCEPT|INTERSECT)\S")
    filters = {"status": "", "owner": "", "solver": "", "token": ""}
    single = cow_explorer.COW_CHAINS[1]
    for section in cow_explorer.SECTION_DEFAULT_DAYS:
        chains = (
            (None, cow_explorer.COW_CHAINS[100])
            if section in cow_explorer.ALL_NETWORK_SECTIONS
            else (single,)
        )
        for chain in chains:
            range_state = cow_explorer._range_state(section, -1, "", "")
            specs = cow_explorer._section_specs(
                section, "production", chain, (TOKEN_A, TOKEN_B), "1h",
                range_state, filters,
            )
            assert specs
            for spec in specs:
                # Comments stripped FIRST. A .sql file's rationale header is part
                # of the rendered SQL now, and prose ending "...joined with UNION
                # ALL." reads to this regex as a glued keyword. See tests/sql_text.
                match = glued.search(sql_code(spec.sql))
                assert match is None, (
                    f"{section}/{spec.key}: glued set-op keyword "
                    f"{match.group(0)!r} in generated SQL"
                )


def test_sql_contracts_cover_price_depth_fee_and_solver_invariants():
    chain = cow_explorer.COW_CHAINS[1]
    relative = cow_explorer._range_state("markets", 30, "", "")
    market = {s.key: s for s in cow_explorer._market_specs(chain, (TOKEN_A, TOKEN_B), "1h", relative)}
    candles = market["price_candles"].sql
    assert "quote_qty/nullIf(base_qty,0)" in candles
    assert "sum(quote_qty)/nullIf(sum(base_qty),0)" in candles
    assert "tuple(block_timestamp,log_index,tx_hash,order_uid)" in candles
    assert "INNER JOIN tm AS b" in candles and "INNER JOIN tm AS q" in candles
    assert "auction_prices" in market["auction_reference_prices"].sql
    assert "chain_blocks" in market["auction_reference_prices"].sql
    assert "pow(10" in market["auction_reference_prices"].sql
    orders = {s.key: s for s in cow_explorer._order_specs("production", chain, (TOKEN_A, TOKEN_B), cow_explorer._range_state("orders", 30, "", ""), {})}
    depth = orders["intent_depth"].sql
    assert "executed_sell_amount<o.sell_amount" in depth
    assert "executed_buy_amount<o.buy_amount" in depth
    assert "o.kind='buy'" in depth
    assert "o.valid_to>" in depth and "presignaturePending" in depth
    overview = {s.key: s for s in cow_explorer._overview_specs("production", relative)}
    fees = overview["fee_policy_counts"].sql
    assert "GROUP BY f.token,f.policy" in fees
    assert "token_symbol" in fees  # symbol projection is mandatory (v2)
    assert "toString(u.amount_sum)" in fees
    # Fees stand alone on protocol_fees FINAL (observed_at basis) — the old
    # trades join only supplied timestamps and was a memory/time hog.
    assert "cow_db.protocol_fees AS f FINAL" in fees
    assert "trades" not in fees
    # v2.1: network_summary is grouped single-pass CTEs on a chain spine —
    # one trades scan + one argMax-deduped orders scan (no per-chain arms,
    # no FINAL; uniq() keeps memory constant at all-history).
    summary_sql = overview["network_summary"].sql
    assert "UNION ALL" not in summary_sql
    assert "arrayJoin" in summary_sql
    assert "GROUP BY t.chain_id" in summary_sql
    # No FINAL on the heavy tables (orders/trades); the small
    # solver_competitions CTE may keep it.
    assert "orders FINAL" not in summary_sql and "orders AS o FINAL" not in summary_sql
    assert "trades FINAL" not in summary_sql
    assert "argMax(status,observed_at)" in summary_sql
    assert "uniq(" in summary_sql and "uniqExact(" not in summary_sql
    pairs_sql = overview["top_pairs"].sql
    assert "token0_symbol" in pairs_sql and "token1_symbol" in pairs_sql
    assert "LIMIT 500" in pairs_sql
    assert pairs_sql.count("GROUP BY token0,token1") == 10
    assert "LEFT JOIN tm" in orders["known_orders"].sql
    assert "FROM enriched" in orders["known_orders"].sql
    assert "FROM normalized" in orders["intent_depth"].sql
    solvers = {s.key: s for s in cow_explorer._solver_specs("production", chain, relative)}
    assert "competition_solver" in solvers["solver_stats"].sql
    assert "settlement_executor" in solvers["execution_flow"].sql
    token_prices = {s.key: s for s in cow_explorer._entity_specs("token", TOKEN_A, chain)}["token_execution_prices"].sql
    assert "sum(quote_qty)/nullIf(sum(base_qty),0)" in token_prices
    assert "vwap_quote_per_token" in token_prices
    trades = {s.key: s for s in cow_explorer._trade_specs("production", chain, relative, {})}
    assert "settlements_canonical" not in trades["trades"].sql
    assert "settlement_executor" not in trades["trades"].sql
    for spec in [*market.values(), *orders.values(), *overview.values(), *solvers.values()]:
        assert "SETTINGS" not in spec.sql.upper()
        assert "cow_db." in spec.sql
        assert spec.parameters["env"] == "production"
        # The exact-capped loader wraps the SQL (+~80 chars) and the server
        # rejects statements over 10,000 — leave headroom for the wrapper.
        assert len(spec.sql) <= 9_900


def test_depth_heatmap_window_validation():
    assert cow_explorer._validate_heatmap_window("24h") == "24h"
    assert cow_explorer._validate_heatmap_window("7d") == "7d"
    assert cow_explorer._validate_heatmap_window("30d") == "30d"
    assert cow_explorer._validate_heatmap_window("90d") == "90d"
    assert cow_explorer._validate_heatmap_window(" ALL ") == "all"
    for bad in ("", "1h", "week", "foo"):
        with pytest.raises(ValueError):
            cow_explorer._validate_heatmap_window(bad)


def test_depth_heatmap_spec_contract():
    chain = cow_explorer.COW_CHAINS[1]
    relative = cow_explorer._range_state("markets", 30, "", "")
    # The footprint spec is built by _market_specs and owns its group.
    assert cow_explorer.SECTION_GROUPS["markets"]["depth_heatmap"] == ("pair_depth_heatmap",)
    market = {
        s.key: s
        for s in cow_explorer._market_specs(chain, (TOKEN_A, TOKEN_B), "1h", relative, "", "7d")
    }
    spec = market["pair_depth_heatmap"]
    assert spec.cache_ttl_seconds == 3600
    assert spec.exact_count is False
    assert spec.parameters["window"] == "7d"
    assert spec.parameters["bucket_seconds"] == 0
    assert spec.parameters["base"] == TOKEN_A and spec.parameters["quote"] == TOKEN_B
    sql = spec.sql
    # Time-grid reconstruction shape.
    assert "arrayJoin(arrayMap(" in sql
    assert "multiIf({window:String}='24h'" in sql
    # Both fill-completion (trades) and terminal-event (order_events) removal so
    # filled-but-not-status-marked orders don't rest forever.
    assert "cow_db.trades" in sql and "filled_out_ts" in sql
    assert "cow_db.order_events" in sql and "terminated_at" in sql
    assert "alive_until" in sql
    # Interval-overlap predicate, not point-in-time.
    assert "p.created < (b.bucket_ts + b.step_s)" in sql
    assert "p.alive_until > b.bucket_ts" in sql
    # Cancelled orders without any timestamped removal evidence are excluded
    # (the backfill cancel-time gap — else they phantom-rest until valid_to).
    assert "status_l!='cancelled'" in sql


def test_depth_footprint_is_one_binned_tier_on_a_per_bucket_reference():
    """Every window ships the SAME binned shape, priced RELATIVE to each
    bucket's own median.

    The relative reference is both a readability fix (an absolute axis leaves a
    multi-year window's book in one or two rows) and a correctness one: the old
    +-30%-of-WINDOW-median clamp retained just 53.3% of mainnet USDC/WETH
    orders, versus 92.5% at +-20% of the bucket median (measured over all
    254,525 of them).
    """
    chain = cow_explorer.COW_CHAINS[1]
    relative = cow_explorer._range_state("markets", 30, "", "")
    for window in ("24h", "7d", "30d", "90d", "all"):
        spec = {
            s.key: s
            for s in cow_explorer._market_specs(chain, (TOKEN_A, TOKEN_B), "1h", relative, "", window)
        }["pair_depth_heatmap"]
        sql = spec.sql
        assert spec.parameters["window"] == window
        # Per-BUCKET reference, keyed by grid index, and the clamp is measured
        # against it — never against a single window-wide median.
        assert "bmed AS (" in sql
        assert "coalesce(m.b_med," in sql
        assert "abs(price / bucket_mid - 1) <= 0.2" in sql
        # `priced` (cand argMax + term + fill) costs ~5.6s per materialization
        # and ClickHouse INLINES CTEs, so every extra reference re-runs the
        # whole chain — two references timed out the 20s interactive budget
        # live. It must be consumed EXACTLY ONCE: pmed derives from bmed, and
        # bmed reads raw orders (exact, since price columns are immutable).
        assert "SELECT quantile(0.5)(b_med) AS p_med FROM bmed" in sql
        code = "\n".join(
            ln for ln in sql.split("\n") if not ln.strip().startswith("--")
        )
        assert code.count("priced") == 2, "priced must be defined once and used once"
        assert "priced AS (" in sql and "CROSS JOIN priced AS p" in sql
        assert "FROM cow_db.orders AS o\n  CROSS JOIN dims AS d" in sql
        # Time-weighted resting depth on every window, not just the deep ones.
        assert "dateDiff('second'" in sql and "/ b.step_s AS w" in sql
        # Output contract the client parses.
        for column in ("AS bucket", "AS bucket_mid", "AS rel_pct", "AS side",
                       "AS depth_base", "AS orders", "AS bucket_seconds"):
            assert column in sql, (window, column)
        assert "GROUP BY bucket, bucket_mid, rel_pct, side" in sql
        # Standard CoW spec invariants.
        assert "cow_db." in sql
        assert "SETTINGS" not in sql.upper()
        assert len(sql) <= 9_900
    # Deep floor is reconstruction-capable history, not the capture start.
    assert "min(creation_date)" in sql


def test_depth_footprint_resolution_is_caller_chosen_and_row_bounded():
    """`bucket_seconds` picks the bucket width; the row budget is a hard cap, so
    a too-fine request is coarsened by the SQL rather than rejected."""
    chain = cow_explorer.COW_CHAINS[1]
    spec = cow_explorer._pair_depth_heatmap_specs(chain, (TOKEN_A, TOKEN_B), "7d", 3600)[0]
    assert spec.parameters["bucket_seconds"] == 3600
    sql = spec.sql
    # Requested width wins over the auto span/60...
    assert "if({bucket_seconds:UInt32} > 0," in sql
    # ...but is floored, and coarsened so the grid fits the bucket cap.
    assert f"toUInt32({cow_explorer._FOOTPRINT_MIN_STEP_S})" in sql
    assert f"ceil(span_s / {float(cow_explorer._FOOTPRINT_MAX_BUCKETS)})" in sql
    assert f"least({cow_explorer._FOOTPRINT_MAX_BUCKETS}," in sql
    # 41 bins x 2 sides x 120 buckets = 9,840 rows, under the 10k result cap.
    bins = int(2 * cow_explorer._FOOTPRINT_REL_PCT / cow_explorer._FOOTPRINT_REL_STEP) + 1
    assert bins * 2 * cow_explorer._FOOTPRINT_MAX_BUCKETS <= 10_000
    # 1.0-point bins over +-20% are exactly the client's display levels, so the
    # two grids coincide and nothing is re-binned.
    assert bins == 41


def test_validate_bucket_seconds():
    assert cow_explorer._validate_bucket_seconds(0) == 0
    assert cow_explorer._validate_bucket_seconds("3600") == 3600
    assert cow_explorer._validate_bucket_seconds(cow_explorer._FOOTPRINT_MIN_STEP_S) \
        == cow_explorer._FOOTPRINT_MIN_STEP_S
    for bad in (1, -5, cow_explorer._FOOTPRINT_MAX_STEP_S + 1, "soon"):
        with pytest.raises(ValueError):
            cow_explorer._validate_bucket_seconds(bad)


def test_depth_heatmap_specs_require_a_pair():
    chain = cow_explorer.COW_CHAINS[1]
    assert cow_explorer._pair_depth_heatmap_specs(chain, ("", ""), "7d") == []
    assert cow_explorer._pair_depth_heatmap_specs(chain, (TOKEN_A, ""), "all") == []


#: v2 invariant — every dataset that surfaces token addresses must project the
#: matching display symbols so the UI never falls back to bare addresses when
#: metadata exists. Keys map dataset → required symbol column aliases.
TOKEN_SYMBOL_CONTRACTS = {
    "top_pairs": ("token0_symbol", "token1_symbol"),
    "fee_policy_counts": ("token_symbol",),
    "trade_pair_breakdown": ("token0_symbol", "token1_symbol"),
    "trades": ("sell_symbol", "buy_symbol"),
    "recent_market_trades": ("sell_symbol", "buy_symbol"),
    "known_orders": ("sell_symbol", "buy_symbol"),
    "execution_flow": ("token0_symbol", "token1_symbol"),
    "order_detail": ("sell_symbol", "buy_symbol"),
    "order_trades": ("sell_symbol", "buy_symbol"),
    "order_fees": ("token_symbol",),
    "transaction_trades": ("sell_symbol", "buy_symbol"),
    "address_trades": ("sell_symbol", "buy_symbol"),
    "address_orders": ("sell_symbol", "buy_symbol"),
    "token_pairs": ("token0_symbol", "token1_symbol"),
    "auction_prices": ("token_symbol",),
    "live_trades": ("sell_symbol", "buy_symbol"),
    "live_open_orders": ("sell_symbol", "buy_symbol"),
}


def test_every_token_bearing_dataset_projects_symbols():
    chain = cow_explorer.COW_CHAINS[1]
    relative = cow_explorer._range_state("markets", 30, "", "")
    specs: dict[str, str] = {}
    for spec in cow_explorer._overview_specs("production", relative):
        specs[spec.key] = spec.sql
    for spec in cow_explorer._market_specs(chain, (TOKEN_A, TOKEN_B), "1h", relative):
        specs[spec.key] = spec.sql
    for spec in cow_explorer._trade_specs("production", chain, relative, {}):
        specs[spec.key] = spec.sql
    for spec in cow_explorer._order_specs("production", chain, (TOKEN_A, TOKEN_B), relative, {}):
        specs[spec.key] = spec.sql
    for spec in cow_explorer._solver_specs("production", chain, relative):
        specs[spec.key] = spec.sql
    for spec in cow_explorer._live_specs("production", chain):
        specs[spec.key] = spec.sql
    for entity_type, identifier in (
        ("order", ORDER_UID), ("transaction", TX_HASH), ("address", OWNER),
        ("token", TOKEN_A), ("auction", "42"), ("solver", OWNER),
    ):
        for spec in cow_explorer._entity_specs(entity_type, identifier, chain):
            specs[spec.key] = spec.sql
    missing_datasets = set(TOKEN_SYMBOL_CONTRACTS) - set(specs)
    assert not missing_datasets, f"contract references unknown datasets: {missing_datasets}"
    for key, symbol_columns in TOKEN_SYMBOL_CONTRACTS.items():
        for column in symbol_columns:
            assert f"AS {column}" in specs[key], (
                f"dataset {key} must project `{column}` next to its token address column"
            )


def test_solver_accounting_and_score_gap_contracts():
    chain = cow_explorer.COW_CHAINS[100]
    specs = {s.key: s.sql for s in cow_explorer._entity_specs("solver", OWNER, chain)}
    imbalance = specs["solver_imbalance_settlements"]
    # Trade-implied flows: sell side in as +, buy side out as -, Int256 math.
    assert "toInt256(sell_amount)" in imbalance
    assert "-toInt256(buy_amount)" in imbalance
    # Native valuation follows the LIVE-VERIFIED convention atoms*price/1e18.
    assert "toFloat64(pr.price)/1e18" in imbalance
    assert "toIntervalDay(30)" in imbalance  # bounded accounting window
    tokens = specs["solver_imbalance_tokens"]
    assert "toString(sum(f.net_atoms)) AS net_amount_raw" in tokens
    assert "token_symbol" in tokens
    gap = specs["solver_score_gap"]
    # reference_score is ALWAYS a JSON map keyed by solver (verified live) —
    # JSONExtractString is the only parse path; parse health is flagged.
    assert "JSONExtractString(c.reference_score,{id:String})" in gap
    assert "scores_parsed" in gap
    summary = specs["solver_summary"]
    # Multi-winner combinatorial auctions are live: winner with ranking!=1 is
    # informational, tracked as a share — never framed as a violation.
    assert "multi_winner_share" in summary
    assert "score_parse_failures" in summary
    for key, sql in specs.items():
        assert "SETTINGS" not in sql.upper(), key
        assert len(sql) <= 9_900, key


def test_native_token_and_explorer_registry_contract():
    assert cow_explorer.NATIVE_TOKEN == "0x" + "ee" * 20
    assert sum(c.explorer.provider == "blockscout" for c in cow_explorer.COW_CHAINS.values()) == 8
    assert cow_explorer.COW_CHAINS[56].explorer.brand == "BscScan"
    assert cow_explorer.COW_CHAINS[43114].explorer.token_url_template.endswith("/address/{address}")
    assert cow_explorer.COW_CHAINS[9745].explorer.brand == "Plasmascan"
    for chain in cow_explorer.COW_CHAINS.values():
        assert "/tx/{hash}" in chain.explorer.transaction_url_template
        assert "/address/{address}" in chain.explorer.address_url_template


def test_coingecko_icons_use_platform_lists_cache_and_fallback_safely(monkeypatch):
    weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    calls: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "tokens": [
                    {
                        "address": weth.upper(),
                        "logoURI": "https://assets.coingecko.com/coins/images/2518/thumb/weth.png",
                    },
                    {
                        "address": TOKEN_A,
                        "logoURI": "https://example.com/untrusted.png",
                    },
                ]
            }

    def get(url, **_kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(coingecko.requests, "get", get)

    class InlineExecutor:
        """Run the background fetch synchronously so the test is deterministic."""

        def submit(self, fn):
            fn()

    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())

    # First call: cache miss → background fetch (inline here) → pending=True
    # and no icons yet. NOTHING blocks.
    icons, pending = coingecko.icon_map_nowait(1)
    assert icons == {} and pending is True
    assert calls == ["https://tokens.coingecko.com/ethereum/all.json"]
    # Second call: cache hit with the fetched map; untrusted hosts filtered.
    icons, pending = coingecko.icon_map_nowait(1)
    assert pending is False
    assert icons[weth] == "https://assets.coingecko.com/coins/images/2518/thumb/weth.png"
    assert TOKEN_A not in icons  # https://example.com is not an allowed host
    assert len(calls) == 1
    # Chains without a CoinGecko platform id resolve to nothing, not a fetch.
    assert coingecko.icon_map_nowait(11155111) == ({}, False)
    # The cow-level alias still resolves to the shared registry.
    assert cow_explorer.COINGECKO_PLATFORM_IDS[57073] == "ink"
    assert cow_explorer.COINGECKO_PLATFORM_IDS is coingecko.PLATFORM_IDS


def test_icon_overlay_maps_dataset_tokens_and_native(monkeypatch):
    weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    with coingecko._LOCK:
        coingecko._ICON_CACHE[1] = (
            __import__("time").monotonic(),
            {weth: "https://assets.coingecko.com/coins/images/2518/thumb/weth.png"},
        )
    from cerebro_mcp.runtime.mini_app_cache import CachedDataset
    from cerebro_mcp.models.mini_app import DatasetStats

    dataset = CachedDataset(
        columns=["chain_id", "token0", "token1"],
        column_types=["int", "str", "str"],
        rows=[[1, weth, cow_explorer.NATIVE_TOKEN], [1, TOKEN_A, weth]],
        stats=DatasetStats(row_count=2, rows_returned=2, mode="exact_capped"),
        sql="--", database="cow_db", parameters={"chain_id": 1},
    )
    overlay, pending = cow_explorer._build_icon_overlay({"top_pairs": dataset})
    assert pending is False
    assert overlay["1"][weth].startswith("https://assets.coingecko.com/")
    assert overlay["1"][cow_explorer.NATIVE_TOKEN].startswith("https://coin-images.coingecko.com/")
    assert TOKEN_A not in overlay["1"]  # no icon known → omitted, monogram client-side


def test_orders_cache_parameters_are_stable_and_pair_scoped():
    chain = cow_explorer.COW_CHAINS[1]
    specs = {
        spec.key: spec
        for spec in cow_explorer._order_specs(
            "production",
            chain,
            (TOKEN_A, TOKEN_B),
            cow_explorer._range_state("orders", 30, "", ""),
            {},
        )
    }
    assert "server_as_of" not in specs["order_status_summary"].parameters
    assert "base" not in specs["order_activity"].parameters
    assert specs["known_orders"].parameters["base"] == TOKEN_A
    assert specs["known_orders"].parameters["server_as_of"].endswith(":00Z")


def test_extended_candle_intervals_remain_server_whitelisted():
    for interval in ("5m", "15m", "30m", "1h", "2h", "4h", "12h", "1d", "1w"):
        resolved, warnings = cow_explorer._resolve_interval(interval, 7)
        assert resolved == interval
        assert warnings == []


def test_visibility_web_registry_and_security_metadata():
    server, _ = _server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    app_only = {
        "load_cow_explorer_section", "search_cow_explorer", "load_cow_entity",
        "load_cow_explorer_datasets", "load_cow_icon_overlay",
    }
    assert "open_cow_explorer" in names
    assert app_only.isdisjoint(names)
    assert app_only <= mini_apps.get_app_only_tool_names()
    config = web_apps.WEB_APP_CONFIGS["cow_explorer"]
    assert config.open_tool == "open_cow_explorer"
    assert config.diagnostics_loader is not None
    assert app_only <= config.allowed_tools
    assert TOOL_RISK_REGISTRY["open_cow_explorer"] == frozenset({RiskClass.READ_ONLY})
    for name in app_only:
        assert TOOL_RISK_REGISTRY[name] == frozenset({RiskClass.APP_ONLY})


def test_section_groups_cover_every_dataset_key_exactly_once():
    for section, groups in cow_explorer.SECTION_GROUPS.items():
        assert "core" in groups, f"{section} must define a core group"
        seen: dict[str, str] = {}
        for group, keys in groups.items():
            assert keys, f"{section}.{group} must not be empty"
            for key in keys:
                assert key not in seen, (
                    f"dataset {key} appears in {section}.{seen[key]} and {section}.{group}"
                )
                seen[key] = group


def test_remove_view_datasets_detaches_and_resets_revisions():
    from cerebro_mcp.runtime.mini_app_cache import CachedDataset
    from cerebro_mcp.models.mini_app import DatasetStats

    view_id = mini_apps.create_view("cow_explorer", "t")
    ds = CachedDataset(
        columns=["a"], column_types=["int"], rows=[[1]],
        stats=DatasetStats(row_count=1, rows_returned=1, mode="exact_capped"),
        sql="--", database="cow_db",
    )
    mini_apps.attach_dataset(view_id, "k", ds)
    mini_apps.attach_dataset(view_id, "k", ds)
    record = mini_apps.get_view(view_id)
    assert record is not None and record.dataset_revisions["k"] == 2
    mini_apps.remove_view_datasets(view_id, ["k", "unknown-key"])
    record = mini_apps.get_view(view_id)
    assert record is not None
    assert "k" not in record.datasets and "k" not in record.dataset_revisions
    mini_apps.attach_dataset(view_id, "k", ds)
    record = mini_apps.get_view(view_id)
    assert record is not None and record.dataset_revisions["k"] == 1


def test_namespaced_split_assets_are_served_for_cow_and_data_catalog():
    _server()

    class Request:
        def __init__(self, app_id, path):
            self.path_params = {"app_id": app_id, "path": path}
            self.headers = {}

    cow_assets = cow_explorer.get_cow_explorer_diagnostics()["assets"]
    cow_js = next(name for name in cow_assets if name.endswith(".js") and "cow-explorer" in name)
    response = asyncio.run(web_apps.serve_app_asset(Request("cow_explorer", cow_js)))
    assert response.status_code == 200
    assert response.headers["cache-control"].endswith("immutable")

    web_apps.register_web_app(
        app_id="data_catalog", open_tool="dummy_catalog", html_loader=lambda: "",
        tools={"dummy_catalog": lambda: None},
    )
    from importlib import resources

    data_root = resources.files("cerebro_mcp").joinpath("static/assets/data_catalog")
    data_js = next(entry.name for entry in data_root.iterdir() if entry.name.endswith(".js") and "data-catalog" in entry.name)
    response = asyncio.run(web_apps.serve_app_asset(Request("data_catalog", data_js)))
    assert response.status_code == 200
    assert response.headers["cache-control"].endswith("immutable")
    assert asyncio.run(web_apps.serve_app_asset(Request("cow_explorer", "../secret"))).status_code == 400


def test_negative_cache_replays_failure_without_requerying():
    """A dataset that just failed must NOT re-run its query on immediate retry."""
    server, ch = _server(StubCH(fail_marker="fill_count DESC"))
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    ).structuredContent
    scope_id = applied["view_state"]["scope_id"]
    first = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=scope_id,
    ).structuredContent
    assert "query_failed" in first["warnings"]
    failing_calls_after_first = sum(
        1 for call in ch.calls if "fill_count DESC" in call[0]
    )
    second = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=scope_id,
    ).structuredContent
    failing_calls_after_second = sum(
        1 for call in ch.calls if "fill_count DESC" in call[0]
    )
    # The failing query ran once; the retry replayed the cached failure.
    assert failing_calls_after_second == failing_calls_after_first
    combined = " ".join(second["warnings"])
    assert "cached failure" in combined
    # An explicit force refresh IS allowed to re-run it.
    _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=scope_id, force_refresh=True,
    )
    assert sum(1 for call in ch.calls if "fill_count DESC" in call[0]) > failing_calls_after_second


def test_tape_specs_use_memory_safe_topn_shape():
    """Row tapes must be top-N-first on base tables — no canonical view, no
    LIMIT BY over unbounded sets, no count() OVER wrapper (exact_count=False)."""
    all_range = cow_explorer._range_state("trades", 0, "", "")
    filters = {"status": "", "owner": "", "solver": "", "token": ""}
    for chain in (None, cow_explorer.COW_CHAINS[100]):
        specs = {s.key: s for s in cow_explorer._trade_specs("production", chain, all_range, filters)}
        tape = specs["trades"]
        assert tape.exact_count is False
        assert "trades_canonical" not in tape.sql
        assert "LIMIT 1 BY" not in tape.sql
        assert f"LIMIT {cow_explorer.TAPE_ARM_LIMIT}" in tape.sql
        assert f"LIMIT {cow_explorer.ROW_CAP}" in tape.sql
        assert "argMax(u.owner,u.observed_at)" in tape.sql
    market = {s.key: s for s in cow_explorer._market_specs(
        cow_explorer.COW_CHAINS[100], (TOKEN_A, TOKEN_B), "1h", all_range)}
    recent = market["recent_market_trades"]
    assert recent.exact_count is False
    assert "trades_canonical" not in recent.sql
    assert f"LIMIT {cow_explorer.TAPE_ARM_LIMIT}" in recent.sql


def test_no_window_clamp_full_history_allowed():
    """The 90d clamp is gone: window=0 must reach the specs as kind=all."""
    state = cow_explorer._range_state("patterns", 0, "", "")
    assert state["kind"] == "all"
    assert not hasattr(cow_explorer, "WINDOW_CLAMPED_SECTIONS")


def test_failed_dataset_stub_attached_to_view():
    server, _ = _server(StubCH(fail_marker="fill_count DESC"))
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="overview", chain_id=0
    ).structuredContent
    _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="breakdown",
        scope_id=applied["view_state"]["scope_id"],
    )
    record = mini_apps.get_view(view_id)
    assert record is not None
    stub = record.datasets.get("top_pairs")
    assert stub is not None
    assert stub.rows == []


def test_correlation_queries_cap_window_even_at_all_history():
    """Patterns/execution_flow join two big tables; their window is capped to
    CORRELATION_MAX_WINDOW_DAYS so the hash-join build stays memory-safe at
    any global selection (the OOM the history tapes do NOT have)."""
    all_range = cow_explorer._range_state("patterns", 0, "", "")  # "All history"
    assert all_range["kind"] == "all"
    chain = cow_explorer.COW_CHAINS[8453]
    patterns = {s.key: s for s in cow_explorer._patterns_specs("production", chain, all_range)}
    cap = cow_explorer.CORRELATION_MAX_WINDOW_DAYS
    for key in ("solver_pair_matrix", "trader_solver_affinity", "fee_policy_quality"):
        spec = patterns[key]
        # The window is a bound param clamped to the cap (NOT the unbounded
        # "AND 1", which carries no window_days param).
        assert spec.parameters.get("window_days") == cap, key
        assert "toIntervalDay({window_days:UInt32})" in spec.sql, key
        # The settlements exec CTE is time-bounded, never a whole-chain scan.
        if "exec AS" in spec.sql:
            assert "block_timestamp >= (SELECT max(block_timestamp) FROM cow_db.settlements" in spec.sql, key
    # execution_flow (solvers, single-chain) gets the same cap.
    solvers = {s.key: s for s in cow_explorer._solver_specs("production", chain, all_range)}
    flow = solvers["execution_flow"]
    assert flow.parameters.get("window_days") == cap
    assert "block_timestamp >= (SELECT max(block_timestamp) FROM cow_db.settlements" in flow.sql
    # A short relative window is left untouched (not artificially widened).
    short = cow_explorer._range_state("patterns", 7, "", "")
    capped, was_capped = cow_explorer._capped_analytical_range(short)
    assert was_capped is False and capped["window_days"] == 7


def test_live_feeds_do_not_self_shadow_block_timestamp_in_where():
    """ClickHouse binds a WHERE identifier to a same-level SELECT alias, so
    `argMax(block_timestamp,observed_at) AS block_timestamp` beside a
    `WHERE block_timestamp >= ...` raises ILLEGAL_AGGREGATION (code 184). The
    live-feed dedup aliases to `block_ts` to avoid it, and still exposes a
    `block_timestamp` output column for the frontend."""
    chain = cow_explorer.COW_CHAINS[100]
    specs = {s.key: s for s in cow_explorer._live_specs("production", chain)}
    for key in ("live_trades", "live_settlements"):
        sql = specs[key].sql
        # The window filter and the self-alias share a query level here.
        assert "block_timestamp >=" in sql, key
        assert "AS block_ts" in sql, key
        # Must NOT re-alias the argMax back onto the raw column name.
        assert "argMax(block_timestamp,observed_at) AS block_timestamp" not in sql, key
        # Output column for the frontend is still named block_timestamp.
        assert "AS block_timestamp" in sql, key


def test_no_unbounded_chain_blocks_final_or_view_builds():
    """Systematic OOM guard (v2.1.2): the memory OOMs were all a whole-table
    `chain_blocks FINAL` used as a join-build side, or a canonical VIEW
    materialized unbounded. Every chain_blocks touch must be pruned by a
    `block_number IN (...)` set, live feeds must carry the block-number floor,
    and the solver accounting must read base `settlements`, not the view."""
    rng_all = cow_explorer._range_state("auctions", 0, "", "")
    # Auctions (all-networks worst case): no `chain_blocks ... FINAL` join.
    for spec in cow_explorer._auction_specs(None, rng_all, "production"):
        assert "chain_blocks AS b FINAL" not in spec.sql, spec.key
        assert "block_number IN (SELECT auction_block" in spec.sql, spec.key
    # coverage_matrix + live_pulse blocks CTEs prune on the checkpoint set.
    ov = {s.key: s for s in cow_explorer._overview_specs("production", rng_all)}
    assert "block_number IN (SELECT checkpoint_block FROM cp)" in ov["coverage_matrix"].sql
    live = {s.key: s for s in cow_explorer._live_specs("production", cow_explorer.COW_CHAINS[100])}
    assert "block_number IN (SELECT checkpoint_block FROM cp)" in live["live_pulse"].sql
    # Live feeds: the 1h block_timestamp bound keeps the GROUP BY hash tiny.
    # block_number is NOT in the settlements/trades sort key, so no block_number
    # floor (it would not prune, and self-aliasing block_number in a WHERE
    # breaks ILLEGAL_AGGREGATION) — the settlements dedup aliases to block_num.
    assert "block_timestamp >= now() - INTERVAL 1 HOUR" in live["live_trades"].sql
    assert "argMax(block_number,observed_at) AS block_number" not in live["live_settlements"].sql
    assert "AS block_num" in live["live_settlements"].sql
    # Solver accounting reads BASE settlements (the canonical view OOMed) and
    # prunes the trades_canonical flows scan by tx_hash (which IS in the trades
    # sort key; block_number is NOT and would not prune).
    solver = {s.key: s for s in cow_explorer._entity_specs("solver", "0x"+"ab"*20, cow_explorer.COW_CHAINS[100])}
    imb = solver["solver_imbalance_settlements"].sql
    assert "FROM cow_db.settlements_canonical" not in imb
    assert "FROM cow_db.settlements\n" in imb or "FROM cow_db.settlements " in imb
    assert "tx_hash IN (SELECT tx_hash FROM exec)" in imb
    # auction_detail entity: chain_blocks pruned to the one auction block.
    auction = {s.key: s for s in cow_explorer._entity_specs("auction", "12345", cow_explorer.COW_CHAINS[100])}
    assert "chain_blocks AS b FINAL" not in auction["auction_detail"].sql
    assert "block_number IN (SELECT auction_block" in auction["auction_detail"].sql


# ---------------------------------------------------------------------------
# v3 additions: order types, solver directory, trader dynamics, live
# all-networks, pair depth
# ---------------------------------------------------------------------------


def _range(kind_days: int) -> dict:
    return cow_explorer._range_state("orders", kind_days, "", "")


def test_trader_dynamics_is_period_capped_and_isolated():
    """Dynamics/retention ignore the global window (fixed 13-month scan bound)
    and each key is the SOLE member of its load group — the all-time first-seen
    hash must never run beside a sibling scan."""
    specs = {
        s.key: s
        for s in cow_explorer._traders_specs("production", None, _range(0))
    }
    for key in ("trader_dynamics", "trader_retention"):
        sql = specs[key].sql
        months = cow_explorer.TRADER_DYNAMICS_MONTHS
        assert f"toIntervalMonth({months + 1})" in sql  # om warm-up bound
        assert "fsall" in sql  # deliberate all-time first-seen CTE
        assert "window_days" not in specs[key].parameters
        assert specs[key].cache_ttl_seconds >= 1800
        # fsall appears once as a definition per statement.
        assert sql.count("fsall AS (") == 1
    groups = cow_explorer.SECTION_GROUPS["traders"]
    assert groups["dynamics"] == ("trader_dynamics",)
    assert groups["retention"] == ("trader_retention",)


def test_solver_directory_uses_small_hash_grouped_scan():
    """The directory is one settlements streaming scan into a tiny
    (chain, solver) hash + small competition tables — no FINAL on settlements,
    no chain_blocks, no trades join, keys via UNION DISTINCT."""
    specs = {
        s.key: s
        for s in cow_explorer._solver_specs("production", None, _range(30))
    }
    sql = specs["solver_directory"].sql
    assert "settlements FINAL" not in sql
    assert "chain_blocks" not in sql
    assert "cow_db.trades" not in sql
    assert "UNION DISTINCT" in sql
    assert "GROUP BY chain_id,solver" in sql
    assert "chain_anchor_at" in sql
    assert specs["solver_directory"].cache_ttl_seconds >= 1800
    # Score gaps: defensive parsing of bigint score strings + JSON map.
    gaps = specs["solver_score_gaps"].sql
    assert "toFloat64OrNull(s.score)" in gaps
    assert "JSONExtractString(c.reference_score,s.solver)" in gaps
    assert "parse_failures" in gaps


def test_live_all_networks_feeds_stay_hour_bounded_and_deduped():
    specs = {s.key: s for s in cow_explorer._live_specs("production", None)}
    ids = ",".join(
        str(c.chain_id)
        for c in cow_explorer.COW_CHAINS.values()
        if c.environment == "production"
    )
    for key in ("live_trades", "live_settlements", "live_minute_activity"):
        sql = specs[key].sql
        assert cow_explorer.LIVE_WINDOW_SQL in sql
        assert f"chain_id IN ({ids})" in sql
    # Merged feeds dedup per chain and keep the single-chain LIMITs.
    assert "GROUP BY chain_id,tx_hash,log_index,order_uid" in specs["live_trades"].sql
    assert "LIMIT 50" in specs["live_trades"].sql
    assert "GROUP BY chain_id,tx_hash,log_index" in specs["live_settlements"].sql
    assert "LIMIT 30" in specs["live_settlements"].sql
    # Single-chain mode still binds the concrete chain.
    single = {
        s.key: s
        for s in cow_explorer._live_specs("production", cow_explorer.COW_CHAINS[100])
    }
    assert "chain_id={chain_id:UInt64}" in single["live_trades"].sql


def test_live_intents_dedup_without_final():
    """Post-backfill OOM guard: `orders`/`order_events` are ~12M rows, so the
    live intents specs must never FINAL-merge them. open_orders bounds its
    argMax hash with the immutable valid_to prefilter (ogopen pattern) and
    keeps creation_date/valid_to as GROUP BY keys (an aggregate alias on
    valid_to beside the same-level WHERE is the code-184 alias-in-WHERE trap);
    order_events dedups within the 1h observed_at bound on the event_id key."""
    for chain in (None, cow_explorer.COW_CHAINS[100]):
        specs = {s.key: s for s in cow_explorer._live_specs("production", chain)}
        for key in ("live_open_orders", "live_order_events"):
            assert "FINAL" not in specs[key].sql, key
        oo = specs["live_open_orders"].sql
        assert "valid_to>toUnixTimestamp(now())" in oo
        assert "GROUP BY chain_id,order_uid,creation_date,valid_to" in oo
        assert "HAVING st='open'" in oo
        assert "LIMIT 100" in oo
        assert "argMax(valid_to" not in oo
        ev = specs["live_order_events"].sql
        assert "observed_at >= now() - INTERVAL 1 HOUR" in ev
        assert "GROUP BY chain_id,event_id" in ev
        assert "LIMIT 50" in ev


def test_depth_family_bounds_order_scans_post_backfill():
    """The depth group's four order-grain shapes must bound their dedup work
    to validity-filtered candidates instead of the whole backfilled table:
    open_intent_pairs and live pair_depth prefilter valid_to (immutable) in
    the raw scan and drop FINAL; the historical cand pushes its at-T bounds
    from HAVING into WHERE; the heatmap cand carries the window floor; and
    depth_horizon counts via HLL uniq, not a multi-million-uid uniqExact."""
    chain = cow_explorer.COW_CHAINS[100]
    live = {s.key: s for s in cow_explorer._pair_depth_specs(chain, ("0xaaa", "0xbbb"))}
    pd = live["pair_depth"].sql
    assert "orders AS o FINAL" not in pd and "orders FINAL" not in pd
    assert "valid_to>toUnixTimestamp(parseDateTime64BestEffort({server_as_of:String}))" in pd
    assert "HAVING st='open'" in pd
    op = live["open_intent_pairs"].sql
    assert "FINAL" not in op
    assert "valid_to>toUnixTimestamp(now())" in op
    assert "GROUP BY order_uid,valid_to" in op
    horizon = live["depth_horizon"].sql
    assert "uniqExact" not in horizon
    assert "uniq(order_uid)" in horizon
    hist = {
        s.key: s
        for s in cow_explorer._pair_depth_specs(chain, ("0xaaa", "0xbbb"), "2026-07-22T12:00:00Z")
    }["pair_depth"].sql
    assert "HAVING created<=" not in hist
    assert "creation_date<=parseDateTime64BestEffort({at_ts:String})" in hist
    assert "toDateTime(valid_to)>parseDateTime64BestEffort({at_ts:String})" in hist
    # Cancelled-without-cancel-time exclusion: unbounded terminal-existence
    # check (term_any has NO at_ts cap — an order cancelled after T must stay
    # in the book at T) gating only status_l='cancelled' candidates.
    assert "term_any" in hist
    assert "status_l!='cancelled'" in hist
    assert hist.count("event_timestamp<=parseDateTime64BestEffort({at_ts:String})") == 1
    heatmap = cow_explorer._pair_depth_heatmap_specs(chain, ("0xaaa", "0xbbb"), "7d")[0].sql
    assert "toDateTime(valid_to) > (SELECT w_start FROM win)" in heatmap


def test_orders_all_networks_skips_pair_and_quality_groups():
    multi = {
        s.key
        for s in cow_explorer._order_specs(
            "production", None, ("", ""), _range(30), {}
        )
    }
    assert "order_status_summary" in multi
    assert "order_type_summary" in multi
    assert "conditional_order_activity" in multi
    # Pair-scoped intents and the trades-join quality datasets are single-chain.
    assert not multi & {
        "known_orders", "known_intents", "intent_depth",
        "order_quality_summary", "fill_latency_distribution",
        "surplus_distribution", "surplus_by_class",
    }


def test_order_type_specs_dedup_orders_via_argmax_not_final():
    for chain in (None, cow_explorer.COW_CHAINS[1]):
        specs = cow_explorer._order_specs(
            "production", chain, ("", ""), _range(30), {}
        )
        for spec in specs:
            if spec.key in {
                "order_type_summary", "order_flavor_mix", "order_type_trend",
            }:
                assert "orders AS o FINAL" not in spec.sql
                assert "argMax(class,observed_at)" in spec.sql
                assert "GROUP BY chain_id,order_uid" in spec.sql
    # TWAP signal comes from the doubly-nested app-data JSON path (live-verified).
    appdata = {
        s.key: s
        for s in cow_explorer._order_specs(
            "production", None, ("", ""), _range(30), {}
        )
    }["appdata_order_classes"]
    assert "JSONExtractString(JSONExtractString(argMax(full_app_data,observed_at),'fullAppData')" in appdata.sql
    assert "'metadata','orderClass','orderClass'" in appdata.sql


def test_quote_delta_parses_python_repr_policy_defensively():
    """protocol_fees.policy is Python-repr (single quotes), so the embedded
    priceImprovement quote is read via a quote swap + JSON_VALUE, never raw
    JSONExtract on the original string."""
    specs = {
        s.key: s
        for s in cow_explorer._patterns_specs(
            "production", cow_explorer.COW_CHAINS[100], _range(30)
        )
    }
    sql = specs["quote_delta_quality"].sql
    assert "replaceAll(q.policy,'\\'','\"')" in sql
    assert "priceImprovement.quote.sellAmount" in sql
    assert "toFloat64OrNull(JSON_VALUE" in sql
    assert "'unquoted'" in sql


def test_protocol_kpis_volume_overlay_only_for_short_windows():
    """Counts are always present; the approximate native-volume overlay exists
    ONLY for relative windows <= 7 days (current-snapshot valuation — there is
    no historical price source), NULL otherwise."""
    short = {
        s.key: s for s in cow_explorer._overview_specs("production", _range(7))
    }
    assert "cow_db.native_prices" in short["protocol_kpis"].sql
    assert "approx_native_volume" in short["protocol_kpis"].sql
    for days in (30, 0):
        wide = {
            s.key: s
            for s in cow_explorer._overview_specs("production", _range(days))
        }
        sql = wide["protocol_kpis"].sql
        assert "cow_db.native_prices" not in sql
        assert "NULL AS Nullable(Float64)) AS approx_native_volume" in sql
    # All-time totals keep BNB (no block_timestamp filter on the counts).
    alltime = short["alltime_chain_totals"].sql
    assert "block_timestamp IS NOT NULL" not in alltime
    # Share trend coarsens to weeks beyond 180d / at all-history.
    assert "toStartOfDay" in short["chain_share_trend"].sql
    assert "toStartOfWeek" in {
        s.key: s for s in cow_explorer._overview_specs("production", _range(0))
    }["chain_share_trend"].sql


def test_pair_depth_live_and_historical_sql_shapes():
    chain = cow_explorer.COW_CHAINS[56]
    live = {
        s.key: s
        for s in cow_explorer._pair_depth_specs(chain, (TOKEN_A, TOKEN_B))
    }
    assert set(live) == {"depth_horizon", "pair_depth", "open_intent_pairs"}
    # The open-pairs rescue list is chain-scoped and returned even pairless
    # (an empty Gnosis book must still steer users toward pairs with intents).
    pairless = {
        s.key for s in cow_explorer._pair_depth_specs(chain, ("", ""))
    }
    assert pairless == {"depth_horizon", "open_intent_pairs"}
    pairs_sql = live["open_intent_pairs"].sql
    assert "status='open'" in pairs_sql
    assert "orders AS o FINAL" not in pairs_sql
    assert "token0_symbol" in pairs_sql
    sql = live["pair_depth"].sql
    # Post-backfill shape: argMax dedup over the pair's live-validity orders
    # replaced the whole-chain FINAL merge; mutable status filters via HAVING.
    assert "orders AS o FINAL" not in sql and "orders FINAL" not in sql
    assert "HAVING st='open'" in sql
    assert "server_as_of" in live["pair_depth"].parameters
    assert live["pair_depth"].parameters["server_as_of"].endswith(":00Z")
    assert live["pair_depth"].cache_ttl_seconds == 60
    # Orientation invariants: price is quote-per-base for BOTH sides; both
    # denominations ship so Flip is a client-side re-projection.
    for column in ("side", "price", "amount_base", "amount_quote",
                   "sell_symbol", "buy_symbol", "order_uid", "owner"):
        assert column in sql
    hist = {
        s.key: s
        for s in cow_explorer._pair_depth_specs(
            chain, (TOKEN_A, TOKEN_B), "2026-07-21T12:00:00Z"
        )
    }
    hsql = hist["pair_depth"].sql
    assert hist["pair_depth"].cache_ttl_seconds == 3600  # point-in-time immutable
    assert hist["pair_depth"].coverage_mode == "reconstructed_point_in_time"
    assert hist["pair_depth"].parameters["at_ts"] == "2026-07-21T12:00:00+00:00Z".replace("+00:00", "")
    # Bounded joins pruned by the tiny candidate set (fills, term, term_any);
    # no FINAL on trades.
    assert hsql.count("order_uid IN (SELECT order_uid FROM cand)") == 3
    assert "trades AS t FINAL" not in hsql
    assert "block_timestamp<=parseDateTime64BestEffort({at_ts:String})" in hsql
    assert "event_timestamp<=parseDateTime64BestEffort({at_ts:String})" in hsql
    # cand must not self-alias argMax to filtered column names (code 184).
    assert "argMax(sell_token,observed_at) AS sell_token" not in hsql
    assert len(hsql) <= 9_900
    # depth_at validation: future and garbage rejected.
    with pytest.raises(ValueError):
        cow_explorer._validate_depth_at("not-a-date")
    with pytest.raises(ValueError):
        cow_explorer._validate_depth_at("2999-01-01T00:00:00Z")


def test_depth_at_group_reload_patches_state_and_resets_on_section_apply():
    server, ch = _server()
    opened = _tool(server, "open_cow_explorer")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=1, section="markets", chain_id=100,
    ).structuredContent
    assert applied["view_state"]["depth_at"] == ""
    scope_id = applied["view_state"]["scope_id"]
    patched = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=2, section="markets", group="depth",
        scope_id=scope_id, depth_at="2026-07-21T12:00:00Z",
    ).structuredContent
    assert patched["patch"]["depth_at"] == "2026-07-21T12:00:00+00:00".replace("+00:00", "Z")
    # "" keeps the current timestamp on retries; "live" clears it.
    kept = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=3, section="markets", group="depth",
        scope_id=scope_id,
    ).structuredContent
    assert kept["patch"]["depth_at"].endswith("Z") and kept["patch"]["depth_at"] != ""
    cleared = _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=4, section="markets", group="depth",
        scope_id=scope_id, depth_at="live",
    ).structuredContent
    assert cleared["patch"]["depth_at"] == ""
    # A section apply always returns the panel to the live book (the reset is
    # part of every full section-apply state build).
    _tool(server, "load_cow_explorer_datasets")(
        view_id=view_id, request_id=5, section="markets", group="depth",
        scope_id=scope_id, depth_at="2026-07-21T12:00:00Z",
    )
    reapplied = _tool(server, "load_cow_explorer_section")(
        view_id=view_id, request_id=6, section="trades", chain_id=100,
    ).structuredContent
    assert reapplied["view_state"]["depth_at"] == ""
