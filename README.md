# Cerebro MCP

![Cerebro MCP](img/header-banner.png)

**Model Context Protocol server for Gnosis Chain on-chain analytics.**

Cerebro MCP connects AI assistants (Claude Desktop, VS Code, Claude Code) to Gnosis Chain's ClickHouse data warehouse and dbt model layer, enabling natural-language queries, interactive reports, and automated data analysis.

---

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │            MCP Host (Client)            │
                          │  Claude Desktop / VS Code / Claude Code │
                          └────────────────┬────────────────────────┘
                                           │ MCP Protocol
                                           │ (stdio / SSE)
                          ┌────────────────▼────────────────────────┐
                          │          cerebro-mcp (FastMCP)          │
                          │                                         │
                          │  ┌──────────┐ ┌──────────┐ ┌────────┐   │
                          │  │  Query   │ │  Schema  │ │  dbt   │   │
                          │  │  Tools   │ │  Tools   │ │  Tools │   │
                          │  └────┬─────┘ └────┬─────┘ └───┬────┘   │
                          │       │            │           │        │
                          │  ┌────▼────────────▼───────────▼────┐   │
                          │  │       ClickHouse Client          │   │
                          │  │    (clickhouse-connect + cache)  │   │
                          │  └────────────────┬─────────────────┘   │
                          │                   │                     │
                          │  ┌────────────────┴─────────────────┐   │
                          │  │  Visualization  │  Reasoning     │   │
                          │  │  Tools          │  Tracing       │   │
                          │  └────────┬────────┘────────────────┘   │
                          │           │                             │
                          │  ┌────────▼──────────────────────────┐  │
                          │  │  React Report UI (Vite bundle)    │  │
                          │  │  ECharts + Sidebar + Theme toggle │  │
                          │  └───────────────────────────────────┘  │
                          └────────────────┬────────────────────────┘
                                           │
                          ┌────────────────▼────────────────────────┐
                          │     ClickHouse Cloud (Gnosis Chain)     │
                          │                                         │
                          │  execution │ consensus │ crawlers_data  │
                          │  nebula    │ nebula_discv4 │ dbt        │
                          └─────────────────────────────────────────┘
```

---

## Data Pipeline

```
  Raw Blockchain Data           dbt-cerebro Models              Cerebro MCP
  ─────────────────        ────────────────────────        ──────────────────

  Execution Layer    ──►   stg_execution_*      ──►   ┌─────────────────────┐
   - blocks                int_execution_*      ──►   │  search_models      │
   - transactions          fct_execution_*      ──►   │  describe_table     │
   - logs, traces          api_execution_*      ──►   │  execute_query      │
   - contracts                                        │  generate_chart     │
                                                      │  generate_report    │
  Consensus Layer    ──►   stg_consensus_*      ──►   │                     │
   - validators            int_consensus_*      ──►   │  ┌───────────────┐  │
   - attestations          api_consensus_*      ──►   │  │  Interactive  │  │
   - rewards                                          │  │  Report UI    │  │
                                                      │  │  (React +     │  │
  Off-Chain Data     ──►   stg_crawlers_*       ──►   │  │   ECharts)    │  │
   - dune_labels           int_bridges_*        ──►   │  └───────────────┘  │
   - dune_prices           fct_bridges_*        ──►   │                     │
   - bridge flows          api_bridges_*        ──►   └─────────────────────┘
                                                              │
  P2P Network        ──►   stg_p2p_*            ──►           ▼
   - crawls, visits        int_p2p_*            ──►    ~/.cerebro/reports/
                           api_p2p_*            ──►    (standalone HTML)
```

**dbt model tiers** (always prefer higher tiers for speed):
- `api_*` -- Pre-aggregated daily/weekly views (fastest)
- `fct_*` -- Fact tables: immutable events
- `int_*` -- Intermediate: business logic joins
- `stg_*` -- Staging: minimal cleaning of raw tables

---

## Report Workflow

```
  User: "Give me a weekly Gnosis Chain report"
         │
         ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  Phase 1: Analytics Reporter                                    │
  │                                                                 │
  │  search_models("transactions daily")                            │
  │       │                                                         │
  │       ▼                                                         │
  │  describe_table("api_execution_transactions_daily")             │
  │       │                                                         │
  │       ▼                                                         │
  │  execute_query("SELECT dt, txs FROM ... WHERE dt >= ...")       │
  │       │                                                         │
  │       ▼                                                         │
  │  generate_chart(sql=..., chart_type="line", title="Daily Txs")  │
  │       │                                     Returns: chart_1    │
  │       ▼                                                         │
  │  (repeat for each metric: validators, gas, bridges, etc.)       │
  │       Returns: chart_2, chart_3, chart_4, ...                   │
  └───────┬─────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  Phase 2: UI Designer                                           │
  │                                                                 │
  │  Assembles markdown with {{chart:chart_1}} placeholders:        │
  │                                                                 │
  │    ## Overview                                                  │
  │    Key metrics for the week ending 2026-03-10.                  │
  │    {{chart:chart_1}}                                            │
  │                                                                 │
  │    ## Network Activity                                          │
  │    {{chart:chart_2}}                                            │
  │    ...                                                          │
  │                                                                 │
  │  generate_report(title="Weekly Report", content_markdown=...)   │
  └───────┬─────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  Phase 3: Reality Checker                                       │
  │                                                                 │
  │  Validates: correct column names, date ranges, chart types,     │
  │  data integrity, no emojis, report structure, narrative matches │
  └───────┬─────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  Output                                                         │
  │                                                                 │
  │  1. Standalone HTML saved to ~/.cerebro/reports/                │
  │  2. Interactive UI rendered in MCP App iframe (Claude Desktop)  │
  │  3. Chat shows: title, file:// link, report ID                  │
  │  4. User can reopen with: open_report("abc12345")               │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Report UI

```
  ┌──────────────────────────────────────────────────────────────┐
  │  ┌───────────┐  ┌─────────────────────────────────────────┐  │
  │  │           │  │  [owl] Weekly Report       [sun] toggle │  │
  │  │ Sidebar   │  │  2026-03-10 12:00 UTC                   │  │
  │  │           │  ├─────────────────────────────────────────┤  │
  │  │ Overview  │  │                                         │  │
  │  │ Network   │  │  ## Overview                            │  │
  │  │ Validators│  │                                         │  │
  │  │ Bridges   │  │  ┌──────────────────────────────────┐   │  │
  │  │           │  │  │  Daily Transactions    [img][tbl]│   │  │
  │  │  [<] hide │  │  │  ┌─────────────────────────┐     │   │  │
  │  │           │  │  │  │      Line Chart         │     │   │  │
  │  │           │  │  │  │                         │     │   │  │
  │  │           │  │  │  │                    [owl]│     │   │  │
  │  │           │  │  │  └─────────────────────────┘     │   │  │
  │  │           │  │  └──────────────────────────────────┘   │  │
  │  │           │  │                                         │  │
  │  │           │  │  Key highlights:                        │  │
  │  │           │  │  - Transactions increased by +12.3%     │  │
  │  │           │  │  - Gas usage decreased by -5.1%         │  │
  │  │           │  │                                         │  │
  │  └───────────┘  └─────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────┘

  [img] = Save chart as PNG       [tbl] = View raw data
  [owl] = Gnosis watermark        [<]   = Collapse sidebar
  +12.3% rendered in green        -5.1% rendered in red
```

### Report UI Features

- **Collapsible sidebar** (224px expanded / 56px collapsed) with section navigation
- **Light/dark theme** toggle (light by default), syncs with MCP host
- **Gnosis owl watermark** on every chart (theme-aware)
- **Chart toolbar**: save as PNG image (2x resolution), view raw data table
- **Value coloring**: `+` values in green, `-` values in red
- **Responsive layout**, print-friendly (sidebar hidden when printing)
- **Three rendering modes**: MCP App (iframe), standalone HTML (browser), dev server (hot reload)

---

## Databases

```
  ┌─────────────────────────────────────────────────────────────┐
  │                    ClickHouse Cloud                         │
  │                                                             │
  │  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐   │
  │  │  execution   │  │  consensus  │  │  crawlers_data    │   │
  │  │              │  │             │  │                   │   │
  │  │  blocks      │  │  validators │  │  dune_labels (5M) │   │
  │  │  transactions│  │  attestation│  │  dune_prices      │   │
  │  │  logs        │  │  rewards    │  │  bridge_flows     │   │
  │  │  traces      │  │  deposits   │  │  gno_supply       │   │
  │  │  contracts   │  │  blobs      │  │  probelab_*       │   │
  │  │  transfers   │  │  specs      │  │  gpay_wallets     │   │
  │  └──────────────┘  └─────────────┘  └───────────────────┘   │
  │                                                             │
  │  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐   │
  │  │  nebula      │  │ nebula_     │  │  dbt              │   │
  │  │              │  │  discv4     │  │                   │   │
  │  │  crawls      │  │  (variant)  │  │  ~400 models      │   │
  │  │  visits      │  │             │  │  8 modules:       │   │
  │  │  (P2P data)  │  │             │  │   execution (208) │   │
  │  │              │  │             │  │   consensus (54)  │   │
  │  │              │  │             │  │   contracts (44)  │   │
  │  │              │  │             │  │   p2p (27)        │   │
  │  │              │  │             │  │   bridges (18)    │   │
  │  │              │  │             │  │   ESG (18)        │   │
  │  │              │  │             │  │   probelab (9)    │   │
  │  │              │  │             │  │   crawlers (9)    │   │
  │  └──────────────┘  └─────────────┘  └───────────────────┘   │
  └─────────────────────────────────────────────────────────────┘
```

---

## Agent Personas

Cerebro MCP uses three specialized agent personas for complex report generation:

```
  ┌────────────────────┐    ┌─────────────────────┐    ┌───────────────────┐
  │ Analytics Reporter │    │    UI Designer      │    │  Reality Checker  │
  │                    │    │                     │    │                   │
  │ - search_models    │    │ - Chart type        │    │ - SQL safety      │
  │ - describe_table   │──► │   selection         │──► │ - Data validation │
  │ - execute_query    │    │ - Markdown layout   │    │ - Chart specs     │
  │ - generate_chart   │    │ - generate_report   │    │ - Report structure│
  │                    │    │                     │    │ - Formatting QA   │
  │ SOP: DISCOVER →    │    │ Enforces:           │    │                   │
  │   VERIFY → SAMPLE  │    │ - Min 2 h2 sections │    │ Zero tolerance:   │
  │   → EXECUTE →      │    │ - Descriptive titles│    │ - Wrong columns   │
  │   VISUALIZE        │    │ - No emojis         │    │ - Missing dates   │
  │                    │    │ - Report link       │    │ - Bad chart types │
  └────────────────────┘    └─────────────────────┘    └───────────────────┘
```

Each persona is loaded via `get_agent_persona(role)` and provides strict operational rules, success metrics, and BAD/GOOD formatting examples.

---

## Tools

### Query & Schema

| Tool | Description |
|------|-------------|
| `execute_query` | Run read-only SQL against ClickHouse (6 databases) |
| `start_query` | Submit long-running query, returns query ID |
| `get_query_results` | Poll async query status and results |
| `explain_query` | Show ClickHouse execution plan |
| `list_tables` | List tables in a database with row counts |
| `describe_table` | Column schema with dbt descriptions |
| `get_sample_data` | Sample rows to understand data shape |

### dbt Models

| Tool | Description |
|------|-------------|
| `search_models` | Search ~400 dbt models by name, description, tags, or module |
| `get_model_details` | Full model info: SQL, columns, lineage, dependencies |

### Visualization

| Tool | Description |
|------|-------------|
| `generate_chart` | Create ECharts visualization (line, area, bar, pie, numberDisplay) |
| `generate_report` | Assemble interactive report with chart placeholders |
| `list_charts` | Show registered charts in current session |
| `open_report` | Reopen a saved report by ID |
| `list_reports` | List all saved reports on disk |

### Metadata

| Tool | Description |
|------|-------------|
| `list_databases` | All ClickHouse databases with descriptions |
| `system_status` | Server health: ClickHouse, manifest, config |
| `resolve_address` | Look up address labels (5.3M entries from Dune) |
| `get_token_metadata` | Token info: address, decimals, price data |
| `search_models_by_address` | Find dbt models related to a contract |
| `search_docs` | Search platform documentation and references |

### Saved Queries

| Tool | Description |
|------|-------------|
| `save_query` | Save a query for reuse |
| `list_saved_queries` | Show all saved queries |
| `run_saved_query` | Execute a saved query by name |

### Reasoning & Tracing

| Tool | Description |
|------|-------------|
| `set_thinking_mode` | Enable/disable reasoning capture |
| `log_reasoning` | Record a decision point for audit |
| `get_reasoning_log` | Retrieve trace for a session |
| `get_performance_stats` | Aggregate metrics across sessions |

---

## MCP App (Interactive Reports)

Cerebro MCP implements the [MCP Apps](https://github.com/modelcontextprotocol/ext-apps) standard to deliver interactive reports as native UI within MCP clients.

```
  generate_chart() ──► chart_registry (in-memory, 2h TTL)
       (repeat)                │
                               ▼
  generate_report() ──► CallToolResult
                         ├── content: TextContent (summary + file:// link)
                         └── structuredContent: { title, charts, sections_html }
                                │
                   ┌────────────┼────────────────┐
                   ▼            ▼                ▼
              Claude Desktop   VS Code      Claude Code
              (MCP App iframe) (MCP App)    (file:// link)
                   │            │                │
                   ▼            ▼                ▼
              React Report UI renders      Browser opens
              via ontoolresult callback    standalone HTML
```

### Rendering by Client

| Client | Behavior |
|--------|----------|
| **Claude Desktop** | Renders MCP App inline in conversation |
| **VS Code** | Renders MCP App inline in chat panel |
| **Claude Code** | Returns summary text with `file://` link |

### Report Storage

Reports are saved as self-contained HTML files at `~/.cerebro/reports/` with embedded JSON data. They can be reopened anytime with `open_report("8charID")` or shared as standalone files.

---

## Quickstart

### Prerequisites

- Python 3.10+
- Node.js 20+ (for UI build)
- ClickHouse Cloud credentials (Gnosis Chain instance)

### Install

```bash
git clone https://github.com/gnosischain/cerebro-mcp.git
cd cerebro-mcp

# Configure
cp .env.example .env
# Edit .env with your CLICKHOUSE_PASSWORD

# Build UI and install
make install
# Runs: npm ci && npm run build → pip install -e .
```

### Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "cerebro-mcp",
      "env": {
        "CLICKHOUSE_PASSWORD": "your_password"
      }
    }
  }
}
```

### Connect to Claude Code

Add to `.mcp.json` in your project or `~/.claude/.mcp.json` for global access:

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

### Connect to VS Code

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "cerebro": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

### Docker

```bash
docker build -t cerebro-mcp .
docker run --env-file .env cerebro-mcp
```

---

## Deployment

### CI/CD

Push to `main` triggers GitHub Actions to build and push multi-arch Docker images:

```
ghcr.io/gnosischain/gc-cerebro-mcp:latest
ghcr.io/gnosischain/gc-cerebro-mcp:<commit-sha>
```

### SSE Transport

The `--sse` flag starts an HTTP server (uvicorn) for remote MCP clients:

```bash
cerebro-mcp --sse
# Listens on http://0.0.0.0:8000 (configurable via FASTMCP_HOST / FASTMCP_PORT)
```

Without `--sse`, the server uses stdio transport (default for local Claude Desktop).

### Authentication

Set `MCP_AUTH_TOKEN` to require Bearer token authentication on all endpoints:

```bash
export MCP_AUTH_TOKEN=$(openssl rand -hex 32)
cerebro-mcp --sse
```

- All requests require `Authorization: Bearer <token>` header
- `/health` endpoint bypasses auth (for K8s probes)
- When `MCP_AUTH_TOKEN` is unset, auth is disabled (local dev)

### Hosted Endpoint

The team instance is deployed on EKS at `mcp.analytics.gnosis.io`:

```json
{
  "mcpServers": {
    "cerebro": {
      "url": "https://mcp.analytics.gnosis.io/sse",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Terraform deployment details are in the [infrastructure repo](https://github.com/gnosischain/infrastructure-gnosis-analytics-deployments/tree/main/aws/deployments/gnosis-analytics/mcp).

---

## Configuration

All settings via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKHOUSE_HOST` | `ujt1j3jrk0.eu-central-1.aws.clickhouse.cloud` | ClickHouse server |
| `CLICKHOUSE_PORT` | `8443` | ClickHouse port |
| `CLICKHOUSE_USER` | `default` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | *(required)* | ClickHouse password |
| `CLICKHOUSE_SECURE` | `True` | Use TLS |
| `DBT_MANIFEST_URL` | `https://gnosischain.github.io/dbt-cerebro/manifest.json` | dbt manifest |
| `DBT_MANIFEST_PATH` | -- | Local manifest fallback |
| `MAX_ROWS` | `10000` | Max rows per query |
| `QUERY_TIMEOUT_SECONDS` | `30` | Query timeout |
| `MAX_QUERY_LENGTH` | `10000` | Max SQL length |
| `TOOL_RESPONSE_MAX_CHARS` | `40000` | Max chars per tool response |
| `THINKING_ALWAYS_ON` | `True` | Auto-capture all tool calls |
| `THINKING_LOG_DIR` | `.cerebro/logs` | Trace log directory |
| `THINKING_LOG_RETENTION_DAYS` | `30` | Log retention |
| `CEREBRO_REPORT_DIR` | `~/.cerebro/reports` | Saved report directory |
| `CEREBRO_SAVED_QUERIES_DIR` | `~/.cerebro-mcp` | Saved queries directory |
| `MCP_AUTH_TOKEN` | -- | Bearer token for SSE auth (disabled when unset) |
| `FASTMCP_HOST` | `127.0.0.1` | SSE server bind address |
| `FASTMCP_PORT` | `8000` | SSE server port |

---

## Safety

All SQL is validated before execution:

- **Read-only**: Only `SELECT`, `EXPLAIN`, `DESCRIBE`, `SHOW` allowed
- **No writes**: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE` blocked
- **No injection**: Identifiers validated (alphanumeric + underscore only)
- **Size limits**: Max query length, max rows, query timeout
- **Auto LIMIT**: Queries without `LIMIT` get one appended automatically

### Recommended: Read-Only ClickHouse User

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

---

## Reasoning & Tracing

Built-in tracing records every tool call and reasoning step as JSON session traces.

```
  Tool Call ──► Auto-capture ──► Session Trace (.cerebro/logs/)
                                  │
                  ┌───────────────┼───────────────┐
                  │               │               │
                  ▼               ▼               ▼
              tool_name       duration_ms     success/error
              tool_args       timestamp       (redacted secrets)
              tool_result     step_number
```

- **Auto-capture**: Every tool call recorded when `THINKING_ALWAYS_ON=True`
- **Sensitive data redaction**: passwords, tokens, API keys automatically stripped
- **30-day retention**: Old traces auto-pruned
- **Performance stats**: Aggregate metrics across sessions via `get_performance_stats`

---

## Project Structure

```
cerebro-mcp/
├── src/cerebro_mcp/
│   ├── server.py                    # FastMCP server, tool registration
│   ├── config.py                    # Settings from env vars
│   ├── clickhouse_client.py         # ClickHouse connection pool + cache
│   ├── manifest_loader.py           # dbt manifest loading + indexing
│   ├── safety.py                    # SQL validation + injection prevention
│   ├── tools/
│   │   ├── query.py                 # execute_query, explain_query
│   │   ├── query_async.py           # start_query, get_query_results
│   │   ├── schema.py                # list_tables, describe_table, get_sample_data
│   │   ├── dbt.py                   # search_models, get_model_details
│   │   ├── metadata.py              # list_databases, resolve_address, tokens
│   │   ├── saved_queries.py         # save/list/run saved queries
│   │   ├── visualization.py         # generate_chart, generate_report
│   │   ├── reasoning.py             # tracing, performance stats
│   │   └── agents.py                # get_agent_persona
│   ├── prompts/
│   │   ├── templates.py             # MCP prompts
│   │   └── agents/                  # Agent persona definitions
│   │       ├── analytics_reporter.md
│   │       ├── ui_designer.md
│   │       └── reality_checker.md
│   ├── resources/
│   │   ├── context.py               # Platform overview, SQL guide
│   │   └── reference.py             # Chain params, addresses, metrics
│   └── static/
│       └── report.html              # Built React UI (generated by make build-ui)
├── ui/                              # React + Vite frontend
│   ├── src/
│   │   ├── App.tsx                  # Dashboard layout with sidebar
│   │   ├── components/              # ChartCard, Sidebar, ReportHeader, ...
│   │   ├── hooks/                   # useReportData, useTheme
│   │   ├── themes/                  # tokens.css, global.css, ECharts themes
│   │   └── assets/                  # Gnosis watermark PNGs (base64)
│   ├── package.json
│   └── vite.config.ts               # Single-file HTML build (vite-plugin-singlefile)
├── tests/                           # pytest suite (131 tests)
├── pyproject.toml
├── Dockerfile                       # Multi-stage: Node UI build + Python
├── Makefile                         # build-ui, install, dev
├── CLAUDE.md                        # Client-side instructions for Claude Code
└── .env.example
```

---

## Development

```bash
# UI dev server with hot reload
make dev

# Build UI only
make build-ui

# Run tests
pytest -v

# Full install (build UI + pip install)
make install
```

### Testing with MCP Inspector

```bash
# Spawn server with Inspector UI
uv run mcp dev src/cerebro_mcp/server.py

# Or run with SSE transport
uv run cerebro-mcp --sse
```

---

## Usage Examples

### Quick Queries (Markdown Output)
- "How many transactions were there on Gnosis Chain yesterday?"
- "What's the GNO token price trend this week?"
- "Show me the top addresses by gas usage in the last 7 days"

### Visual Reports (Interactive HTML)
- "Give me a weekly report on Gnosis Chain activity with charts"
- "Show me DeFi TVL trends over the past month"
- "Create a report comparing bridge flow volumes by chain"
- "Analyze validator performance trends"

### Protocol Exploration
- "Explore the Circles protocol data"
- "What Balancer pools exist on Gnosis Chain?"
- "Find all models related to bridge flows"

---

## Gnosis Chain Reference

| Parameter | Value |
|-----------|-------|
| Chain ID | 100 |
| Block time | 5 seconds (~17,280 blocks/day) |
| Gas token | xDAI (18 decimals) |
| Staking token | GNO (18 decimals, 1 per validator) |
| Slots per epoch | 16 |
| USDC/USDT decimals | 6 |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `mcp[cli]` >= 1.2.0 | MCP Python SDK (FastMCP) |
| `clickhouse-connect` >= 0.7.0 | ClickHouse client |
| `pydantic-settings` >= 2.0 | Settings management |
| `python-dotenv` >= 1.0 | .env file loading |
| `requests` >= 2.31 | HTTP client for manifest |

**Frontend**: React 19, ECharts 5.6, Tailwind CSS 4, Lucide React, `@modelcontextprotocol/ext-apps`

---

## License

See [LICENSE](LICENSE) for details.
