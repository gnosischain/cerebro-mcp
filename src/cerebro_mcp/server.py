import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.docs_loader import docs_index
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
from cerebro_mcp.tools.reasoning import (
    install_auto_tool_tracing,
    register_reasoning_tools,
)
from cerebro_mcp.tools.agents import register_agent_tools

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
        "At least 1 chart must use series_field. Include a scatter/heatmap for relationships.\n"
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
        "At least 1 chart must use series_field for dimensional breakdown. "
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

# Load dbt manifest
manifest.load()

# Load external docs index
docs_index.load()

# Register all tools
register_query_tools(mcp, ch)
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
register_reasoning_tools(mcp)
register_agent_tools(mcp)
install_auto_tool_tracing(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main():
    import sys

    transport = "sse" if "--sse" in sys.argv else "stdio"

    if transport == "sse":
        _run_sse_with_auth()
    else:
        mcp.run(transport="stdio")


def _run_sse_with_auth():
    """Run SSE transport, optionally wrapped with Bearer token auth."""
    import anyio
    import uvicorn
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    auth_token = os.environ.get("MCP_AUTH_TOKEN")

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Health endpoint bypasses auth
            if request.url.path == "/health":
                return await call_next(request)
            auth_header = request.headers.get("Authorization", "")
            if auth_header != f"Bearer {auth_token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    starlette_app = mcp.sse_app()

    if auth_token:
        starlette_app.add_middleware(BearerAuthMiddleware)

    async def _serve():
        config = uvicorn.Config(
            starlette_app,
            host=os.environ.get("FASTMCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("FASTMCP_PORT", "8000")),
            log_level=mcp.settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)


if __name__ == "__main__":
    main()
