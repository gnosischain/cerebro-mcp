"""Pinned case tables for the semantic-layer benchmark suite (Suite 5).

Import-pure: stdlib only at import time (dataclasses + literal dicts). The
suite module (``benchmarks/suites/semantic.py``) does all ``cerebro_mcp``
imports lazily so ``benchmarks/run.py`` can redirect env paths first.

Every pinned metric / dimension / query here was verified against the frozen
``tests/fixtures/routing_registry.json.gz`` (see the case comments for what
each pin exercises). Regenerating that fixture may invalidate pins — the run
fingerprint (``fixture_sha``) recorded in the result environment is what
``compare`` uses to refuse cross-fixture diffs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ──────────────────────────────────────────────────────────────────────
# Section A — runtime build / refresh
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuntimeCase:
    id: str
    op: str                    # snapshot_build | index_build | token_idf_build |
    #                            search_index_build | refresh_noop |
    #                            cold_load_real | refresh_noop_real
    iters: int
    warmup: int
    budget_ms: float
    needs_live: bool = False
    # refresh_noop is a pure in-memory no-op; anything above hard_error_factor
    # x budget means the no-op path regressed into real work -> case ERROR.
    hard_error_factor: float | None = None


RUNTIME_CASES: tuple[RuntimeCase, ...] = (
    RuntimeCase("semantic.runtime.snapshot_build", "snapshot_build", 5, 1, 500.0),
    RuntimeCase("semantic.runtime.index_build", "index_build", 5, 1, 300.0),
    RuntimeCase("semantic.runtime.token_idf_build", "token_idf_build", 5, 1, 200.0),
    RuntimeCase("semantic.runtime.search_index_build", "search_index_build", 5, 1, 300.0),
    RuntimeCase(
        "semantic.runtime.refresh_noop", "refresh_noop", 50, 3, 5.0,
        hard_error_factor=5.0,
    ),
    RuntimeCase(
        "semantic.runtime.cold_load_real", "cold_load_real", 3, 0, 10_000.0,
        needs_live=True,
    ),
    RuntimeCase(
        "semantic.runtime.refresh_noop_real", "refresh_noop_real", 10, 1, 500.0,
        needs_live=True,
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Section B — routing latency + cache
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoutingCase:
    id: str
    tool: str                  # preflight_analytics_request | find
    query: str
    mode: str                  # answer | chart
    phase: str                 # cold | cached
    iters: int
    budget_ms: float


# One representative query per authored class (all four are also pinned in
# benchmarks/queries/semantic_routing_queries.json with their class tags).
_Q_EXACT = "bridge_netflow_7d"                       # exact metric name
_Q_SYNONYM = "net inflow gnosis last week"           # question_synonym of bridge_netflow_7d
_Q_NATURAL = "bridge netflow last week"              # NL -> semantic_ready (verified)
_Q_GAP = "solana meme coin sniper profitability"     # NL -> semantic_coverage_gap (verified)

ROUTING_LATENCY_CASES: tuple[RoutingCase, ...] = (
    RoutingCase("semantic.routing.preflight_exact_name_cold",
                "preflight_analytics_request", _Q_EXACT, "answer", "cold", 5, 250.0),
    RoutingCase("semantic.routing.preflight_exact_name_cached",
                "preflight_analytics_request", _Q_EXACT, "answer", "cached", 10, 50.0),
    RoutingCase("semantic.routing.preflight_synonym_cold",
                "preflight_analytics_request", _Q_SYNONYM, "answer", "cold", 5, 250.0),
    RoutingCase("semantic.routing.preflight_synonym_cached",
                "preflight_analytics_request", _Q_SYNONYM, "answer", "cached", 10, 50.0),
    RoutingCase("semantic.routing.preflight_natural_language_cold",
                "preflight_analytics_request", _Q_NATURAL, "answer", "cold", 5, 250.0),
    RoutingCase("semantic.routing.preflight_natural_language_cached",
                "preflight_analytics_request", _Q_NATURAL, "answer", "cached", 10, 50.0),
    RoutingCase("semantic.routing.preflight_coverage_gap_cold",
                "preflight_analytics_request", _Q_GAP, "answer", "cold", 5, 250.0),
    RoutingCase("semantic.routing.preflight_coverage_gap_cached",
                "preflight_analytics_request", _Q_GAP, "answer", "cached", 10, 50.0),
    RoutingCase("semantic.routing.find_answer_cold",
                "find", _Q_NATURAL, "answer", "cold", 5, 400.0),
    RoutingCase("semantic.routing.find_chart_cold",
                "find", _Q_NATURAL, "chart", "cold", 5, 400.0),
)

ROUTE_DIRECT_CASE_ID = "semantic.routing.route_direct"
ROUTE_DIRECT_BUDGET_MS = 150.0
CACHE_CORRECTNESS_CASE_ID = "semantic.routing.preflight_cache_correctness"
ROUTE_DISTRIBUTION_CASE_ID = "semantic.routing.route_distribution"


# ──────────────────────────────────────────────────────────────────────
# Synthetic micro-registry (build_indexes-shaped raw registry)
# ──────────────────────────────────────────────────────────────────────
# The frozen fixture has no ratio/derived metrics and no metric whose
# allowed_dimensions include a REMOTE (joined) dimension, so the tool-layer
# enriched path and the derived-metric planner path are exercised on this
# deterministic micro-registry instead (mirrors tests/test_semantic_tools.
# _make_snapshot, plus one many_to_one relationship and one ratio metric).

MICRO_REGISTRY: dict[str, Any] = {
    "metadata": {"manifest_hash": "bench-micro", "catalog_hash": "bench-micro"},
    "models": {
        "api_bench_txs_daily": {
            "name": "api_bench_txs_daily",
            "module": "execution",
            "relation_name": "`dbt`.`api_bench_txs_daily`",
            "semantic_status": "approved",
            "quality_tier": "approved",
            "description": "Benchmark fact: daily transactions per sector id.",
            "dimensions": [
                {"name": "day", "type": "time", "expr": "day",
                 "type_params": {"time_granularity": "day"}},
                {"name": "sector_id", "type": "categorical", "expr": "sector_id"},
            ],
            "measures": [
                {"name": "bench_txs_value", "agg": "sum", "expr": "txs"},
                {"name": "bench_fees_value", "agg": "sum", "expr": "fees_usd"},
            ],
        },
        "dim_bench_sectors": {
            "name": "dim_bench_sectors",
            "module": "execution",
            "relation_name": "`dbt`.`dim_bench_sectors`",
            "semantic_status": "approved",
            "quality_tier": "approved",
            "description": "Benchmark dimension: sector id -> sector name.",
            "dimensions": [
                {"name": "sector_id", "type": "categorical", "expr": "sector_id"},
                {"name": "sector_name", "type": "categorical", "expr": "sector_name"},
            ],
            "measures": [],
        },
    },
    "metrics": {
        "bench_txs": {
            "name": "bench_txs",
            "label": "Bench Transactions",
            "description": "Benchmark transaction count.",
            "module": "execution",
            "root_model": "api_bench_txs_daily",
            "measure": "bench_txs_value",
            "type": "simple",
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["day", "sector_name"],
            "supported_time_grains": ["day"],
            "default_filters": [],
            "question_synonyms": ["bench tx count"],
        },
        "bench_fees": {
            "name": "bench_fees",
            "label": "Bench Fees",
            "description": "Benchmark fee volume in USD.",
            "module": "execution",
            "root_model": "api_bench_txs_daily",
            "measure": "bench_fees_value",
            "type": "simple",
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["day"],
            "supported_time_grains": ["day"],
            "default_filters": [],
            "question_synonyms": ["bench fee volume"],
        },
        "bench_fee_per_tx": {
            "name": "bench_fee_per_tx",
            "label": "Bench Fee Per Transaction",
            "description": "Benchmark ratio metric: fees / transactions.",
            "module": "execution",
            "root_model": "api_bench_txs_daily",
            "measure": "",
            "type": "ratio",
            "type_params": {"numerator": "bench_fees", "denominator": "bench_txs"},
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["day"],
            "supported_time_grains": ["day"],
            "default_filters": [],
            "question_synonyms": ["bench fee per tx"],
        },
    },
    "relationships": [
        {
            "name": "bench_txs_to_sectors",
            "left_model": "api_bench_txs_daily",
            "right_model": "dim_bench_sectors",
            "left_keys": ["sector_id"],
            "right_keys": ["sector_id"],
            "cardinality": "many_to_one",
            "quality_tier": "approved",
        },
    ],
}


# ──────────────────────────────────────────────────────────────────────
# Section C — planner
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannerCase:
    id: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    allow_candidate: bool = False
    expected_mode: str = "single_model"
    expect_derived: bool = False
    snapshot: str = "fixture"      # fixture | micro
    cache: str = "warm"            # warm | cold (clear binding/path caches per sample)
    iters: int = 10
    warmup: int = 2
    budget_ms: float = 25.0


PLANNER_CASES: tuple[PlannerCase, ...] = (
    # 0-dim single root (fct weekly netflow, approved).
    PlannerCase(
        "semantic.planner.single_0dim",
        metrics=("bridge_netflow_weekly_by_bridge",),
    ),
    # 2 local dims + a WHERE-side filter on int_bridges_flows_daily.
    PlannerCase(
        "semantic.planner.single_2dim_filter",
        metrics=("bridge_flow_netflow_daily",),
        dimensions=("date", "bridge"),
        filters=({"field": "bridge", "op": "=", "value": "xdai_bridge"},),
    ),
    # Remote dimension via approved relationship path (verified in fixture:
    # int_GBCDeposit_deposists_daily -> int_consensus_validators_labels).
    PlannerCase(
        "semantic.planner.enriched",
        metrics=("GBCDeposit_deposists_daily__amount_value",),
        dimensions=("validator_index",),
        expected_mode="enriched_single_model",
    ),
    # Two approved metrics on DIFFERENT roots sharing the `date` axis.
    PlannerCase(
        "semantic.planner.multi_branch",
        metrics=("bridge_flow_netflow_daily", "GBCDeposit_deposists_daily__amount_value"),
        dimensions=("date",),
        expected_mode="multi_branch_aggregate_join",
    ),
    # Candidate-tier metric with approved root: allow_candidate opt-in.
    PlannerCase(
        "semantic.planner.candidate_allow",
        metrics=("annual_rolling_fees_total_value",),
        dimensions=("week",),
        allow_candidate=True,
    ),
    # Ratio metric (micro-registry — the fixture registry has none).
    PlannerCase(
        "semantic.planner.derived_ratio",
        metrics=("bench_fee_per_tx",),
        dimensions=("day",),
        expect_derived=True,
        snapshot="micro",
    ),
    # Cold-cache variant: planner binding + graph path caches cleared per sample.
    PlannerCase(
        "semantic.planner.enriched_coldcache",
        metrics=("GBCDeposit_deposists_daily__amount_value",),
        dimensions=("validator_index",),
        expected_mode="enriched_single_model",
        cache="cold",
        iters=5,
        warmup=0,
        budget_ms=150.0,
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Section C — SQL compiler goldens
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqlGoldenCase:
    id: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    order_by: tuple[str, ...] = ()
    limit: int = 100
    force_qualified: bool = False
    allow_candidate: bool = False
    snapshot: str = "fixture"
    budget_ms: float = 15.0


SQL_GOLDEN_CASES: tuple[SqlGoldenCase, ...] = (
    SqlGoldenCase(
        "semantic.sqlgolden.single_0dim_weekly_netflow",
        metrics=("bridge_netflow_weekly_by_bridge",), limit=10,
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.single_1dim_week_ordered",
        metrics=("bridge_netflow_weekly_by_bridge",),
        dimensions=("week",), order_by=("week DESC",), limit=52,
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.single_2dim_daily_netflow",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.single_2dim_where_filter",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        filters=({"field": "bridge", "op": "=", "value": "xdai_bridge"},),
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.single_token_neq_filter",
        metrics=("bridges_token_netflow_daily_by_bridge__value_value",),
        dimensions=("date", "token"),
        filters=({"field": "token", "op": "!=", "value": ""},),
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.single_force_qualified",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        force_qualified=True,
    ),
    # Metric-alias filter compiles to HAVING, which forces the CTE path
    # (the inline single-branch shortcut refuses HAVING).
    SqlGoldenCase(
        "semantic.sqlgolden.single_metric_having",
        metrics=("bridge_flow_netflow_daily",), dimensions=("bridge",),
        filters=({"field": "bridge_flow_netflow_daily", "op": ">", "value": 0},),
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.enriched_validator_labels",
        metrics=("GBCDeposit_deposists_daily__amount_value",),
        dimensions=("validator_index",), limit=50,
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.multi_branch_date",
        metrics=("bridge_flow_netflow_daily", "GBCDeposit_deposists_daily__amount_value"),
        dimensions=("date",),
    ),
    # Zero-dim multi-root -> single-row branches CROSS JOIN.
    SqlGoldenCase(
        "semantic.sqlgolden.multi_branch_0dim",
        metrics=("bridge_flow_netflow_daily", "GBCDeposit_deposists_daily__amount_value"),
        limit=1,
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.candidate_allow_week",
        metrics=("annual_rolling_fees_total_value",), dimensions=("week",),
        allow_candidate=True,
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.micro_enriched_sector",
        metrics=("bench_txs",), dimensions=("day", "sector_name"),
        snapshot="micro",
    ),
    SqlGoldenCase(
        "semantic.sqlgolden.micro_ratio_day",
        metrics=("bench_fee_per_tx",), dimensions=("day",),
        snapshot="micro",
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Section D — query_metrics end-to-end (fake ClickHouse)
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QueryMetricsCase:
    id: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    kind: str = "clean"            # clean | fail_once | fail_always | candidate_error
    snapshot: str = "fixture"      # fixture | micro
    fail_error: str = ""
    expected_repair: str = ""
    expected_planner_mode: str = ""
    allow_candidate: bool = False
    budget_ms: float = 75.0
    iters: int = 5
    warmup: int = 1


QUERY_METRICS_CASES: tuple[QueryMetricsCase, ...] = (
    QueryMetricsCase(
        "semantic.query.clean_single",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        expected_planner_mode="single_model",
    ),
    # Tool-layer enriched path needs a metric whose allowed_dimensions carry a
    # remote dimension — only the micro-registry has one (see MICRO_REGISTRY).
    QueryMetricsCase(
        "semantic.query.clean_enriched",
        metrics=("bench_txs",), dimensions=("day", "sector_name"),
        snapshot="micro",
        expected_planner_mode="enriched_single_model",
    ),
    QueryMetricsCase(
        "semantic.query.clean_multi",
        metrics=("bridge_flow_netflow_daily", "GBCDeposit_deposists_daily__amount_value"),
        dimensions=("date",),
        expected_planner_mode="multi_branch_aggregate_join",
    ),
    # First execution fails with a repairable identifier error -> the tool
    # recompiles force_qualified and succeeds on attempt 2.
    QueryMetricsCase(
        "semantic.query.repair_unknown_identifier",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        kind="fail_once",
        fail_error="Code: 47. DB::Exception: UNKNOWN_IDENTIFIER: bridge",
        expected_repair="qualify_identifiers",
        budget_ms=150.0, iters=3, warmup=0,
    ),
    # Aggregate-boundary error class (matches `not in group by` in
    # _classify_repairable_error) -> group_by_aliases repair.
    QueryMetricsCase(
        "semantic.query.repair_group_by",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        kind="fail_once",
        fail_error=(
            "Code: 215. DB::Exception: Column date is not under aggregate "
            "function and not in GROUP BY"
        ),
        expected_repair="group_by_aliases",
        budget_ms=150.0, iters=3, warmup=0,
    ),
    # Every attempt fails -> terminal semantic_repair_failed error string.
    QueryMetricsCase(
        "semantic.query.fail_always",
        metrics=("bridge_flow_netflow_daily",), dimensions=("date", "bridge"),
        kind="fail_always",
        fail_error="Code: 47. DB::Exception: UNKNOWN_IDENTIFIER: bridge",
        budget_ms=150.0, iters=3, warmup=0,
    ),
    # Candidate-tier metric without the allow_candidate opt-in -> refused.
    QueryMetricsCase(
        "semantic.query.candidate_gate_error",
        metrics=("annual_rolling_fees_total_value",),
        kind="candidate_error",
        budget_ms=75.0, iters=3, warmup=0,
    ),
)

# Real-ClickHouse pinned metric queries (skipped unless CEREBRO_EVAL_CLICKHOUSE=1).
# (metric, dimensions) — all approved in the frozen fixture registry.
REAL_QUERY_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bridge_netflow_weekly_by_bridge", ("week",)),
    ("bridge_flow_netflow_daily", ("date",)),
    ("bridge_flow_volume_daily", ("date", "bridge")),
    ("bridge_flow_transfers_daily", ("date",)),
    ("bridges_token_netflow_daily_by_bridge__value_value", ("date", "token")),
    ("cum_netflow_usd_value", ("week", "bridge")),
    ("validators_active", ("day",)),
    ("bridge_netflow_7d", ()),
    ("bridge_distinct_chains", ()),
    ("bridges_flows_daily__volume_token_value", ("date", "token")),
)
REAL_QUERY_BUDGET_MS = 4_000.0


# ──────────────────────────────────────────────────────────────────────
# Section E — registry coverage scalars
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CoverageCase:
    id: str
    stat: str
    direction: str                 # higher_is_better | must_be_zero | informational


COVERAGE_CASES: tuple[CoverageCase, ...] = (
    CoverageCase("semantic.coverage.metrics_total", "metrics_total", "higher_is_better"),
    CoverageCase("semantic.coverage.metrics_approved_count", "metrics_approved_count", "higher_is_better"),
    CoverageCase("semantic.coverage.metrics_candidate_count", "metrics_candidate_count", "informational"),
    CoverageCase("semantic.coverage.models_by_semantic_status", "models_by_semantic_status", "higher_is_better"),
    CoverageCase("semantic.coverage.pct_models_with_entities", "pct_models_with_entities", "higher_is_better"),
    CoverageCase("semantic.coverage.pct_metrics_with_synonyms", "pct_metrics_with_synonyms", "higher_is_better"),
    CoverageCase("semantic.coverage.dimension_index_size", "dimension_index_size", "higher_is_better"),
    CoverageCase("semantic.coverage.pct_metrics_with_allowed_dimensions", "pct_metrics_with_allowed_dimensions", "higher_is_better"),
    CoverageCase("semantic.coverage.orphan_metrics", "orphan_metrics", "must_be_zero"),
    CoverageCase("semantic.coverage.per_module_approved", "per_module_approved", "informational"),
)


# ──────────────────────────────────────────────────────────────────────
# Section F — semantic chart tools
# ──────────────────────────────────────────────────────────────────────

# Batch specs: series-broken weekly netflow (numeric values pinned via a
# BenchClickHouse override — the default canned shaper would render the metric
# column as category strings because its name contains "bridge"), plus two
# validators charts that the default canned shaper handles fine.
BATCH_CHART_SPECS: tuple[dict[str, Any], ...] = (
    {
        "metrics": ["bridge_netflow_weekly_by_bridge"],
        "dimensions": ["week", "bridge"],
        "chart_type": "line",
        "x_field": "week",
        "y_field": "bridge_netflow_weekly_by_bridge",
        "series_field": "bridge",
        "title": "Weekly netflow by bridge (bench)",
    },
    {
        "metrics": ["validators_active"],
        "dimensions": ["day"],
        "chart_type": "line",
        "title": "Active validators (bench)",
    },
    {
        "metrics": ["validators_active"],
        "dimensions": ["day"],
        "chart_type": "bar",
        "title": "Active validators bar (bench)",
    },
)

# Override payload for the weekly-netflow batch spec (regex, (columns, rows)).
WEEKLY_NETFLOW_OVERRIDE: tuple[str, tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]] = (
    r"fct_bridges_netflow_weekly_by_bridge",
    (
        ("week", "bridge", "bridge_netflow_weekly_by_bridge"),
        tuple(
            (f"2026-0{month}-0{monday}", bridge, round(1_000_000.0 + month * 37_500.5 + i * 11.25, 2))
            for i, (month, monday, bridge) in enumerate(
                (m, d, b)
                for m in (4, 5, 6)
                for d in (1, 8)
                for b in ("xdai_bridge", "omni_bridge")
            )
        ),
    ),
)

# numberDisplay requires a SINGLE-row result; the default canned shaper emits
# 30 rows for a 0-dim aggregate, so the scalar KPI query is pinned to one row.
# The pattern anchors on the 0-dim select head so the day-series validators
# query (SELECT day AS day, sum(cnt) ...) keeps its default canned rows.
SCALAR_KPI_OVERRIDE: tuple[str, tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]] = (
    r"SELECT\s+sum\(cnt\)\s+AS\s+validators_active\s+FROM",
    (("validators_active",), ((145_123.0,),)),
)


@dataclass(frozen=True)
class ChartCase:
    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    kind: str = "positive"         # positive | explain | gate_negative
    expect_anchor: str = "Chart ID:"
    budget_ms: float = 100.0
    iters: int = 5
    warmup: int = 1


CHART_CASES: tuple[ChartCase, ...] = (
    ChartCase(
        "semantic.chart.quick_line_day",
        "quick_metric_chart",
        args={
            "metrics": ["validators_active"],
            "dimensions": ["day"],
            "chart_type": "line",
            "title": "Validators active (bench)",
        },
    ),
    ChartCase(
        "semantic.chart.quick_number_scalar",
        "quick_metric_chart",
        args={
            "metrics": ["validators_active"],
            "chart_type": "numberDisplay",
            "title": "Validators KPI (bench)",
        },
    ),
    ChartCase(
        "semantic.chart.metric_charts_batch",
        "generate_metric_charts",
        args={"charts": list(BATCH_CHART_SPECS)},
        expect_anchor="Generated 3/3 semantic charts",
        budget_ms=400.0,
        iters=3,
    ),
    ChartCase(
        "semantic.chart.explain_metric_query",
        "explain_metric_query",
        args={"metrics": ["bridge_flow_netflow_daily"], "dimensions": ["date"]},
        kind="explain",
        expect_anchor="",
        budget_ms=200.0,
    ),
    ChartCase(
        "semantic.chart.gate_negative",
        "quick_metric_chart",
        args={
            "metrics": ["validators_active"],
            "dimensions": ["day"],
            "chart_type": "line",
        },
        kind="gate_negative",
        expect_anchor="Semantic preflight required",
        budget_ms=50.0,
        iters=1,
        warmup=0,
    ),
)

# Root model recorded against the session gate for chart-positive cases
# (MIN_MODELS_DETAILED_LITE=1 / MIN_TABLES_VERIFIED=1 in chart mode).
CHART_GATE_ROOT_MODEL = "api_consensus_validators_active_daily"
