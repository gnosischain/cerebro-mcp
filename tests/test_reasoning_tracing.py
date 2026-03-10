import asyncio
import json
import os
import time
from pathlib import Path

import pytest
import mcp.types as types
from mcp.server.fastmcp import FastMCP

import cerebro_mcp.tools.reasoning as reasoning


@pytest.fixture(autouse=True)
def reset_reasoning_state(tmp_path, monkeypatch):
    log_dir = tmp_path / ".cerebro" / "logs"
    monkeypatch.setattr(reasoning, "_log_dir", log_dir)
    monkeypatch.setattr(reasoning, "_current_session", None)
    monkeypatch.setattr(reasoning, "_thinking_enabled", True)
    monkeypatch.setattr(reasoning, "_thinking_always_on", False)
    monkeypatch.setattr(reasoning, "_retention_days", 30)
    monkeypatch.setattr(reasoning, "_last_prune_check_ts", 0.0)
    yield
    reasoning._current_session = None
    reasoning._thinking_enabled = True
    reasoning._thinking_always_on = False
    reasoning._retention_days = 30
    reasoning._last_prune_check_ts = 0.0


def _build_test_mcp() -> FastMCP:
    mcp = FastMCP("tracing-test")

    @mcp.tool()
    def echo(
        value: str,
        password: str = "",
        api_key: str = "",
        nested: dict | None = None,
    ) -> dict:
        return {
            "value": value,
            "token": "result-token",
            "nested": nested or {},
            "api_key": api_key,
            "client_secret": "result-secret",
        }

    @mcp.tool()
    def explode(secret: str = "") -> dict:
        raise ValueError(f"boom:{secret}")

    reasoning.register_reasoning_tools(mcp)
    reasoning.install_auto_tool_tracing(mcp)
    return mcp


def _call_tool(mcp: FastMCP, name: str, arguments: dict) -> object:
    return asyncio.run(mcp.call_tool(name, arguments))


def _call_request(mcp: FastMCP, request: object) -> object:
    handler = mcp._mcp_server.request_handlers[type(request)]
    return asyncio.run(handler(request))


def _result_text(payload: object) -> str:
    if isinstance(payload, tuple) and payload:
        return _result_text(payload[0])

    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, list) and first:
            return _result_text(first)
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
        text_attr = getattr(first, "text", None)
        if isinstance(text_attr, str):
            return text_attr
    return str(payload)


def _auto_steps_for_action(action: str) -> list[reasoning.ReasoningStep]:
    assert reasoning._current_session is not None
    return [
        step
        for step in reasoning._current_session.steps
        if (
            step.auto_captured
            and step.event_kind == "tool_call"
            and step.action == action
        )
    ]


def _mcp_request_steps(
    request_method: str = "",
) -> list[reasoning.ReasoningStep]:
    assert reasoning._current_session is not None
    return [
        step
        for step in reasoning._current_session.steps
        if (
            step.auto_captured
            and step.event_kind == "mcp_request"
            and (not request_method or step.request_method == request_method)
        )
    ]


def _session_files(log_dir: Path) -> list[Path]:
    return sorted(log_dir.glob("session_*.json"))


def _extract_result_payload(step: reasoning.ReasoningStep) -> dict:
    """Handle structured dict results and unstructured FastMCP text blocks."""
    if isinstance(step.tool_result, dict):
        return step.tool_result

    if (
        isinstance(step.tool_result, list)
        and step.tool_result
        and isinstance(step.tool_result[0], dict)
    ):
        text_payload = step.tool_result[0].get("text")
        if isinstance(text_payload, dict):
            return text_payload
        if isinstance(text_payload, str):
            return json.loads(text_payload)

    raise AssertionError(f"Unexpected tool_result shape: {type(step.tool_result)!r}")


def test_auto_logs_successful_tool_call_with_full_payloads():
    mcp = _build_test_mcp()
    _call_tool(
        mcp,
        "echo",
        {
            "value": "ok",
            "password": "hunter2",
            "api_key": "client-key",
            "nested": {"authorization": "Bearer abc", "safe": "yes"},
        },
    )

    steps = _auto_steps_for_action("echo")
    assert len(steps) == 1
    step = steps[0]
    assert step.success is True
    assert step.duration_ms >= 0
    assert step.tool_name == "echo"
    assert step.tool_args["value"] == "ok"
    assert step.tool_args["password"] == reasoning._REDACTED_VALUE
    assert step.tool_args["api_key"] == reasoning._REDACTED_VALUE
    result_payload = _extract_result_payload(step)
    assert result_payload["token"] == reasoning._REDACTED_VALUE
    assert result_payload["client_secret"] == reasoning._REDACTED_VALUE


def test_auto_logs_failed_tool_call_with_error_payload():
    mcp = _build_test_mcp()

    with pytest.raises(Exception):
        _call_tool(mcp, "explode", {"secret": "do-not-log"})

    steps = _auto_steps_for_action("explode")
    assert len(steps) == 1
    step = steps[0]
    assert step.success is False
    assert step.duration_ms >= 0
    assert step.tool_args["secret"] == reasoning._REDACTED_VALUE
    assert step.tool_error["type"]
    assert "boom:" in step.tool_error["message"]


def test_redacts_sensitive_keys_in_args_and_results():
    mcp = _build_test_mcp()
    _call_tool(
        mcp,
        "echo",
        {
            "value": "v",
            "password": "p",
            "api_key": "k",
            "nested": {
                "token": "abc",
                "authorization": "Bearer secret",
                "safe": "visible",
            },
        },
    )

    step = _auto_steps_for_action("echo")[0]
    assert step.tool_args["password"] == reasoning._REDACTED_VALUE
    assert step.tool_args["api_key"] == reasoning._REDACTED_VALUE
    assert step.tool_args["nested"]["token"] == reasoning._REDACTED_VALUE
    assert (
        step.tool_args["nested"]["authorization"]
        == reasoning._REDACTED_VALUE
    )
    assert step.tool_args["nested"]["safe"] == "visible"

    result_payload = _extract_result_payload(step)
    assert result_payload["token"] == reasoning._REDACTED_VALUE
    assert result_payload["api_key"] == reasoning._REDACTED_VALUE
    assert result_payload["client_secret"] == reasoning._REDACTED_VALUE
    assert result_payload["nested"]["token"] == reasoning._REDACTED_VALUE


def test_excluded_tracing_tools_do_not_generate_auto_steps():
    mcp = _build_test_mcp()
    assert reasoning._current_session is not None
    assert len(reasoning._current_session.steps) == 0

    _call_tool(mcp, "get_reasoning_log", {})
    assert len(reasoning._current_session.steps) == 0

    _call_tool(
        mcp,
        "log_reasoning",
        {"step": "manual", "content": "manual step"},
    )
    assert len(reasoning._current_session.steps) == 1
    assert reasoning._current_session.steps[0].auto_captured is False

    _call_tool(mcp, "get_performance_stats", {"last_n": 5})
    assert len(reasoning._current_session.steps) == 1


def test_disable_and_reenable_creates_new_session_and_resumes_capture(tmp_path):
    mcp = _build_test_mcp()
    _call_tool(mcp, "echo", {"value": "before"})
    assert reasoning._current_session is not None
    first_session_id = reasoning._current_session.session_id

    _call_tool(mcp, "set_thinking_mode", {"enabled": False})
    assert reasoning._thinking_enabled is False
    assert reasoning._current_session is None

    files = _session_files(tmp_path / ".cerebro" / "logs")
    assert any(first_session_id in f.name for f in files)

    _call_tool(mcp, "echo", {"value": "disabled"})
    assert reasoning._current_session is None

    _call_tool(mcp, "set_thinking_mode", {"enabled": True})
    assert reasoning._thinking_enabled is True
    assert reasoning._current_session is not None
    assert reasoning._current_session.session_id != first_session_id

    _call_tool(mcp, "echo", {"value": "after"})
    resumed_steps = _auto_steps_for_action("echo")
    assert len(resumed_steps) >= 1


def test_install_auto_tool_tracing_is_idempotent():
    mcp = FastMCP("tracing-idempotent")

    @mcp.tool()
    def ping() -> dict:
        return {"ok": True}

    reasoning.register_reasoning_tools(mcp)
    reasoning.install_auto_tool_tracing(mcp)
    reasoning.install_auto_tool_tracing(mcp)

    _call_tool(mcp, "ping", {})

    steps = _auto_steps_for_action("ping")
    assert len(steps) == 1


def test_lowlevel_call_tool_request_is_traced_and_persisted(tmp_path):
    mcp = _build_test_mcp()
    request = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="echo",
            arguments={
                "value": "via-handler",
                "password": "handler-secret",
            },
        )
    )

    _call_request(mcp, request)

    tool_steps = _auto_steps_for_action("echo")
    assert len(tool_steps) == 1

    request_steps = _mcp_request_steps("tools/call")
    assert len(request_steps) == 1
    req_step = request_steps[0]
    assert req_step.request_type == "CallToolRequest"
    assert req_step.request_payload["name"] == "echo"
    assert (
        req_step.request_payload["arguments"]["password"]
        == reasoning._REDACTED_VALUE
    )
    assert isinstance(req_step.response_payload, dict)
    response_payload = req_step.response_payload.get(
        "root",
        req_step.response_payload,
    )
    if isinstance(response_payload.get("structuredContent"), dict):
        assert response_payload["structuredContent"]["token"] == (
            reasoning._REDACTED_VALUE
        )
    else:
        assert response_payload["content"][0]["text"]["token"] == (
            reasoning._REDACTED_VALUE
        )

    files = _session_files(tmp_path / ".cerebro" / "logs")
    assert files
    data = json.loads(files[-1].read_text())
    assert any(
        step.get("event_kind") == "mcp_request" for step in data["steps"]
    )


def test_non_tool_request_is_traced():
    mcp = _build_test_mcp()
    _call_request(mcp, types.ListToolsRequest())

    request_steps = _mcp_request_steps("tools/list")
    assert len(request_steps) == 1
    step = request_steps[0]
    assert step.request_type == "ListToolsRequest"
    assert step.request_payload is None
    assert isinstance(step.response_payload, dict)
    response_payload = step.response_payload.get("root", step.response_payload)
    assert "tools" in response_payload
    assert _auto_steps_for_action("echo") == []


def test_set_thinking_mode_disable_is_noop_when_always_on():
    mcp = _build_test_mcp()
    reasoning._thinking_always_on = True
    assert reasoning._current_session is not None
    sid_before = reasoning._current_session.session_id

    result = _call_tool(mcp, "set_thinking_mode", {"enabled": False})

    assert "always-on" in _result_text(result).lower()
    assert reasoning._thinking_enabled is True
    assert reasoning._current_session is not None
    assert reasoning._current_session.session_id == sid_before


def test_retention_prunes_old_session_files(tmp_path):
    mcp = _build_test_mcp()
    log_dir = tmp_path / ".cerebro" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stale_file = log_dir / "session_stale.json"
    stale_file.write_text("{}")
    stale_ts = time.time() - (40 * 24 * 60 * 60)
    os.utime(stale_file, (stale_ts, stale_ts))

    fresh_file = log_dir / "session_fresh.json"
    fresh_file.write_text("{}")
    now_ts = time.time()
    os.utime(fresh_file, (now_ts, now_ts))

    reasoning._last_prune_check_ts = 0.0
    _call_tool(mcp, "echo", {"value": "trigger-prune"})

    assert not stale_file.exists()
    assert fresh_file.exists()


def test_session_file_contains_auto_capture_payloads(tmp_path):
    mcp = _build_test_mcp()
    _call_tool(mcp, "echo", {"value": "persist", "password": "x"})
    _call_tool(mcp, "set_thinking_mode", {"enabled": False})

    files = _session_files(tmp_path / ".cerebro" / "logs")
    assert files
    data = json.loads(files[-1].read_text())
    first_auto = next(step for step in data["steps"] if step["auto_captured"])
    assert first_auto["tool_name"] == "echo"
    assert first_auto["tool_args"]["password"] == reasoning._REDACTED_VALUE
    assert "tool_result" in first_auto
