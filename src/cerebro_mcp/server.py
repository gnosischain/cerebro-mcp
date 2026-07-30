import logging
import os
import time
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro_mcp.runtime import runtime_state
from cerebro_mcp.runtime.bootstrap import (
    ensure_writable_dir,
    init_ssl_trust,
    validate_remote_transport_auth,
)
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.loaders.catalog import catalog
from cerebro_mcp.config import settings
from cerebro_mcp.loaders.docs import docs_index
from cerebro_mcp.loaders.manifest import manifest
from cerebro_mcp.runtime.observability import (
    PrometheusMiddleware,
    log_event,
    metrics_response,
    observe_report_token_auth,
    setup_logging,
)
from cerebro_mcp.research.store import ResearchStore
from cerebro_mcp.loaders.semantic import semantic_runtime
from cerebro_mcp.tools.analytics.query import register_query_tools
from cerebro_mcp.tools.analytics.schema import register_schema_tools
from cerebro_mcp.tools.analytics.dbt import register_dbt_tools
from cerebro_mcp.tools.analytics.lineage_graph import register_lineage_graph_tools
from cerebro_mcp.tools.analytics.metadata import register_metadata_tools
from cerebro_mcp.tools.analytics.agent_knowledge import register_agent_knowledge_tools
from cerebro_mcp.resources.context import register_resources
from cerebro_mcp.resources.reference import register_reference_resources
from cerebro_mcp.prompts.templates import register_prompts
from cerebro_mcp.tools.analytics.query_async import register_async_query_tools
from cerebro_mcp.tools.analytics.saved_queries import register_saved_query_tools
from cerebro_mcp.tools.visualization.charts import register_visualization_tools
from cerebro_mcp.tools.research.research import register_research_tools
from cerebro_mcp.tools.semantic.semantic import register_semantic_tools
from cerebro_mcp.tools.governance.reasoning import (
    install_auto_tool_tracing,
    register_reasoning_tools,
)
from cerebro_mcp.tools.governance.agents import register_agent_tools
from cerebro_mcp.tools.visualization.dashboard_builder import register_dashboard_tools
from cerebro_mcp.tools.analytics.custom_queries import register_custom_query_tools
from cerebro_mcp.tools.analytics.sandbox import register_sandbox_tools
from cerebro_mcp.tools.workflow.resume import register_workflow_resume_tools
from cerebro_mcp.tools.governance.cross_check import register_cross_check_tools
from cerebro_mcp.tools.storyteller.storyteller import register_storyteller_tools
from cerebro_mcp.tools.visualization.mini_apps import (
    register_load_tools_tool,
    register_mini_app_infra,
)
from cerebro_mcp.tools.visualization.web_apps import register_web_app_routes
from cerebro_mcp.tools.web3.contract_explorer import register_contract_explorer_tools
from cerebro_mcp.tools.visualization.metric_lab import register_metric_lab_tools
from cerebro_mcp.tools.visualization.report_studio import register_report_studio_tools
from cerebro_mcp.tools.semantic.graph_explorer import register_graph_explorer_tools
from cerebro_mcp.tools.visualization.dev_apps import register_dev_mini_apps
from cerebro_mcp.tools.semantic.data_catalog import register_data_catalog_tools
from cerebro_mcp.tools.visualization.cow_explorer import register_cow_explorer_tools
from cerebro_mcp.tools.visualization.governance_explorer import register_governance_tools
from cerebro_mcp.tools.semantic.find import register_find_tool
from cerebro_mcp.tools.analytics.list_unifier import register_list_tool
from cerebro_mcp.tools.web3.rpc import register_rpc_tools
from cerebro_mcp.tools.web3.rpc_scan import register_rpc_scan_tools
from cerebro_mcp.tools.visualization.grafana import register_grafana_tools


runtime_state.ssl_trust_injected = init_ssl_trust()
RESEARCH_DIR = Path(settings.CEREBRO_RESEARCH_DIR).expanduser()
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "cerebro-mcp",
    host="0.0.0.0",
    instructions=(
        "Gnosis Chain data platform MCP server.\n\n"

        "RESPONSE RULES (ALWAYS FOLLOW):\n"
        "1. After a full report-mode `generate_report`, summarize insights and ask if they want "
        "the HTML exported (via `export_report`) or converted to docx/pdf/pptx. "
        "For lightweight visual answers, just summarize the insight.\n"
        "2. No emojis or Unicode symbols — use clean, professional markdown only.\n\n"

        "DBT ENGINEERING WORK (changing/backfilling/reviewing dbt models):\n"
        "- Call `get_dbt_change_context(models=...)` BEFORE touching any dbt model — it "
        "returns the model's resolved contract (grain, invariants), known hazards with "
        "status, lineage impact, and the safe reprocess runbook. "
        "`search_dbt_knowledge(query)` finds the dbt repo's lesson records by symptom "
        "(wipes, duplicates, negative balances, stale snapshots). Both are read-only; "
        "on-chain verification stays with the RPC/contract tools.\n\n"

        "CEREBRO-MCP ENGINEERING WORK (changing THIS repo's own code):\n"
        "- Call `get_cerebro_change_context(paths=...)` BEFORE editing — it returns the "
        "rules and known hazards for that layer, which guides to read, and how to "
        "validate. `search_cerebro_knowledge(query)` finds this repo's lesson records by "
        "symptom (a query returning zero rows with no error, code 241/184, a UI change "
        "that does not appear, a gate that never fires). This is a SEPARATE corpus from "
        "the dbt one above: use dbt's tools for a dbt model, these for cerebro-mcp "
        "itself. Diagnosed a new mistake class? Record it — docs/workflows/incident.md.\n\n"

        "DEFAULT DISCOVERY PATH:\n"
        "- For almost any analytical question, call `find(query, mode=\"answer\")` FIRST. "
        "It routes in one call to the right tools, metrics, and models and returns a "
        "pre-filled `recommended_action`. For a plain answer, follow it straight to "
        "`query_metrics` — NO `preflight_analytics_request` is needed in answer mode. "
        "Only use `mode=\"chart\"`/`\"report\"` on `find` when the user asked for a chart/report; "
        "those modes route you through preflight so the chart/report gate is respected.\n\n"

        "ENFORCEMENT GATES (CANNOT BE BYPASSED):\n"
        "- `preflight_analytics_request` is REQUIRED only before `generate_chart` / "
        "`generate_charts` / `generate_report` (the chart/report hard gates). "
        "Plain answer-mode questions do NOT need preflight — use `find` -> `query_metrics`.\n"
        "- When `SEMANTIC_ENABLED=true`, chart/report workflows MUST call "
        "`preflight_analytics_request` first.\n"
        "  - If the route is `semantic_ready`, use semantic tools "
        "(`quick_metric_chart`, `generate_metric_charts`, `query_metrics`) "
        "before any raw charting.\n"
        "  - If the route is `hybrid_ready`, use semantic tools for covered "
        "topics and raw SQL for uncovered topics. Both semantic and raw "
        "charts can coexist in one report.\n"
        "  - If the route is `semantic_coverage_gap`, use raw SQL directly.\n"
        "- `generate_charts` (batch) and `generate_chart` (single) are BLOCKED until you run "
        "`search_models` (or `discover_models`), explore at least 3 models via "
        "`get_model_details`, AND verify at least 1 table via `describe_table`.\n"
        "- `generate_report` is BLOCKED until: (a) >= 3 charts created (with trend and/or "
        "breakdown), (b) 2+ exploratory queries run, (c) at least 1 statistical query "
        "(quantiles/stddev/corr), (d) at least 1 chart with series_field or pie/treemap/"
        "heatmap/sankey type (dimensional breakdown), (e) at least 1 scatter/heatmap chart "
        "OR correlation query (relational analysis).\n"
        "- For quick ad-hoc plots, use `quick_chart` instead — no gates required.\n\n"

        "CUSTOM TOOL ROUTING:\n"
        "- Before writing raw SQL via `execute_query`, check if a custom parameterized tool "
        "exists for the user's request (e.g., `get_validator_balance_history`, "
        "`get_token_transfers_for_address`, `get_bridge_flows_by_token`). "
        "Always prefer custom tools over raw SQL for common domain queries.\n"
        "- Use `list_custom_tools` to see available parameterized tools.\n"
        "- Raw `execute_query` is reserved for exploratory analysis, complex joins, and "
        "novel research questions where no custom tool exists.\n\n"

        "RPC SCAN ROUTING (on-chain forensics; tools present when RPC_SCAN_ENABLED):\n"
        "- Two data planes exist. ClickHouse dbt models = indexed history (aggregates, "
        "USD, trends). rpc_scan_* tools = the chain itself (pinned-block state, traces, "
        "storage, bytecode, data not yet indexed).\n"
        "- Use rpc_scan_logs / rpc_batch_call / rpc_read_storage / rpc_get_code / "
        "rpc_scan_traces / rpc_find_block when ANY of: (1) you need state AT a pinned "
        "block across many addresses, (2) the events/contracts are not decoded by any "
        "dbt model, (3) the window is too recent for dbt, (4) you need storage slots, "
        "bytecode/proxy identity, or native-value traces (dbt does not cover these), "
        "(5) independent verification of a pipeline number.\n"
        "- Results land in scratch.rpc_* ClickHouse tables — ALWAYS continue analysis "
        "by joining them to dbt models via `execute_query`; never re-scan to "
        "re-aggregate. Count scratch tables with uniqExact/FINAL (ReplacingMergeTree).\n"
        "- Address sets: <=500 inline; otherwise pass address_sql (any dbt model or a "
        "previous scan's scratch table works as the source).\n"
        "- Single-address current reads stay on `contract_call_function`; single-tx "
        "decoding stays on `contract_decode_transaction_input` / "
        "`contract_decode_receipt_logs` / `rpc_trace_transaction`.\n"
        "- Pin anchor blocks FIRST (`rpc_find_block` kind=timestamp), then sweep, then "
        "classify in SQL.\n\n"

        "QUERY EFFICIENCY:\n"
        "- Prefer pre-aggregated dbt models (int_*, fct_*, api_*) over raw source tables. "
        "Raw tables (execution.logs, execution.transactions, consensus.attestations) have "
        "billions of rows — only query them as a last resort and always with tight filters "
        "(date range, specific address, LIMIT).\n"
        "- For simple factual lookups (address transfers, balances, validator info), "
        "skip the full discover/search/describe ceremony. Go directly to the custom tool "
        "or a single execute_query against the known dbt model.\n\n"

        "NUMBER VERIFICATION (MANDATORY):\n"
        "- Before reporting computed numbers (sums, nets, percentages, totals), "
        "call `verify_numbers` with your claims, formulas, and component values.\n"
        "- Provide the formula you used (e.g., 'received - sent') and the component "
        "values so the tool can independently verify your arithmetic.\n"
        "- Include a check_query when possible to cross-reference against an "
        "independent dbt model (e.g., balance model to verify net transfers).\n"
        "- If any claim fails: DO NOT report it. Fix the computation first.\n"
        "- When reporting verified numbers, note: 'verified via verify_numbers'.\n\n"

        "OUTPUT FORMAT RULES:\n"
        "You have two output modes. ALWAYS use the correct mode:\n\n"

        "MODE 1: VISUAL OUTPUT (INTERACTIVE UI)\n"
        "TRIGGER: User asks for a chart, plot, graph, report, dashboard, or visual output.\n"
        "NOTE: 'how many ... over time?' and daily/weekly/monthly trend questions are CHART "
        "requests, NOT reports. Never escalate a chart request into a report.\n"
        "\n"
        "MODE 1A: PLAIN CHART REQUEST (default for 'show me / plot / chart X'):\n"
        "  1. Call `preflight_analytics_request(query, mode=\"chart\")` first.\n"
        "  2. Follow the route: when `semantic_ready`, use semantic charts "
        "(`generate_metric_charts`, or `quick_metric_chart` for one-offs); when the route is "
        "`semantic_coverage_gap` / `semantic_disabled` / `semantic_unavailable`, use raw SQL "
        "(`discover_models` -> `describe_table` -> `execute_query` -> `generate_charts` / "
        "`quick_chart`); when `hybrid_ready`, mix both.\n"
        "  3. Produce the chart(s) the user asked for — NO minimum count — then STOP. Do NOT "
        "call `generate_report`. In your reply, present the chart(s) with a one-line insight.\n"
        "\n"
        "MODE 1B: REPORT / DASHBOARD / ANALYSIS (only when the user explicitly asks for one):\n"
        "  1. Call `preflight_analytics_request(query, mode=\"report\")` first.\n"
        "  2. Select tools by route exactly as in 1A step 2. On the raw path, include at least "
        "1 statistical query and 1 correlation query (medians/percentiles over means).\n"
        "  3. Batch ALL chart specs in ONE call (`generate_metric_charts` and/or "
        "`generate_charts`; in hybrid mode, both). Do NOT use individual `generate_chart` for "
        "reports. Minimum 3 charts: KPIs + trends + dimensional breakdowns; at least 1 with "
        "series_field, plus a scatter/heatmap for relationships. KPI `numberDisplay` charts "
        "come from single-row SQL only (e.g. `ORDER BY month DESC LIMIT 1`). Multi-metric trend "
        "charts need comma-separated `y_field` or long form with `series_field`.\n"
        "  4. Write markdown with `{{chart:CHART_ID}}` placeholders, then call "
        "`generate_report(title, content_markdown)`.\n"
        "  5. In your reply: summarize insights, offer `export_report` for HTML download, ask "
        "about format conversion. Do NOT echo the report markdown text.\n\n"

        "MODE 2: QUICK QUERIES & RAW DATA (MARKDOWN OUTPUT)\n"
        "TRIGGER: User asks for raw data, numbers, or a simple text explanation WITHOUT charts.\n"
        "- Default here for plain analytical questions, including time-series questions, "
        "unless the user explicitly asks for visual output.\n"
        "- Workflow: if this is a business-metric question and semantic is enabled, call "
        "`find(query, mode=\"answer\")` first, then follow its `recommended_action` — "
        "usually `query_metrics` directly when the route is `semantic_ready` (NO preflight "
        "needed in answer mode). Otherwise query data and output a Markdown response.\n"
        "- Answer mode MAY include one or two supporting visualizations. If you already "
        "have an answer-mode or chart-mode chart, you may render it in the visual UI "
        "without satisfying the full report-quality gate.\n"
        "- Structure: ### Objective → ### Query (SQL block) → ### Results (Markdown table) → "
        "### Key Insights.\n\n"

        "MODE 3: LONG-HORIZON RESEARCH\n"
        "TRIGGER: User asks for a deep-dive investigation, hypothesis-driven study, "
        "or multi-step research project.\n"
        "WORKFLOW:\n"
        "  1. `start_research_project`\n"
        "  2. `plan_research_phase(..., \"mapping\")`\n"
        "  3. `execute_research_phase(..., \"mapping\")`\n"
        "  4. Use `capture_schema_snapshot` and `record_research_memory`\n"
        "  5. Repeat for `hypothesis`\n"
        "  6. During `execution`, use `execute_query(..., research_project_id=..., "
        "persist_result=True)` for synchronous evidence\n"
        "  7. Attach charts/reports with `attach_research_evidence`\n"
        "  8. `verify_research_phase`\n"
        "  9. `prepare_peer_review`\n"
        "  10. Apply `conduct_research_peer_review`\n"
        "  11. `record_peer_review`\n"
        "  12. `publish_research_report`\n"
        "CRITICAL:\n"
        "- Do NOT skip phases.\n"
        "- Do NOT use `save_query` for research evidence.\n"
        "- Do NOT publish before verification and peer review.\n\n"

        "STANDARD OPERATING PROCEDURE:\n"
        "1. DISCOVER: For analytical questions, call `find(query, mode=\"answer\")` first "
        "(use `mode=\"chart\"`/`\"report\"` when a chart/report was requested). Follow its "
        "`recommended_action`: for an answer-mode `semantic_ready`/`hybrid_ready` route go "
        "straight to `query_metrics`; for chart/report modes it routes you through "
        "`preflight_analytics_request`. For uncovered topics (or `semantic_coverage_gap`), "
        "use `discover_models(query, detail_top_n=5)` for combined search + details in one "
        "call. Only use separate `search_models` + `get_model_details` when you need more "
        "than 5 models detailed.\n"
        "2. EXPLORE: Ensure at least 3 models explored via `get_model_details` "
        "(discover_models counts). Map lineage. Identify all dimensions "
        "(token, action, user segment). Use int_* models when marts lack needed breakdowns.\n"
        "3. VERIFY: Call `describe_table` or `get_model_details` before writing SQL.\n"
        "4. EDA (MANDATORY): Run distribution queries BEFORE final analysis. "
        "Use quantiles(0.25, 0.5, 0.75), stddevPop(), min/max, count() to assess "
        "data shape and outliers. Must include at least 1 statistical query and 1 correlation "
        "query — generate_report REJECTS without them.\n"
        "5. EXECUTE: Write ClickHouse SQL. Use fully-qualified table names and partition filters.\n"
        "6. BATCH CHART: Use `generate_charts` (batch tool) with ALL chart specs in ONE call. "
        "Do NOT call `generate_chart` individually for reports.\n"
        "7. REPORT DEPTH: Reports MUST include KPIs + time-series trends + dimensional breakdowns. "
        "KPI `numberDisplay` charts must be backed by single-row SQL, never raw time series. "
        "For monthly summaries, latest-period KPIs should use `ORDER BY month DESC LIMIT 1`. "
        "Multi-metric trend charts must either use comma-separated `y_field` values or "
        "long-form data with `series_field`. At least 1 chart must use series_field "
        "for dimensional breakdown. "
        "At least 1 scatter/heatmap chart or correlation query for relational analysis.\n"
        "8. STATS NEED CHARTS: Every statistical claim must have a supporting chart. "
        "Do NOT write 'volume was $2.15M' without a numberDisplay or trend chart. "
        "Text annotates charts; charts carry the data.\n"
        "9. REPORT LAYOUT: Use {{grid:N}}...{{/grid}} for side-by-side charts.\n"
        "   KPIs → {{grid:3}} or {{grid:4}}. Breakdowns → {{grid:2}}. Trends → full-width.\n"
        "   Text goes BETWEEN chart groups: KPI grid → commentary → trend → commentary → breakdown grid.\n"
        "10. MULTI-DIMENSIONAL: Do NOT analyze metrics in isolation. Compute corr() between "
        "metric pairs, use simpleLinearRegression(y, x) for relationships, scatter charts for "
        "strong correlations (|r| > 0.5). Look at dimensional interactions (GROUP BY dim_a, dim_b).\n\n"

        "GNOSIS CHAIN SPECIFICS:\n"
        "- Call `get_platform_constants()` for infrastructure details.\n"
        "- Key: Block time 5s, xDAI (gas), GNO (staking), Chain ID 100.\n\n"

        "MODE 4: STORYTELLER (OPT-IN, DO NOT AUTO-UPGRADE)\n"
        "TRIGGER: User EXPLICITLY asks for a story, narrative, memo, "
        "executive brief, pitch, recommendation, decision artifact, or uses "
        "`/storyteller`. Do NOT silently upgrade a standard report request "
        "into a storyteller run; ask if ambiguous.\n"
        "WORKFLOW (multi-agent, gated):\n"
        "  1. `storyteller_start_session`\n"
        "  2. `get_agent_persona(\"storyteller_orchestrator\")` and "
        "`get_agent_persona(\"storyteller_context\")`; collect audience, "
        "required action, mechanism, tone, background, biases, weakens_case.\n"
        "  3. `storyteller_record_context_brief(...)` — gate for all downstream agents.\n"
        "  4. Explore data with the normal Cerebro tools to gather evidence.\n"
        "  5. `get_agent_persona(\"storyteller_narrative\")`; write one-sentence "
        "big idea with stakes. `storyteller_record_big_idea(...)`.\n"
        "  6. Build setup -> tension -> resolution storyboard. "
        "`storyteller_record_storyboard(...)`.\n"
        "  7. `get_agent_persona(\"storyteller_visual_designer\")`; one "
        "`storyteller_record_visual_spec` per scene. Relationship-first. "
        "One focal element per scene.\n"
        "  8. Render charts with `generate_charts` (standard tool) and attach "
        "`chart_id` values back via `storyteller_record_visual_spec`.\n"
        "  9. `get_agent_persona(\"storyteller_writer\")`; assemble the final "
        "story with `{{chart:CHART_ID}}` placeholders and grid directives. "
        "`storyteller_record_final_story(title, content_markdown)`.\n"
        "  10. `get_agent_persona(\"storyteller_critic\")`; run four clarity "
        "tests and four audits. `storyteller_run_clarity_checks(checks=[...])`. "
        "On failure, loop back to the earliest failing phase.\n"
        "  11. `get_agent_persona(\"storyteller_accessibility\")`; check "
        "colorblind palette, contrast, language, tone. "
        "`storyteller_record_accessibility_pass(passed, notes)`.\n"
        "  12. `storyteller_generate_story_report()` to render the final artifact.\n"
        "GATES: context_brief -> big_idea -> storyboard -> visual_specs -> "
        "final_story -> review -> accessibility -> handoff. Skipping any gate "
        "raises an error. Standard mode is unaffected.\n"
    ),
)

# Initialize ClickHouse connection manager
ch = ClickHouseManager()
research_store = ResearchStore(str(RESEARCH_DIR))

# How long a ClickHouse readiness result stays valid. Well under the probe's
# failureThreshold window so a genuine outage is still detected promptly.
_HEALTH_CACHE_TTL_SECONDS = 5.0

# Register all tools
register_query_tools(mcp, ch, research_store)
register_schema_tools(mcp, ch)
register_dbt_tools(mcp)
register_lineage_graph_tools(mcp)
register_metadata_tools(mcp, ch)
register_agent_knowledge_tools(mcp)

# Register resources and prompts
register_resources(mcp)
register_reference_resources(mcp, ch)
register_prompts(mcp)

# Register advanced tools
register_async_query_tools(mcp, ch)
register_saved_query_tools(mcp, ch)
register_visualization_tools(mcp, ch)
register_research_tools(mcp, ch, research_store)
register_semantic_tools(mcp, ch, research_store)
register_reasoning_tools(mcp)
register_agent_tools(mcp)
register_dashboard_tools(mcp)
register_custom_query_tools(mcp, ch)
if settings.SANDBOX_ENABLED:
    register_sandbox_tools(mcp, ch)
if settings.WORKFLOW_RESUME_TOOLS_ENABLED:
    register_workflow_resume_tools(mcp)
register_cross_check_tools(mcp, ch)
register_storyteller_tools(mcp, ch)
register_rpc_tools(mcp, ch)
# Bulk RPC scans write into the ClickHouse scratch DB — opt-in because the
# deployment user needs CREATE/INSERT/DROP grants there (see config.py).
if settings.RPC_SCAN_ENABLED:
    register_rpc_scan_tools(mcp, ch)

# Mini-app platform: install the visibility filter first so subsequent
# app registrations can mark hydration tools as app-only.
register_mini_app_infra(mcp, ch)
register_contract_explorer_tools(mcp, ch)
register_metric_lab_tools(mcp, ch)
register_graph_explorer_tools(mcp, ch)
register_data_catalog_tools(mcp, ch)
register_cow_explorer_tools(mcp, ch)
register_governance_tools(mcp, ch)
register_report_studio_tools(mcp, ch)
# Dev-only apps (portfolio, model lineage) — absent unless
# DEV_MINI_APPS_ENABLED (see tools/visualization/dev_apps.py).
register_dev_mini_apps(mcp, ch)

# Standalone web-app delivery: serve the mini-apps as plain browser URLs
# (GET /app/{id}) with HTTP tool dispatch (POST /app/{id}/api/tool/{tool}).
# Must run after the mini-app registrations above so the tool registry is full.
register_web_app_routes(mcp)

# Grafana dashboard publishing (no-op unless GRAFANA_TOOLS_ENABLED).
register_grafana_tools(mcp, ch)

# `list(kind=...)` unifier (Phase 4): one read-only listing front door that
# delegates to the same helpers the legacy `list_*` shims call. Register after
# the tools whose `list_*_impl` helpers it imports are defined.
register_list_tool(mcp, ch)

# `find` MUST register LAST: it builds its tool corpus lazily from the full
# registered-tool map on first call, so every other tool must already be
# registered for the corpus to be complete.
register_find_tool(mcp)

# `load_tools` un-hides advanced tools when LEAN_CORE_ENABLED is on. Register
# after the full surface exists so it can validate names against every tool.
register_load_tools_tool(mcp)

install_auto_tool_tracing(mcp)


@mcp.custom_route("/livez", methods=["GET"])
async def liveness_check(request: Request) -> JSONResponse:
    """Process liveness ONLY — deliberately does no external I/O.

    This is what the Kubernetes ``livenessProbe`` must point at. ``/health``
    checks ClickHouse and 503s when it is unreachable; wiring liveness to that
    means a ClickHouse blip lasting longer than
    ``periodSeconds * failureThreshold`` gets the container killed, taking the
    MCP server down for a dependency outage it could otherwise ride out. If this
    handler answers at all, the event loop is alive and the process should live.
    """
    return JSONResponse({"status": "ok"}, status_code=200)


def _clickhouse_health() -> tuple[bool, str]:
    """Probe ClickHouse for the readiness check, with a short TTL cache.

    Cached because readiness runs every ``periodSeconds`` forever; without it
    the probe issues a query every few seconds for the life of the pod.
    """
    now = time.monotonic()
    cached = runtime_state.clickhouse_health
    if cached is not None and (now - cached[0]) < _HEALTH_CACHE_TTL_SECONDS:
        return cached[1], cached[2]
    try:
        info = ch.get_server_info("dbt")
        result = (True, info["version"])
    except Exception as exc:
        result = (False, str(exc))
    runtime_state.clickhouse_health = (now, result[0], result[1])
    return result


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Readiness: reports whether this pod can actually serve queries.

    ``ch.get_server_info`` is a BLOCKING ClickHouse round-trip, so it runs on a
    worker thread rather than inline — an inline call stalls the single event
    loop on every probe, which at a 10s readiness period is a permanent
    low-grade tax and a hard stall whenever ClickHouse is slow.
    """
    connected, detail = await anyio.to_thread.run_sync(_clickhouse_health)
    if connected:
        return JSONResponse(
            {
                "status": "ok",
                "clickhouse_connected": True,
                "clickhouse_version": detail,
                "ssl_trust_injected": runtime_state.ssl_trust_injected,
            },
            status_code=200,
        )
    return JSONResponse(
        {
            "status": "error",
            "clickhouse_connected": False,
            "error": detail,
            "ssl_trust_injected": runtime_state.ssl_trust_injected,
        },
        status_code=503,
    )


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(request: Request) -> Response:
    return metrics_response()


@mcp.custom_route("/reports/{report_id}", methods=["GET"])
async def download_report(request: Request) -> JSONResponse | HTMLResponse:
    """Serve a report HTML file by ID (full UUID or 8-char prefix)."""
    from cerebro_mcp.tools.visualization.charts import _resolve_report

    report_id = request.path_params["report_id"]

    # Auth: accept Bearer header or ?token= query param
    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    if auth_token:
        auth_header = request.headers.get("Authorization", "")
        query_token = request.query_params.get("token", "")

        if auth_header == f"Bearer {auth_token}":
            auth_method = "bearer"
            auth_success = True
        elif query_token == auth_token:
            auth_method = "query_token"
            auth_success = True
        else:
            auth_method = "none"
            auth_success = False

        log_event(
            logger,
            "report_token_auth",
            report_id=report_id,
            auth_method=auth_method,
            success=auth_success,
        )
        observe_report_token_auth(status="success" if auth_success else "denied")

        if not auth_success:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        html, resolved_id, _ = _resolve_report(report_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)

    if html is None:
        return JSONResponse(
            {"error": f"Report '{report_id}' not found"},
            status_code=404,
        )

    return HTMLResponse(content=html)


def main():
    import sys

    setup_logging()
    # Transport selection (precedence: --http > --sse > stdio):
    #   --http / --streamable-http  Streamable HTTP at /mcp (+ legacy /sse).
    #                               The modern, LB-friendly remote transport.
    #   --sse                       Legacy SSE only (/sse + /messages/).
    #   (neither)                   stdio (local single-process).
    if "--http" in sys.argv or "--streamable-http" in sys.argv:
        transport = "streamable-http"
    elif "--sse" in sys.argv:
        transport = "sse"
    else:
        transport = "stdio"
    log_event(logger, "transport_selected", transport=transport)
    ensure_writable_dir(RESEARCH_DIR)
    manifest.load()
    catalog.load()
    docs_index.load()
    semantic_runtime.load()

    # Phase 2: ensure simulation sandboxes are torn down on exit. The
    # periodic sweeper is installed lazily by the first sandbox tool call
    # (so it can grab the running event loop), not here at process boot.
    from cerebro_mcp.runtime.bootstrap import install_sandbox_atexit, init_event_store_sync
    if settings.SANDBOX_ENABLED:
        install_sandbox_atexit()

    # Phase 3: open the workflow event store and run the WorkflowRegistry
    # resume sweep on abandoned workflows from previous crashes. Stale
    # `running` / `waiting_gate` rows get a resume_hint event; rows
    # without a registered handler fall back to `orphaned`. Non-fatal —
    # log and continue if the SQLite db can't be opened, since most tools
    # don't depend on it.
    try:
        counts = init_event_store_sync()
        if isinstance(counts, dict) and any(counts.values()):
            logger.warning(
                "Workflow resume sweep on startup: %s",
                ", ".join(f"{k}={v}" for k, v in counts.items() if v),
            )
    except Exception:
        logger.exception("event store bootstrap failed (non-fatal)")

    if transport == "streamable-http":
        validate_remote_transport_auth(os.environ.get("MCP_AUTH_TOKEN"))
        _run_streamable_http_with_auth()
    elif transport == "sse":
        validate_remote_transport_auth(os.environ.get("MCP_AUTH_TOKEN"))
        _run_sse_with_auth()
    else:
        # Phase 3 multi-tenant: stdio is single-user-per-process. Read
        # CEREBRO_OWNER once at boot and stash the (hashed) identifier in
        # the contextvar for the lifetime of this process. Every workflow
        # written from this stdio session is stamped with this owner.
        # Unset env var → contextvar stays None → workflows go in with
        # owner=NULL (single-tenant fallback, backward compatible).
        from cerebro_mcp.runtime.identity import (
            get_current_owner,
            initial_stdio_owner,
            set_current_owner,
        )
        stdio_owner = initial_stdio_owner()
        if stdio_owner:
            set_current_owner(stdio_owner)
            # Log only the hash prefix — plaintext never enters the log.
            owner_hash = get_current_owner() or ""
            log_event(
                logger, "stdio_owner_set",
                owner_hash_prefix=owner_hash[:12],
            )
        mcp.run(transport="stdio")


class BearerAuthMiddleware:
    """Pure ASGI middleware — compatible with SSE / streaming responses.

    Do NOT subclass ``starlette.middleware.base.BaseHTTPMiddleware`` here:
    its ``body_stream`` buffers the downstream response into an async
    queue and asserts a strict ``http.response.start`` → ``http.response.body``
    sequence, which breaks FastMCP's SSE transport (the long-poll GET
    ``/sse`` stream and the POST-to-``/messages/`` channel).
    """

    def __init__(self, app: ASGIApp, auth_token: str) -> None:
        self.app = app
        self._auth_token = auth_token
        self._expected = f"Bearer {auth_token}".encode("latin-1")

    @staticmethod
    def _path_allows_query_token(path: str) -> bool:
        """Streamable HTTP (`/mcp`) additionally accepts a ``?token=`` param.

        This mirrors the ``/reports`` and ``/app`` handlers: a browser
        navigation — or a Claude Desktop *native* connector whose UI only
        takes a URL and no custom ``Authorization`` header — can then
        authenticate via the query string. The Bearer header stays the
        preferred path (a query token can leak into access logs), so this
        is a fallback, not a replacement.
        """
        return path == "/mcp" or path.startswith("/mcp/")

    def _query_token_ok(self, scope: Scope) -> bool:
        from urllib.parse import parse_qs

        qs = scope.get("query_string", b"").decode("latin-1")
        token = parse_qs(qs).get("token", [""])[0]
        # Plain equality, same as the /reports and /app query-token check.
        return bool(self._auth_token) and token == self._auth_token

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        if (
            path == "/health"
            or path == "/livez"
            or path == "/metrics"
            or path == "/favicon.ico"
            or path == "/"
            or path == "/apps"
            or path.startswith("/reports/")
            or path.startswith("/app/")
        ):
            # These routes either need no auth or do their own (the
            # /reports/, /app/ and app-catalog ("/", "/apps") handlers accept a
            # ?token= query param, which a browser navigation can supply where
            # an Authorization header cannot).
            await self.app(scope, receive, send)
            return

        header_value = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header_value = value
                break

        if header_value != self._expected:
            # /mcp additionally accepts ?token= (see _path_allows_query_token)
            # so a URL-only native connector can still authenticate.
            if not (
                self._path_allows_query_token(path)
                and self._query_token_ok(scope)
            ):
                unauthorized = JSONResponse(
                    {"error": "unauthorized"}, status_code=401
                )
                await unauthorized(scope, receive, send)
                return

        # Phase 3 multi-tenant: scope the per-request owner from the
        # `X-Cerebro-Owner` header. The bearer token authenticates the
        # connection; the owner header carries the identity claim. We
        # do NOT verify the claim here — that's the upstream proxy's
        # job (or, in deployments without a proxy, this is
        # self-attested and the trust model is documented in
        # docs/phase3_resumable_workflows.md). `set_current_owner`
        # hashes before storage; plaintext never persists.
        owner_plain: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"x-cerebro-owner":
                try:
                    owner_plain = value.decode("latin-1").strip() or None
                except Exception:
                    owner_plain = None
                break

        from cerebro_mcp.runtime.identity import (
            reset_current_owner,
            set_current_owner,
        )
        token = set_current_owner(owner_plain)
        try:
            await self.app(scope, receive, send)
        finally:
            # Restore the prior contextvar state — critical so the
            # next request on this worker doesn't inherit the previous
            # caller's owner.
            reset_current_owner(token)


def build_sse_app(auth_token: str | None = None):
    starlette_app = mcp.sse_app()
    if auth_token:
        starlette_app.add_middleware(BearerAuthMiddleware, auth_token=auth_token)
    starlette_app.add_middleware(PrometheusMiddleware)
    return starlette_app


def build_streamable_http_app(
    auth_token: str | None = None, *, include_sse: bool = True
):
    """Return the Streamable HTTP ASGI app (single ``/mcp`` endpoint).

    Streamable HTTP is the modern, load-balancer-friendly MCP transport:
    one endpoint, no long-lived idle stream for a proxy to reap, and — with
    ``STREAMABLE_HTTP_STATELESS`` on (the default) — no per-pod session
    affinity requirement. It is the transport Claude Desktop's *native*
    remote connector speaks, so switching to it lets clients drop the
    fragile ``mcp-remote`` bridge (the usual cause of "the connection keeps
    breaking" against a remote SSE deployment behind an ALB).

    When ``include_sse`` is True (the default for ``--http``), the legacy
    ``/sse`` + ``/messages/`` routes are folded into the SAME app so existing
    ``mcp-remote -> /sse`` clients keep working during migration — a
    zero-downtime cutover rather than a hard switch.

    ``mcp.streamable_http_app()`` already registers every ``@mcp.custom_route``
    (``/health``, ``/metrics``, ``/reports/*``, ``/app/*``, ``/``, ``/apps``)
    and wires the ``StreamableHTTPSessionManager`` lifespan, so here we only
    fold in the SSE routes and add the auth + Prometheus middleware.
    """
    # These must be set BEFORE the app builds its session manager — that
    # happens on the first streamable_http_app() call, which reads
    # mcp.settings.{stateless_http,json_response}.
    mcp.settings.stateless_http = settings.STREAMABLE_HTTP_STATELESS
    mcp.settings.json_response = settings.STREAMABLE_HTTP_JSON_RESPONSE

    starlette_app = mcp.streamable_http_app()

    if include_sse:
        # Fold the legacy SSE transport's own routes (/sse, /messages/) into
        # this app so both transports serve from one process. The custom
        # routes (/health, /, ...) are already present on both, so skip
        # duplicates by path. Appending to router.routes before the server
        # starts is safe — Starlette matches against this list per request.
        sse_app = mcp.sse_app()
        existing = {
            getattr(r, "path", None) for r in starlette_app.router.routes
        }
        for route in sse_app.router.routes:
            if getattr(route, "path", None) not in existing:
                starlette_app.router.routes.append(route)

    if auth_token:
        starlette_app.add_middleware(BearerAuthMiddleware, auth_token=auth_token)
    starlette_app.add_middleware(PrometheusMiddleware)
    return starlette_app


def _run_sse_with_auth():
    """Run SSE transport, optionally wrapped with Bearer token auth."""
    import anyio
    import uvicorn

    os.environ["CEREBRO_TRANSPORT"] = "sse"

    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    validate_remote_transport_auth(auth_token)
    log_event(logger, "auth_middleware_enabled", enabled=bool(auth_token))
    starlette_app = build_sse_app(auth_token)

    # Move trace persistence + the security audit off the single event loop, so
    # concurrent SSE sessions don't serialize behind each other's per-call
    # disk/JSON bookkeeping. No-op unless THINKING_ASYNC_PERSIST is on.
    from cerebro_mcp.tools.governance import reasoning as _reasoning

    _reasoning.start_async_writer()

    async def _serve():
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = int(os.environ.get("FASTMCP_PORT", "8000"))
        log_event(
            logger,
            "sse_server_starting",
            host=host,
            port=port,
        )
        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level=mcp.settings.log_level.lower(),
            log_config=None,
        )
        server = uvicorn.Server(config)
        await server.serve()

    try:
        anyio.run(_serve)
    finally:
        _reasoning.stop_async_writer()


def _run_streamable_http_with_auth():
    """Run the Streamable HTTP transport, dual-served with legacy SSE.

    Mirrors :func:`_run_sse_with_auth` — same auth validation, off-loop
    trace writer, and uvicorn host/port — but serves the ``/mcp`` endpoint
    (plus ``/sse`` + ``/messages/`` for back-compat).
    """
    import anyio
    import uvicorn

    os.environ["CEREBRO_TRANSPORT"] = "streamable-http"

    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    validate_remote_transport_auth(auth_token)
    log_event(
        logger,
        "auth_middleware_enabled",
        enabled=bool(auth_token),
        stateless=settings.STREAMABLE_HTTP_STATELESS,
        json_response=settings.STREAMABLE_HTTP_JSON_RESPONSE,
    )
    starlette_app = build_streamable_http_app(auth_token, include_sse=True)

    # Same off-loop hardening as SSE: keep per-call trace/audit disk writes
    # off the single event loop. No-op unless THINKING_ASYNC_PERSIST is on.
    from cerebro_mcp.tools.governance import reasoning as _reasoning

    _reasoning.start_async_writer()

    async def _serve():
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = int(os.environ.get("FASTMCP_PORT", "8000"))
        log_event(
            logger,
            "streamable_http_server_starting",
            host=host,
            port=port,
            mcp_path=mcp.settings.streamable_http_path,
            sse_dual=True,
        )
        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level=mcp.settings.log_level.lower(),
            log_config=None,
        )
        server = uvicorn.Server(config)
        await server.serve()

    try:
        anyio.run(_serve)
    finally:
        _reasoning.stop_async_writer()


if __name__ == "__main__":
    main()
