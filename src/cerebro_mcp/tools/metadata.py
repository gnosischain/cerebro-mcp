import time
from datetime import datetime, timezone

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.tools.query import truncate_response


DATABASE_DESCRIPTIONS = {
    "execution": (
        "On-chain L1 execution layer data: blocks, transactions, logs, traces, "
        "native_transfers, contracts, balance_diffs, code_diffs, nonce_diffs, "
        "storage_diffs, withdrawals. Populated by cryo-indexer from Gnosis Chain."
    ),
    "consensus": (
        "Beacon chain consensus data: blocks, attestations, validators, withdrawals, "
        "deposits, rewards, blob_commitments, blob_sidecars, specs. "
        "~25 tables from beacon API."
    ),
    "crawlers_data": (
        "Off-chain enrichment data: dune_labels, dune_prices, dune_bridge_flows, "
        "dune_gno_supply, ember_electricity_data, probelab network stats, "
        "gpay_wallets. Populated by click-runner."
    ),
    "nebula": (
        "P2P network discovery data: crawls, visits (peer connectivity, agent info, "
        "protocol versions). From Discv5 network crawlers."
    ),
    "dbt": (
        "Transformed/modeled data from dbt-cerebro: ~400 models as views and tables. "
        "Organized in modules: execution, consensus, contracts (15+ DeFi protocols), "
        "p2p, bridges, ESG, probelab, crawlers_data. "
        "Models follow naming: stg_ (staging), int_ (intermediate), "
        "api_/fct_ (marts/API-ready)."
    ),
}


def register_metadata_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def list_databases() -> str:
        """List all available ClickHouse databases with descriptions and table counts.

        Returns:
            Database listing with name, description, and table count.
        """
        try:
            lines = ["# Available Databases\n"]

            for db_name in settings.ALLOWED_DATABASES:
                desc = DATABASE_DESCRIPTIONS.get(db_name, "")
                try:
                    sql = (
                        "SELECT count() FROM system.tables "
                        "WHERE database = {db:String}"
                    )
                    cache_key = f"tables:{db_name}:"
                    result = ch.execute_raw_cached(
                        sql, db_name, cache_key, parameters={"db": db_name}
                    )
                    count = result["rows"][0][0] if result["rows"] else "?"
                except Exception:
                    count = "?"

                lines.append(
                    f"## {db_name} ({count} tables)\n{desc}\n"
                )

            return truncate_response("\n".join(lines))
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def system_status() -> str:
        """Show server status: ClickHouse connectivity, manifest state, config."""
        lines = ["# System Status\n"]

        # ClickHouse connectivity
        lines.append("## ClickHouse Connectivity\n")
        for db_name in settings.ALLOWED_DATABASES:
            try:
                client = ch.get_client(db_name)
                client.query("SELECT 1")
                lines.append(f"- **{db_name}**: connected")
            except Exception as e:
                lines.append(f"- **{db_name}**: error — {e}")

        # Manifest state
        lines.append("\n## Manifest\n")
        lines.append(f"- **Loaded:** {manifest.is_loaded}")
        lines.append(f"- **Model count:** {manifest.model_count}")
        lines.append(f"- **Source:** {settings.DBT_MANIFEST_URL or settings.DBT_MANIFEST_PATH or 'none'}")
        lines.append(f"- **Content hash:** {manifest.content_hash or 'n/a'}")
        if manifest.last_load_time:
            ts = datetime.fromtimestamp(manifest.last_load_time, tz=timezone.utc)
            lines.append(f"- **Last load:** {ts.isoformat()}")
        else:
            lines.append("- **Last load:** never")
        if manifest.last_refresh_error:
            lines.append(f"- **Last refresh error:** {manifest.last_refresh_error}")

        # Config
        lines.append("\n## Config\n")
        lines.append(f"- MAX_ROWS: {settings.MAX_ROWS}")
        lines.append(f"- QUERY_TIMEOUT_SECONDS: {settings.QUERY_TIMEOUT_SECONDS}")
        lines.append(f"- TOOL_RESPONSE_MAX_CHARS: {settings.TOOL_RESPONSE_MAX_CHARS}")
        lines.append(f"- MANIFEST_REFRESH_INTERVAL_SECONDS: {settings.MANIFEST_REFRESH_INTERVAL_SECONDS}")
        lines.append(f"- ALLOWED_DATABASES: {', '.join(settings.ALLOWED_DATABASES)}")

        # Cache
        lines.append("\n## Cache\n")
        lines.append(f"- Schema cache entries: {ch.schema_cache_size}")
        lines.append(f"- Schema cache TTL: {ch.SCHEMA_CACHE_TTL}s")

        return "\n".join(lines)
