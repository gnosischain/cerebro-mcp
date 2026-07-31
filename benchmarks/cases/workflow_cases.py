"""Pinned agent-workflow cases for the ``workflows`` suite (Suite 3).

Every input is PINNED — query strings, model names, chart SQL, markdown — so a
run is comparable across commits. The pins were verified against the recorded
fixtures (``tests/fixtures/search_corpus.json.gz`` +
``tests/fixtures/routing_registry.json.gz``):

- Covered query: ``"bridge netflow last week"`` routes ``semantic_ready`` with
  top metric ``bridge_netflow_7d`` and ``recommended_action.tool ==
  query_metrics`` in answer mode.
- Uncovered queries (route ``semantic_coverage_gap`` in the pinned mode, so
  the raw-SQL path is legal): ``"omnibridge relayer gossip fanout entropy"``
  (chart), ``"relayer gossip fanout entropy deep dive"`` /
  ``"relayer firmware checksum drift"`` / ``"typewriter ribbon procurement
  forecast"`` (report/answer). The fixture registry has wide bridge coverage,
  so most realistic bridge phrasings route ``hybrid_ready`` — these pins were
  chosen because they genuinely gap.
- Discovery pin: ``search_models(query="bridges flows daily", limit=15)``
  returns exactly 15 models including all three pinned models. The fixture
  manifest's module index collapses to ``models`` (paths all start with
  ``models/``), so a ``module="bridges"`` filter matches nothing — the pin
  deliberately omits it.
- Pinned models: ``int_bridges_flows_daily`` (line/EDA),
  ``fct_bridges_netflow_weekly_by_bridge`` (bar/statistical EDA),
  ``api_bridges_token_netflow_daily_by_bridge`` (scatter).
- ``BenchClickHouse`` shapes canned rows from the SELECT list, so aliases are
  chosen to produce the right value kinds: ``day``/``date``/``week`` -> date
  strings, ``sector``/``bridge`` -> categories, everything else -> numeric.
  Chart ids are sequential ``chart_1``, ``chart_2``, ... per
  ``_next_chart_id`` in ``tools/visualization/charts.py`` (the counter is
  zeroed by ``reset_server_state`` before each case).

This module must stay import-pure (stdlib only) — the runner imports it before
any ``cerebro_mcp`` import is legal.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    """One pinned tool call plus the assertions run on its flattened result."""

    tool: str
    args: dict
    # The step is EXPECTED to hit a workflow gate (block markers from
    # reasoning._WORKFLOW_BLOCK_PATTERNS or "Report quality gate failed").
    # A mismatch in either direction fails the case.
    expect_block: bool = False
    expect_substrings: tuple = ()
    forbid_substrings: tuple = ()
    # Expected `route` field parsed from a find/preflight result object.
    route_expect: str | None = None


@dataclass(frozen=True)
class WorkflowCase:
    id: str
    tier: str
    steps: tuple
    # The hand-derived minimum tool-call count for this task shape; the suite
    # reports executed/optimal as overhead_ratio.
    optimal_calls: int
    needs_clickhouse: bool = False
    # Probe cases mutate/paint global gate state in ways that are only safe
    # against a private in-process server, never a shared SSE one.
    sse_safe: bool = True
    notes: str = ""


# ---------------------------------------------------------------------------
# Pinned inputs
# ---------------------------------------------------------------------------

COVERED_ANSWER_QUERY = "bridge netflow last week"          # -> semantic_ready
COVERED_TOP_METRIC = "bridge_netflow_7d"

UNCOVERED_CHART_QUERY = "omnibridge relayer gossip fanout entropy"
UNCOVERED_REPORT_QUERY = "relayer gossip fanout entropy deep dive"
UNCOVERED_REPORT_QUERY_2 = "relayer firmware checksum drift"
UNCOVERED_REPORT_QUERY_3 = "typewriter ribbon procurement forecast"
UNCOVERED_ANSWER_QUERY = "relayer firmware checksum drift"

DISCOVERY_QUERY = "bridges flows daily"   # 15 fixture models, includes all 3 pins

MODEL_LINE = "int_bridges_flows_daily"
MODEL_BAR = "fct_bridges_netflow_weekly_by_bridge"
MODEL_SCATTER = "api_bridges_token_netflow_daily_by_bridge"
PINNED_MODELS = (MODEL_LINE, MODEL_BAR, MODEL_SCATTER)

LINE_SQL = (
    "SELECT date AS day, volume_usd AS value, bridge AS sector "
    f"FROM dbt.{MODEL_LINE} "
    "GROUP BY day, value, sector ORDER BY day LIMIT 30"
)
BAR_SQL = (
    "SELECT bridge, sum(netflow_usd_week) AS netflow_usd "
    f"FROM dbt.{MODEL_BAR} "
    "GROUP BY bridge ORDER BY netflow_usd DESC LIMIT 3"
)
SCATTER_SQL = (
    "SELECT value AS x_value, bridge_order AS y_value "
    f"FROM dbt.{MODEL_SCATTER} LIMIT 40"
)
EDA_SQL_PLAIN = (
    "SELECT date, count() AS txs_cnt "
    f"FROM dbt.{MODEL_LINE} "
    "WHERE date >= '2026-06-01' GROUP BY date ORDER BY date LIMIT 30"
)
EDA_SQL_STATISTICAL = (
    "SELECT quantiles(0.5, 0.9)(netflow_usd_week) AS netflow_quantiles, "
    "corr(netflow_usd_week, cum_netflow_usd) AS netflow_corr "
    f"FROM dbt.{MODEL_BAR} "
    "WHERE week >= '2026-05-01' LIMIT 1"
)
ANSWER_SQL = (
    "SELECT date, sum(volume_usd) AS volume_usd_total "
    f"FROM dbt.{MODEL_LINE} "
    "WHERE date >= '2026-06-24' GROUP BY date ORDER BY date LIMIT 7"
)

LINE_CHART_SPEC = {
    "sql": LINE_SQL,
    "chart_type": "line",
    "x_field": "day",
    "y_field": "value",
    "series_field": "sector",
    "title": "Bridge volume by bridge (daily)",
}
BAR_CHART_SPEC = {
    "sql": BAR_SQL,
    "chart_type": "bar",
    "x_field": "bridge",
    "y_field": "netflow_usd",
    "title": "Weekly netflow by bridge",
}
SCATTER_CHART_SPEC = {
    "sql": SCATTER_SQL,
    "chart_type": "scatter",
    "x_field": "x_value",
    "y_field": "y_value",
    "title": "Token netflow vs bridge rank",
}

REPORT_TITLE = "Bridge flows deep-dive"
REPORT_MARKDOWN = (
    "## Bridge flow trend\n\n"
    "{{chart:chart_1}}\n\n"
    "Daily volume split by bridge shows corridor concentration.\n\n"
    "## Breakdown and relationship\n"
    "{{grid:2}}\n"
    "{{chart:chart_2}}\n"
    "{{chart:chart_3}}\n"
    "{{/grid}}\n"
)

# Shared step shapes -------------------------------------------------------

_DISCOVER_LINE = WorkflowStep(
    tool="get_model_details",
    args={"model_name": MODEL_LINE},
    expect_substrings=(MODEL_LINE,),
)
_DISCOVER_BAR = WorkflowStep(
    tool="get_model_details",
    args={"model_name": MODEL_BAR},
    expect_substrings=(MODEL_BAR,),
)
_DISCOVER_SCATTER = WorkflowStep(
    tool="get_model_details",
    args={"model_name": MODEL_SCATTER},
    expect_substrings=(MODEL_SCATTER,),
)
_DESCRIBE_LINE = WorkflowStep(
    tool="describe_table",
    args={"table": MODEL_LINE},
    expect_substrings=("volume_usd",),
    forbid_substrings=("Error:",),
)
_EDA_PLAIN = WorkflowStep(
    tool="execute_query",
    args={"sql": EDA_SQL_PLAIN},
    forbid_substrings=("Error:",),
)
_EDA_STATISTICAL = WorkflowStep(
    tool="execute_query",
    args={"sql": EDA_SQL_STATISTICAL},
    forbid_substrings=("Error:",),
)
_CHARTS_BATCH_3 = WorkflowStep(
    tool="generate_charts",
    args={"charts": [LINE_CHART_SPEC, BAR_CHART_SPEC, SCATTER_CHART_SPEC]},
    expect_substrings=("Generated 3/3 charts", "chart_3"),
    forbid_substrings=("Failed (",),
)
_REPORT_OK = WorkflowStep(
    tool="generate_report",
    args={"title": REPORT_TITLE, "content_markdown": REPORT_MARKDOWN},
    expect_substrings=("Report generated",),
    forbid_substrings=("Error:",),
)

# The W2/W5 chart-tier flow (chart-mode preflight -> lite discovery -> chart).
_CHART_TIER_FLOW = (
    WorkflowStep(
        tool="preflight_analytics_request",
        args={"query": UNCOVERED_CHART_QUERY, "mode": "chart"},
        route_expect="semantic_coverage_gap",
    ),
    WorkflowStep(
        tool="search_models",
        args={"query": DISCOVERY_QUERY, "limit": 10},
        expect_substrings=("Found",),
    ),
    _DISCOVER_LINE,
    _DESCRIBE_LINE,
    WorkflowStep(
        tool="generate_charts",
        args={"charts": [LINE_CHART_SPEC]},
        # Chart mode delivers the model-inline render payload plus the batch
        # summary table (whose "Chart ID" header column carries the ids).
        expect_substrings=(
            "RENDER THESE CHARTS INLINE",
            "Generated 1/1 charts",
            "Chart ID",
            "chart_1",
        ),
    ),
)


# ---------------------------------------------------------------------------
# The seven workflows
# ---------------------------------------------------------------------------

WORKFLOW_CASES: tuple[WorkflowCase, ...] = (
    WorkflowCase(
        id="workflows/w1_quick_answer_semantic",
        tier="quick_answer",
        optimal_calls=2,
        steps=(
            WorkflowStep(
                tool="find",
                args={"query": COVERED_ANSWER_QUERY, "mode": "answer"},
                route_expect="semantic_ready",
                expect_substrings=(
                    '"tool": "query_metrics"',
                    COVERED_TOP_METRIC,
                ),
            ),
            WorkflowStep(
                tool="query_metrics",
                args={"metrics": [COVERED_TOP_METRIC]},
                forbid_substrings=("Error:",),
            ),
        ),
        notes=(
            "Covered scalar question. find(answer) routes semantic_ready and "
            "pre-fills query_metrics; no preflight is needed in answer mode."
        ),
    ),
    WorkflowCase(
        id="workflows/w2_single_chart_fast",
        tier="single_chart",
        optimal_calls=5,
        steps=_CHART_TIER_FLOW,
        notes=(
            "Uncovered chart ask (route semantic_coverage_gap -> raw path "
            "legal). Chart-tier gate needs only 1 get_model_details "
            "(MIN_MODELS_DETAILED_LITE); generate_charts renders the inline "
            "visual answer and the workflow STOPS — no report."
        ),
    ),
    WorkflowCase(
        id="workflows/w3_full_report_fast_path",
        tier="full_report",
        optimal_calls=11,
        steps=(
            WorkflowStep(
                tool="preflight_analytics_request",
                args={"query": UNCOVERED_REPORT_QUERY, "mode": "report"},
                route_expect="semantic_coverage_gap",
            ),
            WorkflowStep(
                tool="search_models",
                args={"query": DISCOVERY_QUERY, "limit": 15},
                expect_substrings=("Found 15 model(s)",),
            ),
            _DISCOVER_LINE,
            _DISCOVER_BAR,
            _DISCOVER_SCATTER,
            _DESCRIBE_LINE,
            _EDA_PLAIN,
            _EDA_STATISTICAL,
            WorkflowStep(
                tool="exclude_all_discovered_except",
                args={
                    "keep": list(PINNED_MODELS),
                    "reason": "only the three pinned bridge-flow models are in scope",
                },
                expect_substrings=("Excluded",),
                forbid_substrings=("Error:",),
            ),
            _CHARTS_BATCH_3,
            _REPORT_OK,
        ),
        notes=(
            "The minimum-cost clean full_report run (Fast Path SOP): one call "
            "per gate plus one coverage sweep. After the report the session "
            "state must be reset (search_models_count == 0)."
        ),
    ),
    WorkflowCase(
        id="workflows/w4_gate_violation_probe",
        tier="probe",
        optimal_calls=0,
        sse_safe=False,
        steps=(
            WorkflowStep(
                tool="generate_report",
                args={"title": "Cold probe", "content_markdown": "No charts here."},
                expect_block=True,
                expect_substrings=(
                    "Report quality gate failed",
                    "preflight_analytics_request",
                ),
            ),
            WorkflowStep(
                tool="generate_charts",
                args={"charts": [LINE_CHART_SPEC]},
                expect_block=True,
                # ALL unmet prerequisites must arrive in ONE response.
                expect_substrings=(
                    "**Analysis depth check failed:**",
                    "search_models",
                    "get_model_details",
                    "describe_table",
                ),
            ),
            WorkflowStep(
                tool="preflight_analytics_request",
                args={"query": UNCOVERED_REPORT_QUERY_3, "mode": "report"},
                route_expect="semantic_coverage_gap",
            ),
            WorkflowStep(
                tool="generate_report",
                # No longer blocked. A chart shortfall means the report is
                # THIN, not wrong, so it renders with a "Known limitations"
                # section instead of refusing. Refusing here is what made a
                # real session abandon a finished analysis and write markdown
                # files; the routing prerequisite in step 1 still blocks,
                # because that one changes which tools you use next.
                args={"title": "Cold probe 2", "content_markdown": "Still no charts."},
                expect_block=False,
            ),
        ),
        notes=(
            "Deliberate violations. Step 1 must still block: preflight is a "
            "routing prerequisite. Step 4 must NOT block: an unmet composition "
            "requirement is disclosed in the artifact. meta.blocks records, "
            "per block message, its size and whether it is actionable (names "
            "at least one registered tool in backticks). optimal_calls is 0: "
            "the whole case is waste by construction."
        ),
    ),
    WorkflowCase(
        id="workflows/w5_tier_discipline_probe",
        tier="probe",
        optimal_calls=5,
        sse_safe=False,
        steps=_CHART_TIER_FLOW
        + (
            WorkflowStep(
                tool="generate_report",
                args={
                    "title": "Tier escalation probe",
                    "content_markdown": "{{chart:chart_1}}",
                },
                expect_block=True,
                expect_substrings=("not routed as a report",),
            ),
        ),
        notes=(
            "Chart-mode request escalated to generate_report must hard-block "
            "(REPORT_REQUIRES_EXPLICIT_MODE): the chart IS the deliverable."
        ),
    ),
    WorkflowCase(
        id="workflows/w6_coverage_sweep_recovery",
        tier="full_report",
        # The pinned recovery flow is exactly 13 calls: the 10-step W3 flow
        # without its coverage sweep, plus blocked report + 2 sweeps + retry.
        optimal_calls=13,
        sse_safe=False,
        steps=(
            WorkflowStep(
                tool="preflight_analytics_request",
                args={"query": UNCOVERED_REPORT_QUERY_2, "mode": "report"},
                route_expect="semantic_coverage_gap",
            ),
            WorkflowStep(
                tool="search_models",
                args={"query": DISCOVERY_QUERY, "limit": 15},
                expect_substrings=("Found 15 model(s)",),
            ),
            _DISCOVER_LINE,
            _DISCOVER_BAR,
            _DISCOVER_SCATTER,
            _DESCRIBE_LINE,
            _EDA_PLAIN,
            _EDA_STATISTICAL,
            _CHARTS_BATCH_3,
            WorkflowStep(
                tool="generate_report",
                args={"title": REPORT_TITLE, "content_markdown": REPORT_MARKDOWN},
                # Coverage is a composition requirement: an unused discovery
                # makes the report narrow, not wrong. It ships with the
                # shortfall disclosed, and the reply tells the caller so it can
                # choose to sweep and regenerate at full depth.
                expect_block=False,
                expect_substrings=(
                    "disclosed limitation",
                    "Discovered-but-unused models:",
                ),
            ),
            WorkflowStep(
                tool="exclude_models_by_prefix",
                args={
                    "prefix": "api_bridges_",
                    "reason": "api tier duplicates the fct/int models already queried",
                },
                expect_substrings=("Excluded",),
                forbid_substrings=("Error:",),
            ),
            WorkflowStep(
                tool="exclude_all_discovered_except",
                args={
                    "keep": list(PINNED_MODELS),
                    "reason": "remaining discovery out of scope for this report",
                },
                expect_substrings=("Excluded",),
                forbid_substrings=("Error:",),
            ),
            _REPORT_OK,
        ),
        notes=(
            "W3 without the coverage sweep: generate_report SHIPS with the "
            "discovered-but-unused shortfall disclosed rather than refusing, "
            "then two sweep calls let it regenerate at full depth. This case "
            "used to assert a block; coverage is a composition requirement, so "
            "an unused discovery makes the report narrow, not wrong. "
            "meta.recovery_calls counts the calls between the disclosed "
            "shipment and the clean one."
        ),
    ),
    WorkflowCase(
        id="workflows/w7_raw_fallback_answer",
        tier="quick_answer",
        optimal_calls=4,
        steps=(
            WorkflowStep(
                tool="find",
                args={"query": UNCOVERED_ANSWER_QUERY, "mode": "answer"},
                route_expect="semantic_coverage_gap",
                expect_substrings=('"tool": "discover_models"',),
            ),
            WorkflowStep(
                tool="discover_models",
                args={"query": DISCOVERY_QUERY, "detail_top_n": 5},
                expect_substrings=("Expanded",),
                forbid_substrings=("Error:",),
            ),
            _DESCRIBE_LINE,
            WorkflowStep(
                tool="execute_query",
                args={"sql": ANSWER_SQL},
                forbid_substrings=("Error:",),
            ),
        ),
        notes=(
            "Uncovered answer question: find routes semantic_coverage_gap and "
            "recommends raw discovery; the whole flow must run block-free."
        ),
    ),
)
