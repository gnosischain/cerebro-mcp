"""Case table for the per-tool latency suite (``--suite latency``).

PURE STDLIB at import time — ``benchmarks/run.py`` imports the suite (and
therefore this module) only AFTER redirecting the artifact env vars, but the
case table itself must stay importable without ``cerebro_mcp`` so tooling
(compare, docs) can enumerate cases without booting a server.

Conventions:

- ``args`` values may contain ``"{metric}"`` / ``"{report_markdown}"``
  placeholders. The suite substitutes them at runtime: ``{metric}`` with an
  approved day-grain metric discovered from the fixture registry,
  ``{report_markdown}`` with markdown produced by the ``report_gate`` setup
  (it needs the chart IDs of the setup's untimed ``generate_charts`` call).
- ``setup`` is a STRING key resolved to an async callable in
  ``benchmarks/suites/latency.py`` (keeps this table import-pure).
- SQL is pinned against the recorded-corpus bridge family; the fake
  ClickHouse shapes canned rows from the SELECT list, so the column names in
  the SQL are what the chart pipeline sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolLatencyCase:
    """One timed tool invocation.

    Mode semantics: in fake mode every case with ``fake_ok=True`` runs against
    the canned stack; ``fake_ok=False`` cases are skipped there. In real mode
    (``CEREBRO_EVAL_CLICKHOUSE=1``) EVERY case runs against the production
    module-global server; ``needs_clickhouse=True`` marks the cases whose
    timed call reaches ClickHouse (they default to fewer iterations there),
    and ``real_args`` — when set — replaces ``args`` so the SQL targets the
    real warehouse columns instead of the canned-shape aliases.

    ``setup_each`` forces the gate setup to re-arm before EVERY iteration
    (needed when the tool itself resets the session state on success, e.g.
    ``generate_report``); the re-arm runs outside the timed region.
    """

    id: str
    tool: str
    args: dict
    real_args: dict | None = None
    budget_ms: float | None = None
    needs_clickhouse: bool = False
    fake_ok: bool = True
    needs_semantic: bool = False
    setup: str | None = None
    teardown: bool = True
    check_substrings: tuple = ()
    forbid_substrings: tuple = ("Error:",)
    iters: int | None = None
    warmup: int | None = None
    setup_each: bool = False
    meta: dict = field(default_factory=dict)


# Per-tool latency budgets (ms), fallback when a case sets no budget_ms.
# Seeded from the benchmark plan; informational (over-budget flags, not
# failures). Fake-mode cases may pin a tighter per-case budget_ms.
BUDGETS_MS: dict[str, float] = {
    "find": 150.0,
    "search_models": 100.0,
    "discover_models": 400.0,
    "get_model_details": 100.0,
    "search_docs": 100.0,
    "search_graph_catalog": 100.0,
    "get_upstream_lineage": 150.0,
    "get_downstream_impact": 150.0,
    "get_help": 50.0,
    "list_databases": 500.0,
    "list_tables": 800.0,
    "describe_table": 800.0,
    "get_sample_data": 1500.0,
    "execute_query": 3000.0,
    "explain_query": 1500.0,
    "system_status": 1500.0,
    "preflight_analytics_request": 1000.0,
    "query_metrics": 3000.0,
    "get_metric_details": 100.0,
    "explain_metric_query": 200.0,
    "quick_chart": 3000.0,
    "quick_metric_chart": 3000.0,
    "generate_chart": 3000.0,
    "generate_charts": 8000.0,
    "generate_metric_charts": 8000.0,
    "generate_report": 2000.0,
    "verify_numbers": 3000.0,
    "load_tools": 100.0,
}

# Pinned corpus/registry model family (present in BOTH the search corpus and
# the routing registry fixtures).
PINNED_MODELS: tuple[str, ...] = (
    "int_bridges_flows_daily",
    "fct_bridges_netflow_weekly_by_bridge",
    "api_bridges_token_netflow_daily_by_bridge",
)
PRIMARY_MODEL = PINNED_MODELS[0]

# Chart-friendly pinned SQL: the fake ClickHouse shapes rows from the SELECT
# list (day -> dates, sector -> categories, value -> numerics).
CHART_SQL = (
    "SELECT day, value, sector FROM dbt.int_bridges_flows_daily "
    "ORDER BY day LIMIT 30"
)
SCATTER_SQL = "SELECT x_value, y_value FROM dbt.int_bridges_flows_daily LIMIT 30"
BAR_SQL = "SELECT sector, value FROM dbt.int_bridges_flows_daily LIMIT 3"
# Statistical EDA query: matches BOTH the statistical (quantiles) and the
# correlation (corr) session-state classifiers.
STATS_SQL = (
    "SELECT quantiles(0.5, 0.9)(net_usd) AS q, stddevPop(net_usd) AS sd, "
    "corr(net_usd, net_usd) AS c FROM dbt.int_bridges_flows_daily "
    "WHERE date >= '2026-06-01'"
)

# ── real-mode SQL (CEREBRO_EVAL_CLICKHOUSE=1) ────────────────────────
# Pinned to the ACTUAL columns of int_bridges_flows_daily (bridge, date,
# net_usd, txs, volume_usd, ...). Aliases keep the chart field names (day /
# value / sector / x_value / y_value) identical to fake mode so the chart
# specs need no per-mode variants. All queries are date-bounded aggregations
# with LIMITs — read-only and cheap on the shared warehouse.
REAL_CHART_SQL = (
    "SELECT date AS day, sum(volume_usd) AS value, bridge AS sector "
    "FROM dbt.int_bridges_flows_daily WHERE date >= today() - 30 "
    "GROUP BY day, sector ORDER BY day LIMIT 200"
)
REAL_BAR_SQL = (
    "SELECT bridge AS sector, sum(volume_usd) AS value "
    "FROM dbt.int_bridges_flows_daily WHERE date >= today() - 30 "
    "GROUP BY sector ORDER BY value DESC LIMIT 10"
)
REAL_SCATTER_SQL = (
    "SELECT sum(txs) AS x_value, sum(volume_usd) AS y_value "
    "FROM dbt.int_bridges_flows_daily WHERE date >= today() - 90 "
    "GROUP BY date LIMIT 90"
)
REAL_STATS_SQL = (
    "SELECT quantiles(0.5, 0.9)(volume_usd) AS q, stddevPop(volume_usd) AS sd, "
    "corr(toFloat64(volume_usd), toFloat64(txs)) AS c "
    "FROM dbt.int_bridges_flows_daily WHERE date >= today() - 90"
)
REAL_EDA_SQL = (
    "SELECT date, sum(volume_usd) AS volume FROM dbt.int_bridges_flows_daily "
    "WHERE date >= today() - 30 GROUP BY date ORDER BY date LIMIT 30"
)

_VERIFY_CLAIMS = (
    '[{"label": "bench net flow", "value": 100.0, "formula": "a - b", '
    '"components": {"a": 150.0, "b": 50.0}}]'
)


def _case(case_id: str, tool: str, args: dict, **kwargs: Any) -> ToolLatencyCase:
    return ToolLatencyCase(id=f"latency/{case_id}", tool=tool, args=args, **kwargs)


CASES: list[ToolLatencyCase] = [
    # ── discovery / routing ─────────────────────────────────────────
    _case(
        "find",
        "find",
        {"query": "bridge netflow last week", "mode": "answer"},
        needs_semantic=True,
        check_substrings=("bridge_netflow_7d",),
        meta={"metric": "bridge_netflow_7d"},
    ),
    _case(
        "preflight_analytics_request",
        "preflight_analytics_request",
        {"query": "bridge netflow last week", "mode": "report"},
        needs_semantic=True,
        check_substrings=("route",),
    ),
    _case(
        "search_models",
        "search_models",
        {"query": "bridge netflow", "module": "bridges", "limit": 15},
        check_substrings=("netflow",),
    ),
    _case(
        "discover_models",
        "discover_models",
        {"query": "bridge netflow", "module": "bridges", "detail_top_n": 3},
        check_substrings=("netflow",),
    ),
    _case(
        "get_model_details",
        "get_model_details",
        {"model_name": PRIMARY_MODEL},
        check_substrings=(PRIMARY_MODEL,),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "search_docs",
        "search_docs",
        {"topic": "bridge"},
    ),
    _case(
        "search_graph_catalog",
        "search_graph_catalog",
        {"query": "bridge", "limit": 10},
        needs_semantic=True,
    ),
    _case(
        "get_upstream_lineage",
        "get_upstream_lineage",
        {"model_name": "fct_bridges_netflow_weekly_by_bridge"},
        check_substrings=("fct_bridges_netflow_weekly_by_bridge",),
        meta={"pinned_model": "fct_bridges_netflow_weekly_by_bridge"},
    ),
    _case(
        "get_downstream_impact",
        "get_downstream_impact",
        {"model_name": "fct_bridges_netflow_weekly_by_bridge"},
        check_substrings=("fct_bridges_netflow_weekly_by_bridge",),
        meta={"pinned_model": "fct_bridges_netflow_weekly_by_bridge"},
    ),
    # ── schema / metadata ───────────────────────────────────────────
    _case(
        "list_databases",
        "list_databases",
        {},
        needs_clickhouse=True,
        check_substrings=("Available Databases",),
    ),
    _case(
        "list_tables",
        "list_tables",
        {"database": "dbt", "name_pattern": "%bridges%", "page_size": 50},
        needs_clickhouse=True,
        check_substrings=("bridges",),
    ),
    _case(
        "describe_table",
        "describe_table",
        {"table": PRIMARY_MODEL, "database": "dbt"},
        needs_clickhouse=True,
        check_substrings=("net_usd",),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "get_sample_data",
        "get_sample_data",
        {"table": PRIMARY_MODEL, "database": "dbt", "limit": 5},
        needs_clickhouse=True,
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "get_help",
        "get_help",
        {},
    ),
    _case(
        "system_status",
        "system_status",
        {},
        needs_clickhouse=True,
        fake_ok=False,
    ),
    # ── query plane ─────────────────────────────────────────────────
    _case(
        "execute_query__select_1",
        "execute_query",
        {"sql": "SELECT 1", "database": "dbt", "max_rows": 1},
        needs_clickhouse=True,
        budget_ms=500.0,  # protocol floor: no data shaping beyond one row
    ),
    _case(
        "execute_query__stats",
        "execute_query",
        {"sql": STATS_SQL, "database": "dbt", "max_rows": 10},
        real_args={"sql": REAL_STATS_SQL, "database": "dbt", "max_rows": 10},
        needs_clickhouse=True,
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "explain_query",
        "explain_query",
        {"sql": "SELECT date, net_usd FROM dbt.int_bridges_flows_daily LIMIT 10"},
        real_args={"sql": REAL_EDA_SQL},
        needs_clickhouse=True,
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    # ── semantic metric plane ───────────────────────────────────────
    _case(
        "query_metrics",
        "query_metrics",
        {"metrics": ["{metric}"], "dimensions": ["day"], "limit": 30},
        needs_clickhouse=True,
        needs_semantic=True,
        check_substrings=("{metric}",),
    ),
    _case(
        "get_metric_details",
        "get_metric_details",
        {"metric_name": "{metric}"},
        needs_semantic=True,
        check_substrings=("{metric}",),
    ),
    _case(
        "explain_metric_query",
        "explain_metric_query",
        {"metrics": ["{metric}"], "dimensions": ["day"], "limit": 30},
        needs_semantic=True,
        check_substrings=("sql",),
    ),
    # ── charting (raw path) ─────────────────────────────────────────
    _case(
        "quick_chart",
        "quick_chart",
        {
            "sql": CHART_SQL,
            "chart_type": "line",
            "x_field": "day",
            "y_field": "value",
            "series_field": "sector",
            "title": "Bench quick chart",
        },
        real_args={
            "sql": REAL_CHART_SQL,
            "chart_type": "line",
            "x_field": "day",
            "y_field": "value",
            "series_field": "sector",
            "title": "Bench quick chart",
        },
        needs_clickhouse=True,
        setup="chart_gate",
        check_substrings=("chart_",),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "generate_chart",
        "generate_chart",
        {
            "sql": CHART_SQL,
            "chart_type": "line",
            "x_field": "day",
            "y_field": "value",
            "series_field": "sector",
            "title": "Bench single chart",
        },
        real_args={
            "sql": REAL_CHART_SQL,
            "chart_type": "line",
            "x_field": "day",
            "y_field": "value",
            "series_field": "sector",
            "title": "Bench single chart",
        },
        needs_clickhouse=True,
        setup="chart_gate",
        check_substrings=("Chart ID",),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    _case(
        "generate_charts",
        "generate_charts",
        {
            "charts": [
                {
                    "sql": CHART_SQL,
                    "chart_type": "line",
                    "x_field": "day",
                    "y_field": "value",
                    "series_field": "sector",
                    "title": "Bench trend by sector",
                },
                {
                    "sql": BAR_SQL,
                    "chart_type": "bar",
                    "x_field": "sector",
                    "y_field": "value",
                    "title": "Bench sector breakdown",
                },
                {
                    "sql": SCATTER_SQL,
                    "chart_type": "scatter",
                    "x_field": "x_value",
                    "y_field": "y_value",
                    "title": "Bench relational scatter",
                },
            ]
        },
        real_args={
            "charts": [
                {
                    "sql": REAL_CHART_SQL,
                    "chart_type": "line",
                    "x_field": "day",
                    "y_field": "value",
                    "series_field": "sector",
                    "title": "Bench trend by sector",
                },
                {
                    "sql": REAL_BAR_SQL,
                    "chart_type": "bar",
                    "x_field": "sector",
                    "y_field": "value",
                    "title": "Bench sector breakdown",
                },
                {
                    "sql": REAL_SCATTER_SQL,
                    "chart_type": "scatter",
                    "x_field": "x_value",
                    "y_field": "y_value",
                    "title": "Bench relational scatter",
                },
            ]
        },
        needs_clickhouse=True,
        setup="chart_gate",
        check_substrings=("Generated 3/3",),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    # ── charting (semantic path) ────────────────────────────────────
    _case(
        "quick_metric_chart",
        "quick_metric_chart",
        {
            "metrics": ["{metric}"],
            "dimensions": ["day"],
            "chart_type": "line",
            "title": "Bench metric chart",
        },
        needs_clickhouse=True,
        setup="semantic_preflight",
        needs_semantic=True,
        check_substrings=("Chart ID",),
    ),
    _case(
        "generate_metric_charts",
        "generate_metric_charts",
        {
            "charts": [
                {
                    "metrics": ["{metric}"],
                    "dimensions": ["day"],
                    "chart_type": "line",
                    "title": "Bench metric batch",
                }
            ]
        },
        needs_clickhouse=True,
        setup="semantic_chart_gate",
        needs_semantic=True,
        check_substrings=("Generated 1/1",),
    ),
    # ── reporting ───────────────────────────────────────────────────
    _case(
        "generate_report",
        "generate_report",
        {
            "title": "Bench latency report",
            "content_markdown": "{report_markdown}",
        },
        setup="report_gate",
        setup_each=True,  # generate_report resets session state on success
        iters=3,
        check_substrings=("Report generated",),
        meta={"pinned_model": PRIMARY_MODEL},
    ),
    # ── verification / meta ─────────────────────────────────────────
    _case(
        "verify_numbers",
        "verify_numbers",
        {"claims_json": _VERIFY_CLAIMS},
        check_substrings=("PASS",),
    ),
    _case(
        "load_tools",
        "load_tools",
        {"names": ["search_models"]},
        check_substrings=("unhidden",),
    ),
]
