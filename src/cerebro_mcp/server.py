import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro_mcp import runtime_state
from cerebro_mcp.bootstrap import (
    ensure_writable_dir,
    init_ssl_trust,
    validate_remote_transport_auth,
)
from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.catalog_loader import catalog
from cerebro_mcp.config import settings
from cerebro_mcp.docs_loader import docs_index
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.observability import (
    PrometheusMiddleware,
    log_event,
    metrics_response,
    observe_report_token_auth,
    setup_logging,
)
from cerebro_mcp.research_store import ResearchStore
from cerebro_mcp.semantic_loader import semantic_runtime
from cerebro_mcp.tools.query import register_query_tools
from cerebro_mcp.tools.schema import register_schema_tools
from cerebro_mcp.tools.dbt import register_dbt_tools
from cerebro_mcp.tools.metadata import register_metadata_tools
from cerebro_mcp.resources.context import register_resources
from cerebro_mcp.resources.reference import register_reference_resources
from cerebro_mcp.prompts.templates import register_prompts
from cerebro_mcp.tools.query_async import register_async_query_tools
from cerebro_mcp.tools.saved_queries import register_saved_query_tools
from cerebro_mcp.tools.visualization import register_visualization_tools
from cerebro_mcp.tools.research import register_research_tools
from cerebro_mcp.tools.semantic import register_semantic_tools
from cerebro_mcp.tools.reasoning import (
    install_auto_tool_tracing,
    register_reasoning_tools,
)
from cerebro_mcp.tools.agents import register_agent_tools
from cerebro_mcp.tools.dashboard_builder import register_dashboard_tools
from cerebro_mcp.tools.custom_queries import register_custom_query_tools
from cerebro_mcp.tools.sandbox import register_sandbox_tools
from cerebro_mcp.tools.workflow_resume import register_workflow_resume_tools
from cerebro_mcp.tools.cross_check import register_cross_check_tools
from cerebro_mcp.tools.storyteller import register_storyteller_tools
from cerebro_mcp.tools.mini_apps import register_mini_app_infra
from cerebro_mcp.tools.token_explorer import register_token_explorer_tools
from cerebro_mcp.tools.metric_lab import register_metric_lab_tools
from cerebro_mcp.tools.yield_opportunities import register_yield_opportunities_tools
from cerebro_mcp.tools.portfolio import register_portfolio_tools
from cerebro_mcp.tools.graph_explorer import register_graph_explorer_tools
from cerebro_mcp.tools.quarterly_review import register_quarterly_review_tools


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

        "ENFORCEMENT GATES (CANNOT BE BYPASSED):\n"
        "- When `SEMANTIC_ENABLED=true`, analytical chart/report workflows MUST call "
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

        "MODE 1: REPORTS & VISUALIZATIONS (INTERACTIVE UI)\n"
        "TRIGGER: User EXPLICITLY asks for a report, chart, plot, graph, dashboard, "
        "or visual output.\n"
        "NOTE: Questions like 'how many ... over time?' or requests mentioning "
        "daily/weekly/monthly trends do NOT automatically mean report mode.\n"
        "REQUIRED WORKFLOW:\n"
        "  1. Call `preflight_analytics_request(query, mode=\"report\")` first.\n"
        "  2. If the route is `semantic_ready`, stay on the semantic path: use "
        "`discover_metrics`, `get_metric_details`, `query_metrics`, and "
        "`generate_metric_charts` (or `quick_metric_chart` for one-offs). "
        "Only fall back to raw SQL if preflight returns `semantic_coverage_gap`, "
        "`semantic_disabled`, or `semantic_unavailable`.\n"
        "  2b. If the route is `hybrid_ready`, use semantic tools for covered "
        "topics and raw SQL for uncovered topics. You can mix "
        "`generate_metric_charts` and `generate_charts` calls — the report "
        "pipeline accepts charts from both sources.\n"
        "  3. On the raw fallback path, use `discover_models(query, detail_top_n=5)`, "
        "`describe_table`, and `execute_query` (use medians/percentiles over means). "
        "Include at least 1 statistical query and 1 correlation query when you are on "
        "the raw SQL path.\n"
        "  4. Call `generate_metric_charts` for semantic report charts or "
        "`generate_charts` for raw-SQL report charts, with ALL chart specs in ONE call. "
        "In hybrid mode, you may call both batch tools. "
        "Do NOT use individual `generate_chart` calls for reports — use the batch tool. "
        "Minimum 3 charts: KPIs + trends + dimensional breakdowns. "
        "KPI `numberDisplay` charts MUST come from single-row SQL only. "
        "For monthly or weekly headline KPIs, use latest-period queries such as "
        "`ORDER BY month DESC LIMIT 1`. Multi-metric trend charts do NOT auto-plot "
        "extra numeric columns — use comma-separated `y_field` values or reshape to "
        "long form with `series_field`. At least 1 chart must use series_field. "
        "Include a scatter/heatmap for relationships.\n"
        "  4. Write markdown with `{{chart:CHART_ID}}` placeholders.\n"
        "  5. Call `generate_report(title, content_markdown)`.\n"
        "  6. In your reply: summarize insights, offer `export_report` for HTML download, "
        "ask about format conversion.\n"
        "CRITICAL: After `generate_report` returns, do NOT echo the report markdown text.\n\n"

        "MODE 2: QUICK QUERIES & RAW DATA (MARKDOWN OUTPUT)\n"
        "TRIGGER: User asks for raw data, numbers, or a simple text explanation WITHOUT charts.\n"
        "- Default here for plain analytical questions, including time-series questions, "
        "unless the user explicitly asks for visual output.\n"
        "- Workflow: if this is a business-metric question and semantic is enabled, call "
        "`preflight_analytics_request(query, mode=\"answer\")` first, then use "
        "`query_metrics` when the route is `semantic_ready`. Otherwise query data and "
        "output a Markdown response.\n"
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
        "1. DISCOVER: For analytical questions, call `preflight_analytics_request` first. "
        "If the route is `semantic_ready` or `hybrid_ready`, continue with "
        "`discover_metrics` and `get_metric_details` for covered topics; "
        "for uncovered topics (or `semantic_coverage_gap`), use `discover_models(query, detail_top_n=5)` "
        "for combined search + details in one call. Only use separate `search_models` + "
        "`get_model_details` when you need more than 5 models detailed.\n"
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

# Register all tools
register_query_tools(mcp, ch, research_store)
register_schema_tools(mcp, ch)
register_dbt_tools(mcp)
register_metadata_tools(mcp, ch)

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
register_sandbox_tools(mcp, ch)
register_workflow_resume_tools(mcp)
register_cross_check_tools(mcp, ch)
register_storyteller_tools(mcp, ch)

# Mini-app platform: install the visibility filter first so subsequent
# app registrations can mark hydration tools as app-only.
register_mini_app_infra(mcp, ch)
register_token_explorer_tools(mcp, ch)
register_metric_lab_tools(mcp, ch)
register_yield_opportunities_tools(mcp, ch)
register_portfolio_tools(mcp, ch)
register_graph_explorer_tools(mcp, ch)
register_quarterly_review_tools(mcp, ch, research_store)

install_auto_tool_tracing(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    try:
        info = ch.get_server_info("dbt")
        return JSONResponse(
            {
                "status": "ok",
                "clickhouse_connected": True,
                "clickhouse_version": info["version"],
                "ssl_trust_injected": runtime_state.ssl_trust_injected,
            },
            status_code=200,
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "clickhouse_connected": False,
                "error": str(exc),
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
    from cerebro_mcp.tools.visualization import _resolve_report

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
    transport = "sse" if "--sse" in sys.argv else "stdio"
    log_event(logger, "transport_selected", transport=transport)
    ensure_writable_dir(RESEARCH_DIR)
    manifest.load()
    catalog.load()
    docs_index.load()
    semantic_runtime.load()

    # Phase 2: ensure simulation sandboxes are torn down on exit. The
    # periodic sweeper is installed lazily by the first sandbox tool call
    # (so it can grab the running event loop), not here at process boot.
    from cerebro_mcp.bootstrap import install_sandbox_atexit, init_event_store_sync
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

    if transport == "sse":
        validate_remote_transport_auth(os.environ.get("MCP_AUTH_TOKEN"))
        _run_sse_with_auth()
    else:
        # Phase 3 multi-tenant: stdio is single-user-per-process. Read
        # CEREBRO_OWNER once at boot and stash the (hashed) identifier in
        # the contextvar for the lifetime of this process. Every workflow
        # written from this stdio session is stamped with this owner.
        # Unset env var → contextvar stays None → workflows go in with
        # owner=NULL (single-tenant fallback, backward compatible).
        from cerebro_mcp.identity import (
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
        self._expected = f"Bearer {auth_token}".encode("latin-1")

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        if (
            path == "/health"
            or path == "/metrics"
            or path.startswith("/reports/")
        ):
            await self.app(scope, receive, send)
            return

        header_value = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header_value = value
                break

        if header_value != self._expected:
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

        from cerebro_mcp.identity import (
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


def _run_sse_with_auth():
    """Run SSE transport, optionally wrapped with Bearer token auth."""
    import anyio
    import uvicorn

    os.environ["CEREBRO_TRANSPORT"] = "sse"

    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    validate_remote_transport_auth(auth_token)
    log_event(logger, "auth_middleware_enabled", enabled=bool(auth_token))
    starlette_app = build_sse_app(auth_token)

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

    anyio.run(_serve)


if __name__ == "__main__":
    main()
