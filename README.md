# cerebro-mcp

MCP (Model Context Protocol) server for the Gnosis Chain data platform. Gives Claude (or any MCP client) the ability to query 5 ClickHouse databases with full dbt model context — descriptions, column docs, lineage, compiled SQL — so it can write accurate analytical queries without guessing schemas.


## Databases

All databases live on a single ClickHouse Cloud instance.

| Database | Content | Key Tables |
|----------|---------|------------|
| `execution` | On-chain L1 data (Gnosis Chain) | blocks, transactions, logs, traces, native_transfers, contracts, balance_diffs, code_diffs, nonce_diffs, storage_diffs, withdrawals |
| `consensus` | Beacon chain data | blocks, attestations, validators, withdrawals, deposits, rewards, blob_commitments, blob_sidecars, specs (~25 tables) |
| `crawlers_data` | Off-chain enrichment | dune_labels, dune_prices, dune_bridge_flows, dune_gno_supply, ember_electricity_data, probelab stats, gpay_wallets |
| `nebula` | P2P network discovery | crawls, visits (peer connectivity, agent versions, protocols) |
| `dbt` | Transformed/modeled data | ~400 views and tables from dbt-cerebro, organized in 8 modules |

## dbt Modules

The `dbt` database contains models organized into these modules:

| Module | Models | Description |
|--------|--------|-------------|
| execution | 208 | Blocks, transactions, transfers, tokens, state, prices, pools, yields, DEX analytics |
| consensus | 54 | Validators, attestations, rewards, blob data, network health |
| contracts | 44 | Decoded events/calls for 15+ DeFi protocols (Aave, Balancer, Uniswap, Swapr, Circles, etc.) |
| p2p | 27 | Network topology, peer distribution, client diversity |
| bridges | 18 | Cross-chain bridge flow analytics |
| ESG | 18 | Environmental metrics, energy consumption |
| probelab | 9 | Network probe statistics |
| crawlers_data | 9 | Dune labels, prices, supply data |

### Model Naming Convention

- `stg_*` — Staging: minimal cleaning of raw source tables
- `int_*` — Intermediate: business logic, aggregations, joins
- `api_*` — API/reporting tier: daily/weekly aggregates, ready for consumption
- `fct_*` — Fact tables: event-based, immutable records
- `contracts_*` — Decoded smart contract calls and events

## MCP Tools (8)

### Query Execution

| Tool | Arguments | Description |
|------|-----------|-------------|
| `execute_query` | `sql`, `database="dbt"`, `max_rows=100` | Execute a read-only SQL query against any ClickHouse database. Results returned as a formatted markdown table. |
| `explain_query` | `sql`, `database="dbt"` | Show the ClickHouse execution plan for a query without running it. |

### Schema Discovery

| Tool | Arguments | Description |
|------|-----------|-------------|
| `list_databases` | — | List all 5 databases with descriptions and live table counts. |
| `list_tables` | `database`, `name_pattern=""` | List tables in a database with engine type, row counts, and sizes. Supports LIKE patterns (e.g., `%validators%`). |
| `describe_table` | `table`, `database="dbt"` | Get column schema (name, type, default, comment). Enriched with dbt column descriptions when available. |
| `get_sample_data` | `table`, `database="dbt"`, `limit=5` | Preview sample rows from a table to understand data shape and values. |

### dbt Context

| Tool | Arguments | Description |
|------|-----------|-------------|
| `search_models` | `query`, `tags=None`, `module=None` | Search dbt models by name, description, or tags. Filter by module (execution, consensus, contracts, etc.) and/or tags (production, dev, tier0, etc.). |
| `get_model_details` | `model_name` | Get full model info: description, table name, materialization, all columns with types and descriptions, raw SQL code, upstream/downstream lineage. |

## MCP Resources (4)

Resources are read-only contextual documents the LLM can pull into its context window.

| Resource URI | Description |
|---|---|
| `gnosis://platform-overview` | Platform architecture, all databases, model conventions, tips |
| `gnosis://clickhouse-sql-guide` | ClickHouse SQL syntax guide: date functions, aggregates, type casting, common query patterns, gotchas |
| `gnosis://dbt-modules/{module_name}` | Per-module model listing with descriptions, grouped by layer (staging/intermediate/marts) |
| `gnosis://source-tables/{database}` | Raw source table schemas per database from dbt source definitions |

## MCP Prompts (3)

Prompts are guided workflows that help the LLM approach common analytical tasks.

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `analyze-data` | `topic` | Guided analysis: search models, understand schema, query data, interpret results |
| `explore-protocol` | `protocol` | Explore a DeFi protocol's decoded on-chain data (Aave, Balancer, Uniswap, etc.) |
| `write-query` | `question`, `database="dbt"` | Step-by-step guide for writing correct ClickHouse SQL to answer a question |

## Setup

### 1. Install

```bash
# Using uv (recommended)
cd cerebro-mcp
uv sync

# Or pip
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your ClickHouse credentials
```

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse host |
| `CLICKHOUSE_PORT` | `8443` | ClickHouse HTTPS port |
| `CLICKHOUSE_USER` | `default` | ClickHouse username |
| `CLICKHOUSE_PASSWORD` | — | ClickHouse password |
| `CLICKHOUSE_SECURE` | `True` | Use HTTPS |
| `DBT_MANIFEST_URL` | GitHub Pages URL | URL to fetch dbt manifest.json |
| `DBT_MANIFEST_PATH` | — | Local fallback path for manifest |
| `MAX_ROWS` | `10000` | Maximum rows per query result |
| `QUERY_TIMEOUT_SECONDS` | `30` | Query timeout |
| `MAX_QUERY_LENGTH` | `10000` | Maximum SQL query length |

### 3. Use with Claude Code

Add to your project's `.mcp.json` (or `~/.claude/.mcp.json` for global access):

> **Important:** MCP clients spawn the server as a subprocess with a minimal PATH.
> If `uv` is installed in a non-standard location (e.g., `~/.local/bin/uv`),
> you must use the **full absolute path** to the `uv` binary. Find it with `which uv`.

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "/Users/you/.local/bin/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

### 4. Use with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "/Users/you/.local/bin/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"],
      "env": {
        "CLICKHOUSE_HOST": "your-clickhouse-host",
        "CLICKHOUSE_PORT": "8443",
        "CLICKHOUSE_USER": "mcp_reader",
        "CLICKHOUSE_PASSWORD": "your_password",
        "CLICKHOUSE_SECURE": "True"
      }
    }
  }
}
```

### 5. Test with MCP Inspector

The MCP Inspector is an interactive web UI for testing and debugging MCP servers.
It lets you call every tool, read every resource, and run every prompt — without
needing Claude or any other LLM client.

```bash
cd cerebro-mcp
uv run mcp dev src/cerebro_mcp/server.py
```

This starts the server and opens a browser at `http://localhost:6274` (by default).

#### What the Inspector shows

The Inspector has three tabs that map to the three MCP primitives:

| Tab | What it does |
|-----|-------------|
| **Tools** | Lists all 8 tools. Select one, fill in the arguments (JSON), hit "Run" and see the result. Great for verifying queries actually hit ClickHouse and return data. |
| **Resources** | Lists all resources (`gnosis://platform-overview`, etc.). Click one to see its content — the same text an LLM would receive as context. |
| **Prompts** | Lists all prompts (`analyze-data`, `explore-protocol`, `write-query`). Fill in the arguments and see the expanded prompt template. |

#### Recommended test sequence

1. **Smoke test** — go to Tools, run `list_databases` with no arguments. You should see all 5 databases with table counts. This confirms ClickHouse connectivity and dbt manifest loading.

2. **Schema discovery** — run `list_tables` with `database: "dbt"` and `name_pattern: "%validators%"`. Verify it returns consensus validator models.

3. **dbt context** — run `search_models` with `query: "transactions"`, `module: "execution"`. Then take one of the returned model names and run `get_model_details` on it to see full column docs, SQL, and lineage.

4. **Query execution** — run `execute_query` with:
   ```json
   {
     "sql": "SELECT count() FROM dbt.api_execution_transactions_7d",
     "database": "dbt"
   }
   ```

5. **Safety check** — try running `execute_query` with `sql: "DROP TABLE dbt.foo"`. Confirm it gets rejected with a clear error.

6. **Resources** — click `gnosis://platform-overview` and `gnosis://clickhouse-sql-guide` to review the context documents that Claude will see.

7. **Prompts** — try `analyze-data` with `topic: "validator count trends"` to see the guided prompt template.

#### Troubleshooting

- **Server fails to start**: Check that `.env` exists with valid ClickHouse credentials. The server logs errors to stderr which appear in the Inspector's server output pane.
- **Tools return connection errors**: Verify `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, and `CLICKHOUSE_USER` are correct. Test with `clickhouse-client` directly if unsure.
- **dbt tools return "manifest not loaded"**: Check that `DBT_MANIFEST_URL` is reachable, or set `DBT_MANIFEST_PATH` to a local `manifest.json`.

## Usage Examples

Once connected, you can ask Claude things like:

- "How many transactions were there on Gnosis Chain yesterday?"
- "Show me the daily active validator count for the past month"
- "What Aave V3 events are tracked in the data platform?"
- "Find all models related to bridge flows and show me recent data"
- "What's the GNO token price trend this week?"
- "Show me the top addresses by gas usage in the last 7 days"
- "Explore the Circles protocol data"

Claude will use the MCP tools to discover schemas, find relevant dbt models, write correct ClickHouse SQL, and return results — all without needing to know the database structure upfront.

## Project Structure

```
cerebro-mcp/
├── pyproject.toml                     # Package config and dependencies
├── .env.example                       # Environment variable template
├── .gitignore
├── Dockerfile
├── README.md
├── src/cerebro_mcp/
│   ├── server.py                      # FastMCP entry point
│   ├── config.py                      # Settings via pydantic-settings
│   ├── clickhouse_client.py           # ClickHouse connection manager
│   ├── manifest_loader.py             # dbt manifest loading and indexing
│   ├── safety.py                      # Query validation and read-only enforcement
│   ├── tools/
│   │   ├── query.py                   # execute_query, explain_query
│   │   ├── schema.py                  # list_tables, describe_table, get_sample_data
│   │   ├── dbt.py                     # search_models, get_model_details
│   │   └── metadata.py               # list_databases
│   ├── resources/
│   │   └── context.py                 # Platform overview, SQL guide, module/source resources
│   └── prompts/
│       └── templates.py               # analyze-data, explore-protocol, write-query
└── tests/
    ├── test_safety.py                 # Query validation tests (23 tests)
    ├── test_manifest_loader.py        # Manifest parsing and search tests (19 tests)
    └── test_tools.py                  # Tool integration tests
```

## Security

### Query Validation

All queries go through `safety.py` before execution:

- **Allowed statements**: SELECT, EXPLAIN, DESCRIBE, SHOW, WITH (CTEs), EXISTS
- **Blocked keywords**: INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, RENAME, ATTACH, DETACH, OPTIMIZE, GRANT, REVOKE, KILL, SYSTEM, INTO OUTFILE
- **Multi-statement injection**: Blocked (no semicolon-separated statements)
- **String literal awareness**: Keywords inside string literals (e.g., `WHERE name = 'DELETE THIS'`) are correctly ignored
- **Table name validation**: Only alphanumeric + underscores allowed, existence verified via `system.tables`

### Result Limits

- Automatic LIMIT appended if not present in query
- Results capped at `MAX_ROWS` (default 10,000)
- Query timeout enforced via ClickHouse `send_receive_timeout`

### Recommended: Read-Only ClickHouse User

For defense in depth, create a dedicated read-only user:

```sql
CREATE USER mcp_reader IDENTIFIED BY '...';
GRANT SELECT ON execution.* TO mcp_reader;
GRANT SELECT ON consensus.* TO mcp_reader;
GRANT SELECT ON crawlers_data.* TO mcp_reader;
GRANT SELECT ON nebula.* TO mcp_reader;
GRANT SELECT ON dbt.* TO mcp_reader;
GRANT SELECT ON system.tables TO mcp_reader;
GRANT SELECT ON system.columns TO mcp_reader;
```

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `mcp[cli]` | >= 1.2.0 | MCP Python SDK (FastMCP) |
| `clickhouse-connect` | >= 0.7.0 | ClickHouse client (same as all Gnosis repos) |
| `pydantic-settings` | >= 2.0 | Settings management from env vars |
| `python-dotenv` | >= 1.0 | .env file loading |
| `requests` | >= 2.31 | Fetching dbt manifest from URL |

## Running Tests

```bash
# Unit tests (no ClickHouse needed)
pytest tests/ -v

# Include integration tests with real manifest
# (requires dbt-cerebro/target/manifest.json to exist)
pytest tests/ -v --no-header
```

## License

This project is licensed under the [MIT License](LICENSE).