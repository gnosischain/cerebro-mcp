# cerebro-mcp

MCP (Model Context Protocol) server for the Gnosis Chain data platform. Gives Claude (or any MCP client) the ability to query 5 ClickHouse databases with full dbt model context — descriptions, column docs, lineage, compiled SQL — so it can write accurate analytical queries without guessing schemas.

Reports are rendered as interactive [MCP Apps](https://github.com/modelcontextprotocol/ext-apps) with ECharts visualizations — displayed inline in GUI clients (Claude Desktop, VS Code) and saved as standalone HTML files with `file://` links for terminal clients (Claude Code).

## Table of Contents

- [Quick Start](#quick-start)
- [Databases](#databases)
- [dbt Modules](#dbt-modules)
- [MCP Tools](#mcp-tools)
- [MCP Resources](#mcp-resources)
- [MCP Prompts](#mcp-prompts)
- [Workflows](#workflows)
- [MCP App (Interactive Reports)](#mcp-app-interactive-reports)
- [Setup](#setup)
- [Testing with MCP Inspector](#testing-with-mcp-inspector)
- [Usage Examples](#usage-examples)
- [Reasoning & Performance Tracing](#reasoning--performance-tracing)
- [Security](#security)
- [Project Structure](#project-structure)
- [Dependencies](#dependencies)
- [Running Tests](#running-tests)

## Quick Start

```bash
# 1. Clone and install
cd cerebro-mcp
uv sync

# 2. Configure ClickHouse credentials
cp .env.example .env
# Edit .env with your credentials

# 3. Add to your MCP client (Claude Code example)
# In .mcp.json:
{
  "mcpServers": {
    "cerebro": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}

# 4. Start asking questions
# "How many transactions were there on Gnosis Chain yesterday?"
# "Give me a weekly report on network activity with charts"
```

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

## MCP Tools

cerebro-mcp exposes 22 tools across 6 categories.

### Query Execution (7 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `execute_query` | `sql`, `database="dbt"`, `max_rows=100` | Execute a read-only SQL query. Results as markdown table. Includes nudges toward visualization after 3+ queries. |
| `explain_query` | `sql`, `database="dbt"` | Show ClickHouse execution plan without running the query. |
| `start_query` | `sql`, `database="dbt"`, `max_rows=100` | Submit a long-running query for async execution. Returns a query ID to poll. Use for queries that may exceed 30s. |
| `get_query_results` | `query_id` | Check status and retrieve results of an async query. |
| `save_query` | `name`, `sql`, `database="dbt"`, `description=""`, `overwrite=false` | Save a query for reuse. Validates SQL before saving. Stored in `~/.cerebro-mcp/saved_queries.json`. |
| `run_saved_query` | `name`, `max_rows=100` | Execute a previously saved query by name. |
| `list_saved_queries` | — | List all saved queries with names, databases, and descriptions. |

### Schema Discovery (4 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `list_databases` | — | List all databases with descriptions and live table counts. |
| `list_tables` | `database`, `name_pattern=""` | List tables with engine type, row counts, and sizes. Supports LIKE patterns (e.g., `%validators%`). |
| `describe_table` | `table`, `database="dbt"` | Get column schema (name, type, default, description). Enriched with dbt column docs when available. |
| `get_sample_data` | `table`, `database="dbt"`, `limit=5` | Preview sample rows to understand data shape and values. |

### dbt Context (2 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `search_models` | `query=""`, `tags=None`, `module=None`, `limit=50` | Search dbt models by name, description, or tags. Filter by module and/or tags. Appends workflow hints for report-related queries. |
| `get_model_details` | `model_name` | Full model info: description, table name, materialization, all columns with types/descriptions, raw SQL, upstream/downstream lineage. |

### Visualization & Reports (3 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `generate_chart` | `sql`, `database="dbt"`, `chart_type="line"`, `x_field`, `y_field`, `series_field`, `title`, `max_rows=500` | Execute a query and generate an ECharts visualization spec. Registers the chart in a session registry. |
| `generate_report` | `title`, `content_markdown` | Assemble markdown + chart placeholders into an interactive MCP App report. Returns `CallToolResult` with `structuredContent` for inline rendering + `file://` link fallback. |
| `list_charts` | — | List all registered chart IDs, titles, types, and data point counts. |
| `list_reports` | `limit=20` | List previously saved HTML reports with IDs, dates, sizes, and `file://` links. |
| `open_report` | `report_ref` | Reopen a saved report by full UUID or 8-char prefix. Returns same `CallToolResult` format as `generate_report`. |

**Supported chart types:** `line`, `area`, `bar`, `pie`, `numberDisplay`

### Metadata (5 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `list_databases` | — | List all 5 databases with descriptions and table counts. |
| `resolve_address` | `address_or_name` | Look up an address label or find addresses by name from dune_labels (5.3M entries). |
| `get_token_metadata` | `symbol_or_address` | Get token address, decimals, name, and price data availability. Covers major Gnosis Chain tokens. |
| `search_models_by_address` | `contract_address` | Find dbt models related to a specific smart contract. Searches whitelist, ABI, manifest SQL, and labels. |
| `search_docs` | `topic` | Search across all platform documentation (overview, SQL guide, address directory, metrics, cookbook). |

### Reasoning & Tracing (4 tools)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `set_thinking_mode` | `enabled` | Enable/disable reasoning capture. Creates session traces saved as JSON in `.cerebro/logs/`. |
| `log_reasoning` | `step`, `content`, `agent=""`, `action=""`, ... | Record a reasoning step at a key decision point. |
| `get_reasoning_log` | `session_id=""` | Retrieve the reasoning trace for a session (current or by ID). |
| `get_performance_stats` | `last_n=10` | Aggregate performance metrics across recent sessions. |

### System (1 tool)

| Tool | Arguments | Description |
|------|-----------|-------------|
| `system_status` | — | Show server health: ClickHouse connectivity per database, manifest state, config values, tracing status, cache stats. |

## MCP Resources

Resources are read-only contextual documents the LLM can pull into its context window.

| Resource URI | Description |
|---|---|
| `gnosis://platform-overview` | Platform architecture, all databases, model conventions, tips |
| `gnosis://clickhouse-sql-guide` | ClickHouse SQL syntax guide: date functions, aggregates, type casting, common patterns, gotchas |
| `gnosis://dbt-modules/{module_name}` | Per-module model listing with descriptions, grouped by layer (staging/intermediate/marts) |
| `gnosis://source-tables/{database}` | Raw source table schemas per database from dbt source definitions |
| `ui://cerebro/report` | Static MCP App HTML shell (generic report renderer, receives data via `structuredContent`) |

## MCP Prompts

Prompts are guided workflows that help the LLM approach common analytical tasks. cerebro-mcp provides 8 prompts:

### User-Facing Prompts

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `analyze-data` | `topic` | Step-by-step data analysis: discover models, understand schema, query data, interpret results. |
| `explore-protocol` | `protocol` | Explore a DeFi protocol's decoded on-chain data (Aave, Balancer, Uniswap, Circles, etc.). |
| `write-query` | `question`, `database="dbt"` | Guided query writing: discover schema, check columns, preview data, execute with proper ClickHouse SQL. |
| `report` | `period="last 7 days"`, `topics=""`, `focus=""` | Generate a comprehensive interactive report with charts. Enforces the full visualization pipeline. |

### Multi-Agent Role Prompts

These prompts define specialized agent roles for complex analytical workflows:

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `orchestrator` | `user_request` | Decomposes requests into task plans, delegates to specialist agents. Decides between interactive UI and markdown output. |
| `data-engineer` | `task`, `context=""` | Expert ClickHouse SQL agent. Follows mandatory SOP: discover, verify, sample, execute. |
| `data-scientist` | `task`, `data_description=""` | Statistical analysis agent. Correlations, trends, anomaly detection. Works with Python (Pandas/NumPy/SciPy). |
| `frontend-agent` | `task`, `data=""` | Visualization agent. Transforms data into ECharts specs and interactive HTML reports. |

## Workflows

### Standard Data Query

```
1. search_models(query)        →  find relevant dbt models (prefer api_*/fct_*)
2. describe_table(table)       →  verify exact column names and types
3. execute_query(sql)          →  extract data with date filters and LIMIT
```

### Interactive Report Generation

```
1. search_models(query)        →  find relevant data models
2. describe_table(table)       →  verify column names
3. execute_query(sql)          →  extract data (repeat per section)
4. generate_chart(sql, ...)    →  create ECharts spec (repeat per metric, min 3)
5. generate_report(title, md)  →  assemble into interactive HTML report
```

The report workflow is enforced by multiple guidance layers:
- **Server instructions** define MODE 1 (interactive UI) vs MODE 2 (markdown) based on the user's request
- **Tool docstrings** include mandatory workflow reminders
- **Nudge logic** in `execute_query` suggests `generate_chart` after 3+ queries
- **Workflow hints** in `search_models` for report-related keywords
- **CLAUDE.md** provides client-side instructions for Claude Code

### Chart Type Selection Guide

| Chart Type | When to Use | Example |
|------------|-------------|---------|
| `line` | Time series trends | Daily transaction count over 30 days |
| `area` | Time series with volume emphasis | Gas utilization trends |
| `bar` | Comparisons | Top 10 protocols by volume |
| `pie` | Proportions/distribution | Client diversity, market share |
| `numberDisplay` | Single KPI values | Total active validators, current APY |

### Multi-Agent Workflow

For complex analytical requests, the orchestrator prompt decomposes the task:

```
User: "Analyze DeFi activity on Gnosis Chain this month"

Orchestrator → Task Plan:
  TASK 1: [Data Engineer] Discover DeFi models
  TASK 2: [Data Engineer] Query DEX volumes, TVL, user counts
  TASK 3: [Visualization Agent] Generate charts per metric
  TASK 4: [Visualization Agent] Assemble interactive report
```

### Async Queries for Heavy Workloads

For queries that may take longer than 30 seconds (e.g., full table scans on raw `execution` or `consensus` tables):

```
1. start_query(sql)            →  returns query_id
2. get_query_results(query_id) →  poll until completed
```

The async executor runs up to 3 concurrent queries in background threads. Jobs auto-expire after 10 minutes.

### Saved Queries

Save and reuse frequently needed queries:

```
1. save_query(name, sql)       →  validate and store in ~/.cerebro-mcp/
2. list_saved_queries()        →  see all saved queries
3. run_saved_query(name)       →  execute by name
```

## MCP App (Interactive Reports)

cerebro-mcp implements the [MCP Apps](https://github.com/modelcontextprotocol/ext-apps) standard (`@modelcontextprotocol/ext-apps`) to deliver interactive reports as native UI within MCP clients.

### How It Works

1. **`generate_chart`** executes a SQL query and creates an [ECharts](https://echarts.apache.org/) visualization spec. The chart is registered in a session-scoped registry with a unique ID (e.g., `chart_1`).

2. **`generate_report`** takes markdown content with `{{chart:CHART_ID}}` placeholders, resolves them against the chart registry, converts markdown to HTML, and returns a `CallToolResult` with:
   - **`content`**: `TextContent` with a summary and `file://` link to the saved HTML
   - **`structuredContent`**: Chart specs, rendered HTML sections, title, and timestamp — consumed by the MCP App

3. **The MCP App** is a static HTML resource at `ui://cerebro/report` served with `mime_type="text/html;profile=mcp-app"`. It:
   - Imports the [MCP Apps SDK](https://www.npmjs.com/package/@modelcontextprotocol/ext-apps): `import { App } from "@modelcontextprotocol/ext-apps"`
   - Receives chart data via `app.ontoolresult` callback (reads `structuredContent`)
   - Renders ECharts instances, tabbed sections, light/dark theme, Gnosis owl watermark
   - Adapts to host theme via `app.onhostcontextchanged`

4. **Standalone fallback**: Reports are also saved to disk as self-contained HTML with chart data embedded in a `<script id="report-data" type="application/json">` tag, openable directly in any browser via the `file://` link.

### Architecture

```
generate_chart (multiple calls) → chart_registry
                                      ↓
generate_report → CallToolResult { content + structuredContent }
                                      ↓
    MCP host sees meta.ui.resourceUri → fetches ui://cerebro/report
                                      ↓
    HTML App receives structuredContent via ontoolresult → renders charts
```

### Rendering Behavior by Client

| Client | Behavior |
|--------|----------|
| **Claude Desktop** | Renders MCP App inline in the conversation via `structuredContent` |
| **VS Code (Copilot Chat)** | Renders MCP App inline in the chat panel |
| **Claude Code (terminal)** | Returns summary text with `file://` link to open in browser |
| **MCP Inspector** | Returns `CallToolResult` JSON with `structuredContent` for inspection |

### Report Features

- Light/dark theme toggle (synced with host via MCP Apps SDK, or manual)
- Auto-tabbed sections (from `## ` markdown headers)
- Gnosis owl watermark on all charts
- Responsive layout (desktop, tablet, mobile)
- Print-friendly styles

### Report Caching

Reports are cached in-memory with a 1-hour TTL and a maximum of 20 entries. The cache is thread-safe and self-pruning. Cached `structuredContent` is reused by `open_report` to re-render reports without re-querying.

### Chart Registry

Charts are stored in a session-scoped registry with a 2-hour TTL. The registry is pruned automatically when new charts are created. Each chart entry includes:
- The ECharts option spec (JSON)
- Chart title, type, and data point count
- Creation timestamp (for TTL)

### Time Series Ordering

Line and area charts automatically sort data chronologically by the x-axis field. Bar, pie, and number display charts preserve the original query order.

### Format Conversion

After generating an interactive HTML report, the LLM will offer to convert it to other formats (docx, pdf, pptx). This is handled by the MCP client's built-in skills, not by cerebro-mcp itself.

### Example Report Markdown

```markdown
## Executive Summary

Gnosis Chain processed 1.2M transactions this week, up 8% from last week.

{{chart:chart_1}}

## Transaction Activity

Daily transaction volume showed steady growth.

{{chart:chart_2}}

| Date       | Transactions | Change |
|------------|-------------|--------|
| 2026-03-03 | 165,432     | +3.2%  |
| 2026-03-04 | 172,891     | +4.5%  |

## Client Diversity

{{chart:chart_3}}

Nethermind remains the dominant execution client at 52%.
```

## Setup

### 1. Install

```bash
# Using uv (recommended)
cd cerebro-mcp
uv sync

# Or pip
pip install -e .

# Install dev dependencies for testing
uv sync --extra dev
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
| `CLICKHOUSE_PASSWORD` | — | ClickHouse password (required) |
| `CLICKHOUSE_SECURE` | `True` | Use HTTPS |
| `DBT_MANIFEST_URL` | GitHub Pages URL | URL to fetch dbt manifest.json |
| `DBT_MANIFEST_PATH` | — | Local fallback path for manifest |
| `MAX_ROWS` | `10000` | Maximum rows per query result |
| `QUERY_TIMEOUT_SECONDS` | `30` | Query timeout |
| `MAX_QUERY_LENGTH` | `10000` | Maximum SQL query length |
| `TOOL_RESPONSE_MAX_CHARS` | `40000` | Maximum characters per tool response |
| `MANIFEST_REFRESH_INTERVAL_SECONDS` | `300` | How often to check for manifest updates (5 min) |
| `THINKING_MODE_ENABLED` | `True` | Enable reasoning/performance tracing |
| `THINKING_ALWAYS_ON` | `True` | Auto-start tracing on every session |
| `THINKING_LOG_DIR` | `.cerebro/logs` | Directory for session trace files |
| `THINKING_LOG_RETENTION_DAYS` | `30` | Auto-prune traces older than this |
| `CEREBRO_REPORT_DIR` | `~/.cerebro/reports/` | Directory for saved HTML report files |

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

Claude Code is a terminal-based client. Reports generated via `generate_report` include a `file://` link in the text output that you can open in your browser. A `CLAUDE.md` file in the project root provides client-side instructions ensuring the `generate_chart` -> `generate_report` pipeline is always followed for report requests.

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

In Claude Desktop, reports render as interactive MCP Apps directly in the conversation. The `@modelcontextprotocol/ext-apps` SDK synchronizes the report theme with the host application.

### 5. Use with VS Code

Add to your workspace's `.vscode/mcp.json`:

```json
{
  "servers": {
    "cerebro": {
      "command": "/Users/you/.local/bin/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

## Testing with MCP Inspector

The MCP Inspector is an interactive web UI for testing and debugging MCP servers. It lets you call every tool, read every resource, and run every prompt — without needing Claude or any other LLM client.

```bash
cd cerebro-mcp

# Option 1: via mcp dev (spawns subprocess)
uv run mcp dev src/cerebro_mcp/server.py

# Option 2: via SSE transport (direct, useful for debugging)
uv run cerebro-mcp --sse
# Then connect the Inspector to http://localhost:8000/sse
```

Option 1 starts the server and opens a browser at `http://localhost:6274` (by default). Option 2 runs the server directly with SSE transport.

### What the Inspector Shows

The Inspector has three tabs that map to the three MCP primitives:

| Tab | What it does |
|-----|-------------|
| **Tools** | Lists all 22 tools. Select one, fill in arguments (JSON), hit "Run" and see the result. |
| **Resources** | Lists all resources (`gnosis://platform-overview`, etc.). Click to see the content. |
| **Prompts** | Lists all 8 prompts. Fill in arguments and see the expanded template. |

### Recommended Test Sequence

1. **Smoke test** — run `list_databases` with no arguments. Should show all databases with table counts. Confirms ClickHouse connectivity and dbt manifest loading.

2. **Schema discovery** — run `list_tables` with `database: "dbt"` and `name_pattern: "%validators%"`. Verify it returns consensus validator models.

3. **dbt context** — run `search_models` with `query: "transactions"`, `module: "execution"`. Then run `get_model_details` on a returned model to see full column docs, SQL, and lineage.

4. **Query execution** — run `execute_query` with:
   ```json
   {
     "sql": "SELECT count() FROM dbt.api_execution_transactions_7d",
     "database": "dbt"
   }
   ```

5. **Safety check** — try `execute_query` with `sql: "DROP TABLE dbt.foo"`. Confirm it gets rejected.

6. **Visualization** — run `generate_chart` with:
   ```json
   {
     "sql": "SELECT date, cnt FROM dbt.api_execution_transactions_daily ORDER BY date DESC LIMIT 30",
     "chart_type": "line",
     "x_field": "date",
     "y_field": "cnt",
     "title": "Daily Transactions"
   }
   ```
   Then run `generate_report` with the returned chart ID:
   ```json
   {
     "title": "Test Report",
     "content_markdown": "## Transactions\n\n{{chart:chart_1}}"
   }
   ```

7. **Resources** — click `gnosis://platform-overview` and `gnosis://clickhouse-sql-guide` to review context documents.

8. **Prompts** — try `analyze-data` with `topic: "validator count trends"` to see the guided prompt template.

9. **System health** — run `system_status` to check all database connections, manifest state, config, and tracing status.

### Troubleshooting

- **Server fails to start**: Check that `.env` exists with valid ClickHouse credentials. The server logs errors to stderr which appear in the Inspector's server output pane.
- **Tools return connection errors**: Verify `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, and `CLICKHOUSE_USER` are correct. Test with `clickhouse-client` directly if unsure.
- **dbt tools return "manifest not loaded"**: Check that `DBT_MANIFEST_URL` is reachable, or set `DBT_MANIFEST_PATH` to a local `manifest.json`.
- **Charts not rendering in report**: Ensure `generate_chart` was called first and the chart ID matches the `{{chart:ID}}` placeholder exactly.
- **Report not rendering inline**: MCP App rendering requires a client that supports `@modelcontextprotocol/ext-apps` (Claude Desktop, VS Code). In terminal clients, use the `file://` link from the text output. Set `CEREBRO_REPORT_DIR` to a writable directory if the default `~/.cerebro/reports/` has issues.
- **Reopening a closed report**: Use `list_reports()` to see saved reports, then `open_report("8charID")` to reopen.

## Usage Examples

Once connected, you can ask Claude things like:

### Quick Queries (Markdown Output)
- "How many transactions were there on Gnosis Chain yesterday?"
- "What's the GNO token price trend this week?"
- "Show me the top addresses by gas usage in the last 7 days"
- "What Aave V3 events are tracked in the data platform?"

### Visual Reports (Interactive HTML)
- "Give me a weekly report on Gnosis Chain activity with charts"
- "Show me DeFi TVL trends over the past month with visualizations"
- "Create a report comparing bridge flow volumes by chain"
- "Analyze validator performance trends and generate a visual summary"

### Protocol Exploration
- "Explore the Circles protocol data"
- "What Balancer pools exist on Gnosis Chain? Show me volume trends"
- "Find all models related to bridge flows and show me recent data"

### Advanced
- "Save a query that shows daily transaction counts for the last 30 days"
- "Run my saved query 'daily_txs' and chart the results"
- "What dbt models reference the GNO token contract address?"
- "Show me the execution plan for this query before running it"

Claude will use the MCP tools to discover schemas, find relevant dbt models, write correct ClickHouse SQL, and return results — all without needing to know the database structure upfront.

## Reasoning & Performance Tracing

cerebro-mcp includes a built-in tracing system that records every tool call and reasoning step as JSON session traces. This is useful for:

- **Debugging** — understand why a query failed or returned unexpected results
- **Performance monitoring** — track query times, success rates, and tool usage patterns
- **Audit** — review what data was accessed and how

### Configuration

Tracing is enabled by default (`THINKING_ALWAYS_ON=True`). Session traces are saved to `.cerebro/logs/` as JSON files with auto-pruning after 30 days.

### Tools

- `set_thinking_mode(true/false)` — manually control tracing
- `log_reasoning(step, content)` — record decision points during analysis
- `get_reasoning_log(session_id)` — view a session trace
- `get_performance_stats(last_n=10)` — aggregate metrics across sessions

### Auto-Captured Events

When tracing is enabled, the system automatically captures:
- Every MCP tool call (name, arguments, result, duration, success/failure)
- Every MCP request/response pair (for low-level protocol debugging)
- Sensitive fields (passwords, tokens, API keys) are automatically redacted

### Session Trace Format

```json
{
  "session_id": "20260309_181332_e70899",
  "started_at": "2026-03-09T18:13:32+00:00",
  "steps": [
    {
      "step_number": 1,
      "event_kind": "tool_call",
      "tool_name": "search_models",
      "tool_args": {"query": "transactions"},
      "duration_ms": 45,
      "success": true
    }
  ],
  "summary": {
    "total_steps": 31,
    "charts_generated": 0,
    "queries_executed": 18,
    "models_used": ["api_execution_transactions_daily"]
  }
}
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
- Tool responses capped at `TOOL_RESPONSE_MAX_CHARS` (default 40,000)
- Query timeout enforced via ClickHouse `send_receive_timeout`

### Sensitive Data Handling

- Session traces automatically redact fields matching sensitive key patterns (password, token, api_key, secret, etc.)
- Normalized payloads are inspected recursively, including JSON embedded in text blocks

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

## Project Structure

```
cerebro-mcp/
├── pyproject.toml                        # Package config and dependencies
├── .env.example                          # Environment variable template
├── CLAUDE.md                             # Client-side instructions for Claude Code
├── Dockerfile
├── README.md
├── src/cerebro_mcp/
│   ├── server.py                         # FastMCP entry point, tool registration
│   ├── config.py                         # Settings via pydantic-settings
│   ├── clickhouse_client.py              # ClickHouse connection manager with caching
│   ├── manifest_loader.py                # dbt manifest loading, indexing, and search
│   ├── safety.py                         # Query validation and read-only enforcement
│   ├── tools/
│   │   ├── query.py                      # execute_query, explain_query
│   │   ├── query_async.py                # start_query, get_query_results
│   │   ├── saved_queries.py              # save_query, run_saved_query, list_saved_queries
│   │   ├── schema.py                     # list_tables, describe_table, get_sample_data
│   │   ├── dbt.py                        # search_models, get_model_details
│   │   ├── metadata.py                   # list_databases, resolve_address, get_token_metadata,
│   │   │                                 #   search_models_by_address, search_docs, system_status
│   │   ├── visualization.py              # generate_chart, generate_report, list_charts, list_reports, open_report,
│   │   │                                 #   MCP App resource, report cache, chart registry, HTML templates
│   │   └── reasoning.py                  # set_thinking_mode, log_reasoning,
│   │                                     #   get_reasoning_log, get_performance_stats,
│   │                                     #   auto-tracing hooks
│   ├── resources/
│   │   ├── context.py                    # Platform overview, SQL guide, module/source resources
│   │   └── reference.py                  # Address directory, metric definitions, query cookbook
│   └── prompts/
│       └── templates.py                  # All 8 prompt templates (user-facing + agent roles)
└── tests/
    ├── test_safety.py                    # Query validation tests
    ├── test_manifest_loader.py           # Manifest parsing and search tests
    ├── test_tools.py                     # Tool integration tests
    ├── test_visualization.py             # UIResource, cache, pruning, nudge tests
    └── test_reasoning_tracing.py         # Tracing and performance stats tests
```

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `mcp[cli]` | >= 1.2.0 | MCP Python SDK (FastMCP, CallToolResult, structuredContent) |
| `clickhouse-connect` | >= 0.7.0, < 1.0 | ClickHouse client (HTTP interface) |
| `pydantic-settings` | >= 2.0 | Settings management from env vars |
| `python-dotenv` | >= 1.0 | .env file loading |
| `requests` | >= 2.31 | Fetching dbt manifest from URL |

**Dev dependencies:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >= 8.0 | Test framework |
| `pytest-asyncio` | >= 0.23 | Async test support |

## Running Tests

```bash
# Unit tests (no ClickHouse needed)
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_visualization.py -v

# Run with output
uv run pytest tests/ -v --no-header -s
```

Tests cover:
- **Query safety** — SQL injection, blocked keywords, string literal handling
- **Manifest loading** — parsing, search, fuzzy matching
- **Visualization** — MCP App resource, CallToolResult output, report caching, chart registry pruning, time series ordering, nudge logic
- **Reasoning tracing** — session lifecycle, auto-capture, performance aggregation

## Gnosis Chain Quick Reference

| Fact | Value |
|------|-------|
| Block time | 5 seconds (~17,280 blocks/day) |
| Native gas token | xDAI (not ETH) |
| Staking token | GNO (1 per validator, not 32 ETH) |
| Chain ID | 100 |
| Slots per epoch | 16 |
| xDAI/GNO/WETH decimals | 18 |
| USDC/USDT decimals | 6 |

## License

This project is licensed under the [MIT License](LICENSE).
