from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.tools.query import register_query_tools
from cerebro_mcp.tools.schema import register_schema_tools
from cerebro_mcp.tools.dbt import register_dbt_tools
from cerebro_mcp.tools.metadata import register_metadata_tools
from cerebro_mcp.resources.context import register_resources
from cerebro_mcp.prompts.templates import register_prompts

mcp = FastMCP(
    "cerebro-mcp",
    instructions=(
        "Gnosis Chain data platform MCP server.\n\n"
        "STANDARD OPERATING PROCEDURE:\n"
        "When asked to analyze data, find insights, or write queries, "
        "you MUST follow this workflow:\n"
        "1. DISCOVER: Always use `search_models` or `list_tables` first to find "
        "the correct data source. Prefer `dbt` models.\n"
        "2. VERIFY: Use `get_model_details` or `describe_table` to verify column "
        "names and types. Never guess schema.\n"
        "3. SAMPLE: Use `get_sample_data` to understand data formatting "
        "(e.g., are addresses lowercased? are timestamps ms or seconds?).\n"
        "4. EXECUTE: Write and run ClickHouse SQL using `execute_query`. "
        "Always use LIMIT and date filters.\n\n"
        "STANDARDIZED OUTPUT FORMAT:\n"
        "Always present your final answer to the user using this exact "
        "markdown structure:\n"
        "### Objective\n"
        "[Briefly state what you are analyzing]\n\n"
        "### Query\n"
        "```sql\n[Insert the ClickHouse SQL you ran]\n```\n\n"
        "### Results\n"
        "[Show the data in a concise markdown table, limiting to the top "
        "5-10 most relevant rows]\n\n"
        "### Key Insights\n"
        "- **[Insight 1]**: [Explanation based on the data]\n"
        "- **[Insight 2]**: [Explanation based on the data]\n"
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
register_prompts(mcp)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
