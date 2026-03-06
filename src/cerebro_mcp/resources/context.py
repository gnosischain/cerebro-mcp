from cerebro_mcp.manifest_loader import manifest


PLATFORM_OVERVIEW = """\
# Gnosis Chain Data Platform

## Architecture
```
Blockchain RPCs --> cryo-indexer --> ClickHouse (execution, consensus)
External APIs  --> click-runner --> ClickHouse (crawlers_data)
P2P Crawlers   --> nebula       --> ClickHouse (nebula)
                                        |
                                   dbt-cerebro (387 models)
                                        |
                                  ClickHouse (dbt)
                                   /         \\
                            cerebro-api    cerebro-mcp
                            (REST API)     (LLM interface)
```

## Databases (all on same ClickHouse Cloud instance)

| Database | Purpose | Key Tables |
|----------|---------|------------|
| execution | On-chain L1 data | blocks, transactions, logs, traces, native_transfers, contracts, balance/code/nonce/storage_diffs, withdrawals |
| consensus | Beacon chain | blocks, attestations, validators, withdrawals, deposits, rewards, blob_commitments, blob_sidecars |
| crawlers_data | Off-chain data | dune_labels, dune_prices, dune_bridge_flows, dune_gno_supply, ember_electricity_data, probelab stats |
| nebula | P2P network | crawls, visits |
| dbt | Transformed models | ~400 views/tables organized in 8 modules |

## dbt Model Naming Convention
- `stg_*` = Staging (minimal cleaning of raw source tables)
- `int_*` = Intermediate (business logic, aggregations, joins)
- `api_*` = API/reporting tier (daily/weekly aggregates, ready for consumption)
- `fct_*` = Fact tables (event-based, immutable records)
- `contracts_*` = Decoded smart contract calls and events

## dbt Modules (8)
- **execution** (208 models): Blocks, transactions, transfers, tokens, state, prices, pools, yields, DEX analytics
- **consensus** (54 models): Validators, attestations, rewards, blob data, network health
- **contracts** (44 models): Decoded events/calls for 15+ protocols (Aave, Balancer, Uniswap, Swapr, Circles, etc.)
- **p2p** (27 models): Network topology, peer distribution, client diversity
- **bridges** (18 models): Cross-chain bridge flow analytics
- **ESG** (18 models): Environmental metrics, energy consumption
- **probelab** (9 models): Network probe statistics
- **crawlers_data** (9 models): Dune labels, prices, supply data

## Tips
- Prefer querying `dbt` database models over raw tables — they have cleaner schemas and documentation
- Use `search_models` to find relevant pre-computed models before writing raw SQL
- Raw tables in `execution` use ReplacingMergeTree — add FINAL to get deduplicated results
- All timestamps are UTC
- Addresses are lowercase hex strings (0x-prefixed in most tables)
"""

CLICKHOUSE_SQL_GUIDE = """\
# ClickHouse SQL Guide for Gnosis Chain Data

## Key Differences from Standard SQL
- Use backticks or double quotes for identifiers with special chars
- No UPDATE/DELETE (append-only, use ReplacingMergeTree for dedup)
- Use `FINAL` keyword after table name to get latest version of rows in ReplacingMergeTree tables
- Arrays are first-class: `arrayJoin()`, `groupArray()`, `arrayMap()`
- Use `Nullable()` types carefully — they have performance implications

## Common Date/Time Functions
```sql
-- Date manipulation
toDate('2024-01-15')
toStartOfDay(timestamp)
toStartOfWeek(timestamp)
toStartOfMonth(timestamp)
toStartOfYear(timestamp)
dateDiff('day', start_date, end_date)
now()
today()
yesterday()

-- Date formatting
formatDateTime(timestamp, '%Y-%m-%d')
toYYYYMM(date)
```

## Common Aggregate Functions
```sql
count()
uniq(column)           -- Approximate unique count (fast)
uniqExact(column)      -- Exact unique count (slower)
quantile(0.95)(column) -- Percentile
groupArray(column)     -- Collect into array
sumIf(amount, condition)
countIf(condition)
```

## Type Casting
```sql
toUInt64(value)
toString(value)
toFloat64(value)
toDecimal128(value, 18)  -- For wei amounts
reinterpretAsUInt256(unhex(substr(hex_string, 3)))  -- Hex to uint256
lower(address)  -- Normalize addresses
```

## Common Query Patterns

### Count transactions per day
```sql
SELECT toDate(block_timestamp) AS day, count() AS tx_count
FROM execution.transactions
WHERE block_timestamp >= today() - 30
GROUP BY day
ORDER BY day
```

### Query with FINAL for ReplacingMergeTree
```sql
SELECT * FROM execution.blocks FINAL
WHERE block_number > 30000000
LIMIT 10
```

### Cross-database join
```sql
SELECT t.transaction_hash, l.address, l.label
FROM execution.transactions AS t
LEFT JOIN crawlers_data.dune_labels AS l
  ON lower(t.from_address) = lower(l.address)
WHERE t.block_timestamp >= today() - 1
LIMIT 100
```

### Using dbt models (preferred)
```sql
-- Pre-computed daily aggregates are faster than raw table scans
SELECT * FROM dbt.api_execution_transactions_7d
LIMIT 100
```

## Gotchas
- `DateTime64` vs `DateTime` vs `Date` — be careful with comparisons
- Hex values: addresses are `String` type with '0x' prefix, use `lower()` for case-insensitive matching
- Large scans: Always include date filters on partitioned tables to avoid scanning all data
- `value_string` / `value_f64` in transfers: value_f64 is approximate, value_string is exact
- ReplacingMergeTree: Without `FINAL`, you may get duplicate rows for updated records
"""


def register_resources(mcp):
    @mcp.resource("gnosis://platform-overview")
    def platform_overview() -> str:
        """Overview of the Gnosis Chain data platform architecture, databases, and conventions."""
        return PLATFORM_OVERVIEW

    @mcp.resource("gnosis://clickhouse-sql-guide")
    def clickhouse_sql_guide() -> str:
        """ClickHouse-specific SQL syntax guide with common patterns for Gnosis Chain data."""
        return CLICKHOUSE_SQL_GUIDE

    @mcp.resource("gnosis://dbt-modules/{module_name}")
    def dbt_module_context(module_name: str) -> str:
        """Detailed listing of dbt models for a specific module."""
        if not manifest.is_loaded:
            return "dbt manifest not loaded."

        models = manifest.get_module_models(module_name)
        if not models:
            available = ", ".join(manifest.get_modules().keys())
            return f"Module '{module_name}' not found. Available: {available}"

        lines = [f"# dbt Module: {module_name}\n", f"Models: {len(models)}\n"]

        # Group by layer
        staging = [m for m in models if m["name"].startswith("stg_")]
        intermediate = [m for m in models if m["name"].startswith("int_")]
        marts = [
            m
            for m in models
            if m["name"].startswith(("api_", "fct_"))
        ]
        other = [
            m
            for m in models
            if not m["name"].startswith(("stg_", "int_", "api_", "fct_"))
        ]

        for label, group in [
            ("Staging (stg_*)", staging),
            ("Intermediate (int_*)", intermediate),
            ("Marts/API (api_*/fct_*)", marts),
            ("Other", other),
        ]:
            if group:
                lines.append(f"\n## {label} ({len(group)} models)\n")
                for m in group:
                    desc = m["description"][:150] if m["description"] else ""
                    lines.append(
                        f"- **{m['name']}** ({m['materialized']}): {desc}"
                    )

        return "\n".join(lines)

    @mcp.resource("gnosis://source-tables/{database}")
    def source_tables(database: str) -> str:
        """Raw source table schemas for a given database from dbt source definitions."""
        if not manifest.is_loaded:
            return "dbt manifest not loaded."

        sources = manifest.get_sources_for_database(database)
        if not sources:
            return f"No source definitions found for database '{database}'."

        lines = [f"# Source Tables: {database}\n"]

        for src in sources:
            lines.append(f"\n## {src['name']}")
            if src["description"]:
                lines.append(f"{src['description']}")
            if src["columns"]:
                lines.append("\n| Column | Type | Description |")
                lines.append("|--------|------|-------------|")
                for col_name, col_info in src["columns"].items():
                    dtype = col_info.get("data_type", "")
                    desc = col_info.get("description", "")
                    lines.append(f"| {col_name} | {dtype} | {desc} |")

        return "\n".join(lines)
