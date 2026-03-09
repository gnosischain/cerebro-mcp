from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.tools.query import truncate_response


# --- Static Resource Content ---

CHAIN_PARAMETERS_PREAMBLE = """\
# Gnosis Chain Parameters

## Critical Differences from Ethereum Mainnet
| Parameter | Gnosis Chain | Ethereum Mainnet |
|-----------|-------------|-----------------|
| Block time | 5 seconds | 12 seconds |
| Blocks per day | ~17,280 | ~7,200 |
| Native gas token | xDAI | ETH |
| Staking token | GNO | ETH |
| Stake per validator | 1 GNO | 32 ETH |
| Chain ID | 100 | 1 |
| Slots per epoch | 16 | 32 |
| Epoch duration | 80 seconds | 384 seconds |
| Consensus mechanism | Beacon Chain (PoS) | Beacon Chain (PoS) |

## Key Addresses
- Deposit contract: `0x0b98057ea310f4d31f2a452b414647007d1645d9`
- WXDAI: `0xe91d153e0b41518a2ce8dd3d7944fa863463a97d`
- GNO: `0x9c58bacc331c9aa871afd802db6379a98e80cedb`

## Dual-Token Model
- **xDAI**: Native gas token. Bridged DAI from Ethereum, pegged to 1 USD. Used for transaction fees and payments.
- **GNO**: Governance and staking token. 1 GNO required per validator. Used for protocol governance.

## Consensus Specification Parameters
"""

ADDRESS_DIRECTORY = """\
# Gnosis Chain Address Directory

## Native & Major Tokens

| Token | Symbol | Address | Decimals | Notes |
|-------|--------|---------|----------|-------|
| xDAI | xDAI | native (no contract) | 18 | Native gas token, bridged DAI |
| Wrapped xDAI | WXDAI | 0xe91d153e0b41518a2ce8dd3d7944fa863463a97d | 18 | ERC-20 wrapped xDAI |
| Gnosis | GNO | 0x9c58bacc331c9aa871afd802db6379a98e80cedb | 18 | Governance & staking token |
| Wrapped ETH | WETH | 0x6a023ccd1ff6f2045c3309768ead9e68f978f6e1 | 18 | Bridged via Omnibridge |
| USD Coin | USDC | 0xddafbb505ad214d7b80b1f830fccc89b60fb7a83 | 6 | Bridged from Ethereum |
| Tether | USDT | 0x4ecaba5870353805a9f068101a40e0f32ed605c6 | 6 | Bridged from Ethereum |
| Monerium EUR | EURe | 0xcb444e90d8198415266c6a2724b7900fb12fc56e | 18 | EUR-pegged stablecoin |
| Wrapped stETH | wstETH | 0x6c76971f98945ae98dd7d4dfca8711ebea946ea6 | 18 | Lido wrapped staked ETH |
| Savings DAI | sDAI | 0xaf204776c7245bf4147c2612bf6e5972ee483701 | 18 | MakerDAO savings DAI |
| CoW Protocol | COW | 0x177127622c4a00f3d409b75571e12cb3c8973d3c | 18 | CoW Protocol governance |
| Safe | SAFE | 0x4d18815d14fe5c3304e87b3ea4a5383f683cb578 | 18 | Safe governance token |

## Decimal Quick Reference
- **18 decimals**: xDAI, WXDAI, GNO, WETH, EURe, wstETH, sDAI, COW, SAFE
- **6 decimals**: USDC, USDT
- When aggregating token values, ALWAYS divide by 10^decimals

## Top DeFi Protocols

### DEXs
| Protocol | Type | Key Contract | Address |
|----------|------|-------------|---------|
| Honeyswap | UniswapV2 fork | Router | 0x1c232f01118cb8b424793ae03f870aa7d0ac7f77 |
| SushiSwap | UniswapV2 fork | TridentRouter | 0xcaabdd9cf4b61813d4a52f980d6bc1b713fe66f5 |
| Swapr | DXswap | Factory | 0x5d48c95adffd4b40c1aaadc4e08fc44117e02179 |
| Swapr V3 | Algebra V3 | Factory | 0xa0864cca6e114013ab0e27cbd5b6f4c8947da766 |
| Uniswap V3 | Concentrated liquidity | Factory | 0xe32f7dd7e3f098d518ff19a22d5f028e076489b1 |
| Balancer V2 | Weighted pools | Vault | 0xba12222222228d8ba445958a75a0704d566bf2c8 |
| Balancer V3 | Next-gen pools | Vault | 0xba1333333333a1ba1108e8412f11850a5c319ba9 |
| CoW Protocol | Batch auctions | Settlement | 0x177127622c4a00f3d409b75571e12cb3c8973d3c |

### Lending
| Protocol | Type | Key Contract | Address |
|----------|------|-------------|---------|
| Agave | Aave V2 fork | LendingPool (proxy) | 0x5e15d5e33d318dced84bfe3f4eace07909be6d9c |
| Aave V3 | Lending protocol | Pool (proxy) | 0xb50201558b00496a145fe76f7424749556e326d8 |

### Bridges
| Protocol | Type | Key Contract | Address |
|----------|------|-------------|---------|
| xDAI Bridge | Canonical DAI bridge | Mediator | 0x7301cfa0e1756b71869e93d4e4dca5c7d0eb0aa6 |
| Omnibridge | ERC-20 bridge | Mediator | 0x6a023ccd1ff6f2045c3309768ead9e68f978f6e1 |

### Other Protocols
| Protocol | Type | Key Contract | Address |
|----------|------|-------------|---------|
| Circles | UBI protocol | Hub V1 | 0x29b9a7fbb8995b2423a71cc17cf9810798f6c543 |
| Circles V2 | UBI protocol | Hub V2 | 0xc12c1e50abb450d6205ea2c3fa861b3b834d13e8 |
| Gnosis Pay | Payment card | Main contract | 0x90830ed558f12d826370dc52e9d87947a7f18de9 |
| Gnosis Pay | Payment card | Spender | 0xcff260bfbc199dc82717494299b1acade25f549b |

## Note
For full address-to-label resolution (5.3M labeled addresses), use the `resolve_address` tool.
For contract-to-dbt-model mapping, use the `search_models_by_address` tool.
"""

METRIC_DEFINITIONS = """\
# Gnosis Chain Metric Definitions

Standard formulas for consistent metric calculation. Always use these definitions.

## Daily Active Users (DAU)
**Definition:** Count of unique sender addresses with at least one successful transaction per day.
**Preferred model:** `dbt.api_execution_dau_daily` (if available)
**Raw SQL:**
```sql
SELECT toDate(block_timestamp) AS day,
       uniqExact(from_address) AS dau
FROM execution.transactions
WHERE status = 1
  AND block_timestamp >= today() - 30
GROUP BY day ORDER BY day
```
**Rules:**
- MUST filter `status = 1` (successful transactions only)
- Use `uniqExact()` for precise count; `uniq()` acceptable for trend analysis
- Count `from_address` only (senders, not receivers)

## Active Validators
**Definition:** Validators with status in ('active_ongoing', 'active_exiting', 'active_slashed').
**Preferred model:** `dbt.api_consensus_validators_active_daily`
**Rules:**
- Each validator requires 1 GNO staked (NOT 32 ETH)
- Use consensus-layer data, not execution-layer

## Transaction Volume
**Definition:** Count of successful transactions per time period.
**Preferred model:** `dbt.api_execution_transactions_7d` or similar time-bucketed models
**Rules:**
- Always filter `status = 1`
- Specify time granularity (daily/weekly/monthly)
- Gnosis has ~17,280 blocks/day (5-second block time)

## Gas Usage & Utilization
**Definition:** `gas_used / gas_limit` per block for utilization percentage.
**Preferred model:** `dbt.api_execution_blocks_gas_usage_pct_daily`
**Rules:**
- Report as percentage (0-100%)
- Gnosis has 5-second blocks, so daily block count is ~17,280

## Bridge Volumes
**Definition:** Inflow/outflow of assets bridged to/from Gnosis Chain, measured in USD.
**Preferred model:** `dbt.api_bridges_cum_netflow_weekly_by_bridge`
**Rules:**
- Net flow = inflow - outflow
- Always include bridge name for per-bridge breakdown
- Use `dbt.api_bridges_*` models for pre-computed aggregates

## Token Transfer Volume
**Rules:**
- ALWAYS divide raw values by `10^decimals` before aggregating
- xDAI/GNO/WETH/EURe/wstETH/sDAI = 18 decimals (divide by 1e18)
- USDC/USDT = 6 decimals (divide by 1e6)
- For USD conversion, join with `crawlers_data.dune_prices` on (block_date, symbol)
- Use `get_token_metadata` tool to verify decimals if unsure

## DEX Volume
**Preferred models:** Check `dbt` for `contracts_*_events` models per protocol
**Rules:**
- Volume = sum of absolute swap amounts converted to USD
- Use decoded swap events from the contracts module

## Staking Metrics
**Preferred models:**
- Total staked GNO: `dbt.api_consensus_staked_daily`
- Validator APY: `dbt.api_consensus_info_apy_latest`
- Entry queue: `dbt.api_consensus_entry_queue_daily`
**Rules:**
- 1 GNO = 1 validator
- APY calculated from consensus rewards, not token price appreciation

## Client Diversity
**Preferred models:**
- Execution clients: `dbt.api_execution_blocks_clients_pct_daily`
- Consensus clients: `dbt.api_consensus_blocks_clients_pct_daily`
- P2P crawl data: `dbt.api_p2p_*` models
"""

QUERY_COOKBOOK = """\
# Gnosis Chain Query Cookbook

Optimized, tested ClickHouse SQL templates. Prefer dbt models over raw tables.

## 1. Daily Transaction Count
**Use dbt model when available:**
```sql
SELECT * FROM dbt.api_execution_transactions_7d
ORDER BY day DESC
```
**Raw table fallback:**
```sql
SELECT toDate(block_timestamp) AS day,
       count() AS tx_count,
       uniqExact(from_address) AS unique_senders
FROM execution.transactions
WHERE block_timestamp >= today() - 30
  AND status = 1
GROUP BY day ORDER BY day
```

## 2. Top Gas Consumers (last 7 days)
```sql
SELECT t.to_address AS contract,
       l.label,
       count() AS tx_count,
       sum(t.gas_used) AS total_gas
FROM execution.transactions AS t
LEFT JOIN crawlers_data.dune_labels AS l
  ON lower(t.to_address) = lower(l.address)
WHERE t.block_timestamp >= today() - 7
  AND t.status = 1
GROUP BY contract, l.label
ORDER BY total_gas DESC
LIMIT 20
```

## 3. Active Validators Over Time
```sql
SELECT * FROM dbt.api_consensus_validators_active_daily
WHERE day >= today() - 30
ORDER BY day
```

## 4. Bridge Net Flows (weekly, by bridge)
```sql
SELECT * FROM dbt.api_bridges_cum_netflow_weekly_by_bridge
ORDER BY week DESC
LIMIT 20
```

## 5. Token Price Lookup
```sql
SELECT block_date, symbol, price
FROM crawlers_data.dune_prices
WHERE symbol = 'GNO'
  AND block_date >= today() - 30
ORDER BY block_date
```

## 6. Address Label Lookup
```sql
SELECT address, label
FROM crawlers_data.dune_labels
WHERE lower(address) = lower('0x...')
LIMIT 10
```
**Tip:** Use the `resolve_address` tool for convenience.

## 7. Gas Utilization Trend
```sql
SELECT * FROM dbt.api_execution_blocks_gas_usage_pct_daily
WHERE day >= today() - 90
ORDER BY day
```

## 8. Execution Client Diversity
```sql
SELECT * FROM dbt.api_execution_blocks_clients_pct_daily
WHERE day >= today() - 30
ORDER BY day
```

## 9. Consensus Client Diversity
```sql
SELECT * FROM dbt.api_consensus_blocks_clients_pct_daily
WHERE day >= today() - 30
ORDER BY day
```

## 10. Native Transfer Volume (xDAI)
```sql
SELECT toDate(block_timestamp) AS day,
       count() AS transfer_count,
       sum(toFloat64(value) / 1e18) AS xdai_volume
FROM execution.native_transfers FINAL
WHERE block_timestamp >= today() - 30
GROUP BY day ORDER BY day
```
**Warning:** Native transfers use xDAI (18 decimals). Always divide by 1e18.

## 11. Contract Event Frequency
```sql
SELECT event_name, count() AS occurrences
FROM dbt.contracts_aave_events
WHERE block_timestamp >= today() - 7
GROUP BY event_name
ORDER BY occurrences DESC
LIMIT 20
```
Replace `contracts_aave_events` with the relevant protocol's event table.

## 12. Validator Staking Metrics
```sql
SELECT * FROM dbt.api_consensus_staked_daily
WHERE day >= today() - 90
ORDER BY day
```

## Query Optimization Checklist
- [ ] Include WHERE on partition key (block_timestamp, block_date, slot)
- [ ] Check for dbt api_*/fct_* model before querying raw tables
- [ ] Add FINAL for ReplacingMergeTree raw tables (execution, consensus)
- [ ] Use LIMIT to cap result size
- [ ] Verify token decimals before aggregating values
- [ ] Use uniq() instead of uniqExact() when approximate counts are acceptable
- [ ] For large time ranges, aggregate by week/month instead of day
- [ ] Use lower() for case-insensitive address matching
"""


def register_reference_resources(mcp, ch: ClickHouseManager):
    """Register domain knowledge reference resources."""

    @mcp.resource("gnosis://chain-parameters")
    def chain_parameters() -> str:
        """Gnosis Chain parameters: block time, token model, epoch config, and full consensus specs."""
        content = CHAIN_PARAMETERS_PREAMBLE

        try:
            sql = "SELECT parameter_name, parameter_value FROM consensus.specs ORDER BY parameter_name"
            cache_key = "resource:chain-params"
            result = ch.execute_raw_cached(
                sql, "consensus", cache_key
            )

            if result["rows"]:
                content += "\n| Parameter | Value |\n|-----------|-------|\n"
                for row in result["rows"]:
                    content += f"| {row[0]} | {row[1]} |\n"
        except Exception:
            content += "\n*(Consensus specs unavailable — ClickHouse connection failed. Key parameters are listed above.)*\n"

        return truncate_response(content)

    @mcp.resource("gnosis://address-directory")
    def address_directory() -> str:
        """Token addresses, decimals, and major DeFi protocol contracts on Gnosis Chain."""
        return ADDRESS_DIRECTORY

    @mcp.resource("gnosis://metric-definitions")
    def metric_definitions() -> str:
        """Standardized formulas for Gnosis Chain metrics: DAU, validators, gas, bridges, staking."""
        return METRIC_DEFINITIONS

    @mcp.resource("gnosis://query-cookbook")
    def query_cookbook() -> str:
        """Optimized ClickHouse SQL templates for common Gnosis Chain queries."""
        return QUERY_COOKBOOK
