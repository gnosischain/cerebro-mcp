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
        "Gnosis Chain data platform MCP server. "
        "Query ClickHouse databases (execution, consensus, crawlers_data, nebula, dbt) "
        "with full dbt model context (descriptions, columns, lineage, SQL)."
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
