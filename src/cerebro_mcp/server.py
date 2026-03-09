from mcp.server.fastmcp import FastMCP

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

mcp = FastMCP(
    "cerebro-mcp",
    instructions=(
        "Gnosis Chain data platform MCP server.\n\n"

        "STANDARD OPERATING PROCEDURE:\n"
        "When asked to analyze data, find insights, or write queries, "
        "you MUST follow this workflow:\n"
        "1. DISCOVER: Always use `search_models` or `list_tables` first to find "
        "the correct data source. ALWAYS check for dbt `api_*/fct_*` mart models first — "
        "they are 100x faster than raw tables.\n"
        "2. VERIFY: Use `get_model_details` or `describe_table` to verify column "
        "names and types. Never guess schema.\n"
        "3. SAMPLE: Use `get_sample_data` to understand data formatting "
        "(e.g., are addresses lowercased? are timestamps ms or seconds?).\n"
        "4. EXECUTE: Write and run ClickHouse SQL using `execute_query`. "
        "Always use LIMIT and date filters. For long-running queries, use "
        "`start_query`/`get_query_results` async pattern.\n\n"

        "GNOSIS CHAIN SPECIFICS:\n"
        "Gnosis Chain is NOT Ethereum. Do NOT assume Ethereum parameters.\n"
        "- Block time: 5 seconds (not 12). ~17,280 blocks per day.\n"
        "- Native gas token: xDAI (not ETH). Gas is paid in xDAI.\n"
        "- Staking token: GNO. 1 GNO per validator (not 32 ETH).\n"
        "- Chain ID: 100. Slots per epoch: 16.\n"
        "- Use `gnosis://chain-parameters` resource for full consensus specs.\n"
        "- Use `gnosis://address-directory` for token addresses and decimals.\n\n"

        "QUERY BEST PRACTICES:\n"
        "- CRITICAL: Always include WHERE on partition key (block_timestamp, block_date, "
        "or slot) to enable partition pruning. Never full-scan.\n"
        "- Verify token decimals before aggregating values: "
        "xDAI/GNO/WETH = 18 decimals, USDC/USDT = 6 decimals. "
        "Use `get_token_metadata` to check.\n"
        "- On query errors, use `explain_query` or `describe_table` to debug. "
        "Do NOT randomly guess syntax fixes.\n"
        "- Use `resolve_address` for address-to-label lookups.\n"
        "- Use `search_models_by_address` to find dbt models by contract address.\n"
        "- Use `search_docs` for targeted documentation lookups.\n"
        "- Check `gnosis://query-cookbook` for optimized query templates.\n"
        "- Use `generate_chart` to produce ECharts visualizations for trends.\n\n"

        "STANDARDIZED OUTPUT FORMAT:\n"
        "Always present your final answer to the user using this exact "
        "markdown structure:\n"
        "### Objective\n"
        "[Briefly state what you are analyzing]\n\n"
        "### Query\n"
        "```sql\n[Insert the ClickHouse SQL you ran]\n```\n\n"
        "### Results\n"
        "[Show the data in a concise markdown table, limiting to the top "
        "5-10 most relevant rows. Round financial metrics to 2 decimal places. "
        "Dates in YYYY-MM-DD UTC format. Specify if amounts are in wei or native units.]\n\n"
        "### Key Insights\n"
        "- **[Insight 1]**: [Explanation based on the data]\n"
        "- **[Insight 2]**: [Explanation based on the data]\n\n"

        "INTERACTIVE REPORTS:\n"
        "For interactive reports with rendered charts, follow this workflow:\n"
        "1. Generate charts with `generate_chart` (each returns a chart ID like chart_1)\n"
        "2. Use `list_charts` to see all registered chart IDs\n"
        "3. Write markdown content with `{{chart:CHART_ID}}` placeholders where charts should appear\n"
        "4. Call `generate_report` to produce a standalone HTML file with rendered ECharts visualizations\n"
        "Use the `weekly_report` prompt for a guided weekly report workflow.\n"
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


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
