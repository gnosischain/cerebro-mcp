import re
import time
from datetime import datetime, timezone

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.docs_loader import docs_index
from cerebro_mcp.manifest_loader import manifest
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
        lines.append(
            f"- **Source:** {settings.DOCS_SEARCH_INDEX_URL or settings.DOCS_SEARCH_INDEX_PATH or 'none'}"
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
        lines.append(f"- QUERY_TIMEOUT_SECONDS: {settings.QUERY_TIMEOUT_SECONDS}")
        lines.append(f"- TOOL_RESPONSE_MAX_CHARS: {settings.TOOL_RESPONSE_MAX_CHARS}")
        lines.append(f"- MANIFEST_REFRESH_INTERVAL_SECONDS: {settings.MANIFEST_REFRESH_INTERVAL_SECONDS}")
        lines.append(f"- ALLOWED_DATABASES: {', '.join(settings.ALLOWED_DATABASES)}")

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

            # 2. Search external MkDocs index and merge
            if docs_index.is_loaded:
                doc_results = docs_index.search(topic, limit=10)
                base_docs_url = "https://docs.analytics.gnosis.io/"

                for r in doc_results:
                    full_url = f"{base_docs_url}{r['location']}"
                    text_block = (
                        f"**[Docs: {r['title']}]({full_url})**\n"
                        f"{r['snippet']}\n"
                        f"*To read full text, call tool: `get_doc_chunk(\"{r['location']}\")`*"
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
            if not docs_index.is_loaded:
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
        complete content of that page.

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
