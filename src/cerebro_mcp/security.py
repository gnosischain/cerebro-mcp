"""Detection-first security hardening for Cerebro MCP.

Provides:
- Tool risk classification registry
- Suspicious-call flag detection
- Append-only JSONL security audit log
- Top-level assessment function called from the tracing wrapper

This module is **observation-only** (``log_only`` mode). It never blocks
tool execution. A later enforcement phase can reuse the same risk metadata
and audit pipeline by switching ``MCP_SECURITY_POLICY_MODE``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk classes
# ---------------------------------------------------------------------------

class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    SERVER_STATE_WRITE = "server_state_write"
    WORKSPACE_WRITE = "workspace_write"
    SUBPROCESS = "subprocess"
    APP_ONLY = "app_only"


_RISK_PRIORITY: list[RiskClass] = [
    RiskClass.SUBPROCESS,
    RiskClass.WORKSPACE_WRITE,
    RiskClass.APP_ONLY,
    RiskClass.SERVER_STATE_WRITE,
    RiskClass.READ_ONLY,
]

# ---------------------------------------------------------------------------
# Tool risk registry — static mapping of tool name → risk classes
# ---------------------------------------------------------------------------

_RO = frozenset({RiskClass.READ_ONLY})
_SW = frozenset({RiskClass.SERVER_STATE_WRITE})
_WS = frozenset({RiskClass.WORKSPACE_WRITE, RiskClass.SUBPROCESS})
_AO = frozenset({RiskClass.APP_ONLY})

TOOL_RISK_REGISTRY: dict[str, frozenset[RiskClass]] = {
    # ── read_only tools ──────────────────────────────────────────────
    # schema / metadata
    "describe_table": _RO,
    "list_tables": _RO,
    "list_databases": _RO,
    "get_sample_data": _RO,
    "get_platform_constants": _RO,
    "get_token_metadata": _RO,
    "search_docs": _RO,
    "get_doc_chunk": _RO,
    "resolve_address": _RO,
    "system_status": _RO,
    "get_help": _RO,
    # dbt discovery
    "search_models": _RO,
    "get_model_details": _RO,
    "discover_models": _RO,
    "search_models_by_address": _RO,
    # query execution (read-only against ClickHouse)
    "execute_query": _RO,
    "start_query": _RO,
    "get_query_results": _RO,
    "explain_query": _RO,
    "list_saved_queries": _RO,
    "run_saved_query": _RO,
    # visualization (chart generation is ephemeral / in-memory)
    "generate_chart": _RO,
    "generate_charts": _RO,
    "quick_chart": _RO,
    "list_charts": _RO,
    # semantic
    "preflight_analytics_request": _RO,
    "discover_metrics": _RO,
    "get_metric_details": _RO,
    "explain_metric_query": _RO,
    "query_metrics": _RO,
    "quick_metric_chart": _RO,
    "generate_metric_charts": _RO,
    # cross-check
    "verify_numbers": _RO,
    # agents
    "get_agent_persona": _RO,
    "get_clickhouse_query_rules": _RO,
    # reasoning (read)
    "log_reasoning": _RO,
    "get_reasoning_log": _RO,
    "get_performance_stats": _RO,
    # dashboard discovery
    "discover_dashboard_metrics": _RO,
    "list_custom_tools": _RO,
    # mini-app openers / loaders / updates (ephemeral in-memory views)
    "open_metric_lab": _RO,
    "open_metric_lab_from_sql": _RO,
    "open_metric_lab_from_metrics": _RO,
    "load_metric_lab_metric": _RO,
    "update_metric_lab_chart": _RO,
    "open_token_explorer": _RO,
    "load_token_explorer_token": _RO,
    "update_token_explorer_focus": _RO,
    "open_graph_explorer": _RO,
    "load_graph_explorer_seed": _RO,
    "expand_graph_explorer_node": _RO,
    "update_graph_explorer_focus": _RO,
    # custom query tools (parameterized, read-only SQL)
    # — dynamically registered; defaults to _RO via get_risk_classes()
    # research read
    "get_research_project": _RO,
    "get_research_evidence": _RO,
    "get_research_findings": _RO,
    "get_research_memory": _RO,
    # storyteller read
    "storyteller_status": _RO,
    # custom tools (bridge flows, deposits, etc.)
    "get_bridge_flows_by_token": _RO,
    "get_deposit_events": _RO,
    "get_gpay_wallet_activity": _RO,
    "get_liquidity_providers_by_token": _RO,
    "get_token_transfers_for_address": _RO,
    "get_validator_balance_history": _RO,
    "get_validator_withdrawals": _RO,

    # ── server_state_write tools ─────────────────────────────────────
    "save_query": _SW,
    "generate_report": _SW,
    "open_report": _SW,
    "export_report": _SW,
    "list_reports": _RO,
    "set_thinking_mode": _SW,
    # research persistence
    "start_research_project": _SW,
    "plan_research_phase": _SW,
    "execute_research_phase": _SW,
    "record_research_finding": _SW,
    "record_research_memory": _SW,
    "attach_research_evidence": _SW,
    "capture_schema_snapshot": _SW,
    "verify_research_phase": _SW,
    "prepare_peer_review": _SW,
    "record_peer_review": _SW,
    "publish_research_report": _SW,
    # storyteller persistence
    "storyteller_start_session": _SW,
    "storyteller_record_context_brief": _SW,
    "storyteller_record_big_idea": _SW,
    "storyteller_record_storyboard": _SW,
    "storyteller_record_visual_spec": _SW,
    "storyteller_record_final_story": _SW,
    "storyteller_run_clarity_checks": _SW,
    "storyteller_record_accessibility_pass": _SW,
    "storyteller_generate_story_report": _SW,
    "storyteller_end_session": _SW,

    # ── workspace_write + subprocess ─────────────────────────────────
    "scaffold_dashboard_tab": _WS,

    # ── app_only ─────────────────────────────────────────────────────
    "get_mini_app_rows": _AO,
    "get_mini_app_state": _AO,
}


def get_risk_classes(tool_name: str) -> frozenset[RiskClass]:
    """Return risk classes for a tool. Defaults to ``{READ_ONLY}`` for unknown tools."""
    return TOOL_RISK_REGISTRY.get(tool_name, _RO)


def primary_risk_class(tool_name: str) -> RiskClass:
    """Return the highest-priority risk class for a tool."""
    classes = get_risk_classes(tool_name)
    for rc in _RISK_PRIORITY:
        if rc in classes:
            return rc
    return RiskClass.READ_ONLY


# ---------------------------------------------------------------------------
# Suspicious flag detection
# ---------------------------------------------------------------------------

def detect_suspicious_flags(
    tool_name: str,
    risk_classes: frozenset[RiskClass],
    transport: str,
) -> list[str]:
    """Return a list of suspicious-flag strings (empty = not suspicious)."""
    flags: list[str] = []
    if RiskClass.APP_ONLY in risk_classes:
        flags.append("app_only_tool_called")
    if transport == "sse" and (
        RiskClass.WORKSPACE_WRITE in risk_classes
        or RiskClass.SUBPROCESS in risk_classes
    ):
        flags.append("workspace_write_via_sse")
    if tool_name not in TOOL_RISK_REGISTRY:
        flags.append("unknown_tool")
    return flags


# ---------------------------------------------------------------------------
# Canonical hashing (for audit trail integrity)
# ---------------------------------------------------------------------------

def _canonical_hash(payload: Any) -> str:
    """SHA-256 of canonical JSON of a redacted payload."""
    from cerebro_mcp.tools.reasoning import _redact_sensitive

    redacted = _redact_sensitive(payload)
    canonical = json.dumps(
        redacted, sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Append-only JSONL audit writer
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()
_audit_file_handle: Any = None
_audit_current_date: str = ""


def _get_audit_file() -> Any:
    """Return a file handle for today's audit log, rotating on day change."""
    global _audit_file_handle, _audit_current_date  # noqa: PLW0603

    from cerebro_mcp.config import settings

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _audit_current_date or _audit_file_handle is None:
        if _audit_file_handle is not None:
            try:
                _audit_file_handle.close()
            except Exception:
                pass
        audit_dir = Path(settings.MCP_SECURITY_LOG_DIR)
        audit_dir.mkdir(parents=True, exist_ok=True)
        filepath = audit_dir / f"security_audit_{today}.jsonl"
        _audit_file_handle = open(filepath, "a", encoding="utf-8")  # noqa: SIM115
        _audit_current_date = today
    return _audit_file_handle


def write_audit_event(event: dict[str, Any]) -> None:
    """Append a single JSONL line to the daily security audit log."""
    line = json.dumps(event, default=str, separators=(",", ":"))
    with _audit_lock:
        fh = _get_audit_file()
        fh.write(line + "\n")
        fh.flush()


# ---------------------------------------------------------------------------
# Top-level security assessment — called from tracing wrapper
# ---------------------------------------------------------------------------

def assess_tool_call(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: Any | None,
    success: bool,
    duration_ms: int,
    error: str | None = None,
) -> None:
    """Assess a completed tool call for security; emit audit log and metrics.

    Called from the tracing wrapper after the tool has executed.
    This is log_only mode — never blocks execution.
    """
    from cerebro_mcp.observability import (
        log_event as _log_event,
        observe_security_app_only_call,
        observe_security_high_risk_call,
        observe_security_suspicious_call,
    )
    from cerebro_mcp.tools.reasoning import _redact_sensitive

    transport = os.environ.get("CEREBRO_TRANSPORT", "stdio")
    risk_classes = get_risk_classes(tool_name)
    risk_class_primary = primary_risk_class(tool_name)

    # Auth presence (SSE transport uses MCP_AUTH_TOKEN)
    auth_present = bool(os.environ.get("MCP_AUTH_TOKEN"))

    # Compute hashes on redacted payloads
    arg_hash = _canonical_hash(arguments) if arguments else ""
    result_hash = _canonical_hash(result) if result is not None else ""

    # Redacted argument summary (first 200 chars)
    try:
        redacted_args = _redact_sensitive(arguments or {})
        arg_summary = json.dumps(
            redacted_args, default=str, separators=(",", ":"),
        )[:200]
    except Exception:
        arg_summary = ""

    # Suspicious flags
    flags = detect_suspicious_flags(tool_name, risk_classes, transport)

    # Build and write audit event
    audit_event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transport": transport,
        "auth_present": auth_present,
        "tool_name": tool_name,
        "risk_class": risk_class_primary.value,
        "visibility": "app_only" if RiskClass.APP_ONLY in risk_classes else "public",
        "redacted_arg_summary": arg_summary,
        "arg_hash": arg_hash,
        "result_hash": result_hash,
        "duration_ms": duration_ms,
        "success": success,
        "suspicious_flags": flags,
    }
    if error:
        audit_event["error"] = error

    write_audit_event(audit_event)

    # Structured log for Loki/stdout (separate from JSONL file)
    if flags:
        _log_event(
            logger,
            "security_audit",
            tool_name=tool_name,
            risk_class=risk_class_primary.value,
            transport=transport,
            suspicious_flags=",".join(flags),
            success=success,
        )

    # Prometheus counters
    if risk_class_primary != RiskClass.READ_ONLY:
        observe_security_high_risk_call(
            tool_name=tool_name,
            risk_class=risk_class_primary.value,
            transport=transport,
        )

    for flag in flags:
        observe_security_suspicious_call(tool_name=tool_name, flag_type=flag)

    if RiskClass.APP_ONLY in risk_classes:
        observe_security_app_only_call(tool_name=tool_name, transport=transport)
