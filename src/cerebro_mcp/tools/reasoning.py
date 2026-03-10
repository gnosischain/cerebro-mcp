"""Persistent performance tracing and reasoning capture tools.

Stores session traces as JSON files in .cerebro/logs/ for monitoring
automatic reasoning/performance analysis across MCP sessions.
"""

import atexit
import json
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cerebro_mcp.config import settings


@dataclass
class ReasoningStep:
    """A single step in a reasoning trace."""

    step_number: int
    timestamp: str
    step: str
    content: str
    agent: str = ""
    action: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: int = 0
    success: bool = True
    error: str | None = None
    auto_captured: bool = False
    event_kind: str = "reasoning"
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Any = None
    tool_error: Any = None
    request_type: str = ""
    request_method: str = ""
    request_payload: Any = None
    response_payload: Any = None


@dataclass
class SessionTrace:
    """Full trace for a single MCP session."""

    session_id: str
    started_at: str
    user_prompt: str = ""
    steps: list[ReasoningStep] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# --- Module state ---
_current_session: SessionTrace | None = None
_thinking_always_on: bool = settings.THINKING_ALWAYS_ON
_thinking_enabled: bool = settings.THINKING_MODE_ENABLED or _thinking_always_on
_retention_days: int = max(0, settings.THINKING_LOG_RETENTION_DAYS)
_lock = threading.Lock()
_log_dir = Path(settings.THINKING_LOG_DIR)
_last_prune_check_ts: float = 0.0

_REDACTED_VALUE = "***REDACTED***"
_SENSITIVE_KEY_MARKERS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "authorization",
    "auth_header",
    "private_key",
}
_EXCLUDED_AUTO_TRACE_TOOLS = {
    "log_reasoning",
    "get_reasoning_log",
    "get_performance_stats",
}
_PRUNE_INTERVAL_SECONDS = 3600
_AUTO_TRACE_INSTALLED_ATTR = "_cerebro_auto_trace_installed"
_AUTO_TRACE_TOOL_MANAGER_ORIGINAL_ATTR = "_cerebro_original_tool_manager_call_tool"
_AUTO_TRACE_TOOL_MANAGER_INSTALLED_ATTR = "_cerebro_tool_manager_tracing_installed"
_AUTO_TRACE_REQUEST_HANDLERS_INSTALLED_ATTR = "_cerebro_request_handlers_tracing_installed"
_AUTO_TRACE_REQUEST_HANDLERS_ORIGINAL_ATTR = "_cerebro_original_request_handlers"


def _atexit_finalize():
    """Finalize and save current session on server shutdown."""
    if _current_session and _current_session.steps:
        _finalize_session(_current_session)


atexit.register(_atexit_finalize)


def _ensure_log_dir() -> Path:
    """Create log directory if it doesn't exist."""
    _log_dir.mkdir(parents=True, exist_ok=True)
    return _log_dir


def _generate_session_id() -> str:
    """Generate a unique session ID with timestamp."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    return f"{ts}_{short_id}"


def _ensure_active_session_unlocked() -> bool:
    """Ensure there is an active session while lock is held."""
    global _current_session

    if not _thinking_enabled:
        return False

    if _current_session is None:
        _ensure_log_dir()
        _current_session = SessionTrace(
            session_id=_generate_session_id(),
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    return True


def _session_filepath(session_id: str) -> Path:
    """Get the file path for a session trace."""
    return _ensure_log_dir() / f"session_{session_id}.json"


def _save_session(session: SessionTrace) -> None:
    """Write session trace to disk."""
    filepath = _session_filepath(session.session_id)
    data = asdict(session)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def _maybe_prune_old_sessions_unlocked(force: bool = False) -> None:
    """Prune session files older than retention policy (lock must be held)."""
    global _last_prune_check_ts

    now_ts = time.time()
    if (
        not force
        and _last_prune_check_ts
        and (now_ts - _last_prune_check_ts) < _PRUNE_INTERVAL_SECONDS
    ):
        return

    _last_prune_check_ts = now_ts
    if _retention_days <= 0:
        return

    cutoff_ts = now_ts - (_retention_days * 24 * 60 * 60)
    for filepath in _ensure_log_dir().glob("session_*.json"):
        try:
            if filepath.stat().st_mtime < cutoff_ts:
                filepath.unlink()
        except OSError:
            continue


def _session_file_counts() -> tuple[int, int]:
    """Return total and last-24h session file counts."""
    total = 0
    recent = 0
    now_ts = time.time()
    recent_cutoff = now_ts - (24 * 60 * 60)
    for filepath in _ensure_log_dir().glob("session_*.json"):
        try:
            mtime = filepath.stat().st_mtime
        except OSError:
            continue
        total += 1
        if mtime >= recent_cutoff:
            recent += 1
    return total, recent


def get_tracing_status() -> dict[str, Any]:
    """Return current tracing status for diagnostics endpoints."""
    with _lock:
        total_files, recent_files = _session_file_counts()
        return {
            "enabled": _thinking_enabled,
            "always_on": _thinking_always_on,
            "log_dir": str(_ensure_log_dir().resolve()),
            "retention_days": _retention_days,
            "session_files": total_files,
            "recent_session_files": recent_files,
            "active_session_id": (
                _current_session.session_id if _current_session else ""
            ),
        }


def _load_session(filepath: Path) -> dict | None:
    """Load a session trace from disk."""
    try:
        return json.loads(filepath.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _normalize_payload(value: Any) -> Any:
    """Convert payloads into JSON-friendly structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if is_dataclass(value):
        return {
            str(key): _normalize_payload(item)
            for key, item in asdict(value).items()
        }

    if isinstance(value, dict):
        normalized = {
            str(key): _normalize_payload(item)
            for key, item in value.items()
        }
        # FastMCP unstructured tool responses often encode JSON in text blocks.
        # Parse those so redaction can inspect nested keys like token/api_key.
        if normalized.get("type") == "text":
            text_payload = normalized.get("text")
            if isinstance(text_payload, str):
                stripped = text_payload.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        normalized["text"] = _normalize_payload(
                            json.loads(stripped)
                        )
                    except json.JSONDecodeError:
                        pass
        return normalized

    if isinstance(value, (list, tuple, set)):
        return [_normalize_payload(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _normalize_payload(model_dump())
        except Exception:
            return repr(value)

    return repr(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(ch for ch in key.lower() if ch.isalnum() or ch == "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _redact_sensitive(payload: Any) -> Any:
    """Recursively redact known sensitive keys in dictionaries."""
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_sensitive_key(str(key)):
                redacted[str(key)] = _REDACTED_VALUE
            else:
                redacted[str(key)] = _redact_sensitive(value)
        return redacted

    if isinstance(payload, list):
        return [_redact_sensitive(item) for item in payload]

    return payload


def _prepare_payload(value: Any) -> Any:
    """Normalize and redact payloads before persistence."""
    normalized = _normalize_payload(value)
    return _redact_sensitive(normalized)


def _summarize_payload(value: Any, max_chars: int = 240) -> str:
    """Build a compact one-line summary for input/output fields."""
    try:
        text = json.dumps(_prepare_payload(value), default=str, ensure_ascii=True)
    except Exception:
        text = str(value)

    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _record_step(entry: ReasoningStep) -> int | None:
    """Append a reasoning step and persist session state."""
    global _current_session

    with _lock:
        if not _ensure_active_session_unlocked():
            return None

        entry.step_number = len(_current_session.steps) + 1
        _current_session.steps.append(entry)

        # Save after each step for crash safety
        _maybe_prune_old_sessions_unlocked()
        _save_session(_current_session)
        return entry.step_number


def _record_auto_tool_step(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    result: Any = None,
    error: Exception | None = None,
    duration_ms: int,
    success: bool,
) -> None:
    """Capture a tool call automatically when thinking mode is enabled."""
    if not _thinking_enabled:
        return

    if tool_name in _EXCLUDED_AUTO_TRACE_TOOLS:
        return

    safe_args = _prepare_payload(arguments or {})
    safe_result = _prepare_payload(result) if success else None
    safe_error = (
        _prepare_payload(
            {
                "type": type(error).__name__,
                "message": str(error),
            }
        )
        if error is not None
        else None
    )

    output_payload = safe_result if success else safe_error
    entry = ReasoningStep(
        step_number=0,
        timestamp=datetime.now(timezone.utc).isoformat(),
        step="auto_tool_call",
        content=f"Auto-captured MCP tool invocation for '{tool_name}'.",
        action=tool_name,
        input_summary=_summarize_payload(safe_args),
        output_summary=_summarize_payload(output_payload),
        duration_ms=duration_ms,
        success=success,
        error=str(error) if error else None,
        auto_captured=True,
        event_kind="tool_call",
        tool_name=tool_name,
        tool_args=safe_args,
        tool_result=safe_result,
        tool_error=safe_error,
    )
    _record_step(entry)


def _extract_error_text(payload: Any) -> str | None:
    """Extract a human-readable error from a normalized payload."""
    if isinstance(payload, dict):
        if "message" in payload and isinstance(payload["message"], str):
            return payload["message"]

        root = payload.get("root")
        if isinstance(root, dict):
            if isinstance(root.get("isError"), bool) and root["isError"]:
                content = root.get("content")
                if isinstance(content, list):
                    for item in content:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "text"
                            and isinstance(item.get("text"), str)
                        ):
                            return item["text"]
                return "MCP call returned an error result."
    return None


def _extract_request_method(req: Any) -> str:
    """Extract request method from typed MCP request objects."""
    direct_method = getattr(req, "method", None)
    if isinstance(direct_method, str) and direct_method:
        return direct_method

    normalized = _normalize_payload(req)
    if isinstance(normalized, dict):
        method = normalized.get("method")
        if isinstance(method, str) and method:
            return method

        root = normalized.get("root")
        if isinstance(root, dict):
            root_method = root.get("method")
            if isinstance(root_method, str) and root_method:
                return root_method

    return ""


def _record_mcp_request_step(
    request_type: str,
    request_method: str,
    request_payload: Any,
    *,
    response_payload: Any = None,
    error: Exception | None = None,
    duration_ms: int,
    success: bool,
) -> None:
    """Capture a low-level MCP request/response pair."""
    if not _thinking_enabled:
        return

    safe_request = _prepare_payload(request_payload)
    safe_response = _prepare_payload(response_payload)
    safe_error = (
        _prepare_payload(
            {
                "type": type(error).__name__,
                "message": str(error),
            }
        )
        if error is not None
        else None
    )

    output_payload = safe_response if safe_error is None else safe_error
    extracted_error = None if safe_error is None else str(error)
    if extracted_error is None:
        extracted_error = _extract_error_text(output_payload)

    entry = ReasoningStep(
        step_number=0,
        timestamp=datetime.now(timezone.utc).isoformat(),
        step="auto_mcp_request",
        content=(
            "Auto-captured MCP request "
            f"'{request_method or request_type}'."
        ),
        action=request_method or request_type,
        input_summary=_summarize_payload(safe_request),
        output_summary=_summarize_payload(output_payload),
        duration_ms=duration_ms,
        success=success and extracted_error is None,
        error=extracted_error,
        auto_captured=True,
        event_kind="mcp_request",
        request_type=request_type,
        request_method=request_method,
        request_payload=safe_request,
        response_payload=output_payload,
    )
    _record_step(entry)


def _install_tool_manager_tracing(mcp) -> None:
    """Install idempotent tracing around ToolManager.call_tool."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return

    if getattr(tool_manager, _AUTO_TRACE_TOOL_MANAGER_INSTALLED_ATTR, False):
        return

    original_call_tool = getattr(tool_manager, "call_tool", None)
    if original_call_tool is None:
        return

    async def _wrapped_call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        started = time.perf_counter()
        original = getattr(
            tool_manager,
            _AUTO_TRACE_TOOL_MANAGER_ORIGINAL_ATTR,
        )

        try:
            result = await original(
                name,
                arguments,
                context=context,
                convert_result=convert_result,
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            _record_auto_tool_step(
                name,
                arguments,
                error=exc,
                duration_ms=elapsed_ms,
                success=False,
            )
            raise

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _record_auto_tool_step(
            name,
            arguments,
            result=result,
            duration_ms=elapsed_ms,
            success=True,
        )
        return result

    setattr(
        tool_manager,
        _AUTO_TRACE_TOOL_MANAGER_ORIGINAL_ATTR,
        original_call_tool,
    )
    setattr(tool_manager, "call_tool", _wrapped_call_tool)
    setattr(tool_manager, _AUTO_TRACE_TOOL_MANAGER_INSTALLED_ATTR, True)


def _install_request_handler_tracing(mcp) -> None:
    """Install idempotent tracing around low-level request handlers."""
    if getattr(mcp, _AUTO_TRACE_REQUEST_HANDLERS_INSTALLED_ATTR, False):
        return

    lowlevel_server = getattr(mcp, "_mcp_server", None)
    request_handlers = getattr(lowlevel_server, "request_handlers", None)
    if not isinstance(request_handlers, dict):
        return

    originals: dict[type[Any], Callable[..., Any]] = {}

    for request_type, handler in list(request_handlers.items()):
        originals[request_type] = handler

        async def _wrapped_handler(
            req: Any,
            _handler: Callable[..., Any] = handler,
            _request_type: type[Any] = request_type,
        ) -> Any:
            started = time.perf_counter()
            method = _extract_request_method(req)
            if not method:
                model_fields = getattr(_request_type, "model_fields", {})
                method_field = (
                    model_fields.get("method")
                    if isinstance(model_fields, dict)
                    else None
                )
                field_default = getattr(method_field, "default", None)
                if isinstance(field_default, str):
                    method = field_default
            payload = getattr(req, "params", None)

            try:
                response = await _handler(req)
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                _record_mcp_request_step(
                    request_type=_request_type.__name__,
                    request_method=method,
                    request_payload=payload,
                    error=exc,
                    duration_ms=elapsed_ms,
                    success=False,
                )
                raise

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            _record_mcp_request_step(
                request_type=_request_type.__name__,
                request_method=method,
                request_payload=payload,
                response_payload=response,
                duration_ms=elapsed_ms,
                success=True,
            )
            return response

        request_handlers[request_type] = _wrapped_handler

    setattr(mcp, _AUTO_TRACE_REQUEST_HANDLERS_ORIGINAL_ATTR, originals)
    setattr(mcp, _AUTO_TRACE_REQUEST_HANDLERS_INSTALLED_ATTR, True)


def install_auto_tool_tracing(mcp) -> None:
    """Install idempotent tracing around tool and MCP request execution."""
    if getattr(mcp, _AUTO_TRACE_INSTALLED_ATTR, False):
        return

    _install_tool_manager_tracing(mcp)
    _install_request_handler_tracing(mcp)
    setattr(mcp, _AUTO_TRACE_INSTALLED_ATTR, True)


def _finalize_session(session: SessionTrace) -> None:
    """Compute summary stats and save the session."""
    steps = session.steps
    successful = sum(1 for s in steps if s.success)
    failed = sum(1 for s in steps if not s.success)
    total_ms = sum(s.duration_ms for s in steps)

    # Count actions and models
    actions = Counter(s.action for s in steps if s.action)
    charts = sum(1 for s in steps if s.action == "generate_chart" and s.success)
    queries = sum(
        1
        for s in steps
        if s.action in ("execute_query", "start_query") and s.success
    )

    # Extract model names from input summaries
    models: list[str] = []
    for s in steps:
        if s.action in ("describe_table", "get_model_details", "get_sample_data"):
            # Try to extract table/model name from input_summary
            for part in s.input_summary.split(","):
                part = part.strip()
                if part.startswith("table=") or part.startswith("model="):
                    models.append(part.split("=", 1)[1].strip("'\""))

    session.summary = {
        "total_duration_ms": total_ms,
        "total_steps": len(steps),
        "successful_steps": successful,
        "failed_steps": failed,
        "charts_generated": charts,
        "queries_executed": queries,
        "models_used": list(set(models)),
        "actions": dict(actions),
    }
    _maybe_prune_old_sessions_unlocked(force=True)
    _save_session(session)


def _list_session_files(last_n: int = 0) -> list[Path]:
    """List session files sorted by modification time (newest first)."""
    log_dir = _ensure_log_dir()

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    files = sorted(
        log_dir.glob("session_*.json"),
        key=_mtime,
        reverse=True,
    )
    if last_n > 0:
        files = files[:last_n]
    return files


def register_reasoning_tools(mcp):
    """Register thinking/performance tracing tools."""

    @mcp.tool()
    def set_thinking_mode(enabled: bool) -> str:
        """Enable or disable thinking/reasoning capture mode.

        When enabled, creates a new session trace that records every
        reasoning step for later performance analysis.

        Args:
            enabled: True to start a new tracing session, False to
                     finalize and stop the current session.

        Returns:
            Confirmation with session_id when enabling, or summary when disabling.
        """
        global _thinking_enabled, _current_session

        with _lock:
            if enabled:
                _thinking_enabled = True
                _ensure_log_dir()
                session_id = _generate_session_id()
                _current_session = SessionTrace(
                    session_id=session_id,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                return (
                    f"Thinking mode ENABLED. Session: {session_id}\n"
                    "Use `log_reasoning` to record decision points.\n"
                    "Use `set_thinking_mode(false)` to finalize and save."
                )
            else:
                if _thinking_always_on:
                    _thinking_enabled = True
                    return (
                        "Thinking mode is configured as always-on "
                        "(THINKING_ALWAYS_ON=True). Disable request ignored."
                    )

                _thinking_enabled = False
                if _current_session and _current_session.steps:
                    _finalize_session(_current_session)
                    sid = _current_session.session_id
                    n = len(_current_session.steps)
                    _current_session = None
                    return (
                        f"Thinking mode DISABLED. Session {sid} saved "
                        f"with {n} steps to {_session_filepath(sid)}."
                    )
                _current_session = None
                return "Thinking mode DISABLED. No steps were recorded."

    @mcp.tool()
    def log_reasoning(
        step: str,
        content: str,
        agent: str = "",
        action: str = "",
        duration_ms: int = 0,
        success: bool = True,
        input_summary: str = "",
        output_summary: str = "",
        error: str = "",
    ) -> str:
        """Record a reasoning step for audit and performance analysis.

        Call this at key decision points during data analysis workflows.
        Only active when thinking mode is enabled via set_thinking_mode.

        Args:
            step: Short label (e.g., "model_selection", "query_execution").
            content: Your reasoning explanation for this decision.
            agent: Which agent role (e.g., "data_engineer", "visualization").
            action: Tool being called (e.g., "search_models", "generate_chart").
            duration_ms: How long this step took in milliseconds.
            success: Whether this step succeeded.
            input_summary: Brief summary of inputs (e.g., "query='dex volume'").
            output_summary: Brief summary of output (e.g., "Found 3 models").
            error: Error message if success=False.

        Returns:
            Confirmation or note that thinking mode is disabled.
        """
        entry = ReasoningStep(
            step_number=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            step=step,
            content=content,
            agent=agent,
            action=action,
            duration_ms=duration_ms,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error or None,
            event_kind="reasoning",
        )

        step_number = _record_step(entry)
        if step_number is None:
            return "Thinking mode is disabled. Reasoning not recorded."

        return (
            f"Logged step #{step_number}: {step}"
            + (f" [{agent}]" if agent else "")
            + (f" — {action}" if action else "")
        )

    @mcp.tool()
    def get_reasoning_log(session_id: str = "") -> str:
        """Retrieve the reasoning trace for a session.

        Args:
            session_id: Session ID to retrieve. If empty, returns the
                        current active session trace.

        Returns:
            Formatted markdown trace of all reasoning steps.
        """
        if not session_id:
            # Return current session
            with _lock:
                if _current_session is None:
                    return (
                        "No active session. Use `set_thinking_mode(true)` "
                        "to start, or provide a session_id."
                    )
                data = asdict(_current_session)
        else:
            filepath = _session_filepath(session_id)
            if not filepath.exists():
                # Try listing available sessions
                files = _list_session_files(last_n=5)
                available = [f.stem.replace("session_", "") for f in files]
                return (
                    f"Session '{session_id}' not found.\n"
                    f"Recent sessions: {', '.join(available) or 'none'}"
                )
            data = _load_session(filepath)
            if data is None:
                return f"Error reading session file for '{session_id}'."

        # Format as markdown
        lines = [
            f"# Session Trace: {data['session_id']}",
            f"**Started:** {data['started_at']}",
        ]
        if data.get("user_prompt"):
            lines.append(f"**Prompt:** {data['user_prompt']}")
        lines.append("")

        for step in data.get("steps", []):
            status = "OK" if step.get("success", True) else "FAIL"
            header = f"### Step {step['step_number']}: {step['step']} [{status}]"
            if step.get("agent"):
                header += f" ({step['agent']})"
            lines.append(header)

            if step.get("event_kind"):
                lines.append(f"**Event:** `{step['event_kind']}`")
            if step.get("action"):
                lines.append(f"**Action:** `{step['action']}`")
            if step.get("request_type"):
                lines.append(f"**Request Type:** `{step['request_type']}`")
            if step.get("request_method"):
                lines.append(f"**Request Method:** `{step['request_method']}`")
            if step.get("input_summary"):
                lines.append(f"**Input:** {step['input_summary']}")
            if step.get("content"):
                lines.append(f"**Reasoning:** {step['content']}")
            if step.get("output_summary"):
                lines.append(f"**Output:** {step['output_summary']}")
            if step.get("duration_ms"):
                lines.append(f"**Duration:** {step['duration_ms']}ms")
            if step.get("error"):
                lines.append(f"**Error:** {step['error']}")
            lines.append("")

        # Summary if available
        summary = data.get("summary", {})
        if summary:
            lines.append("---")
            lines.append("## Summary")
            lines.append(
                f"| Metric | Value |\n|--------|-------|\n"
                f"| Total Steps | {summary.get('total_steps', 0)} |\n"
                f"| Successful | {summary.get('successful_steps', 0)} |\n"
                f"| Failed | {summary.get('failed_steps', 0)} |\n"
                f"| Total Duration | {summary.get('total_duration_ms', 0)}ms |\n"
                f"| Charts Generated | {summary.get('charts_generated', 0)} |\n"
                f"| Queries Executed | {summary.get('queries_executed', 0)} |"
            )
            if summary.get("models_used"):
                lines.append(
                    f"\n**Models used:** {', '.join(summary['models_used'])}"
                )

        return "\n".join(lines)

    @mcp.tool()
    def get_performance_stats(last_n: int = 10) -> str:
        """Aggregate performance metrics across recent sessions.

        Reads saved session traces and computes statistics for
        monitoring and benchmarking MCP performance over time.

        Args:
            last_n: Number of recent sessions to analyze. Default: 10.

        Returns:
            Markdown report with aggregated performance metrics.
        """
        files = _list_session_files(last_n=last_n)

        if not files:
            return (
                "No session traces found. Enable thinking mode with "
                "`set_thinking_mode(true)` to start recording."
            )

        sessions: list[dict] = []
        for f in files:
            data = _load_session(f)
            if data:
                sessions.append(data)

        if not sessions:
            return "Error: Could not parse any session files."

        # Aggregate metrics
        total_sessions = len(sessions)
        all_steps = []
        all_durations = []
        all_models: list[str] = []
        all_actions: Counter = Counter()
        all_errors: list[str] = []
        charts_total = 0
        queries_total = 0
        success_total = 0
        fail_total = 0

        for s in sessions:
            summary = s.get("summary", {})
            steps = s.get("steps", [])
            all_steps.extend(steps)

            if summary.get("total_duration_ms"):
                all_durations.append(summary["total_duration_ms"])
            charts_total += summary.get("charts_generated", 0)
            queries_total += summary.get("queries_executed", 0)
            success_total += summary.get("successful_steps", 0)
            fail_total += summary.get("failed_steps", 0)
            all_models.extend(summary.get("models_used", []))

            for action, count in summary.get("actions", {}).items():
                all_actions[action] += count

            for step in steps:
                if step.get("error"):
                    all_errors.append(step["error"])

        # Compute stats
        avg_duration = (
            int(sum(all_durations) / len(all_durations)) if all_durations else 0
        )
        avg_steps = (
            round(len(all_steps) / total_sessions, 1) if total_sessions else 0
        )
        total_ops = success_total + fail_total
        success_rate = (
            round(success_total / total_ops * 100, 1) if total_ops else 0
        )
        model_freq = Counter(all_models).most_common(10)
        action_freq = all_actions.most_common(10)
        error_freq = Counter(all_errors).most_common(5)

        # Build report
        lines = [
            f"# Performance Stats ({total_sessions} sessions)\n",
            "## Overview\n",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Sessions Analyzed | {total_sessions} |",
            f"| Total Steps | {len(all_steps)} |",
            f"| Avg Steps/Session | {avg_steps} |",
            f"| Avg Duration/Session | {avg_duration}ms |",
            f"| Success Rate | {success_rate}% ({success_total}/{total_ops}) |",
            f"| Total Charts | {charts_total} |",
            f"| Total Queries | {queries_total} |",
            "",
        ]

        if model_freq:
            lines.append("## Most Used Models\n")
            lines.append("| Model | Uses |")
            lines.append("|-------|------|")
            for model, count in model_freq:
                lines.append(f"| {model} | {count} |")
            lines.append("")

        if action_freq:
            lines.append("## Action Usage\n")
            lines.append("| Action | Calls |")
            lines.append("|--------|-------|")
            for action, count in action_freq:
                lines.append(f"| `{action}` | {count} |")
            lines.append("")

        if error_freq:
            lines.append("## Common Errors\n")
            lines.append("| Error | Occurrences |")
            lines.append("|-------|-------------|")
            for err, count in error_freq:
                # Truncate long errors
                short = err[:80] + "..." if len(err) > 80 else err
                lines.append(f"| {short} | {count} |")
            lines.append("")

        # Recent sessions table
        lines.append("## Recent Sessions\n")
        lines.append(
            "| Session ID | Started | Steps | Duration | Success Rate |"
        )
        lines.append(
            "|------------|---------|-------|----------|--------------|"
        )
        for s in sessions[:10]:
            sid = s.get("session_id", "?")
            started = s.get("started_at", "?")[:19]
            summary = s.get("summary", {})
            n_steps = summary.get("total_steps", len(s.get("steps", [])))
            dur = summary.get("total_duration_ms", 0)
            ok = summary.get("successful_steps", 0)
            total = ok + summary.get("failed_steps", 0)
            rate = f"{round(ok / total * 100)}%" if total else "N/A"
            lines.append(
                f"| {sid} | {started} | {n_steps} | {dur}ms | {rate} |"
            )

        return "\n".join(lines)

    # Auto-start session if config flag is enabled
    global _current_session
    if _thinking_enabled and _current_session is None:
        with _lock:
            _ensure_active_session_unlocked()
