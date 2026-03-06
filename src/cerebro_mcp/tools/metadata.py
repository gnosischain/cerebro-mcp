from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings


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
                        f"WHERE database = '{db_name}'"
                    )
                    result = ch.execute_raw(sql, db_name)
                    count = result["rows"][0][0] if result["rows"] else "?"
                except Exception:
                    count = "?"

                lines.append(
                    f"## {db_name} ({count} tables)\n{desc}\n"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
