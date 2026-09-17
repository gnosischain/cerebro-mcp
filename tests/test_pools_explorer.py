"""Contract tests for the read-only Pool Liquidity Explorer miniapp.

Two kinds of test live here. The flow tests pin the deferred-load state machine
(zero-query open, core-then-stream, scope guards, retention). The SQL-shape
tests pin the properties that a stub ClickHouse structurally cannot catch by
executing anything: which database is read, where FINAL belongs on this plane,
that every view scan is pruned, and that nothing is ever emitted as a
fabricated zero. The live counterpart is tests/test_pools_explorer_live_smoke.py.
"""

from __future__ import annotations

import re

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import INTERACTIVE_QUERY_BUDGET, ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.security import RiskClass, TOOL_RISK_REGISTRY
from cerebro_mcp.tools.tool_meta import TOOL_META
from cerebro_mcp.tools.visualization import mini_apps, pools_explorer as px, web_apps
from tests.sql_text import sql_code


POOL = "0x" + "ab" * 20
TOKEN = "0x" + "cd" * 20
SENTINEL_TEXT = "zzsentinelzz"
TOOLS = (
    "load_pools_token_metadata",
    "open_pools_explorer", "load_pools_explorer_section",
    "load_pools_explorer_datasets", "search_pools_explorer",
    "load_pools_explorer_entity",
)
APP_ONLY_TOOLS = frozenset(TOOLS) - {"open_pools_explorer"}
SEARCH_COLUMNS = [
    "entity_type", "identifier", "label", "role", "evidence_count", "match_rank",
]


class StubCH:
    """Records ``(sql, database, max_rows, parameters, query_budget)`` per call
    and echoes ``__source_rows`` for the exact-capped loader. Freshness-shaped
    queries return the two-source clock rows so ``_freshness_state`` parses."""

    def __init__(self, *, total: int = 2, fail_marker: str = "", rows_by_marker=None):
        self.total = total
        self.fail_marker = fail_marker
        self.rows_by_marker = rows_by_marker or {}
        self.calls: list[tuple] = []

    def run_query(
        self, sql, database="dbt", requested_max_rows=100, audience="tool",
        fetch_mode="auto", parameters=None, query_budget=None,
    ):
        self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
        if self.fail_marker and self.fail_marker in sql:
            raise RuntimeError("planned dataset failure")
        exact_capped = "__source_rows" in sql
        for marker, (columns, rows) in self.rows_by_marker.items():
            if marker in sql:
                if exact_capped:
                    return self._result(sql, database, [*columns, "__source_rows"],
                                        [[*row, len(rows)] for row in rows])
                return self._result(sql, database, list(columns), [list(r) for r in rows])
        if "AS latest_snapshot_date" in sql:
            columns = ["source", "latest_snapshot_date", "latest_anchor_block",
                       "pools_published", "latest_published_at"]
            rows = [["cl_state", "2026-09-16", 48287703, 2519, "2026-09-17T00:00:00Z"],
                    ["reserves", "2026-09-16", 48287703, 4020, "2026-09-17T00:00:00Z"]]
            if exact_capped:
                columns = [*columns, "__source_rows"]
                rows = [[*row, 2] for row in rows]
            return self._result(sql, database, columns, rows)
        n = min(self.total, requested_max_rows)
        columns = ["id", "as_of"]
        rows = [[index, "2026-09-16"] for index in range(n)]
        if exact_capped:
            columns = [*columns, "__source_rows"]
            rows = [[*row, self.total] for row in rows]
        return self._result(sql, database, columns, rows)

    @staticmethod
    def _result(sql, database, columns, rows):
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database, columns=columns,
            rows=rows, row_count=len(rows), elapsed_seconds=0.001,
            fetch_mode="rows", warnings=[],
        )


class SearchCH(StubCH):
    """Returns one candidate for any search arm."""

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool",
                  fetch_mode="auto", parameters=None, query_budget=None):
        if "AS match_rank" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["pool", POOL, "uniswap_v3 0xabab…abab", "cl", 2, 0]])
        return StubCH.run_query(self, sql, database, requested_max_rows, audience,
                                fetch_mode, parameters, query_budget)


def _detail_ch(*, family="cl", probed=1, price_adjusted=None):
    """A stub whose ``pool_detail`` row carries the coverage facts the entity
    warnings are derived from."""
    columns = ["pool_address", "entity_label", "pool_family", "ticks_probed",
               "price_adjusted", "as_of"]
    rows = [[POOL, f"{family} 0xabab…abab", family, probed, price_adjusted,
             "2026-09-16"]]
    return StubCH(rows_by_marker={"AS entity_label": (columns, rows)})


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    px.reset_failure_cache_for_tests()
    mini_apps.reset_views_for_tests()
    web_apps.WEB_APP_CONFIGS.pop(px.POOLS_APP_ID, None)
    for name in TOOLS:
        web_apps.MINI_APP_TOOL_REGISTRY.pop(name, None)
    yield
    reset_cache_for_tests()
    px.reset_failure_cache_for_tests()
    mini_apps.reset_views_for_tests()


def _server(ch=None):
    server = FastMCP("pools-test")
    ch = ch or StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    px.register_pools_explorer_tools(server, ch)
    return server, ch


def _tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def _all_group_keys() -> set[str]:
    return {
        key
        for groups in px.SECTION_GROUPS.values()
        for keys in groups.values()
        for key in keys
    }


def _all_specs() -> list[px.QuerySpec]:
    """Every spec builder with all applicable filters set to bindable sentinel
    values, plus both as-of branches (the unbounded one constant-folds
    differently, which is where the date-typing bug lived)."""
    defaults = px._default_filters()
    specs: list[px.QuerySpec] = []
    for as_of, window in (("", "1y"), ("2026-03-01", "90d"), ("", "all")):
        specs += px._overview_specs(as_of, window)
        specs += px._pools_specs(as_of, {
            **defaults, "query": "0xab", "pool_class": "uniswap_v3",
            "pool_family": "cl", "fee_band": "b3000", "token": TOKEN,
            "live_only": True, "probed_only": True, "sort_by": "tick_count_desc",
        })
        specs += px._pools_specs(as_of, {**defaults, "fee": 3000})
        specs += px._tokens_specs(as_of, {
            **defaults, "query": SENTINEL_TEXT, "sort_by": "symbol_asc",
        })
        specs += px._coverage_specs(as_of, window)
        specs += px._pool_entity_specs(POOL, as_of, window, "1y", True)
        specs += px._pool_entity_specs(POOL, as_of, window, "all", False)
        specs += px._token_entity_specs(TOKEN, as_of)
    return specs


def _search_sql() -> list[str]:
    cfg = px._cfg_cte()
    return [
        sql_loader_sql
        for sql_loader_sql in (
            px.sql_loader.load_sql("pools", "search_address", cfg_cte=cfg),
            px.sql_loader.load_sql("pools", "search_prefix", cfg_cte=cfg),
            px.sql_loader.load_sql(
                "pools", "search_text", cfg_cte=cfg, db=px.POOLS_DB,
                meta_view=px.METADATA_VIEW, chain=px.CHAIN_ID,
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Launch / flow
# ---------------------------------------------------------------------------


def test_launcher_opens_with_zero_clickhouse_round_trips():
    server, ch = _server()
    payload = _tool(server, "open_pools_explorer")().structuredContent
    assert payload["type"] == "INITIAL_LOAD"
    assert payload["app_id"] == "pools_explorer"
    assert payload["view_state"]["section"] == "overview"
    assert payload["view_state"]["as_of"] == ""
    assert payload["view_state"]["window"] == px.DEFAULT_WINDOW
    assert ch.calls == []
    assert payload["datasets"] == {}


def test_open_with_entity_loads_core_only_and_marks_deferred_groups():
    server, ch = _server(_detail_ch())
    payload = _tool(server, "open_pools_explorer")(
        entity_type="pool", identifier=POOL
    ).structuredContent
    state = payload["view_state"]
    assert state["section"] == "pool"
    assert state["selected_entity"]["identifier"] == POOL
    assert state["loaded_groups"]["pool.core"] is True
    for group in ("profile", "history", "fees", "heatmap"):
        assert state["loaded_groups"][f"pool.{group}"] is False
    assert set(payload["datasets"]) <= set(px.SECTION_GROUPS["pool"]["core"]) | {
        "source_freshness"
    }


def test_open_with_query_autoloads_a_single_candidate():
    server, _ = _server(SearchCH(rows_by_marker=_detail_ch().rows_by_marker))
    payload = _tool(server, "open_pools_explorer")(query=POOL).structuredContent
    assert payload["view_state"]["selected_entity"]["identifier"] == POOL


def test_section_apply_loads_core_and_the_group_tool_streams_the_rest():
    server, ch = _server()
    open_tool = _tool(server, "open_pools_explorer")
    view_id = open_tool().structuredContent["view_id"]
    payload = _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    state = payload["view_state"]
    assert state["loaded_groups"]["overview.core"] is True
    assert state["loaded_groups"]["overview.trend"] is False
    assert set(payload["datasets"]) == set(px.SECTION_GROUPS["overview"]["core"])

    patch = _tool(server, "load_pools_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="trend",
        scope_id=state["scope_id"],
    ).structuredContent
    assert patch["type"] == "PATCH_VIEW_STATE"
    assert patch["patch"]["loaded_groups"]["overview.trend"] is True
    # source_freshness rides along on every non-short-circuit load (300s TTL):
    # the freshness strip must never go stale behind a loaded panel.
    assert set(patch["datasets"]) == {"live_pool_trend", "source_freshness"}


def test_group_load_with_a_stale_scope_id_is_a_noop():
    server, ch = _server()
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="overview"
    )
    before = len(ch.calls)
    patch = _tool(server, "load_pools_explorer_datasets")(
        view_id=view_id, request_id=0, section="overview", group="trend",
        scope_id="overview:999",
    ).structuredContent
    assert patch["warnings"] == ["stale_scope"]
    assert patch["patch"] == {}
    assert len(ch.calls) == before


def test_a_stale_request_id_is_ignored():
    server, ch = _server()
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=5, section="overview"
    )
    before = len(ch.calls)
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=2, section="pools"
    )
    assert len(ch.calls) == before


def test_a_tab_return_with_an_unchanged_scope_costs_zero_queries():
    server, ch = _server()
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    section = _tool(server, "load_pools_explorer_section")
    section(view_id=view_id, request_id=1, section="overview")
    section(view_id=view_id, request_id=2, section="pools")
    before = len(ch.calls)
    result = section(view_id=view_id, request_id=3, section="overview")
    assert len(ch.calls) == before
    assert result.structuredContent["view_state"]["section"] == "overview"


def test_the_entity_sections_are_rejected_by_the_section_tool():
    """They are drill-downs: routing them through the section tool would build
    a spec set with no identifier to bind."""
    server, _ = _server()
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    for section in ("pool", "token"):
        result = _tool(server, "load_pools_explorer_section")(
            view_id=view_id, request_id=1, section=section
        )
        assert result.isError
        assert "load_pools_explorer_entity" in result.content[0].text


def test_a_reserves_only_pool_omits_every_concentrated_liquidity_dataset():
    """A Balancer pool has no ticks, no slot0 price and no fee accumulators.
    The datasets are not BUILT for it rather than built and returning nothing:
    an empty profile chart reads as 'no liquidity', which would be false."""
    cl = {s.key for s in px._pool_entity_specs(POOL, "", "1y", "1y", True)}
    reserves = {s.key for s in px._pool_entity_specs(POOL, "", "1y", "1y", False)}
    assert px.CL_ONLY_KEYS <= cl
    assert not (px.CL_ONLY_KEYS & reserves)
    assert reserves == cl - px.CL_ONLY_KEYS


def test_the_group_loader_follows_the_pool_family_from_the_loaded_detail():
    server, _ = _server(_detail_ch(family="reserves_only", probed=0))
    payload = _tool(server, "open_pools_explorer")(
        entity_type="pool", identifier=POOL
    ).structuredContent
    view_id = payload["view_id"]
    patch = _tool(server, "load_pools_explorer_datasets")(
        view_id=view_id, request_id=0, section="pool", group="profile",
        scope_id=payload["view_state"]["scope_id"],
    ).structuredContent
    # The group resolves to no CL specs, so it completes with nothing attached
    # rather than erroring or fabricating an empty profile.
    assert patch["patch"]["loaded_groups"]["pool.profile"] is True
    assert set(patch["datasets"]) <= {"source_freshness"}


def test_entity_warnings_name_the_coverage_state_of_the_pool():
    for family, probed, adjusted, expected in (
        ("reserves_only", 0, None, "reserves_only_pool"),
        ("cl", 0, None, "pool_below_active_threshold"),
        ("cl", 1, None, "metadata_unresolved"),
    ):
        reset_cache_for_tests()
        px.reset_failure_cache_for_tests()
        mini_apps.reset_views_for_tests()
        server, _ = _server(_detail_ch(family=family, probed=probed,
                                       price_adjusted=adjusted))
        payload = _tool(server, "open_pools_explorer")(
            entity_type="pool", identifier=POOL
        ).structuredContent
        assert expected in payload["view_state"]["warnings"], (family, probed)


def test_a_profile_date_change_is_one_additive_group_call():
    """The date picker must not reload the entity — it re-dates the profile
    group in place and echoes the resolved date back."""
    server, _ = _server(_detail_ch())
    payload = _tool(server, "open_pools_explorer")(
        entity_type="pool", identifier=POOL
    ).structuredContent
    patch = _tool(server, "load_pools_explorer_datasets")(
        view_id=payload["view_id"], request_id=0, section="pool", group="profile",
        scope_id=payload["view_state"]["scope_id"], as_of="2026-03-01",
    ).structuredContent
    assert patch["type"] == "PATCH_VIEW_STATE"
    assert patch["patch"]["as_of"] == "2026-03-01"


def test_a_failed_dataset_stays_visible_as_a_stub():
    """The panel must render an error card, never disappear: a missing panel
    reads as 'there is no data', which converts a load failure into an
    apparent finding."""
    server, _ = _server(StubCH(fail_marker="AS pools_configured"))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    payload = _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    state = payload["view_state"]
    assert state["loaded_groups"]["overview.core"] == "partial"
    coverage = state["coverage"]["pools_summary"]
    assert coverage["warning_codes"] == ["query_failed"]
    assert coverage["error"]
    assert "pools_summary" in payload["datasets"]
    assert payload["datasets"]["pools_summary"]["stats"]["rows_returned"] == 0


def test_stale_sources_raise_a_warning_rather_than_reading_as_current():
    columns = ["source", "latest_snapshot_date", "latest_anchor_block",
               "pools_published", "latest_published_at"]
    rows = [["cl_state", "2020-01-01", 1, 1, "2020-01-01T00:00:00Z"],
            ["reserves", "2020-01-01", 1, 1, "2020-01-01T00:00:00Z"]]
    server, _ = _server(StubCH(rows_by_marker={"AS latest_snapshot_date": (columns, rows)}))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    state = _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent["view_state"]
    assert "source_stale" in state["warnings"]
    assert state["freshness"]["cl_state"]["stale"] is True


def test_as_of_parsing_accepts_the_three_shapes_the_driver_returns():
    """The same column arrives as an ISO string, a date, or a raw day number
    depending on whether ClickHouse folded the projection to a constant."""
    import datetime

    assert px._as_date("2026-09-16") == datetime.date(2026, 9, 16)
    assert px._as_date(datetime.date(2026, 9, 16)) == datetime.date(2026, 9, 16)
    assert px._as_date(20712) == datetime.date(2026, 9, 16)
    assert px._as_date(None) is None
    assert px._as_date("not a date") is None
    assert px._as_date(True) is None


# ---------------------------------------------------------------------------
# Contract: groups and keys
# ---------------------------------------------------------------------------


def test_section_groups_cover_every_dataset_key_exactly_once():
    seen: list[str] = []
    for groups in px.SECTION_GROUPS.values():
        for keys in groups.values():
            seen.extend(keys)
    assert len(seen) == len(set(seen)), "dataset keys must be globally unique"
    assert all("core" in groups for groups in px.SECTION_GROUPS.values())


def test_every_built_spec_key_belongs_to_its_section_group():
    declared = _all_group_keys() | {"source_freshness"}
    for spec in _all_specs():
        assert spec.key in declared, spec.key


def test_every_declared_key_is_actually_built():
    built = {spec.key for spec in _all_specs()}
    missing = sorted(_all_group_keys() - built)
    assert missing == [], f"declared but never built: {missing}"


# ---------------------------------------------------------------------------
# Contract: SQL shape
# ---------------------------------------------------------------------------


def test_every_spec_reads_only_the_state_indexer():
    """The product rule, pinned. Mixing a dbt model in would make it impossible
    to say which numbers are chain-verified at a pinned block and which are
    modelled — and dbt carries the USD prices this plane deliberately lacks."""
    for spec in [*_all_specs()]:
        body = sql_code(spec.sql)
        assert "rpc_state_indexer." in body, spec.key
        for foreign in ("dbt.", "cow_db.", "governance_db.", "rpc_log_indexer.",
                        "scratch."):
            assert foreign not in body, f"{spec.key} reads {foreign}"


def test_every_spec_has_a_deterministic_order_by():
    for spec in _all_specs():
        assert "ORDER BY" in sql_code(spec.sql).upper(), spec.key


def test_no_rendered_spec_approaches_the_query_length_cap():
    from cerebro_mcp.config import settings

    over = [
        f"{spec.key}: {len(spec.sql)}"
        for spec in _all_specs()
        if len(spec.sql) > settings.MAX_QUERY_LENGTH - 100
    ]
    assert over == [], over


def test_user_values_are_bound_never_interpolated():
    for spec in _all_specs():
        assert POOL not in spec.sql, spec.key
        assert TOKEN not in spec.sql, spec.key
        assert SENTINEL_TEXT not in spec.sql, spec.key
        assert "2026-03-01" not in spec.sql, spec.key


def test_final_belongs_to_exactly_one_relation_on_this_plane():
    """FINAL is mandatory on config_registry (a ReplacingMergeTree the indexer
    re-registers), forbidden on the v_* views (they resolve dedup internally),
    and meaningless on census_publications (plain MergeTree). The raw pool_*
    tables are never read at all: their sort key includes attempt_id, so even
    FINAL leaves one row per retry."""
    view_final = re.compile(r"\bv_[a-z_]+\s+(?:AS\s+\w+\s+)?FINAL\b")
    raw_table = re.compile(r"rpc_state_indexer\.(pool_cl_state|pool_tick_liquidity|"
                           r"pool_token_balances|token_balances|token_scalars)\b")
    for spec in [*_all_specs(), *_search_sql()]:
        body = sql_code(spec.sql if isinstance(spec, px.QuerySpec) else spec)
        key = spec.key if isinstance(spec, px.QuerySpec) else "search"
        for match in re.finditer(r"config_registry\s+AS\s+(\w+)\s+(\w+)", body):
            assert match.group(2) == "FINAL", f"{key}: config_registry without FINAL"
        assert not view_final.search(body), f"{key}: FINAL on a canonical view"
        assert not raw_table.search(body), f"{key}: reads a raw attempt-keyed table"
        assert not re.search(r"census_publications\s+(?:AS\s+\w+\s+)?FINAL", body), key


def test_dates_resolve_from_publications_and_every_view_scan_is_pruned():
    """A JOIN on resolved dates never prunes a ClickHouse scan; an uncorrelated
    IN folds to a constant set and does. Lesson: fat-view-join-never-prunes."""
    #: live_pool_trend at window="all" deliberately scans the whole state view —
    #: that is what "every published day" means, and it is why the dataset sits
    #: alone in its group with exact_count=False and a 3600s TTL. Measured
    #: 2026-09-17 against live ClickHouse: 4.96s, inside the 20s interactive
    #: budget. Nothing else on this plane is allowed an unbounded view scan.
    ALL_HISTORY_SCANS = {"live_pool_trend"}
    scan = re.compile(r"(?:FROM|JOIN)\s+rpc_state_indexer\.(v_pool_\w+)\s+AS\s+(\w+)")
    for spec in _all_specs():
        body = sql_code(spec.sql)
        if not re.search(r"rpc_state_indexer\.v_pool_", body):
            continue
        assert "census_publications" in body, f"{spec.key}: no publications anchor"
        assert not re.search(r"max\(\s*snapshot_date\s*\)\s*(?:AS \w+\s*)?FROM\s+"
                             r"rpc_state_indexer\.v_", body), spec.key
        if spec.key in ALL_HISTORY_SCANS:
            continue
        # Alias-aware, per scan site. An earlier version only asked whether a
        # prune appeared ANYWHERE in the query, which passed when the prune was
        # dropped off one of two scans — and counting prune strings instead
        # overcounted, because the probe CTE carries the same predicate against
        # the publications table.
        for view, alias in scan.findall(body):
            bounds = (
                f"{alias}.snapshot_date IN (SELECT",
                f"{alias}.pool_address = {{pool:String}}",
                f"{alias}.snapshot_date >= (SELECT as_of FROM asof)",
            )
            assert any(b in body for b in bounds), (
                f"{spec.key}: unbounded scan of {view} AS {alias}"
            )


def test_every_view_scan_pins_its_job_and_chain():
    for spec in _all_specs():
        body = sql_code(spec.sql)
        if "v_pool_cl_state_published" in body or "v_pool_tick_liquidity" in body:
            assert f"job_name = '{px.CL_JOB}'" in body, spec.key
        if "v_pool_token_balances_published" in body:
            assert f"job_name = '{px.RESERVES_JOB}'" in body, spec.key
        assert f"chain_id = {px.CHAIN_ID}" in body, spec.key


def test_a_cte_is_referenced_once_unless_it_is_declared_cheap():
    """ClickHouse inlines a CTE per reference, so an N-referenced CTE scans N
    times. The allowance holds the CTEs that are a handful of rows by
    construction — the as-of resolvers, the one-row metadata arrays, the
    sampled date list — where N cheap scans is the right trade."""
    # Cheap by construction — row counts measured against live ClickHouse on
    # 2026-09-17. N references here is N cheap scans, which is the right trade
    # against restructuring the query around a single read.
    allowed = {
        "asof", "rasof",                      # one row each
        "mm",                                 # one row of per-chain token arrays
        "grid", "axis", "latest",             # one row each
        "cfg_counts", "token_stats", "pool_stats", "totals", "per_band",
        "hist", "probed_from",                # one aggregate row each
        "cfg",                                # 4,022 rows from a 15,348-row registry
        "st", "probe", "res",                 # one pruned snapshot: 2.5k/2.5k/8.2k
        "days", "days_all", "stday",          # <= 1,088 rows for ONE pool
        "daily",                              # per-day publication counts, ~0.1s
    }
    offenders = []
    for spec in _all_specs():
        body = sql_code(spec.sql)
        for name in re.findall(r"(?m)^\s*(\w+) AS \(", body):
            if name in allowed:
                continue
            uses = len(re.findall(rf"(?:FROM|JOIN)\s+{name}\b", body))
            if uses > 1:
                offenders.append(f"{spec.key}: {name} x{uses}")
    assert offenders == [], offenders


def test_nothing_is_emitted_as_a_fabricated_zero():
    """Decimals this plane never observed produce NULL, not a plausible number;
    a fee estimate with nothing to measure produces NULL, not zero fees."""
    adjusted = px._price_adjusted("price_raw", "d0", "d1")
    assert "IS NULL OR" in adjusted and ", NULL," in adjusted
    fees = next(s for s in px._pool_entity_specs(POOL, "", "1y", "1y", True)
                if s.key == "pool_fee_growth")
    body = sql_code(fees.sql)
    assert "rn = 1 OR fg0 < prev0 OR liquidity = 0, NULL" in body
    assert "rn = 1 OR fg1 < prev1 OR liquidity = 0, NULL" in body
    directory = px._pools_specs("", px._default_filters())[0]
    assert "if(has_state, toNullable(st.current_tick), NULL)" in sql_code(directory.sql)


def test_every_date_column_is_emitted_as_a_string():
    """With an unbounded as-of predicate ClickHouse folds the aggregate to a
    constant and the driver returns the raw day number, so the same column
    arrives as '2026-09-16' on one path and 20712 on another. toString() is the
    only cast that survives the fold, so it is applied to every date column and
    the wire contract is uniform."""
    date_columns = {
        "as_of", "reserves_as_of", "snapshot_date", "prev_snapshot_date",
        "bucket_date", "bucket", "first_published", "last_published",
        "profile_available_from", "latest_snapshot_date", "first_snapshot_date",
        "last_snapshot_date",
    }
    offenders = []
    for spec in _all_specs():
        body = sql_code(spec.sql)
        # Only the final projection is the wire contract; a CTE keeps the real
        # Date because it still has to do arithmetic with it.
        starts = [m.start() for m in re.finditer(r"(?m)^SELECT\b", body)]
        assert starts, spec.key
        projection = body[starts[-1]:]
        for match in re.finditer(r"\bAS\s+(\w+)\b", projection):
            name = match.group(1)
            if name not in date_columns:
                continue
            # The projection item can span lines and parentheses, so look back
            # over it rather than trying to split the list on commas.
            if "toString" not in projection[max(0, match.start() - 200):match.start()]:
                offenders.append(f"{spec.key}: {name}")
    assert offenders == [], offenders


def test_the_heatmap_is_bounded_below_the_row_cap():
    assert px.HEATMAP_MAX_DATES * px.HEATMAP_TICK_BUCKETS <= px.ROW_CAP
    spec = next(s for s in px._pool_entity_specs(POOL, "", "1y", "all", True)
                if s.key == "pool_profile_heatmap")
    assert spec.exact_count is False
    body = sql_code(spec.sql)
    assert str(px.HEATMAP_MAX_DATES) in body
    assert str(px.HEATMAP_TICK_BUCKETS) in body
    assert str(px.HEATMAP_AXIS_PAD_TICKS) in body


def test_history_datasets_skip_the_exact_count_envelope():
    """``count() OVER ()`` forces the whole result to materialize before LIMIT.
    The datasets that scan a window rather than one snapshot are the ones that
    cannot afford it, and their row counts are bounded by construction anyway."""
    windowed = {"live_pool_trend", "pool_state_history", "pool_reserves_history",
                "pool_fee_growth", "pool_profile_heatmap"}
    for spec in _all_specs():
        if spec.key in windowed:
            assert spec.exact_count is False, spec.key


def test_specs_carry_the_interactive_query_budget_and_the_right_database():
    server, ch = _server()
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="overview"
    )
    assert ch.calls
    for _, database, _, _, budget in ch.calls:
        assert database == px.POOLS_DB
        assert budget is INTERACTIVE_QUERY_BUDGET


# ---------------------------------------------------------------------------
# Validation and search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"pool_class": "sushiswap"},
    {"pool_family": "amm"},
    {"fee_band": "b42"},
    {"fee": 3000, "fee_band": "b3000"},
    {"token": "not-an-address"},
    {"sort_by": "whatever"},
    {"query": "x" * (px.MAX_QUERY_LENGTH + 1)},
])
def test_bad_filters_are_rejected_before_any_sql(kwargs):
    with pytest.raises(ValueError):
        px._validate_filters("pools", **{
            "query": "", "pool_class": "", "pool_family": "", "fee_band": "",
            "fee": 0, "token": "", "live_only": False, "probed_only": False,
            "sort_by": "", **kwargs,
        })


def test_a_filter_that_cannot_apply_to_the_section_is_an_error():
    """Never a silent no-op: a user who filtered and got everything back would
    read the result as the filtered answer."""
    with pytest.raises(ValueError, match="only to the pools section"):
        px._validate_filters(
            "overview", "", "uniswap_v3", "", "", 0, "", False, False, "",
        )
    with pytest.raises(ValueError, match="pools and tokens"):
        px._validate_filters(
            "coverage", "gno", "", "", "", 0, "", False, False, "",
        )


@pytest.mark.parametrize("value", ["2026-13-01", "yesterday", "1999-01-01",
                                   "2999-01-01"])
def test_bad_as_of_values_are_rejected(value):
    with pytest.raises(ValueError):
        px._validate_as_of(value)


def test_as_of_and_window_defaults():
    assert px._validate_as_of("") == ""
    assert px._validate_as_of("2026-03-01") == "2026-03-01"
    assert px._validate_window("") == px.DEFAULT_WINDOW
    with pytest.raises(ValueError):
        px._validate_window("7d")
    with pytest.raises(ValueError):
        px._validate_window("90d", allowed=("1y",))


@pytest.mark.parametrize("identifier", ["0xnothex", "abc", "0x" + "ab" * 19])
def test_bad_entity_identifiers_are_rejected(identifier):
    with pytest.raises(ValueError):
        px._validate_entity_identifier("pool", identifier)


def test_search_routes_to_the_arm_that_matches_the_query_shape():
    ch = SearchCH()
    for query, marker in (
        (POOL, "cfg.pool_address = {q:String}"),
        ("0xabab", "startsWith(cfg.pool_address, {q:String})"),
        ("sDAI", "positionCaseInsensitive"),
    ):
        ch.calls.clear()
        candidates = px._search_candidates(ch, query)
        assert len(candidates) == 1
        assert marker in ch.calls[-1][0], query
        assert ch.calls[-1][1] == px.POOLS_DB


def test_search_never_scans_a_pool_view():
    """A search must cost one small scan however much history the plane holds."""
    for sql in _search_sql():
        body = sql_code(sql)
        assert "v_pool_" not in body
        assert body.lstrip().startswith("WITH ")


def test_an_empty_or_oversized_search_is_rejected():
    ch = SearchCH()
    assert px._search_candidates(ch, "   ") == []
    with pytest.raises(ValueError):
        px._search_candidates(ch, "x" * (px.MAX_QUERY_LENGTH + 1))


def test_the_entity_label_is_composed_from_the_class_and_address():
    """Breadcrumbs render their label raw and token symbols on this chain are
    attacker-authored, so the label is built server-side from facts that cannot
    be spoofed."""
    detail = next(s for s in px._pool_entity_specs(POOL, "", "1y", "1y", True)
                  if s.key == "pool_detail")
    body = sql_code(detail.sql)
    assert "concat(cfg.pool_class" in body
    assert "AS entity_label" in body
    assert "symbol" not in body.split("AS entity_label")[0].split("concat(cfg.pool_class")[1]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_visibility_web_registry_and_security_metadata():
    server, _ = _server()
    names = {t.name for t in server._tool_manager._tools.values()}
    assert TOOLS[0] in names
    hidden = mini_apps.get_app_only_tool_names()
    for name in APP_ONLY_TOOLS:
        assert name in hidden, name
    assert px.POOLS_APP_ID in web_apps.WEB_APP_CONFIGS
    config = web_apps.WEB_APP_CONFIGS[px.POOLS_APP_ID]
    assert config.open_tool == "open_pools_explorer"
    # web_apps also grants every app the shared paging tools.
    assert set(config.allowed_tools) >= set(TOOLS)
    assert RiskClass.READ_ONLY in TOOL_RISK_REGISTRY["open_pools_explorer"]
    for name in APP_ONLY_TOOLS:
        assert RiskClass.APP_ONLY in TOOL_RISK_REGISTRY[name], name
    assert TOOL_META["open_pools_explorer"]["domain"] == "visualization"


def test_the_app_resource_is_registered_under_its_uri():
    server, _ = _server()
    uris = {str(r.uri) for r in server._resource_manager._resources.values()}
    assert px.POOLS_URI in uris


def test_the_bundle_reports_its_build_hint_when_it_is_missing():
    """A capability that is merely unbuilt must say so rather than look broken."""
    diagnostics = px.get_pools_explorer_diagnostics()
    assert set(diagnostics) >= {"bundle_sha256", "bundle_mtime", "assets"}


def test_the_frontend_mirror_of_section_groups_cannot_drift():
    """Both sides pin their own frozen literal, which is the house pattern — but
    two literals nobody compares are two literals that drift. This is the one
    place they meet, so a key added on either side without the other fails here
    rather than as a panel that silently never loads."""
    import json
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / (
        "ui/src/mini-apps/pools-explorer/model/datasetGroups.ts"
    )
    if not source.exists():  # pragma: no cover - backend may land first
        pytest.skip("frontend mirror not present")
    text = source.read_text(encoding="utf-8")
    body = text[text.index("export const SECTION_GROUPS"):]
    body = body[body.index("{"):body.index("\n};") + 2]
    body = re.sub(r"//[^\n]*", "", body)              # comments
    body = re.sub(r"(\w+):", r'"\1":', body)           # bare keys -> JSON keys
    body = re.sub(r",(\s*[}\]])", r"\1", body)         # trailing commas
    mirror = json.loads(body)
    assert {s: {g: list(k) for g, k in groups.items()}
            for s, groups in px.SECTION_GROUPS.items()} == mirror


def test_an_unmeasured_aggregate_is_null_not_the_type_default():
    """A pool the indexer never probed has no publication rows to take a min
    over, and a bare min() returns the Date DEFAULT — which reached the UI as
    "profile since 1970-01-01" for a pool that has no profile at any date. The
    empty-set-aware aggregate is the fix, and it is the same NULL-never-zero
    rule the price and fee columns follow."""
    detail = next(s for s in px._pool_entity_specs(POOL, "", "1y", "1y", True)
                  if s.key == "pool_detail")
    body = sql_code(detail.sql)
    assert "minOrNull(p.snapshot_date) AS profile_available_from" in body
    assert "min(p.snapshot_date) AS profile_available_from" not in body


# ---------------------------------------------------------------------------
# Live token metadata overlay
# ---------------------------------------------------------------------------


def _overlay_ch(tokens):
    """A stub whose pool_directory rows carry token columns, including the
    array form a Balancer pool uses."""
    columns = ["pool_address", "token0", "token1", "assets", "as_of"]
    rows = [[POOL, tokens[0], tokens[1], list(tokens), "2026-09-16"]]
    return StubCH(rows_by_marker={"AS pool_address": (columns, rows)})


def test_the_token_column_pattern_matches_the_planes_columns_and_nothing_else():
    for name in ("token0", "token1", "token_address", "assets", "counter_tokens",
                 "reserve_tokens"):
        assert px.TOKEN_COLUMN_RE.fullmatch(name), name
    # A loose pattern would sweep pool addresses into a token lookup.
    for name in ("pool_address", "token0_symbol", "token0_decimals", "token_index",
                 "counter_labels", "token_decimals", "reserve_token_raw"):
        assert not px.TOKEN_COLUMN_RE.fullmatch(name), name


def test_the_overlay_resolves_the_visible_tokens_including_array_columns(monkeypatch):
    """A pool with more than two assets carries them as an Array(String), and
    reading that cell with str() yields the repr of a list — which matches no
    address and silently resolves nothing."""
    seen: list[set[str]] = []

    def fake_resolve(chain_id, addresses, *, force_refresh=False):
        seen.append(set(addresses))
        metas = {
            a: px.token_rpc.TokenMeta(a, "SYM", "Name", 18, 999, "string")
            for a in addresses
        }
        return metas, px.token_rpc.ResolveStats(len(metas), 0, len(metas), 0, False, 999)

    monkeypatch.setattr(px.token_rpc, "resolve_tokens", fake_resolve)
    tokens = ["0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20]
    server, _ = _server(_overlay_ch(tokens))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="pools"
    )
    patch = _tool(server, "load_pools_token_metadata")(
        view_id=view_id
    ).structuredContent["patch"]
    assert seen and seen[0] == set(tokens), "the third asset came from the array column"
    assert set(patch["token_overlay"]) == set(tokens)
    assert patch["token_overlay_stats"]["source"] == "rpc"


def test_every_overlay_entry_carries_its_provenance(monkeypatch):
    """An indexer value is verified at a pinned finalized block with a
    publication behind it; an RPC value is current chain state with neither. If
    the overlay did not say which it is, the app would quietly stop being able
    to tell the difference."""
    def fake_resolve(chain_id, addresses, *, force_refresh=False):
        metas = {a: px.token_rpc.TokenMeta(a, "SYM", None, 6, 4242, "string")
                 for a in addresses}
        return metas, px.token_rpc.ResolveStats(len(metas), 0, len(metas), 0, False, 4242)

    monkeypatch.setattr(px.token_rpc, "resolve_tokens", fake_resolve)
    tokens = ["0x" + "11" * 20, "0x" + "22" * 20]
    server, _ = _server(_overlay_ch(tokens))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="pools"
    )
    patch = _tool(server, "load_pools_token_metadata")(
        view_id=view_id
    ).structuredContent["patch"]
    for entry in patch["token_overlay"].values():
        assert entry["source"] == "rpc"
        assert entry["block_number"] == 4242


def test_the_overlay_never_touches_a_dataset_column(monkeypatch):
    """It is a separate map by design: writing an RPC value into a dataset
    column would erase the distinction the provenance exists to keep."""
    def fake_resolve(chain_id, addresses, *, force_refresh=False):
        metas = {a: px.token_rpc.TokenMeta(a, "OVERLAY", None, 18, 1, "string")
                 for a in addresses}
        return metas, px.token_rpc.ResolveStats(len(metas), 0, len(metas), 0, False, 1)

    monkeypatch.setattr(px.token_rpc, "resolve_tokens", fake_resolve)
    tokens = ["0x" + "11" * 20, "0x" + "22" * 20]
    server, ch = _server(_overlay_ch(tokens))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="pools"
    )
    before = mini_apps.get_view(view_id).datasets["pool_directory"].rows[0][:]
    _tool(server, "load_pools_token_metadata")(view_id=view_id)
    after = mini_apps.get_view(view_id).datasets["pool_directory"].rows[0]
    assert before == after


def test_the_overlay_is_scoped_to_the_section_being_looked_at(monkeypatch):
    """The view retains several sections, so a pool opened after the directory
    still holds the directory's rows. Feeding all of them to a capped sweep
    spends the budget on rows nobody is looking at and leaves the open pool's
    own tokens unresolved — which is exactly what it did live before this
    narrowed: the entity returned symbol=None for both of its tokens."""
    directory_tokens = ["0x" + f"{n:02x}" * 20 for n in range(0x10, 0x20)]
    entity_tokens = ["0x" + "e1" * 20, "0x" + "e2" * 20]
    # entity_label FIRST: markers are matched in order and pool_detail also
    # projects `AS pool_address`, so the looser marker would answer for both and
    # hand the entity the directory's rows.
    ch = StubCH(rows_by_marker={
        "AS entity_label": (
            ["pool_address", "entity_label", "pool_family", "ticks_probed",
             "price_adjusted", "token0", "token1", "assets", "as_of"],
            [[POOL, "uniswap_v3 0xabab", "cl", 1, None,
              entity_tokens[0], entity_tokens[1], entity_tokens, "2026-09-16"]],
        ),
        "AS pool_address": (
            ["pool_address", "token0", "token1", "assets", "as_of"],
            [[POOL, t0, t1, [t0, t1], "2026-09-16"]
             for t0, t1 in zip(directory_tokens[::2], directory_tokens[1::2])],
        ),
    })
    asked: list[set[str]] = []

    def fake_resolve(chain_id, addresses, *, force_refresh=False):
        asked.append(set(addresses))
        return {}, px.token_rpc.ResolveStats(len(addresses), 0, 0, 0, False, 1)

    monkeypatch.setattr(px.token_rpc, "resolve_tokens", fake_resolve)
    # A cap small enough that the directory alone would exhaust it.
    monkeypatch.setattr(px, "TOKEN_OVERLAY_CAP", 4)
    server, _ = _server(ch)
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="pools"
    )
    _tool(server, "load_pools_explorer_entity")(
        view_id=view_id, request_id=2, entity_type="pool", identifier=POOL
    )
    _tool(server, "load_pools_token_metadata")(view_id=view_id)
    assert asked, "the overlay asked for nothing"
    assert asked[-1] <= set(entity_tokens), (
        f"directory tokens crowded out the open pool's own: {asked[-1]}"
    )
    assert asked[-1], "the open pool's tokens were not resolved at all"


def test_a_dead_rpc_degrades_to_a_warning_not_a_failed_tool(monkeypatch):
    def fake_resolve(chain_id, addresses, *, force_refresh=False):
        return {}, px.token_rpc.ResolveStats(3, 0, 0, 0, False, 0, "no endpoint")

    monkeypatch.setattr(px.token_rpc, "resolve_tokens", fake_resolve)
    tokens = ["0x" + "11" * 20, "0x" + "22" * 20]
    server, _ = _server(_overlay_ch(tokens))
    view_id = _tool(server, "open_pools_explorer")().structuredContent["view_id"]
    _tool(server, "load_pools_explorer_section")(
        view_id=view_id, request_id=1, section="pools"
    )
    result = _tool(server, "load_pools_token_metadata")(view_id=view_id)
    assert not result.isError
    assert result.structuredContent["warnings"] == ["token_rpc_unavailable"]
    assert result.structuredContent["patch"]["token_overlay"] == {}


def test_the_overlay_tool_is_app_only_and_classified():
    server, _ = _server()
    assert "load_pools_token_metadata" in mini_apps.get_app_only_tool_names()
    assert RiskClass.APP_ONLY in TOOL_RISK_REGISTRY["load_pools_token_metadata"]
    config = web_apps.WEB_APP_CONFIGS[px.POOLS_APP_ID]
    assert "load_pools_token_metadata" in config.allowed_tools
