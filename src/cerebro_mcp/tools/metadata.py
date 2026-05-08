import re
import time
from datetime import datetime, timezone

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.docs_loader import docs_index
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp import runtime_state
from cerebro_mcp.tools.query import format_results_table, truncate_response


TOKEN_REGISTRY = {
    "xdai": {"address": "native", "decimals": 18, "name": "xDAI", "symbol": "xDAI"},
    "wxdai": {"address": "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d", "decimals": 18, "name": "Wrapped xDAI", "symbol": "WXDAI"},
    "gno": {"address": "0x9c58bacc331c9aa871afd802db6379a98e80cedb", "decimals": 18, "name": "Gnosis", "symbol": "GNO"},
    "weth": {"address": "0x6a023ccd1ff6f2045c3309768ead9e68f978f6e1", "decimals": 18, "name": "Wrapped ETH", "symbol": "WETH"},
    "usdc": {"address": "0xddafbb505ad214d7b80b1f830fccc89b60fb7a83", "decimals": 6, "name": "USD Coin", "symbol": "USDC"},
    "usdt": {"address": "0x4ecaba5870353805a9f068101a40e0f32ed605c6", "decimals": 6, "name": "Tether", "symbol": "USDT"},
    "eure": {"address": "0xcb444e90d8198415266c6a2724b7900fb12fc56e", "decimals": 18, "name": "Monerium EUR", "symbol": "EURe"},
    "wsteth": {"address": "0x6c76971f98945ae98dd7d4dfca8711ebea946ea6", "decimals": 18, "name": "Wrapped stETH", "symbol": "wstETH"},
    "sdai": {"address": "0xaf204776c7245bf4147c2612bf6e5972ee483701", "decimals": 18, "name": "Savings DAI", "symbol": "sDAI"},
    "cow": {"address": "0x177127622c4a00f3d409b75571e12cb3c8973d3c", "decimals": 18, "name": "CoW Protocol", "symbol": "COW"},
    "safe": {"address": "0x4d18815d14fe5c3304e87b3ea4a5383f683cb578", "decimals": 18, "name": "Safe", "symbol": "SAFE"},
}

# Build reverse lookup: address -> token info
_ADDRESS_TO_TOKEN = {
    info["address"]: info for info in TOKEN_REGISTRY.values() if info["address"] != "native"
}


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
        "GC_Key_Validators, ipinfo, country_codes. Populated by click-runner."
    ),
    "nebula": (
        "P2P network discovery data: crawls, visits (peer connectivity, agent info, "
        "protocol versions), neighbors, discovery_id_prefixes_x_peer_ids. From Discv5 network crawlers."
    ),
     "nebula_discv4": (
        "P2P network discovery data: crawls, visits (peer connectivity, agent info, "
        "protocol versions), neighbors, discovery_id_prefixes_x_peer_ids. From Discv4 network crawlers."
    ),
    "dbt": (
        "Transformed/modeled data from dbt-cerebro: ~400 models as views and tables. "
        "Organized in modules: execution, consensus, contracts (15+ DeFi protocols), "
        "p2p, bridges, ESG, probelab, crawlers_data. "
        "Models follow naming: stg_ (staging), int_ (intermediate), "
        "api_/fct_ (marts/API-ready)."
    ),
}


# --- Hardcoded Platform Constants ---
# All values are small, permanent, frequently used, and foundational.
# See get_platform_constants() tool to expose these to the LLM.

CHAIN_CONSTANTS = {
    "chain_id": 100,
    "chain_name": "Gnosis Chain",
    "block_time_seconds": 5,
    "blocks_per_day_approx": 17_280,
    "slots_per_epoch": 16,
    "epoch_duration_seconds": 80,
    "native_token": "xDAI",
    "staking_token": "GNO",
    "stake_per_validator": "1 GNO",
    "genesis_timestamp": 1539021785,  # 2018-10-08T18:43:05Z (block 1)
    "consensus_genesis_timestamp": 1638993340,  # 2021-12-08T19:55:40Z (GBC slot 0)
}

COMMON_EVENT_SIGNATURES = {
    "Transfer": {
        "topic0": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        "signature": "Transfer(address,address,uint256)",
        "notes": "ERC20 and ERC721 share the same topic0. ERC20: 2 indexed + 1 data; ERC721: 3 indexed.",
    },
    "Approval": {
        "topic0": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
        "signature": "Approval(address,address,uint256)",
        "notes": "ERC20 approval event.",
    },
    "UniswapV2Swap": {
        "topic0": "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",
        "signature": "Swap(address,uint256,uint256,uint256,uint256,address)",
        "notes": "UniswapV2 / Honeyswap / SushiSwap pair swap.",
    },
    "UniswapV3Swap": {
        "topic0": "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",
        "signature": "Swap(address,address,int256,int256,uint160,uint128,int24)",
        "notes": "UniswapV3 / Swapr V3 pool swap.",
    },
    "WETH_Deposit": {
        "topic0": "0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c",
        "signature": "Deposit(address,uint256)",
        "notes": "WETH deposit (wrap).",
    },
    "WETH_Withdrawal": {
        "topic0": "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65",
        "signature": "Withdrawal(address,uint256)",
        "notes": "WETH withdrawal (unwrap).",
    },
    "PairCreated": {
        "topic0": "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        "signature": "PairCreated(address,address,address,uint256)",
        "notes": "UniswapV2 factory pair creation.",
    },
    "GnosisSafeExecutionSuccess": {
        "topic0": "0x442e715f626346e8c54381002da614f62bee8d27386535b2521ec8540898556e",
        "signature": "ExecutionSuccess(bytes32,uint256)",
        "notes": "Gnosis Safe successful execution.",
    },
}

CORE_INFRASTRUCTURE_CONTRACTS = {
    "xdai_bridge": {
        "address": "0x7301cfa0e1756b71869e93d4e4dca5c7d0eb0aa6",
        "name": "xDAI Bridge Proxy (Home)",
        "type": "bridge",
    },
    "omnibridge": {
        "address": "0xf6a78083ca3e2a662d6dd1703c939c8ace2e268d",
        "name": "Omnibridge Multi-Token Mediator (Home)",
        "type": "bridge",
    },
    "amb_proxy": {
        "address": "0x75df5af045d91108662d8080fd1fefad6aa0bb59",
        "name": "AMB Contract Proxy (Home)",
        "type": "bridge",
    },
    "hashi_integration_gc": {
        "address": "0x60aa15198a3adfc86ff15b941549a6447b2ddb49",
        "name": "Hashi Integration Manager (Gnosis)",
        "type": "bridge_security",
    },
    "beacon_deposit_contract": {
        "address": "0x0b98057ea310f4d31f2a452b414647007d1645d9",
        "name": "GBC Deposit Contract",
        "type": "consensus",
    },
    "gno_to_mgno": {
        "address": "0x647507a70ff598f386cb96ae5046486389368c66",
        "name": "GNO-to-mGNO Wrapper",
        "type": "consensus",
    },
    "cowswap_settlement": {
        "address": "0x9008d19f58aabd9ed0d60971565aa8510560ab41",
        "name": "CoW Protocol GPv2Settlement",
        "type": "dex",
    },
    "balancer_v2_vault": {
        "address": "0xba12222222228d8ba445958a75a0704d566bf2c8",
        "name": "Balancer V2 Vault",
        "type": "dex",
    },
    "aave_v3_pool": {
        "address": "0xb50201558b00496a145fe76f7424749556e326d8",
        "name": "Aave V3 Pool (proxy)",
        "type": "lending",
    },
    "gnosis_pay": {
        "address": "0x90830ed558f12d826370dc52e9d87947a7f18de9",
        "name": "Gnosis Pay Main Contract",
        "type": "payments",
    },
    "circles_hub_v2": {
        "address": "0xc12c1e50abb450d6205ea2c3fa861b3b834d13e8",
        "name": "Circles Hub V2",
        "type": "social",
    },
}

TABLE_PARTITION_KEYS = {
    "execution": {
        "time_column": "block_timestamp",
        "partition_expr": "toStartOfMonth(block_timestamp)",
        "requires_final": True,
        "notes": "All execution.* tables use this partitioning.",
    },
    "consensus": {
        "time_column": "slot_timestamp",
        "partition_expr": "toStartOfMonth(slot_timestamp)",
        "requires_final": True,
        "notes": "slot_timestamp is a materialized column. Some tables also have slot as UInt64.",
    },
    "dbt": {
        "time_column": "varies (day, week, block_date)",
        "partition_expr": "varies by model",
        "requires_final": False,
        "notes": "dbt views/tables do not need FINAL. Pre-aggregated and deduplicated.",
    },
}

# Approximate row counts as of March 2026. Used for query planning guidance.
TABLE_ROW_SCALE = {
    "execution.logs": {"approx_rows": "8.7B", "approx_size": "249 GiB", "caution": "high"},
    "execution.native_transfers": {"approx_rows": "7.9B", "approx_size": "42 GiB", "caution": "high"},
    "execution.traces": {"approx_rows": "7.7B", "approx_size": "291 GiB", "caution": "high"},
    "execution.storage_diffs": {"approx_rows": "2.4B", "approx_size": "72 GiB", "caution": "high"},
    "execution.balance_diffs": {"approx_rows": "1.0B", "approx_size": "54 GiB", "caution": "medium"},
    "execution.transactions": {"approx_rows": "346M", "approx_size": "208 GiB", "caution": "medium"},
    "consensus.attestations": {"approx_rows": "2.3B", "approx_size": "256 GiB", "caution": "high"},
    "consensus.validators": {"approx_rows": "415M", "approx_size": "22 GiB", "caution": "medium"},
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
    def quality_metrics() -> str:
        """Show quality-discipline gate evaluations and discovered-model coverage.

        Returns aggregate counts for the report-quality gates introduced to
        catch the recurring failure modes (stock-vs-flow misuse, residual
        bucket exclusion, time-series correlation without stationarity,
        aggregator volume double-counting, and discovered-model coverage).
        Aggregates are read directly from the in-process Prometheus counters,
        which makes the surface useful for ad-hoc inspection without standing
        up Grafana / a metrics scraper.
        """
        from cerebro_mcp.observability import (
            cerebro_discovered_model_coverage_total,
            cerebro_quality_gate_evaluations_total,
            cerebro_quality_report_generations_total,
        )

        def _read_counter(counter, label_keys: list[str]) -> dict[tuple, float]:
            """Sum the metric values per label combination."""
            out: dict[tuple, float] = {}
            for child_labels, child in list(counter._metrics.items()):
                # child_labels is a tuple aligned with the counter's label
                # ordering; child has a `_value.get()` accessor.
                value = float(child._value.get())
                if value:
                    out[child_labels] = value
            return out

        lines: list[str] = ["# Quality Metrics\n"]

        # 1) Per-gate evaluations
        gate_data = _read_counter(
            cerebro_quality_gate_evaluations_total, ["rule", "outcome"]
        )
        lines.append("## Gate evaluations\n")
        if not gate_data:
            lines.append("_No gate evaluations recorded yet._\n")
        else:
            # Pivot: rule -> {outcome: count}
            by_rule: dict[str, dict[str, float]] = {}
            for (rule, outcome), v in gate_data.items():
                by_rule.setdefault(rule, {})[outcome] = v
            lines.append("| Rule | Pass | Fail | Warn | Override | Fail rate |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for rule in sorted(by_rule):
                row = by_rule[rule]
                p = int(row.get("pass", 0))
                f = int(row.get("fail", 0))
                w = int(row.get("warn", 0))
                o = int(row.get("override", 0))
                total = p + f
                rate = f"{(100 * f / total):.1f}%" if total else "n/a"
                lines.append(f"| `{rule}` | {p} | {f} | {w} | {o} | {rate} |")
            lines.append("")

        # 2) Discovered-model coverage
        cov_data = _read_counter(cerebro_discovered_model_coverage_total, ["outcome"])
        lines.append("## Discovered-model coverage\n")
        if not cov_data:
            lines.append("_No reports generated yet (no coverage data)._\n")
        else:
            lines.append("| Outcome | Count |")
            lines.append("|---|---:|")
            for (outcome,), v in sorted(cov_data.items()):
                lines.append(f"| `{outcome}` | {int(v)} |")
            lines.append("")

        # 3) Report generations by outcome
        rep_data = _read_counter(
            cerebro_quality_report_generations_total, ["report_type", "outcome"]
        )
        lines.append("## Report generations\n")
        if not rep_data:
            lines.append("_No report generations tagged yet._\n")
        else:
            lines.append("| Report type | Outcome | Count |")
            lines.append("|---|---|---:|")
            for (rtype, outcome), v in sorted(rep_data.items()):
                lines.append(f"| `{rtype}` | `{outcome}` | {int(v)} |")
            lines.append("")

        lines.append("---")
        lines.append(
            "_Aggregates are in-process Prometheus counters and reset on "
            "server restart. For long-window analysis, scrape `/metrics` "
            "into Prometheus._"
        )
        return "\n".join(lines)

    @mcp.tool()
    def system_status() -> str:
        """Show server status: ClickHouse connectivity, manifest state, config."""
        from cerebro_mcp.tools.reasoning import get_tracing_status

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

        # Docs Index
        lines.append("\n## Docs Index\n")
        lines.append(f"- **Loaded:** {docs_index.is_loaded}")
        lines.append(f"- **Entry count:** {docs_index.entry_count}")
        lines.append(f"- **LLMS entries:** {docs_index.llms_entry_count}")
        lines.append(f"- **Gnosis Chain docs entries:** {docs_index.gnosis_chain_entry_count}")
        lines.append(f"- **Base URL:** {settings.DOCS_BASE_URL or 'none'}")
        lines.append(
            f"- **Source:** {settings.DOCS_SEARCH_INDEX_URL or settings.DOCS_SEARCH_INDEX_PATH or 'none'}"
        )
        lines.append(
            f"- **Gnosis Chain docs source:** {settings.GNOSIS_CHAIN_DOCS_LLM_URL or 'none'}"
        )
        if docs_index.last_load_time:
            ts = datetime.fromtimestamp(docs_index.last_load_time, tz=timezone.utc)
            lines.append(f"- **Last load:** {ts.isoformat()}")
        else:
            lines.append("- **Last load:** never")
        if docs_index.last_refresh_error:
            lines.append(f"- **Last refresh error:** {docs_index.last_refresh_error}")

        # Config
        lines.append("\n## Config\n")
        lines.append(f"- MAX_ROWS: {settings.MAX_ROWS}")
        lines.append(
            f"- effective_query_timeout_seconds: {settings.effective_query_timeout_seconds}"
        )
        lines.append(
            f"- CLICKHOUSE_CONNECT_TIMEOUT: {settings.CLICKHOUSE_CONNECT_TIMEOUT}"
        )
        lines.append(
            f"- CLICKHOUSE_SEND_RECEIVE_TIMEOUT: "
            f"{settings.CLICKHOUSE_SEND_RECEIVE_TIMEOUT}"
        )
        lines.append(f"- CLICKHOUSE_VERIFY: {settings.CLICKHOUSE_VERIFY}")
        lines.append(
            f"- effective_tool_result_max_chars: "
            f"{settings.effective_tool_result_max_chars}"
        )
        lines.append(
            f"- effective_summary_max_chars: {settings.effective_summary_max_chars}"
        )
        lines.append(f"- TOOL_RESULT_MAX_ROWS: {settings.TOOL_RESULT_MAX_ROWS}")
        lines.append(f"- CEREBRO_RESEARCH_DIR: {settings.CEREBRO_RESEARCH_DIR}")
        lines.append(f"- MANIFEST_REFRESH_INTERVAL_SECONDS: {settings.MANIFEST_REFRESH_INTERVAL_SECONDS}")
        lines.append(f"- ALLOWED_DATABASES: {', '.join(settings.ALLOWED_DATABASES)}")
        lines.append(
            f"- legacy_query_timeout_fallback: {settings.using_legacy_query_timeout}"
        )
        lines.append(
            f"- legacy_tool_budget_fallback: {settings.using_legacy_tool_result_budget}"
        )

        tracing_status = get_tracing_status()
        lines.append("\n## Tracing\n")
        lines.append(f"- enabled: {tracing_status['enabled']}")
        lines.append(f"- always_on: {tracing_status['always_on']}")
        lines.append(f"- log_dir: {tracing_status['log_dir']}")
        lines.append(f"- retention_days: {tracing_status['retention_days']}")
        lines.append(f"- session_files: {tracing_status['session_files']}")
        lines.append(
            f"- recent_session_files_24h: {tracing_status['recent_session_files']}"
        )
        lines.append(
            f"- active_session_id: {tracing_status['active_session_id'] or 'none'}"
        )

        # Cache
        lines.append("\n## Cache\n")
        lines.append(f"- Schema cache entries: {ch.schema_cache_size}")
        lines.append(f"- Schema cache TTL: {ch.SCHEMA_CACHE_TTL}s")
        lines.append(f"- Table page cache entries: {ch.table_page_cache_size}")
        lines.append(f"- Table page cache TTL: {ch.TABLE_PAGE_CACHE_TTL}s")

        # Runtime
        lines.append("\n## Runtime\n")
        lines.append(f"- ssl_trust_injected: {runtime_state.ssl_trust_injected}")
        lines.append(
            f"- sse_auth_required_by_default: "
            f"{not settings.ALLOW_INSECURE_REMOTE_TRANSPORT}"
        )

        return "\n".join(lines)

    @mcp.tool()
    def resolve_address(address_or_name: str) -> str:
        """Look up an address label or find addresses by name using dune_labels (5.3M entries).

        Args:
            address_or_name: Either a 0x hex address to look up its label,
                or a name/keyword to search for matching addresses.
                Examples: '0x9c58bacc331c9aa871afd802db6379a98e80cedb', 'Uniswap', 'Agave'

        Returns:
            Matching addresses and labels.
        """
        try:
            query = address_or_name.strip()
            if not query:
                return "Error: Please provide an address or name to search."

            is_address = query.startswith("0x") and len(query) == 42
            if is_address:
                sql = (
                    "SELECT address, label FROM crawlers_data.dune_labels "
                    "WHERE lower(address) = {addr:String} LIMIT 20"
                )
                params = {"addr": query.lower()}
                cache_key = f"resolve:addr:{query.lower()}"
            else:
                sql = (
                    "SELECT address, label FROM crawlers_data.dune_labels "
                    "WHERE label ILIKE {pattern:String} LIMIT 20"
                )
                params = {"pattern": f"%{query}%"}
                cache_key = f"resolve:name:{query.lower()}"

            result = ch.execute_raw_cached(
                sql, "crawlers_data", cache_key, parameters=params
            )

            if not result["rows"]:
                return f"No results found for '{address_or_name}'."

            table = format_results_table(result["columns"], result["rows"])
            return truncate_response(
                f"**Results for:** {address_or_name}\n\n{table}"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_token_metadata(symbol_or_address: str) -> str:
        """Look up token metadata: address, decimals, name, and price data availability.

        Covers major Gnosis Chain tokens. For unlisted tokens, use resolve_address.

        Args:
            symbol_or_address: Token symbol (e.g., 'GNO', 'USDC') or contract address.

        Returns:
            Token name, address, decimals, and price data availability.
        """
        try:
            query = symbol_or_address.strip().lower()

            # Try symbol lookup
            token = TOKEN_REGISTRY.get(query)
            # Try address lookup
            if not token:
                token = _ADDRESS_TO_TOKEN.get(query)

            if not token:
                available = ", ".join(
                    info["symbol"] for info in TOKEN_REGISTRY.values()
                )
                return (
                    f"Token '{symbol_or_address}' not found in registry.\n"
                    f"Available: {available}\n\n"
                    f"Try `resolve_address` for unlisted tokens."
                )

            lines = [
                f"**{token['name']}** ({token['symbol']})",
                f"- Address: `{token['address']}`",
                f"- Decimals: {token['decimals']}",
                f"- Division factor: `1e{token['decimals']}`",
            ]

            # Check price data availability
            if token["address"] != "native":
                try:
                    sql = (
                        "SELECT count() AS cnt FROM crawlers_data.dune_prices "
                        "WHERE symbol = {sym:String}"
                    )
                    result = ch.execute_raw_cached(
                        sql,
                        "crawlers_data",
                        f"token:price:{token['symbol']}",
                        parameters={"sym": token["symbol"]},
                    )
                    count = result["rows"][0][0] if result["rows"] else 0
                    lines.append(
                        f"- Price data: {'available' if count > 0 else 'not available'}"
                        f" ({count} records in dune_prices)"
                    )
                except Exception:
                    lines.append("- Price data: check failed")

            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def search_models_by_address(contract_address: str) -> str:
        """Find dbt models related to a specific smart contract address.

        Searches contracts_whitelist, contracts_abi, and dbt manifest model SQL/descriptions.

        Args:
            contract_address: A 0x hex contract address (e.g., '0xba12222222228d8ba445958a75a0704d566bf2c8').

        Returns:
            Matching dbt models, contract types, and ABIs.
        """
        try:
            addr = contract_address.strip().lower()
            if not addr.startswith("0x") or len(addr) != 42:
                return "Error: Please provide a valid 42-character 0x hex address."

            lines = [f"# Models for `{addr}`\n"]
            found = False

            # 1. Check contracts_whitelist
            try:
                sql = (
                    "SELECT address, contract_type FROM dbt.contracts_whitelist "
                    "WHERE lower(address) = {addr:String}"
                )
                result = ch.execute_raw_cached(
                    sql, "dbt", f"whitelist:{addr}",
                    parameters={"addr": addr},
                )
                if result["rows"]:
                    found = True
                    lines.append("## Contracts Whitelist\n")
                    for row in result["rows"]:
                        lines.append(f"- **{row[1]}**: `{row[0]}`")
            except Exception:
                pass

            # 2. Check contracts_abi
            try:
                sql = (
                    "SELECT contract_address, contract_name, source "
                    "FROM dbt.contracts_abi "
                    "WHERE lower(contract_address) = {addr:String}"
                )
                result = ch.execute_raw_cached(
                    sql, "dbt", f"abi:{addr}",
                    parameters={"addr": addr},
                )
                if result["rows"]:
                    found = True
                    lines.append("\n## Contracts ABI\n")
                    for row in result["rows"]:
                        lines.append(f"- **{row[1]}** (source: {row[2]})")
            except Exception:
                pass

            # 3. Search dbt manifest for models mentioning this address
            if manifest.is_loaded:
                matching_models = []
                for model_name in manifest._models:
                    model = manifest._models[model_name]
                    raw_sql = model.get("raw_sql", "") or model.get("raw_code", "")
                    desc = model.get("description", "")
                    if addr in (raw_sql + desc).lower():
                        matching_models.append({
                            "name": model_name,
                            "description": desc[:100] if desc else "",
                            "materialized": model.get("config", {}).get(
                                "materialized", ""
                            ),
                        })

                if matching_models:
                    found = True
                    lines.append("\n## dbt Models Referencing This Address\n")
                    for m in matching_models[:20]:
                        lines.append(
                            f"- **{m['name']}** ({m['materialized']}): {m['description']}"
                        )

            # 4. Check dune_labels
            try:
                sql = (
                    "SELECT address, label FROM crawlers_data.dune_labels "
                    "WHERE lower(address) = {addr:String} LIMIT 5"
                )
                result = ch.execute_raw_cached(
                    sql, "crawlers_data", f"label:{addr}",
                    parameters={"addr": addr},
                )
                if result["rows"]:
                    found = True
                    lines.append("\n## Known Labels\n")
                    for row in result["rows"]:
                        lines.append(f"- `{row[0]}`: {row[1]}")
            except Exception:
                pass

            if not found:
                return f"No models, ABIs, or labels found for address `{addr}`."

            return truncate_response("\n".join(lines))
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def search_docs(topic: str) -> str:
        """Search across all platform documentation and reference resources.

        Searches platform overview, SQL guide, address directory, metric definitions,
        query cookbook, and the external hosted analytics docs. If an external doc
        is highly relevant, use `get_doc_chunk(location)` to read the full page.

        Args:
            topic: Search term or topic (e.g., 'partition pruning', 'bridge', 'USDC decimals').

        Returns:
            Matching sections and locations from documentation resources.
        """
        try:
            from cerebro_mcp.resources.context import PLATFORM_OVERVIEW, CLICKHOUSE_SQL_GUIDE
            from cerebro_mcp.resources.reference import (
                ADDRESS_DIRECTORY,
                METRIC_DEFINITIONS,
                QUERY_COOKBOOK,
            )

            # Periodically check for docs refresh
            if docs_index.is_loaded and docs_index.last_load_time:
                if time.time() - docs_index.last_load_time > settings.DOCS_REFRESH_INTERVAL_SECONDS:
                    docs_index.reload_if_changed()

            # 1. Score existing static sources
            sources = {
                "Platform Overview": PLATFORM_OVERVIEW,
                "ClickHouse SQL Guide": CLICKHOUSE_SQL_GUIDE,
                "Address Directory": ADDRESS_DIRECTORY,
                "Metric Definitions": METRIC_DEFINITIONS,
                "Query Cookbook": QUERY_COOKBOOK,
            }

            raw_tokens = re.split(r"\s+", topic.lower())
            tokens = [t for t in raw_tokens if len(t) >= 3]
            if not tokens:
                tokens = raw_tokens

            scored_results = []
            for source_name, content in sources.items():
                sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
                for section in sections:
                    section_lower = section.lower()
                    hits = sum(1 for t in tokens if t in section_lower)
                    # Exact phrase boost
                    if topic.lower() in section_lower:
                        hits += 5
                    if hits > 0:
                        trimmed = section.strip()[:600]
                        if len(section.strip()) > 600:
                            trimmed += "\n...(truncated)"
                        scored_results.append(
                            (hits, f"**[Static: {source_name}]**\n{trimmed}")
                        )

            # 2. Search external docs corpora and merge
            if docs_index.is_loaded or docs_index.gnosis_chain_entry_count:
                doc_results = docs_index.search(topic, limit=10)

                for r in doc_results:
                    full_url = r.get("page_url") or docs_index.docs_base_url
                    detail_lines = []
                    if r.get("section"):
                        detail_lines.append(f"*Section:* {r['section']}")
                    if r.get("description"):
                        detail_lines.append(r["description"])
                    detail_lines.append(r["snippet"])
                    source_label = "Docs" if r.get("source") == "platform" else "Gnosis Chain Docs"
                    follow_up = (
                        f"*To read full text, call tool: `get_doc_chunk(\"{r['location']}\")`*"
                        if r.get("source") == "platform"
                        else f"*To read full text, call tool: `get_gnosis_chain_doc_chunk(\"{r['file_path']}\")`*"
                    )
                    text_block = (
                        f"**[{source_label}: {r['title']}]({full_url})**\n"
                        + "\n".join(detail_lines)
                        + "\n"
                        + follow_up
                    )
                    scored_results.append((r["score"], text_block))

            # 3. Sort merged results by relevance
            scored_results.sort(key=lambda x: -x[0])

            # Deduplicate
            seen_texts = set()
            unique_results = []
            for _score, text in scored_results:
                if text not in seen_texts:
                    seen_texts.add(text)
                    unique_results.append(text)
                    if len(unique_results) >= 10:
                        break

            if not unique_results:
                return (
                    f"No documentation found matching '{topic}'.\n\n"
                    "**Tips:** Use short keywords (e.g., 'bridge', 'gas', "
                    "'validator'). Individual topics work better than "
                    "long phrases."
                )

            header = f"# Documentation Search: '{topic}'\n\nFound {len(unique_results)} matching section(s).\n"
            if not docs_index.is_loaded and not docs_index.gnosis_chain_entry_count:
                header += "*(Note: External docs index is currently unavailable; showing static results only)*\n"
            header += "\n"

            body = "\n\n---\n\n".join(unique_results)
            return truncate_response(header + body)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_doc_chunk(location: str, max_chars: int = 6000) -> str:
        """Retrieve full text of a documentation page by its location path.

        Use this tool after search_docs returns a promising match to read the
        complete content of that page. This fetches the published Markdown
        mirror first and falls back to the search index excerpt if the mirror
        is unavailable.

        Args:
            location: Doc location path (e.g., 'data-pipeline/ingestion/cryo-indexer/').
            max_chars: Max characters to return (default 6000).

        Returns:
            The raw text content of the documentation page.
        """
        try:
            return docs_index.get_chunk(location, max_chars)
        except Exception as e:
            return f"Error retrieving document: {e}"

    @mcp.tool()
    def get_docs_overview(max_chars: int = 6000) -> str:
        """Retrieve the curated docs overview published at llms.txt.

        Args:
            max_chars: Max characters to return (default 6000).

        Returns:
            The generated docs overview file used to curate public docs for LLMs.
        """
        try:
            return docs_index.get_overview(max_chars)
        except Exception as e:
            return f"Error retrieving docs overview: {e}"

    @mcp.tool()
    def get_docs_context(full: bool = False, max_chars: int = 12000) -> str:
        """Retrieve the generated broad docs context artifact.

        Args:
            full: When true, fetch the full public corpus (`llms-ctx-full.txt`).
            max_chars: Max characters to return (default 12000).

        Returns:
            The generated docs context file for broad LLM grounding.
        """
        try:
            return docs_index.get_context(full=full, max_chars=max_chars)
        except Exception as e:
            return f"Error retrieving docs context: {e}"

    @mcp.tool()
    def get_gnosis_chain_docs_context(max_chars: int = 12000) -> str:
        """Retrieve the Gnosis Chain docs llms context artifact.

        Args:
            max_chars: Max characters to return (default 12000).

        Returns:
            The `https://docs.gnosischain.com/llms.txt` context artifact.
        """
        try:
            return docs_index.get_gnosis_chain_context(max_chars=max_chars)
        except Exception as e:
            return f"Error retrieving Gnosis Chain docs context: {e}"

    @mcp.tool()
    def get_gnosis_chain_doc_chunk(file_path: str, max_chars: int = 6000) -> str:
        """Retrieve a single Gnosis Chain docs section from the llms artifact.

        Args:
            file_path: The `// File:` path from `docs.gnosischain.com/llms.txt`, or its page path/URL.
            max_chars: Max characters to return (default 6000).

        Returns:
            The matching Gnosis Chain docs chunk.
        """
        try:
            return docs_index.get_gnosis_chain_chunk(file_path, max_chars=max_chars)
        except Exception as e:
            return f"Error retrieving Gnosis Chain docs chunk: {e}"

    @mcp.tool()
    def get_help() -> str:
        """Overview of all available tools, prompts, and resources in Cerebro MCP.

        Call this to discover what you can do with the Gnosis Chain data platform.

        Returns:
            Structured overview of all capabilities with examples.
        """
        return """\
# Cerebro MCP — Capabilities

## Tools

### Query & Schema
| Tool | Description |
|------|-------------|
| `execute_query` | Run read-only SQL against ClickHouse |
| `start_query` / `get_query_results` | Async execution for long-running queries |
| `explain_query` | Show ClickHouse execution plan |
| `list_tables` | List tables in a database with row counts |
| `describe_table` | Column schema with dbt descriptions |
| `get_sample_data` | Sample rows to understand data shape |

### dbt Models
| Tool | Description |
|------|-------------|
| `discover_models` | Search + expand top model details in one call (preferred first step) |
| `search_models` | Search ~400 dbt models by name, description, tags, or module |
| `get_model_details` | Full model info: SQL, columns, lineage, dependencies |
| `search_models_by_address` | Find dbt models related to a contract address |

### Semantic Analytics
| Tool | Description |
|------|-------------|
| `preflight_analytics_request` | Route an analytics question to the best workflow |
| `discover_metrics` | Search governed metrics by name, synonym, or module |
| `get_metric_details` | Metric lineage, allowed dimensions, grains, docs |
| `explain_metric_query` | Show the planner's plan and compiled SQL without executing |
| `query_metrics` | Execute governed metrics with semantic provenance and auto-repair |
| `quick_metric_chart` | One-off semantic chart without writing SQL |
| `generate_metric_charts` | Batch semantic charts for reports |
| `get_clickhouse_query_rules` | ClickHouse best-practice rules for raw SQL fallback |

### Visualization & Reports
| Tool | Description |
|------|-------------|
| `quick_chart` | One-off chart, bypasses workflow gates |
| `generate_chart` | Single gated chart |
| `generate_charts` | Batch chart creation for reports (preferred) |
| `generate_report` | Assemble interactive report with chart placeholders |
| `list_charts` | Show registered charts in current session |
| `open_report` | Reopen a saved report by ID |
| `list_reports` | List all saved reports on disk |
| `export_report` | Get file path or download URL for a report |

### Mini-App Interactive Views
| Tool | Description |
|------|-------------|
| `open_metric_lab` | Open interactive metric explorer with catalog picker |
| `open_metric_lab_from_sql` | Load a custom SQL dataset into Metric Lab |
| `open_metric_lab_from_metrics` | Load a semantic metric into Metric Lab |
| `load_metric_lab_metric` | Swap metrics in an open Metric Lab view |
| `update_metric_lab_chart` | Change chart type, axes, or aggregation |
| `open_token_explorer` | Open interactive token explorer with bridge flows and prices |
| `load_token_explorer_token` | Swap tokens in an open Token Explorer view |
| `update_token_explorer_focus` | Filter by metric, bridge, or direction |
| `open_graph_explorer` | Open the cross-sector semantic Graph Explorer (Circles trust, Safe/GPay ownership, transfers, LP, validators, bridges, labels) |
| `load_graph_explorer_seed` | Seed a 1-hop subgraph around an address; auto-detects profiles via address_roles_current |
| `expand_graph_explorer_node` | Expand a node by one hop (max 2 hops per session) |
| `update_graph_explorer_focus` | Change selection, relation filters, layout, or time window |

### Custom Query Tools
| Tool | Description |
|------|-------------|
| `list_custom_tools` | Show available parameterized query tools |
| `get_bridge_flows_by_token` | Bridge inflow/outflow by token and date range |
| `get_deposit_events` | Beacon chain deposit events |
| `get_gpay_wallet_activity` | Gnosis Pay wallet transaction history |
| `get_liquidity_providers_by_token` | LP positions for a token |
| `get_token_transfers_for_address` | ERC-20 transfers for an address |
| `get_validator_balance_history` | Validator balance over time |
| `get_validator_withdrawals` | Validator withdrawal events |

### Metadata & Reference
| Tool | Description |
|------|-------------|
| `list_databases` | All ClickHouse databases with descriptions |
| `system_status` | Server health: ClickHouse, manifest, semantic, config |
| `get_help` | This capabilities overview |
| `resolve_address` | Look up address labels (5.3M entries from Dune) |
| `get_token_metadata` | Token info: address, decimals, price data |
| `search_docs` | Search platform documentation and references |
| `get_doc_chunk` | Read full text of a documentation page |
| `get_docs_overview` | Read the curated `llms.txt` docs index |
| `get_docs_context` | Read the generated broad docs context corpus |
| `get_gnosis_chain_docs_context` | Read the Gnosis Chain docs `llms.txt` context |
| `get_gnosis_chain_doc_chunk` | Read a single chunk from Gnosis Chain docs `llms.txt` |
| `get_platform_constants` | Chain params, event signatures, contracts, partition keys |

### Saved Queries
| Tool | Description |
|------|-------------|
| `save_query` | Save a query for reuse |
| `list_saved_queries` | Show all saved queries |
| `run_saved_query` | Execute a saved query by name |

### Verification
| Tool | Description |
|------|-------------|
| `verify_numbers` | Check arithmetic and cross-reference computed numbers before reporting |

### Research Workflow
| Tool | Description |
|------|-------------|
| `start_research_project` | Create a durable phase-driven research project |
| `get_research_project` | Compact project summary with counts and current phase |
| `plan_research_phase` / `execute_research_phase` | Move through mapping, hypothesis, execution, verification, publication |
| `attach_research_evidence` | Link query/chart/report/schema artifacts to a project |
| `capture_schema_snapshot` | Persist and attach a schema snapshot for mapping |
| `record_research_memory` | Store reusable domain caveats and facts |
| `record_research_finding` | Store evidence-backed project conclusions |
| `get_research_memory` / `get_research_evidence` / `get_research_findings` | Paginated retrieval for large projects |
| `verify_research_phase` | Run structural validation before peer review |
| `prepare_peer_review` / `record_peer_review` | Client-orchestrated adversarial review workflow |
| `publish_research_report` | Publish the final reviewed research report |

### Storyteller Workflow
| Tool | Description |
|------|-------------|
| `storyteller_start_session` | Start a gated narrative pipeline for decision-oriented artifacts |
| `storyteller_status` | Check current phase and gate status |
| `storyteller_record_context_brief` | Record audience, required action, mechanism, tone (Gate 1) |
| `storyteller_record_big_idea` | One declarative sentence with stakes (Gate 2) |
| `storyteller_record_storyboard` | Scene sequence: setup, tension, evidence, resolution (Gate 3) |
| `storyteller_record_visual_spec` | Chart design rationale per scene (Gate 4) |
| `storyteller_record_final_story` | Assembled markdown with chart placeholders (Gate 5) |
| `storyteller_run_clarity_checks` | Adversarial review — loops back on failure (Gate 6) |
| `storyteller_record_accessibility_pass` | Colorblind, contrast, language check (Gate 7) |
| `storyteller_generate_story_report` | Render final story through the report pipeline |
| `storyteller_end_session` | Abandon and clear session |

### Dashboard Builder
| Tool | Description |
|------|-------------|
| `discover_dashboard_metrics` | Browse api_* models for dashboard building |
| `scaffold_dashboard_tab` | Generate JS query files + YAML config for a new dashboard tab |

### Reasoning & Tracing
| Tool | Description |
|------|-------------|
| `set_thinking_mode` | Enable/disable reasoning capture |
| `log_reasoning` | Record a decision point for audit |
| `get_reasoning_log` | Retrieve trace for a session |
| `get_performance_stats` | Aggregate metrics across sessions |

### Agent Personas
| Tool | Description |
|------|-------------|
| `get_agent_persona` | Load operational rules for a persona |

Available personas:

| Persona | Role |
|---------|------|
| `analytics_reporter` | Data Science Lead — analysis and EDA discipline |
| `ui_designer` | Chart selection and report composition |
| `reality_checker` | QA and adversarial review |
| `storyteller_orchestrator` | Storyteller mode selection and gate enforcement |
| `storyteller_context` | Audience, required action, mechanism, tone |
| `storyteller_narrative` | Big idea and storyboard construction |
| `storyteller_visual_designer` | Relationship-first chart choice, focal-point design |
| `storyteller_writer` | Action titles, annotations, medium adaptation |
| `storyteller_critic` | Adversarial clarity review |
| `storyteller_accessibility` | Colorblind palette, contrast, language, tone match |
| `forecasting_analyst` | Time-series forecasting, trend decomposition, prediction |
| `growth_analyst` | Retention cohorts, funnels, DAU/WAU/MAU, user segmentation |
| `tokenomics_analyst` | Staking economics, token velocity, supply concentration |
| `defi_analyst` | TVL, utilization, liquidation risk, impermanent loss |
| `network_health_analyst` | Client diversity, peer distribution, network partition risk |
| `bridge_security_analyst` | Bridge flow anomalies, efficiency comparison, arbitrage signals |
| `marketing_analyst` | Ecosystem growth framing, competitive benchmarking, external comms |
| `esg_analyst` | Carbon footprint, energy efficiency, sustainability reporting |
| `statistical_reviewer` | Confidence intervals, effect sizes, methodology validation |
| `mmm_analyst` | Marketing Mix Modeling SOP — adstock, response curves, contribution decomposition |
| `mmm_causal_reviewer` | DAG gate before MMM `generate_report`: chronological / non-inclusion / identifiability |
| `mmm_simulator` | Bounded MMM budget reallocation and marginal ROI (±30% per period cap) |
| `mta_analyst` | Multi-Touch Attribution: journey paths, funnels, rule-based + Markov / Shapley credit |
| `unified_causal_reviewer` | MMM/MTA reconciliation gate: incrementality bound, coverage, leakage, identity grain |
| `unified_allocator` | Bounded micro / tactical allocation using MMM lift and MTA shares (calibrated) |
| `cerebro_dispatcher` | Top-level intent triage and gated specialist routing |

## Prompts

Guided workflows you can select in Claude Desktop / VS Code:

| Prompt | Description |
|--------|-------------|
| `getting_started` | Onboarding guide with example workflows |
| `analyze_data(topic)` | Guided data analysis on any topic |
| `explore_protocol(protocol)` | Explore a DeFi protocol's on-chain data |
| `write_query(question)` | Step-by-step SQL query writing |
| `report(period, topics, focus)` | Generate interactive reports with charts |
| `adopt_persona_analytics_reporter` | Load analytics reporter persona |
| `adopt_persona_gnosis_research_analyst` | Load semantic-first research persona |
| `adopt_persona_ui_designer` | Load UI/chart design persona |
| `adopt_persona_reality_checker` | Load QA review persona |
| `conduct_research_peer_review(packet_json)` | Peer-review prompt with strict JSON schema |

## Resources

Reference materials available via MCP resource protocol:

| Resource | Description |
|----------|-------------|
| `gnosis://platform-overview` | Architecture, databases, dbt modules |
| `gnosis://clickhouse-sql-guide` | ClickHouse syntax and common patterns |
| `gnosis://chain-parameters` | Block time, tokens, validators, specs |
| `gnosis://address-directory` | Token addresses, DeFi protocols |
| `gnosis://metric-definitions` | Standard metric formulas (DAU, gas, TVL) |
| `gnosis://query-cookbook` | 12 optimized SQL templates with examples |
| `gnosis://semantic-model/{name}` | Semantic model details and lineage |
| `gnosis://semantic-metric/{name}` | Semantic metric definition and dimensions |
| `gnosis://semantic-relationship/{name}` | Semantic relationship between models |
| `gnosis://semantic-module/{module}` | All models and metrics in a module |
| `gnosis://semantic-graph-overview` | Full semantic graph summary |

## Quick Start Examples

Try asking:
- "What data is available?" — uses `list_databases` and `discover_models`
- "Show me transaction trends this week" — queries data and generates charts
- "What are the bridge flows for USDC?" — uses `get_bridge_flows_by_token`
- "Show transaction count by sector" — uses `discover_metrics` and `query_metrics`
- "Explore the Circles protocol" — finds decoded contract events
- "Look up address 0x9c58ba..." — uses `resolve_address` for label lookup
- "Give me a weekly report" — full report workflow with interactive charts
- "Write me an executive brief on validator performance" — storyteller workflow
- "What is validator 12345's balance history?" — uses `get_validator_balance_history`
"""

    @mcp.tool()
    def get_platform_constants() -> str:
        """Returns hardcoded Gnosis Chain platform constants: chain parameters,
        common event signatures (topic0 hashes), core infrastructure contracts,
        table partition keys, and table scale estimates.

        Call this before writing queries against raw execution/consensus tables,
        when filtering logs by topic0, or when referencing core contract addresses.

        Returns:
            Markdown with chain constants, event signatures, contracts, partition
            keys, and table scale for query planning.
        """
        lines = ["# Gnosis Chain Platform Constants\n"]

        # 1. Chain Constants
        lines.append("## Chain Constants\n")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        for key, val in CHAIN_CONSTANTS.items():
            lines.append(f"| {key} | {val} |")

        # 2. Event Signatures
        lines.append("\n## Common Event Signatures (execution.logs topic0)\n")
        lines.append("| Event | topic0 | Signature | Notes |")
        lines.append("|-------|--------|-----------|-------|")
        for name, info in COMMON_EVENT_SIGNATURES.items():
            lines.append(
                f"| {name} | `{info['topic0']}` | `{info['signature']}` | {info['notes']} |"
            )

        # 3. Infrastructure Contracts
        lines.append("\n## Core Infrastructure Contracts\n")
        lines.append("| Key | Address | Name | Type |")
        lines.append("|-----|---------|------|------|")
        for key, info in CORE_INFRASTRUCTURE_CONTRACTS.items():
            lines.append(
                f"| {key} | `{info['address']}` | {info['name']} | {info['type']} |"
            )

        # 4. Table Partition Keys
        lines.append("\n## Table Partition Keys\n")
        lines.append("| Database | Time Column | Partition Expression | Requires FINAL | Notes |")
        lines.append("|----------|-------------|---------------------|----------------|-------|")
        for db, info in TABLE_PARTITION_KEYS.items():
            lines.append(
                f"| {db} | {info['time_column']} | `{info['partition_expr']}` "
                f"| {'Yes' if info['requires_final'] else 'No'} | {info['notes']} |"
            )

        # 5. Table Scale
        lines.append("\n## Table Scale (approximate, for query planning)\n")
        lines.append("| Table | Rows | Size | Caution |")
        lines.append("|-------|------|------|---------|")
        for table, info in TABLE_ROW_SCALE.items():
            lines.append(
                f"| {table} | {info['approx_rows']} | {info['approx_size']} "
                f"| {info['caution'].upper()} |"
            )

        lines.append("\n**Query Planning Tips:**")
        lines.append("- HIGH caution: MUST include partition key filter AND additional filters (topic0, address)")
        lines.append("- MEDIUM caution: partition key filter required, additional filters recommended")
        lines.append("- Always prefer dbt api_*/fct_* models over raw tables when available")
        lines.append("- Use FINAL on execution/consensus raw tables (ReplacingMergeTree)")

        return truncate_response("\n".join(lines))
