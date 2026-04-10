# Observability

Cerebro MCP exposes comprehensive observability through Prometheus metrics, structured JSON logs, reasoning traces, and a ready-to-import Grafana dashboard.

## Grafana Dashboard

A complete Grafana dashboard is provided at `grafana/cerebro-mcp-observability.json`. Import it into your Grafana instance and configure the template variables.

### Dashboard Sections

| Row | Purpose | Key Panels |
|---|---|---|
| **Overview** | Kubernetes deployment health | Desired/available replicas, active pods, restarts, pod/node table, images |
| **HTTP / SSE** | HTTP transport metrics | Request rate by path/status, p95 latency, in-progress requests, 4xx/5xx rate |
| **MCP Internals** | MCP protocol and tool metrics | MCP request rate, p95 MCP latency, tool call rate, p95 tool latency, top failing tools |
| **Security Audit** | Security detection metrics | Suspicious calls KPI, high-risk tool calls, app-only calls, report auth denials, suspicious call trends, high-risk breakdown by tool |
| **Tool Usage Details** | Granular tool analytics | Top 15 tools by volume, call distribution pie chart, error rate by tool, slowest tools (p99) |
| **Semantic Layer** | Semantic execution health | Semantic enabled state, registry model/metric counts, snapshot age, query attempts, route decisions, e2e latency, planner failures |
| **ClickHouse** | Database query metrics | Query rate by database, p95 latency, error rate, p95 rows returned |
| **Pod Resources** | Container resource usage | CPU usage/requests/limits, throttling, memory, network RX/TX, pod restarts |
| **Logs** | Structured log exploration | Warnings/errors volume, live structured logs, tool call logs, MCP request logs, ClickHouse logs, failed events, security audit log, artifact reload log |

### Template Variables

| Variable | Type | Description |
|---|---|---|
| `$prometheus` | datasource | Prometheus datasource |
| `$loki` | datasource | Loki datasource |
| `$cluster` | query | Kubernetes cluster |
| `$namespace` | query | Kubernetes namespace (default: `analytics-preview`) |
| `$workload` | custom | Deployment name (default: `cerebro-mcp`) |
| `$pod` | query | Pod selector (default: all) |

## Prometheus Metrics

### HTTP / SSE Transport

| Metric | Type | Labels | Description |
|---|---|---|---|
| `cerebro_http_requests_total` | Counter | `method`, `path`, `status` | Total HTTP requests |
| `cerebro_http_request_duration_seconds` | Histogram | `method`, `path` | HTTP request latency |
| `cerebro_http_requests_in_progress` | Gauge | `method`, `path` | Currently active HTTP requests |

### MCP Protocol

| Metric | Type | Labels | Description |
|---|---|---|---|
| `cerebro_mcp_requests_total` | Counter | `method`, `status` | MCP protocol requests |
| `cerebro_mcp_request_duration_seconds` | Histogram | `method` | MCP request latency |
| `cerebro_mcp_tool_calls_total` | Counter | `tool_name`, `status` | Tool invocations |
| `cerebro_mcp_tool_duration_seconds` | Histogram | `tool_name` | Tool execution latency |

### ClickHouse

| Metric | Type | Labels | Description |
|---|---|---|---|
| `cerebro_clickhouse_query_duration_seconds` | Histogram | `database`, `audience`, `fetch_mode`, `status` | Query latency |
| `cerebro_clickhouse_query_errors_total` | Counter | `database`, `audience` | Failed queries |
| `cerebro_clickhouse_rows_returned` | Histogram | `database`, `audience` | Rows returned per query |

### Security Audit

| Metric | Type | Labels | Description |
|---|---|---|---|
| `cerebro_security_high_risk_tool_calls_total` | Counter | `tool_name`, `risk_class`, `transport` | Non-read_only tool calls |
| `cerebro_security_suspicious_calls_total` | Counter | `tool_name`, `flag_type` | Suspicious tool call flags |
| `cerebro_security_app_only_calls_total` | Counter | `tool_name`, `transport` | App-only tool invocations |
| `cerebro_report_token_auth_total` | Counter | `status` | Report endpoint auth events |

### Semantic Layer

| Metric | Type | Labels | Description |
|---|---|---|---|
| `semantic_tool_calls_total` | Counter | `tool_name`, `status`, `agent_role`, `entrypoint` | Semantic tool invocations |
| `semantic_query_attempts_total` | Counter | `planner_mode`, `attempt`, `result`, `agent_role` | Query attempts by outcome |
| `semantic_query_repairs_total` | Counter | `repair_action`, `error_class`, `agent_role` | Auto-repair attempts |
| `semantic_planner_failures_total` | Counter | `reason`, `planner_mode`, `agent_role` | Planner failures by reason |
| `semantic_fallback_total` | Counter | `fallback_target`, `reason`, `agent_role` | Fallback events |
| `semantic_route_total` | Counter | `route`, `mode` | Analytics preflight routing |
| `semantic_bypass_total` | Counter | `stage`, `reason` | Raw SQL redirections |
| `semantic_snapshot_reload_total` | Counter | `status` | Snapshot reload events |
| `semantic_snapshot_stale_total` | Counter | `reason` | Snapshot staleness events |
| `semantic_planner_latency_seconds` | Histogram | `planner_mode` | Planning latency |
| `semantic_sql_compile_latency_seconds` | Histogram | `planner_mode` | SQL compilation latency |
| `semantic_query_end_to_end_latency_seconds` | Histogram | `planner_mode`, `repair_state` | Full query execution latency |
| `semantic_snapshot_reload_latency_seconds` | Histogram | `status` | Snapshot reload time |
| `semantic_snapshot_age_seconds` | Gauge | — | Current snapshot age |
| `semantic_registry_models_total` | Gauge | `semantic_status` | Models by status |
| `semantic_registry_metrics_total` | Gauge | `quality_tier` | Metrics by quality tier |
| `semantic_semantic_enabled` | Gauge | `state` | Current semantic execution state |

## Structured JSON Logging

All server logs use JSON format via `JsonFormatter`. Each log line includes:

```json
{
  "timestamp": "2026-04-10T14:32:01.123456+00:00",
  "level": "INFO",
  "logger": "cerebro_mcp.tools.reasoning",
  "message": "mcp_tool_call",
  "event": "mcp_tool_call",
  "tool_name": "execute_query",
  "duration_ms": 142,
  "success": true
}
```

### Key Event Types

| Event | Source | Description |
|---|---|---|
| `mcp_tool_call` | `tools/reasoning.py` | Every tool invocation with timing and success |
| `mcp_request` | `tools/reasoning.py` | Low-level MCP protocol requests |
| `clickhouse_query` | `clickhouse_client.py` | ClickHouse query execution |
| `security_audit` | `security.py` | Suspicious tool call flags (only emitted when flags are non-empty) |
| `report_token_auth` | `server.py` | Report endpoint auth events |
| `artifact_reload` | `artifact_loader.py` | Artifact load/reload with hash and source |
| `transport_selected` | `server.py` | Server startup transport choice |

### Loki Queries

Useful Loki queries for the Grafana Logs panels:

```logql
# All structured logs
{namespace="$namespace", pod=~"$workload-.*"} |= "\"timestamp\":\""

# Tool calls only
{namespace="$namespace", pod=~"$workload-.*"} |= "\"event\":\"mcp_tool_call\""

# Security audit events (suspicious calls)
{namespace="$namespace", pod=~"$workload-.*"} |= "\"event\":\"security_audit\""

# Artifact reloads
{namespace="$namespace", pod=~"$workload-.*"} |= "\"event\":\"artifact_reload\""

# Failed events
{namespace="$namespace", pod=~"$workload-.*"} |= "\"success\":false"

# Errors and warnings
{namespace="$namespace", pod=~"$workload-.*"} |~ "\"level\":\"(ERROR|WARNING)\""
```

## Reasoning Traces

The reasoning/tracing system (`tools/reasoning.py`) automatically captures every tool call with:

- Tool name, arguments (redacted), and result (redacted)
- Timing (start, duration)
- Success/failure status
- Error details

Traces are persisted to `THINKING_LOG_DIR` (default `.cerebro/logs/`) as session JSON files. They are separate from the security JSONL audit log and serve a different purpose (debugging and performance analysis vs. security audit).

Access traces via:

- `set_thinking_mode(enabled=True)` to start/stop a trace session
- `log_reasoning(step, content)` for manual trace events
- `get_reasoning_log(session_id)` to retrieve a session trace
- `get_performance_stats(last_n)` for aggregated performance metrics

## Metrics Endpoint

In SSE mode, Prometheus metrics are served at `/metrics` (unauthenticated, exempt from bearer auth middleware).

```bash
curl http://localhost:8000/metrics
```
