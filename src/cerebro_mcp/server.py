import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from cerebro_mcp import runtime_state
from cerebro_mcp.bootstrap import (
    ensure_writable_dir,
    init_ssl_trust,
    validate_remote_transport_auth,
)
from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.docs_loader import docs_index
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.observability import (
    PrometheusMiddleware,
    log_event,
    metrics_response,
    setup_logging,
)
from cerebro_mcp.research_store import ResearchStore
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
from cerebro_mcp.tools.reasoning import (
    install_auto_tool_tracing,
    register_reasoning_tools,
)
from cerebro_mcp.tools.agents import register_agent_tools


runtime_state.ssl_trust_injected = init_ssl_trust()
RESEARCH_DIR = Path(settings.CEREBRO_RESEARCH_DIR).expanduser()
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "cerebro-mcp",
    host="0.0.0.0",
    instructions=(
        "Gnosis Chain data platform MCP server.\n\n"

        "RESPONSE RULES (ALWAYS FOLLOW):\n"
        "1. After generate_report, summarize insights and ask if they want the HTML exported "
        "(via `export_report`) or converted to docx/pdf/pptx.\n"
        "2. No emojis or Unicode symbols — use clean, professional markdown only.\n\n"

        "ENFORCEMENT GATES (CANNOT BE BYPASSED):\n"
        "- `generate_charts` (batch) and `generate_chart` (single) are BLOCKED until you run "
        "`search_models` (or `discover_models`), explore at least 3 models via "
        "`get_model_details`, AND verify at least 1 table via `describe_table`.\n"
        "- `generate_report` is BLOCKED until: (a) >= 3 charts created (with trend and/or "
        "breakdown), (b) 2+ exploratory queries run, (c) at least 1 statistical query "
        "(quantiles/stddev/corr), (d) at least 1 chart with series_field or pie/treemap/"
        "heatmap/sankey type (dimensional breakdown), (e) at least 1 scatter/heatmap chart "
        "OR correlation query (relational analysis).\n"
        "- For quick ad-hoc plots, use `quick_chart` instead — no gates required.\n\n"

        "OUTPUT FORMAT RULES:\n"
        "You have two output modes. ALWAYS use the correct mode:\n\n"

        "MODE 1: REPORTS & VISUALIZATIONS (INTERACTIVE UI)\n"
        "TRIGGER: User asks for a report, charts, plots, visual analysis, trends, "
        "or any weekly/daily/monthly summary.\n"
        "REQUIRED WORKFLOW:\n"
        "  1. Discover & Verify: `discover_models(query, detail_top_n=5)` then "
        "`describe_table` to verify columns.\n"
        "  2. Query data with `execute_query` (use medians/percentiles over means). "
        "Include at least 1 statistical query and 1 correlation query.\n"
        "  3. Call `generate_charts` (batch) with ALL chart specs in ONE call. "
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
        "- Workflow: Query data → output a Markdown response.\n"
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
        "1. DISCOVER: Use `discover_models(query, detail_top_n=5)` for combined search + details "
        "in one call. Only use separate `search_models` + `get_model_details` when you need "
        "more than 5 models detailed.\n"
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
        "- Key: Block time 5s, xDAI (gas), GNO (staking), Chain ID 100.\n"
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
register_reasoning_tools(mcp)
register_agent_tools(mcp)
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

    # Auth: accept Bearer header or ?token= query param
    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    if auth_token:
        auth_header = request.headers.get("Authorization", "")
        query_token = request.query_params.get("token", "")
        if auth_header != f"Bearer {auth_token}" and query_token != auth_token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    report_id = request.path_params["report_id"]

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
    docs_index.load()

    if transport == "sse":
        validate_remote_transport_auth(os.environ.get("MCP_AUTH_TOKEN"))
        _run_sse_with_auth()
    else:
        mcp.run(transport="stdio")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_token: str):
        super().__init__(app)
        self._auth_token = auth_token

    async def dispatch(self, request, call_next):
        if (
            request.url.path == "/health"
            or request.url.path == "/metrics"
            or request.url.path.startswith("/reports/")
        ):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {self._auth_token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


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
