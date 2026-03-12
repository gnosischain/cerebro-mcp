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
        "1. After generate_report or open_report, copy the file:// link verbatim into your reply.\n"
        "2. No emojis or Unicode symbols — use clean, professional markdown only.\n\n"

        "ENFORCEMENT GATES (CANNOT BE BYPASSED):\n"
        "- `generate_chart` is BLOCKED until you run `search_models`, explore at least 5 "
        "models via `get_model_details`, AND verify at least 1 table via `describe_table`.\n"
        "- `approve_analysis` will REJECT unless: (a) 5+ models explored via "
        "`get_model_details`, (b) 3+ exploratory queries run, AND (c) at least 1 query "
        "uses statistical functions (quantiles, stddevPop, corr, median, percentile).\n"
        "- `generate_report` is BLOCKED until you create >= 3 charts "
        "(must include trend and/or breakdown) AND successfully call `approve_analysis`.\n"
        "- For quick ad-hoc plots, use `quick_chart` instead — no gates required.\n\n"

        "OUTPUT FORMAT RULES:\n"
        "You have two output modes. ALWAYS use the correct mode:\n\n"

        "MODE 1: REPORTS & VISUALIZATIONS (INTERACTIVE UI)\n"
        "TRIGGER: User asks for a report, charts, plots, visual analysis, trends, "
        "or any weekly/daily/monthly summary.\n"
        "REQUIRED WORKFLOW:\n"
        "  1. Discover & Verify: `search_models`, `get_model_details` (top 10+ if available), "
        "`describe_table`.\n"
        "  2. Query data with `execute_query` (use medians/percentiles over means).\n"
        "  3. Call `generate_chart` for EACH metric (minimum 3 charts, trend + breakdown).\n"
        "  4. Have a review agent call `approve_analysis(notes)`.\n"
        "  5. Write markdown with `{{chart:CHART_ID}}` placeholders.\n"
        "  6. Call `generate_report(title, content_markdown)`.\n"
        "  7. In your reply: include the file:// link, summarize insights, "
        "ask about format conversion.\n"
        "CRITICAL: After `generate_report` returns, do NOT echo the report markdown text.\n\n"

        "MODE 2: QUICK QUERIES & RAW DATA (MARKDOWN OUTPUT)\n"
        "TRIGGER: User asks for raw data, numbers, or a simple text explanation WITHOUT charts.\n"
        "- Workflow: Query data → output a Markdown response.\n"
        "- Structure: ### Objective → ### Query (SQL block) → ### Results (Markdown table) → "
        "### Key Insights.\n\n"

        "STANDARD OPERATING PROCEDURE:\n"
        "1. DISCOVER: Use `search_models` or `list_tables`. "
        "Find models across ALL tiers (api_*, fct_*, int_*) — do not stop at the first match.\n"
        "2. EXPLORE: Call `get_model_details` for at least 5 relevant models. "
        "Map lineage (upstream/downstream). Identify all available dimensions "
        "(token, action, user segment). Use int_* models when marts lack needed breakdowns.\n"
        "3. VERIFY: Call `describe_table` or `get_model_details` before writing SQL.\n"
        "4. EDA (MANDATORY): Run distribution queries BEFORE final analysis. "
        "Use quantiles(0.25, 0.5, 0.75), stddevPop(), min/max, count() to assess "
        "data shape and outliers. This is NOT optional — approve_analysis REJECTS "
        "without at least 1 statistical query.\n"
        "5. EXECUTE: Write ClickHouse SQL. Use fully-qualified table names and partition filters.\n"
        "6. REPORT DEPTH: Reports MUST include KPIs + time-series trends + dimensional breakdowns.\n\n"

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
