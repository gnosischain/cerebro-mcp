"""Timeline mode tests: temporal shapes, build_timeline_sql locked semantics,
the bucket axis, and the load_graph_timeline tool."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from unittest.mock import patch

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.semantic import graph_profiles
from cerebro_mcp.semantic.graph_extraction import GraphProfile
from cerebro_mcp.semantic.graph_profiles import (
    build_evidence_sql,
    build_neighbors_sql,
    build_sample_sql,
    build_timeline_sql,
)
from cerebro_mcp.semantic.flow_queries import (
    FLOWS_RELATION,
    build_timeline_bucket_coverage_sql,
    build_timeline_bucket_edges_sql,
    build_timeline_global_coverage_sql,
    build_timeline_universe_sql,
)
from cerebro_mcp.semantic.address_semantics import ZERO_ADDRESS
from cerebro_mcp.tools.semantic.graph_explorer.state import timeline_bucket_range
from cerebro_mcp.tools.visualization import mini_apps

# Shared fixtures/helpers (tests is a package).
from tests.test_graph_explorer import (  # noqa: F401  (fixtures by name)
    AVATAR,
    OWNER,
    SAFE,
    TRUSTEE,
    FakeSnapshot,
    StubCH,
    _call_tool,
    _graph_model,
    _server,
    reset_state,
)


def _profile(**kw: Any) -> GraphProfile:
    base = dict(
        profile="p",
        model_name="m",
        relation_name="db.m",
        source_column="src",
        target_column="tgt",
        source_kind="address",
        target_kind="address",
    )
    base.update(kw)
    return GraphProfile(**base)


# ---------------------------------------------------------------------------
# temporal_shape inference
# ---------------------------------------------------------------------------


def test_temporal_shape_inference_matrix():
    assert _profile().temporal_shape == "static"
    assert _profile(weight_column="w").temporal_shape == "static"  # no time
    assert _profile(time_column="t", weight_column="w").temporal_shape == "flow"
    assert _profile(time_column="t").temporal_shape == "state"
    assert (
        _profile(time_column="t", time_end_column="te").temporal_shape
        == "interval"
    )
    # Authored override wins over inference.
    assert (
        _profile(
            time_column="t", weight_column="w", temporal_semantics="state"
        ).temporal_shape
        == "state"
    )
    # Unknown override falls back to inference.
    assert (
        _profile(
            time_column="t", weight_column="w", temporal_semantics="bogus"
        ).temporal_shape
        == "flow"
    )


def test_legacy_current_relations_degrade_to_retrieval_snapshot_without_dbt_metadata():
    for profile_id in (
        "safe_ownership",
        "gpay_ownership",
        "lending_user_to_reserve",
        "address_labeled_as",
        "circles_trust",
    ):
        assert (
            _profile(profile=profile_id, time_column="observed_at").relationship_time
            == "current_snapshot"
        )
    assert (
        _profile(
            model_name="fct_execution_positions_latest",
            time_column="observed_at",
        ).relationship_time
        == "current_snapshot"
    )
    # New, explicit contracts always override legacy compatibility inference.
    assert (
        _profile(
            profile="gpay_ownership",
            time_column="event_time",
            temporal_semantics="event",
        ).relationship_time
        == "event"
    )


@pytest.mark.parametrize(
    ("semantics", "expected", "forbidden"),
    [
        (
            "event",
            ("t >= now() - INTERVAL {win:UInt32} DAY", "t < now()"),
            (),
        ),
        ("state_at", ("t <= now()",), ("t >= now() - INTERVAL",)),
        (
            "interval",
            (
                "t < now()",
                "(te IS NULL OR te >= now() - INTERVAL {win:UInt32} DAY)",
            ),
            (),
        ),
        (
            "current_snapshot",
            (),
            ("t >= now() - INTERVAL", "t < now()", "t <= now()"),
        ),
    ],
)
def test_relationship_time_predicate_is_shared_across_query_surfaces(
    semantics: str,
    expected: tuple[str, ...],
    forbidden: tuple[str, ...],
):
    profile = _profile(
        time_column="t",
        time_end_column="te" if semantics == "interval" else None,
        temporal_semantics=semantics,
    )
    queries = [
        build_neighbors_sql(profile, seed_ids=["0xa"])[0],
        build_sample_sql(profile)[0],
        build_evidence_sql(
            profile, source_id="0xa", target_id="0xb", window_days=90
        )[0],
    ]
    for sql in queries:
        for clause in expected:
            assert clause in sql
        for clause in forbidden:
            assert clause not in sql


def test_canonical_relationship_time_accepts_legacy_aliases():
    assert _profile(temporal_semantics="static").relationship_time == "current_snapshot"
    assert _profile(time_column="t", temporal_semantics="flow").relationship_time == "event"
    assert _profile(time_column="t", temporal_semantics="state").relationship_time == "state_at"


# ---------------------------------------------------------------------------
# build_timeline_sql — locked semantics
# ---------------------------------------------------------------------------

RANGE = dict(range_start="2025-01-06", range_end_exclusive="2026-01-05")


def test_flow_sql_buckets_and_half_open_range():
    sql, params = build_timeline_sql(
        _profile(time_column="t", weight_column="w"),
        node_ids=["0xa", "0xb"],
        grain="week",
        limit=100,
        **RANGE,
    )
    assert "toStartOfWeek(t, 1) AS bucket_start" in sql
    assert "GROUP BY source_id, target_id, bucket_start" in sql
    assert "t >= {start:Date} AND t < {endx:Date}" in sql
    assert "sum(w) AS weight" in sql
    assert "ORDER BY weight DESC, source_id, target_id, bucket_start" in sql
    assert params["start"] == "2025-01-06"
    assert params["endx"] == "2026-01-05"
    assert params["lim"] == 101  # limit + 1 = exact truncation detection
    assert params["ids"] == ["0xa", "0xb"]
    # Both endpoints restricted to the known node set.
    assert "toString(src) IN {ids:Array(String)}" in sql
    assert "toString(tgt) IN {ids:Array(String)}" in sql


def test_state_sql_no_lower_bound_but_upper_bound():
    sql, params = build_timeline_sql(
        _profile(time_column="t"),
        node_ids=["0xa"],
        grain="day",
        limit=10,
        **RANGE,
    )
    assert "toDate(min(t)) AS bucket_start" in sql
    assert "CAST(NULL AS Nullable(Date)) AS bucket_end" in sql
    assert "t < {endx:Date}" in sql
    assert "start" not in params  # NO lower time bound for state edges
    assert "GROUP BY source_id, target_id\n" in sql


def test_interval_sql_overlap_filter_and_span_grouping():
    sql, _params = build_timeline_sql(
        _profile(time_column="vf", time_end_column="vt"),
        node_ids=["0xa"],
        grain="month",
        limit=10,
        **RANGE,
    )
    assert "toStartOfMonth(vf) AS bucket_start" in sql
    assert "if(vt IS NULL, CAST(NULL AS Nullable(Date)), toStartOfMonth(vt))" in sql
    assert "vf < {endx:Date}" in sql
    assert "(vt IS NULL OR vt >= {start:Date})" in sql
    assert "GROUP BY source_id, target_id, bucket_start, bucket_end" in sql


def test_static_sql_always_on():
    sql, params = build_timeline_sql(
        _profile(weight_column="w"),
        node_ids=["0xa"],
        grain="week",
        limit=10,
        **RANGE,
    )
    assert "CAST(NULL AS Nullable(Date)) AS bucket_start" in sql
    assert "{start:Date}" not in sql and "{endx:Date}" not in sql
    assert "start" not in params and "endx" not in params


def test_undirected_canonicalizes_with_least_greatest():
    sql, _ = build_timeline_sql(
        _profile(time_column="t", weight_column="w", directed=False),
        node_ids=["0xa"],
        grain="week",
        limit=10,
        **RANGE,
    )
    assert "least(toString(src), toString(tgt)) AS source_id" in sql
    assert "greatest(toString(src), toString(tgt)) AS target_id" in sql


def test_unknown_grain_and_unsafe_identifier_raise():
    with pytest.raises(ValueError):
        build_timeline_sql(
            _profile(time_column="t"), node_ids=["0xa"], grain="hour", **RANGE
        )
    with pytest.raises(ValueError):
        build_timeline_sql(
            _profile(time_column="t", time_end_column="te; DROP TABLE x"),
            node_ids=["0xa"],
            grain="week",
            **RANGE,
        )


def test_money_timeline_queries_share_the_exact_filter_contract():
    common = dict(
        seed_ids=[AVATAR],
        direction="both",
        t0="2026-06-01 00:00:00",
        t1_exclusive="2026-07-01 00:00:00",
        min_usd=25.0,
        tokens=[TOKEN],
    )
    universe_sql, universe_params = build_timeline_universe_sql(
        **common, limit=400
    )
    global_sql, global_params = build_timeline_global_coverage_sql(**common)
    bucket_sql, bucket_params = build_timeline_bucket_edges_sql(
        **common, universe_ids=[AVATAR, TRUSTEE], grain="week", limit=8000
    )
    coverage_sql, coverage_params = build_timeline_bucket_coverage_sql(
        **common, grain="week"
    )

    for sql, params in (
        (universe_sql, universe_params),
        (global_sql, global_params),
        (bucket_sql, bucket_params),
        (coverage_sql, coverage_params),
    ):
        assert FLOWS_RELATION in sql
        assert "d.date >= toDate({t0:DateTime})" in sql
        assert "d.date < toDate({t1:DateTime})" in sql
        assert "amount_usd >= {min_usd:Float64}" in sql
        assert "OR isNull(amount_usd)" in sql
        assert "structural_terminals" in sql
        assert "d.token_address IN {tokens:Array(String)}" in sql
        assert params["seed_ids"] == [AVATAR]
        assert params["t0"] == common["t0"]
        assert params["t1"] == common["t1_exclusive"]
        assert params["tokens"] == [TOKEN]

    assert "LIMIT {lim:UInt32}" in universe_sql
    assert universe_params["lim"] == 401
    assert "countIf(isNotNull(amount_usd)) = 0" in universe_sql
    assert "ORDER BY isNull(amount_usd), amount_usd DESC" in universe_sql
    assert "toStartOfWeek(d.date, 1) AS bucket_start" in bucket_sql
    assert "source_id IN {universe_ids:Array(String)}" in bucket_sql
    assert bucket_params["universe_ids"] == [AVATAR, TRUSTEE]
    assert "GROUP BY bucket_start" in coverage_sql
    assert "excluded_unknown_usd_edges" in global_sql


# ---------------------------------------------------------------------------
# Bucket axis
# ---------------------------------------------------------------------------


def test_bucket_range_week_is_iso_monday_half_open():
    # 2026-07-17 is a Friday; its ISO week starts Monday 2026-07-13.
    start, endx, count = timeline_bucket_range(
        "week", 28, today=date(2026, 7, 17)
    )
    assert start == "2026-06-15"  # Monday of the week containing 06-19
    assert endx == "2026-07-20"   # Monday AFTER the current week (exclusive)
    assert count == 5

def test_bucket_range_day_and_month():
    start, endx, count = timeline_bucket_range("day", 7, today=date(2026, 7, 17))
    assert (start, endx, count) == ("2026-07-10", "2026-07-18", 8)
    start, endx, count = timeline_bucket_range(
        "month", 90, today=date(2026, 7, 17)
    )
    assert start == "2026-04-01"
    assert endx == "2026-08-01"
    assert count == 4


# ---------------------------------------------------------------------------
# load_graph_timeline (tool)
# ---------------------------------------------------------------------------

TOKEN = "0x9999000000000000000000000000000000000009"


class TimelineStubCH(StubCH):
    """Contract-aware stub for the four Money Trail temporal queries."""

    def __init__(
        self,
        edge_rows=None,
        roles=None,
        *,
        universe=None,
        global_coverage=None,
        buckets=None,
        bucket_coverage=None,
        fail: str | None = None,
    ):
        super().__init__(edge_rows, roles)
        self.universe = universe or []
        self.global_coverage = global_coverage or [[0, 0, 0.0, 0]]
        self.buckets = buckets or []
        self.bucket_coverage = bucket_coverage or []
        self.fail = fail
        self.timeline_calls: list[dict[str, Any]] = []

    def run_query(self, sql, database="dbt", requested_max_rows=100,
                  audience="tool", fetch_mode="auto", parameters=None):
        if "AS source_horizon" in sql:
            return super().run_query(
                sql,
                database,
                requested_max_rows,
                audience,
                fetch_mode,
                parameters=parameters,
            )
        if FLOWS_RELATION in sql:
            params = parameters or {}
            if "ORDER BY isNull(amount_usd), amount_usd DESC, counterparty_id" in sql:
                kind = "universe"
                columns = [
                    "counterparty_id", "amount_usd", "transfer_count",
                    "eligible_edge_count",
                ]
                rows = self.universe
            elif "total_transfers" in sql:
                kind = "global"
                columns = [
                    "total_counterparties", "total_edges", "total_usd",
                    "total_transfers", "unknown_usd_edges",
                    "excluded_unknown_usd_edges",
                ]
                rows = self.global_coverage
            elif "universe_ids" in sql:
                kind = "buckets"
                columns = [
                    "source_id", "target_id", "token_address", "symbol",
                    "bucket_start", "raw_amount", "normalized_amount",
                    "known_usd", "transfer_count", "priced_source_rows",
                    "source_rows", "unknown_price_rows",
                    "unknown_decimals_rows",
                ]
                rows = self.buckets
            else:
                kind = "coverage"
                columns = [
                    "bucket_start", "total_counterparties", "total_edges",
                    "known_usd", "unknown_usd_rows", "supply_event_edges",
                ]
                rows = self.bucket_coverage
            self.timeline_calls.append({"kind": kind, "sql": sql, "params": params})
            if self.fail == kind:
                raise RuntimeError(f"forced {kind} failure")
            return ExecutedQuery(
                sql, sql, database, columns, rows, len(rows), 0.0, "rows", []
            )
        return super().run_query(
            sql, database, requested_max_rows, audience, fetch_mode,
            parameters=parameters,
        )


@pytest.fixture
def timeline_snapshot():
    """Four profiles covering all temporal shapes."""
    snap = FakeSnapshot(
        models={
            "api_execution_circles_v2_trust_relations_current": _graph_model(
                "api_execution_circles_v2_trust_relations_current",
                profile="circles_trust",
                module="Circles",
                source_column="truster",
                target_column="trustee",
                source_kind="circles_avatar",
                target_kind="circles_avatar",
                time_column="valid_from",
                time_end_column="valid_to",  # interval
            ),
            "int_execution_transfers_whitelisted_daily": _graph_model(
                "int_execution_transfers_whitelisted_daily",
                profile="token_transfers",
                module="execution",
                source_column="from",
                target_column="to",
                source_kind="address",
                target_kind="address",
                time_column="date",
                weight_column="amount_usd",  # flow
            ),
            "int_execution_safes_current_owners": _graph_model(
                "int_execution_safes_current_owners",
                profile="safe_ownership",
                module="safe",
                source_column="owner",
                target_column="safe_address",
                source_kind="address",
                target_kind="safe",
                time_column="became_owner_at",  # state
            ),
            "fct_execution_circles_v2_avatar_balances_latest": _graph_model(
                "fct_execution_circles_v2_avatar_balances_latest",
                profile="circles_avatar_balances",
                module="Circles",
                source_column="avatar",
                target_column="token_address",
                source_kind="circles_avatar",
                target_kind="token",
                weight_column="balance",  # static
            ),
        },
    )
    from cerebro_mcp.semantic.graph_extraction import synthesize_search_documents

    profiles = tuple(graph_profiles.discover_profiles(models=snap.models))
    snap.graph_profiles = profiles
    snap.profiles_by_id = {p.profile: p for p in profiles}
    snap.kind_to_profiles = graph_profiles.build_kind_index(profiles)
    snap.graph_search_documents = tuple(synthesize_search_documents(profiles))
    with patch.object(graph_profiles, "semantic_runtime") as rt:
        rt.snapshot = snap
        yield snap


def _seeded_view(server, ch) -> str:
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_explorer_seed",
        {
            "view_id": view_id,
            "seed_node_id": AVATAR,
            "seed_model": "",
            "relation_types": ["circles_trust"],
        },
    )
    return view_id


def _money_stub(**overrides):
    values = {
        "universe": [
            [TRUSTEE, 340.0, 4, 1],
            [OWNER, 100.0, 1, 1],
        ],
        "global_coverage": [[2, 2, 440.0, 5]],
        "buckets": [
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", 250.0, 3, 0],
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-08", 90.0, 1, 0],
            [AVATAR, OWNER, TOKEN, "TOK", "2026-06-08", 100.0, 1, 0],
        ],
        "bucket_coverage": [
            ["2026-06-01", 1, 1, 250.0, 0],
            ["2026-06-08", 2, 2, 190.0, 0],
            ["2026-06-15", 0, 0, 0.0, 0],
        ],
    }
    values.update(overrides)
    return TimelineStubCH(**values)


def _money_view(server) -> str:
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    mini_apps.patch_view_state(
        view_id,
        {
            "flows": {
                "seeds": [AVATAR],
                "direction": "out",
                "range_days": 21,
                "t0": "2026-06-01 00:00:00",
                "t1": "2026-06-22 00:00:00",
                "min_usd": 10.0,
                "tokens": [TOKEN],
                "scope": {"scope_id": "flows:applied"},
            }
        },
    )
    return view_id


def test_timeline_uses_one_applied_money_contract_and_fixed_universe(
    timeline_snapshot,
):
    ch = _money_stub()
    server = _server(ch)
    view_id = _money_view(server)
    before = dict(mini_apps.get_view(view_id).dataset_revisions)

    result = _call_tool(
        server,
        "load_graph_timeline",
        {"view_id": view_id, "grain": "week", "request_id": 7},
    )
    assert result.isError is not True
    content = result.structuredContent
    state = content["view_state"]
    timeline = state["timeline"]
    assert state["mode"] == "atlas"  # a data load never flips navigation
    assert state.get("mode_revision", 0) == 0
    assert timeline["scope"] == "money"
    assert timeline["seed_ids"] == [AVATAR]
    assert timeline["profiles"] == ["token_transfers"]
    assert timeline["profile_shapes"] == {"token_transfers": "flow"}
    assert timeline["direction"] == "out"
    assert timeline["tokens"] == [TOKEN]

    # Every query is driven by the exact same applied seed/window/filter set.
    assert [call["kind"] for call in ch.timeline_calls] == [
        "universe", "global", "buckets", "coverage",
    ]
    for call in ch.timeline_calls:
        params = call["params"]
        assert params["seed_ids"] == [AVATAR]
        assert params["t0"] == "2026-06-01 00:00:00"
        assert params["t1"] == "2026-06-22 00:00:00"
        assert params["tokens"] == [TOKEN]
        assert params["min_usd"] == 10.0
        assert "circles_trust" not in call["sql"]
    bucket_call = next(call for call in ch.timeline_calls if call["kind"] == "buckets")
    assert set(bucket_call["params"]["universe_ids"]) == {AVATAR, TRUSTEE, OWNER}

    datasets = content["datasets"]
    assert [column["name"] for column in datasets["timeline_narrative"]["columns"]] == [
        "bucket_start", "direction", "event_kind", "counterparty_id",
        "counterparty_label", "token_address", "token_symbol", "raw_amount",
        "normalized_amount", "transfer_count", "previous_token_amount",
        "current_token_amount", "delta_token_amount", "previous_known_usd",
        "current_known_usd", "delta_known_usd", "price_coverage",
        "volume_driven_usd_effect", "price_driven_usd_effect", "change",
        "scope_id",
    ]
    assert {row[3] for row in datasets["timeline_edges"]["preview_rows"]} == {
        "token_transfers"
    }
    changes = {row[19] for row in datasets["timeline_narrative"]["preview_rows"]}
    assert {"first_observed", "not_observed"} <= changes

    scope = timeline["forensic_scope"]
    assert scope["request_id"] == 7
    assert scope["window"] == {
        "t0": "2026-06-01 00:00:00",
        "t1": "2026-06-22 00:00:00",
        "source": "flows.applied_window",
    }
    assert scope["money_contract"]["source_flow_scope_id"] == "flows:applied"
    assert scope["universe"]["fixed_across_buckets"] is True
    assert scope["universe"]["counterparties"] == {"shown": 2, "total": 2}
    assert len(scope["bucket_coverage"]) == 3
    assert scope["bucket_coverage"][1]["edges"] == {"shown": 2, "total": 2}
    assert scope["reconciliation"] == {
        "status": "unverified", "trend_claims_enabled": False,
    }
    assert scope["verification"]["status"] == "unverified"
    assert all(source["name"].startswith("dbt.") for source in scope["sources"])
    # The scoped result ends in June, while the relation watermark comes from
    # the independent source-contract probe. These clocks must not overwrite
    # each other.
    assert scope["data_horizon"] == "2026-07-18T00:00:00Z"
    assert scope["sources"][0]["horizon"] == "2026-07-18T00:00:00Z"
    assert scope["result_observed_through"] == "2026-06-15"

    after = mini_apps.get_view(view_id).dataset_revisions
    for key in ("nodes", "edges", "atlas_nodes", "atlas_edges"):
        assert after.get(key, 0) == before.get(key, 0)
    for key in ("timeline_nodes", "timeline_edges", "timeline_narrative"):
        assert after[key] > before.get(key, 0)
        assert state["dataset_scopes"][key] == scope["scope_id"]


def test_timeline_explicit_seed_keeps_route_compatibility(timeline_snapshot):
    ch = _money_stub()
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    result = _call_tool(
        server,
        "load_graph_timeline",
        {
            "view_id": opened.structuredContent["view_id"],
            "seed_node_id": AVATAR,
            "t0": "2026-06-01",
            "t1": "2026-06-22",
        },
    )
    assert result.isError is not True
    timeline = result.structuredContent["view_state"]["timeline"]
    assert timeline["scope"] == "money"
    assert timeline["anchor"]["id"] == AVATAR
    assert timeline["forensic_scope"]["money_contract"]["seed_source"] == "explicit_seed"


def test_timeline_requires_seed_or_subgraph(timeline_snapshot):
    ch = TimelineStubCH()
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    assert result.isError is True
    assert "seed_node_id" in result.content[0].text


def test_timeline_rejects_structural_terminal_as_seed(timeline_snapshot):
    server = _server(_money_stub())
    opened = _call_tool(server, "open_graph_explorer", {})
    result = _call_tool(
        server,
        "load_graph_timeline",
        {
            "view_id": opened.structuredContent["view_id"],
            "seed_node_id": ZERO_ADDRESS,
            "t0": "2026-06-01",
            "t1": "2026-06-22",
        },
    )
    assert result.isError is True
    assert "Mint/Burn terminal" in result.content[0].text


def test_timeline_budget_truncation_publishes_measured_coverage(timeline_snapshot):
    ch = _money_stub(
        buckets=[[AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", 250.0, 3, 0]],
    )
    server = _server(ch)
    view_id = _money_view(server)
    result = _call_tool(
        server,
        "load_graph_timeline",
        {"view_id": view_id, "node_budget": 1, "grain": "week"},
    )
    scope = result.structuredContent["view_state"]["timeline"]["forensic_scope"]
    assert scope["status"] == "partial"
    assert scope["truncation"]["truncated"] is True
    assert scope["universe"]["counterparties"] == {"shown": 1, "total": 2}
    assert scope["coverage"]["nodes"] == {"shown": 2, "total": 3}
    bucket_call = next(call for call in ch.timeline_calls if call["kind"] == "buckets")
    assert set(bucket_call["params"]["universe_ids"]) == {AVATAR, TRUSTEE}


def test_direct_activity_classifies_burn_and_excludes_zero_from_counterparties(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[],
        global_coverage=[[0, 1, 2.0, 1, 0, 0, 1]],
        buckets=[[
            AVATAR, ZERO_ADDRESS, TOKEN, "TOK", "2026-06-01",
            "1000000", 1.0, 2.0, 1, 1, 1, 0, 0,
        ]],
        bucket_coverage=[["2026-06-01", 0, 1, 2.0, 0, 1]],
    )
    server = _server(ch)
    view_id = _money_view(server)

    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    content = result.structuredContent
    narrative = content["datasets"]["timeline_narrative"]["preview_rows"]
    burn = next(row for row in narrative if row[19] == "first_observed")
    assert burn[1:5] == ["out", "burn", ZERO_ADDRESS, "Zero address"]
    assert burn[7:10] == ["1000000", 1.0, 1]

    nodes = content["datasets"]["timeline_nodes"]["preview_rows"]
    assert [ZERO_ADDRESS, "structural_terminal", "Zero address", ["token_transfers"]] in nodes
    scope = content["view_state"]["timeline"]["forensic_scope"]
    assert scope["universe"]["counterparties"] == {"shown": 0, "total": 0}
    assert scope["supply_events"] == {
        "shown": 1,
        "total": 1,
        "full_range_edge_groups": 1,
        "counting_basis": "bucket_source_target_token_rows",
        "counterparty_counts_exclude_structural_terminals": True,
    }


def test_direct_activity_classifies_incoming_zero_source_as_mint(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[],
        global_coverage=[[0, 1, 3.0, 1, 0, 0, 1]],
        buckets=[[
            ZERO_ADDRESS, AVATAR, TOKEN, "TOK", "2026-06-01",
            "2000000", 2.0, 3.0, 1, 1, 1, 0, 0,
        ]],
        bucket_coverage=[["2026-06-01", 0, 1, 3.0, 0, 1]],
    )
    server = _server(ch)
    view_id = _money_view(server)
    mini_apps.patch_view_state(view_id, {"flows": {"direction": "in"}})

    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    narrative = result.structuredContent["datasets"]["timeline_narrative"][
        "preview_rows"
    ]
    minted = next(row for row in narrative if row[19] == "first_observed")
    assert minted[1:5] == ["in", "mint", ZERO_ADDRESS, "Zero address"]


def test_direct_activity_uses_token_volume_and_separates_price_effect(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[[TRUSTEE, 65.0, 2, 1]],
        global_coverage=[[1, 1, 65.0, 2, 0, 0, 0]],
        buckets=[
            [
                AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01",
                "10000000", 10.0, 20.0, 1, 1, 1, 0, 0,
            ],
            [
                AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-08",
                "15000000", 15.0, 45.0, 1, 1, 1, 0, 0,
            ],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 20.0, 0, 0],
            ["2026-06-08", 1, 1, 45.0, 0, 0],
        ],
    )
    server = _server(ch)
    result = _call_tool(
        server, "load_graph_timeline", {"view_id": _money_view(server)}
    )
    narrative = result.structuredContent["datasets"]["timeline_narrative"][
        "preview_rows"
    ]
    increased = next(row for row in narrative if row[19] == "increased")
    assert increased[10:17] == [10.0, 15.0, 5.0, 20.0, 45.0, 25.0, 1.0]
    assert increased[17:19] == [10.0, 15.0]
    scope = result.structuredContent["view_state"]["timeline"]["forensic_scope"]
    assert scope["reconciliation"]["trend_claims_enabled"] is False


def test_direct_activity_keeps_volume_change_when_current_price_is_unknown(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[[TRUSTEE, 20.0, 2, 1]],
        global_coverage=[[1, 1, None, 2, 1, 0, 0]],
        buckets=[
            [
                AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01",
                "10000000", 10.0, 20.0, 1, 1, 1, 0, 0,
            ],
            [
                AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-08",
                "15000000", 15.0, None, 1, 0, 1, 1, 0,
            ],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 20.0, 0, 0],
            ["2026-06-08", 1, 1, None, 1, 0],
        ],
    )
    server = _server(ch)
    result = _call_tool(
        server, "load_graph_timeline", {"view_id": _money_view(server)}
    )
    narrative = result.structuredContent["datasets"]["timeline_narrative"][
        "preview_rows"
    ]
    increased = next(row for row in narrative if row[19] == "increased")
    assert increased[10:13] == [10.0, 15.0, 5.0]
    assert increased[14:19] == [None, None, 0.0, None, None]


def test_patch_schema_accepts_timeline_keys_rejects_cursor(timeline_snapshot):
    ch = TimelineStubCH(
        edge_rows={
            "api_execution_circles_v2_trust_relations_current": [
                [AVATAR, TRUSTEE, 1.0, 1],
            ],
        },
    )
    server = _server(ch)
    view_id = _seeded_view(server, ch)
    ok = _call_tool(
        server,
        "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"timeline": {"grain": "month", "window_buckets": 6}}},
    )
    assert ok.isError is not True
    # cursor is CLIENT-LOCAL: the schema must reject it.
    rejected = _call_tool(
        server,
        "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"timeline": {"cursor": 3}}},
    )
    assert rejected.isError is True
    # Mode gate accepts timeline + clears selection.
    mode_ok = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "mode": "timeline"},
    )
    assert mode_ok.isError is not True


def test_legacy_profiles_are_accepted_but_cannot_restore_old_timeline(
    timeline_snapshot,
):
    ch = _money_stub()
    server = _server(ch)
    view_id = _money_view(server)
    result = _call_tool(
        server,
        "load_graph_timeline",
        {"view_id": view_id, "profiles": ["circles_trust"]},
    )
    timeline = result.structuredContent["view_state"]["timeline"]
    assert timeline["profiles"] == ["token_transfers"]
    assert timeline["profile_shapes"] == {"token_transfers": "flow"}
    assert any("Legacy timeline profile filters were ignored" in warning
               for warning in result.structuredContent["view_state"]["warnings"])
    assert all("circles_trust" not in call["sql"] for call in ch.timeline_calls)


def test_timeline_query_failure_is_failed_scope_not_ready_empty_graph(
    timeline_snapshot,
):
    ch = _money_stub(fail="buckets")
    server = _server(ch)
    view_id = _money_view(server)
    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    scope = result.structuredContent["view_state"]["timeline"]["forensic_scope"]
    assert scope["status"] == "failed"
    assert scope["coverage"]["rows"]["total"] is None
    assert scope["sources"][0]["status"] == "error"
    assert "forced buckets failure" in scope["sources"][0]["error"]


def test_unknown_bucket_price_stays_null_and_blocks_directional_claim(
    timeline_snapshot,
):
    ch = _money_stub(
        global_coverage=[[1, 1, 250.0, 2]],
        universe=[[TRUSTEE, 250.0, 2, 1]],
        buckets=[
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", 250.0, 1, 0],
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-08", None, 1, 1],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 250.0, 0],
            ["2026-06-08", 1, 1, 0.0, 1],
            ["2026-06-15", 0, 0, 0.0, 0],
        ],
    )
    server = _server(ch)
    view_id = _money_view(server)
    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    content = result.structuredContent
    edge_rows = content["datasets"]["timeline_edges"]["preview_rows"]
    assert edge_rows[1][4] is None
    narrative = content["datasets"]["timeline_narrative"]["preview_rows"]
    assert not any(row[0] == "2026-06-08" and row[3] in {"increased", "decreased"}
                   for row in narrative)
    scope = content["view_state"]["timeline"]["forensic_scope"]
    assert scope["status"] == "partial"
    assert scope["coverage"]["usd"]["total"] is None


def test_zero_minimum_keeps_wholly_unpriced_timeline_group(timeline_snapshot):
    ch = _money_stub(
        universe=[[TRUSTEE, None, 2, 1]],
        global_coverage=[[1, 1, None, 2, 1, 0]],
        buckets=[
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", None, 2, 1],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 0.0, 1],
        ],
    )
    server = _server(ch)
    view_id = _money_view(server)
    mini_apps.patch_view_state(view_id, {"flows": {"min_usd": 0.0}})

    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    content = result.structuredContent
    edges = content["datasets"]["timeline_edges"]["preview_rows"]
    assert len(edges) == 1
    assert edges[0][4] is None
    scope = content["view_state"]["timeline"]["forensic_scope"]
    assert scope["coverage"]["usd"]["total"] is None
    assert scope["usd_filter_coverage"] == {
        "min_usd": 0.0,
        "eligibility": "priced_or_wholly_unpriced",
        "eligible_unknown_usd_edges": 1,
        "excluded_unknown_usd_edges": 0,
        "counting_basis": "full_range_source_target_token_groups",
    }


def test_mixed_price_timeline_publishes_known_subtotal_and_unknown_coverage(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[[TRUSTEE, 125.0, 2, 1]],
        global_coverage=[[1, 1, None, 2, 1, 0]],
        buckets=[
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", 125.0, 2, 2],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 125.0, 2],
        ],
    )
    server = _server(ch)
    view_id = _money_view(server)
    mini_apps.patch_view_state(view_id, {"flows": {"min_usd": 0.0}})

    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    scope = result.structuredContent["view_state"]["timeline"]["forensic_scope"]
    assert scope["status"] == "partial"
    assert scope["coverage"]["usd"] == {
        "known": 125.0,
        "total": None,
        "unknown_rows": 2,
    }
    assert scope["usd_filter_coverage"]["eligible_unknown_usd_edges"] == 1
    assert any("known subtotals" in warning for warning in scope["warnings"])


def test_positive_minimum_discloses_excluded_unpriced_timeline_group(
    timeline_snapshot,
):
    ch = _money_stub(
        universe=[[TRUSTEE, 250.0, 2, 1]],
        global_coverage=[[1, 1, None, 2, 0, 1]],
        buckets=[
            [AVATAR, TRUSTEE, TOKEN, "TOK", "2026-06-01", 250.0, 2, 0],
        ],
        bucket_coverage=[
            ["2026-06-01", 1, 1, 250.0, 0],
        ],
    )
    server = _server(ch)
    view_id = _money_view(server)

    result = _call_tool(server, "load_graph_timeline", {"view_id": view_id})
    scope = result.structuredContent["view_state"]["timeline"]["forensic_scope"]
    assert scope["status"] == "partial"
    assert scope["truncation"]["truncated"] is True
    assert scope["coverage"]["usd"]["total"] is None
    assert scope["usd_filter_coverage"]["excluded_unknown_usd_edges"] == 1
    assert any(
        "legacy coverage result reported 1" in warning
        for warning in scope["warnings"]
    )


def test_expand_does_not_resurrect_removed_profiles(timeline_snapshot):
    """Investigate untoggle regression: expand must NOT union the request's
    profiles with previously-persisted actives."""
    trust_rows = [[AVATAR, TRUSTEE, 1.0, 1]]
    ch = TimelineStubCH(
        edge_rows={
            "api_execution_circles_v2_trust_relations_current": trust_rows,
            "int_execution_safes_current_owners": [[OWNER, SAFE, 1.0, 1]],
        },
    )
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_explorer_seed",
        {
            "view_id": view_id,
            "seed_node_id": AVATAR,
            "seed_model": "",
            "relation_types": ["circles_trust", "safe_ownership"],
        },
    )
    # User untoggled safe_ownership locally; the next expand passes only the
    # trimmed set. The response's active_profiles must NOT re-add it.
    trust_rows.append([TRUSTEE, OWNER, 1.0, 1])
    result = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": TRUSTEE, "relation_types": ["circles_trust"]},
    )
    actives = result.structuredContent["view_state"]["investigate"]["active_profiles"]
    assert "safe_ownership" not in actives
    assert "circles_trust" in actives
