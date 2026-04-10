# Security Architecture

Cerebro MCP includes a detection-first security hardening layer that classifies tool risk, detects suspicious invocations, and maintains an append-only audit trail. This layer is observation-only (`log_only` mode) and never blocks tool execution.

## Motivation

The LLM supply-chain attack surface described in "Your Agent Is Mine" (arXiv:2604.08407) demonstrates that intermediary routers can inject malicious tool calls that reach MCP servers unverified. Cerebro's security layer makes such attacks visible and auditable, even though it does not enforce blocking in this phase.

## Tool Risk Classification

Every registered tool is assigned one or more risk classes:

| Risk Class | Description | Examples |
|---|---|---|
| `read_only` | No side effects; reads data or computes results | `execute_query`, `describe_table`, `search_models`, `generate_charts` |
| `server_state_write` | Persists state to disk or in-memory caches | `save_query`, `generate_report`, `start_research_project`, `storyteller_record_*` |
| `workspace_write` | Writes files to the workspace filesystem | `scaffold_dashboard_tab` |
| `subprocess` | Spawns external processes | `scaffold_dashboard_tab` (runs `pnpm build`) |
| `app_only` | Hidden from model-facing tool list; only callable by frontend ext-apps SDK | `get_mini_app_rows`, `get_mini_app_state` |

Tools not in the static registry (e.g., dynamically registered custom query tools) default to `read_only`.

The registry is defined in `src/cerebro_mcp/security.py` as the `TOOL_RISK_REGISTRY` dict.

### Risk Priority

When a tool has multiple risk classes (e.g., `scaffold_dashboard_tab` is both `workspace_write` and `subprocess`), the primary risk class is determined by priority order: `subprocess` > `workspace_write` > `app_only` > `server_state_write` > `read_only`.

## Suspicious-Call Detection

The security layer flags tool calls as suspicious when:

| Flag | Condition |
|---|---|
| `app_only_tool_called` | An `app_only` tool is invoked (always flagged regardless of transport) |
| `workspace_write_via_sse` | A `workspace_write` or `subprocess` tool is called over the SSE transport |
| `unknown_tool` | The tool name is not in the static risk registry |

Suspicious flags are emitted to both the JSONL audit log and Prometheus counters (`cerebro_security_suspicious_calls_total`).

## Audit Log

Every tool call produces an append-only JSONL audit event written to `MCP_SECURITY_LOG_DIR` (default `.cerebro/security_audit/`). Files rotate daily with the naming pattern `security_audit_YYYY-MM-DD.jsonl`.

### Audit Event Schema

```json
{
  "timestamp": "2026-04-10T14:32:01.123456+00:00",
  "transport": "stdio",
  "auth_present": false,
  "tool_name": "execute_query",
  "risk_class": "read_only",
  "visibility": "public",
  "redacted_arg_summary": "{\"sql\":\"SELECT count() FROM dbt.api_execution_transactions_daily\",\"database\":\"dbt\"}",
  "arg_hash": "a1b2c3d4e5f6...",
  "result_hash": "f6e5d4c3b2a1...",
  "duration_ms": 142,
  "success": true,
  "suspicious_flags": []
}
```

| Field | Description |
|---|---|
| `timestamp` | ISO 8601 UTC timestamp |
| `transport` | `stdio` or `sse` |
| `auth_present` | Whether `MCP_AUTH_TOKEN` is configured |
| `tool_name` | MCP tool name |
| `risk_class` | Primary risk class |
| `visibility` | `public` or `app_only` |
| `redacted_arg_summary` | First 200 chars of JSON-serialized redacted arguments |
| `arg_hash` | SHA-256 of canonical JSON of redacted arguments |
| `result_hash` | SHA-256 of canonical JSON of redacted result |
| `duration_ms` | Execution time in milliseconds |
| `success` | Whether the tool call succeeded |
| `suspicious_flags` | List of flag strings (empty when not suspicious) |
| `error` | Error message (present only on failure) |

### Redaction

Argument and result payloads are redacted before hashing and summarization using the same `_redact_sensitive` function used by the reasoning trace system. Keys matching `password`, `token`, `api_key`, `secret`, `authorization`, `private_key`, and related markers are replaced with `***REDACTED***`.

### Integrity

Canonical hashing uses `json.dumps(payload, sort_keys=True, separators=(",", ":"))` followed by SHA-256. This provides deterministic digests for tamper detection and audit correlation.

## Prometheus Metrics

| Metric | Labels | Description |
|---|---|---|
| `cerebro_security_high_risk_tool_calls_total` | `tool_name`, `risk_class`, `transport` | Tool calls classified above `read_only` |
| `cerebro_security_suspicious_calls_total` | `tool_name`, `flag_type` | Tool calls flagged as suspicious |
| `cerebro_security_app_only_calls_total` | `tool_name`, `transport` | Calls to app-only tools |
| `cerebro_report_token_auth_total` | `status` | Report endpoint auth events (`success` or `denied`) |

## Artifact Provenance Logging

When remote artifacts (dbt manifest, catalog, semantic registry, docs index) are loaded or reloaded, the server emits a structured `artifact_reload` log event with:

- `label`: artifact name (e.g., "dbt manifest")
- `source`: `local` or `remote`
- `content_hash`: SHA-256 of the artifact content
- `etag`: HTTP ETag header (if available)
- `last_modified`: HTTP Last-Modified header (if available)
- `changed`: whether the content actually changed

These events are visible in Loki/structured logs and the Grafana dashboard's "Artifact Reload Log" panel.

## Report Endpoint Audit

The `/reports/{id}` endpoint emits a `report_token_auth` log event for every access attempt when `MCP_AUTH_TOKEN` is configured. Fields:

- `report_id`: the requested report ID
- `auth_method`: `bearer`, `query_token`, or `none`
- `success`: whether the auth check passed

The `cerebro_report_token_auth_total` Prometheus counter tracks successes and denials.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MCP_SECURITY_POLICY_MODE` | `log_only` | Security policy mode. Future values: `warn`, `enforce` |
| `MCP_SECURITY_LOG_DIR` | `.cerebro/security_audit` | Directory for daily JSONL audit files |
| `MCP_EXPECTED_MANIFEST_SHA256` | empty | Optional manifest content pin; empty disables |

## Architecture

The security layer is implemented as a post-execution hook in the existing tool tracing wrapper (`tools/reasoning.py`). The `assess_tool_call` function runs after every tool invocation and:

1. Looks up the tool's risk classes from the static registry
2. Detects the transport (`stdio` or `sse`) from the environment
3. Computes SHA-256 hashes of redacted arguments and results
4. Detects suspicious flags
5. Writes a JSONL audit event (thread-safe, daily rotation)
6. Emits structured log events for suspicious calls (visible in Loki)
7. Increments Prometheus counters

The security assessment is wrapped in `try/except Exception` with debug-level logging, ensuring it never breaks tool execution. This is critical for the observation-only contract.

### Circular Import Handling

`security.py` imports `_redact_sensitive` from `tools/reasoning.py` at module level. `tools/reasoning.py` imports `assess_tool_call` from `security.py` via deferred import (inside the function body). This breaks the circular dependency cleanly.

## Future: Enforcement Phase

The `MCP_SECURITY_POLICY_MODE` setting is designed for a later enforcement phase. When switched from `log_only` to `warn` or `enforce`, the same risk metadata and audit pipeline can be reused to block suspicious calls or require additional verification. The JSONL audit trail and Prometheus counters provide the observability foundation needed to tune enforcement thresholds before enabling them.
