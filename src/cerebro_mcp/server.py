import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.manifest_loader import manifest
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

        "FORMATTING RULE (MANDATORY):\n"
        "All generated reports, markdown, and summaries MUST strictly adhere to a "
        "professional, clean, corporate style. The use of emojis, emoticons, or "
        "Unicode symbols (e.g., red circle, chart icon, warning sign, checkmarks, "
        "rocket, fire, sparkle, arrow symbols) is STRICTLY FORBIDDEN across ALL "
        "outputs — chat text, headers, lists, tables, and body text.\n"
        "Use standard markdown formatting (bold, italics, blockquotes) for emphasis.\n"
        "Examples:\n"
        "  BAD:  'Transactions surged by 42%! 🚀🔥'\n"
        "  BAD:  '📊 Weekly Overview'\n"
        "  BAD:  '✅ Validators increased'\n"
        "  GOOD: 'Transactions increased by 42%.'\n"
        "  GOOD: 'Weekly Overview'\n"
        "  GOOD: 'Validators increased by 3%.'\n\n"

        "AGENCY WORKFLOW:\n"
        "For complex analytical tasks, adopt specialized agent personas by calling "
        "`get_agent_persona(role)` before each phase:\n"
        "  Phase 1 (Analytics Reporter): discover, verify, query, generate_chart\n"
        "  Phase 2 (UI Designer): chart type selection, markdown layout, generate_report\n"
        "  Phase 3 (Reality Checker): validate SQL safety, check chart specs, confirm\n\n"

        "OUTPUT FORMAT RULES (MANDATORY):\n"
        "You have two output modes. ALWAYS use the correct mode:\n\n"

        "MODE 1: REPORTS & VISUALIZATIONS (INTERACTIVE UI)\n"
        "TRIGGER: User asks for a report, charts, plots, visual analysis, trends, "
        "or any weekly/daily/monthly summary.\n"
        "REQUIRED WORKFLOW:\n"
        "  1. Query data with `execute_query`\n"
        "  2. Call `generate_chart` for EACH metric (minimum 3 charts for reports)\n"
        "  3. Write markdown with `{{chart:CHART_ID}}` placeholders\n"
        "  4. Call `generate_report(title, content_markdown)` — returns interactive UI resource\n"
        "  5. Summarize key insights in your response text\n"
        "  6. Ask user if they want the report converted to another format (docx/pdf/pptx)\n"
        "CRITICAL: After `generate_report` or `open_report` returns, do NOT echo the "
        "report markdown or {{chart:...}} placeholders as conversation text. "
        "Only summarize insights and include the report URI.\n"
        "ALWAYS complete this workflow. `generate_report` produces a native interactive "
        "UI resource and opens the report in the user's browser.\n"
        "MANDATORY: After generate_report succeeds, ALWAYS include the file:// link "
        "in your text response so the user can open the report directly.\n\n"

        "MODE 2: QUICK QUERIES & RAW DATA (MARKDOWN OUTPUT)\n"
        "TRIGGER: User asks for raw data, numbers, or a simple text explanation WITHOUT charts.\n"
        "- Workflow: Query data → output a Markdown response.\n"
        "- Structure: ### Objective → ### Query (SQL block) → ### Results (Markdown table, max 10 rows) → "
        "### Key Insights (Bullet points).\n\n"

        "STANDARD OPERATING PROCEDURE (MANDATORY):\n"
        "1. DISCOVER: Use `search_models` or `list_tables`. "
        "Check for dbt `api_*/fct_*` models first (they are much faster).\n"
        "2. VERIFY SCHEMA: Call `describe_table` or `get_model_details` "
        "to get EXACT column names before writing SQL. "
        "Do NOT guess generic names like 'transactions' or 'value' without verifying.\n"
        "3. EXECUTE: Write ClickHouse SQL using verified column names. "
        "Always use LIMIT and partition key filters (e.g., block_timestamp/block_date).\n\n"

        "GNOSIS CHAIN SPECIFICS:\n"
        "- Block time: 5 seconds. ~17,280 blocks per day.\n"
        "- Native gas token: xDAI. Staking token: GNO (1 GNO per validator).\n"
        "- Chain ID: 100. Slots per epoch: 16.\n"
        "- Verify token decimals (xDAI/GNO/WETH = 18, USDC/USDT = 6) via `get_token_metadata`.\n"
    ),
)

# Initialize ClickHouse connection manager
ch = ClickHouseManager()

# Load dbt manifest
manifest.load()

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
