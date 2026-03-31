import asyncio
import importlib
import json
import logging
import sys
from types import SimpleNamespace

import mcp.types as types
import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import cerebro_mcp.tools.reasoning as reasoning
from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.observability import (
    JsonFormatter,
    cerebro_clickhouse_query_duration_seconds,
    cerebro_clickhouse_query_errors_total,
    cerebro_clickhouse_rows_returned,
    cerebro_mcp_request_duration_seconds,
    cerebro_mcp_requests_total,
    cerebro_mcp_tool_calls_total,
    cerebro_mcp_tool_duration_seconds,
    observe_semantic_fallback,
    observe_semantic_bypass,
    observe_semantic_route,
    observe_semantic_query_attempt,
    observe_semantic_query_repair,
    observe_semantic_tool_call,
    semantic_bypass_total,
    semantic_fallback_total,
    semantic_query_attempts_total,
    semantic_query_repairs_total,
    semantic_route_total,
    semantic_tool_calls_total,
    setup_logging,
)


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


@pytest.fixture(autouse=True)
def restore_logging_state():
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    root_level = root.level

    uvicorn_states = []
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        uvicorn_states.append(
            (name, list(logger.handlers), logger.level, logger.propagate)
        )

    yield

    root.handlers.clear()
    root.handlers.extend(root_handlers)
    root.setLevel(root_level)

    for name, handlers, level, propagate in uvicorn_states:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.handlers.extend(handlers)
        logger.setLevel(level)
        logger.propagate = propagate


def _histogram_count(metric, **labels) -> float:
    return metric.labels(**labels)._buckets[-1].get()


def _histogram_sum(metric, **labels) -> float:
    return metric.labels(**labels)._sum.get()


def _build_test_mcp() -> FastMCP:
    mcp = FastMCP("observability-test")

    @mcp.tool()
    def echo(value: str) -> dict:
        return {"value": value}

    @mcp.tool()
    def explode() -> dict:
        raise ValueError("boom")

    reasoning.register_reasoning_tools(mcp)
    reasoning.install_auto_tool_tracing(mcp)
    return mcp


def _call_tool(mcp: FastMCP, name: str, arguments: dict) -> object:
    return asyncio.run(mcp.call_tool(name, arguments))


def _call_request(mcp: FastMCP, request: object) -> object:
    handler = mcp._mcp_server.request_handlers[type(request)]
    return asyncio.run(handler(request))


def test_json_formatter_serializes_message_as_string():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="cerebro_mcp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=SimpleNamespace(value="hello"),
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "namespace(value='hello')"


def test_setup_logging_writes_json_to_stderr_only(capsys):
    setup_logging()
    logging.getLogger("cerebro_mcp.test").info("hello observability")

    captured = capsys.readouterr()

    assert captured.out == ""
    payload = json.loads(captured.err.strip())
    assert payload["message"] == "hello observability"
    assert payload["logger"] == "cerebro_mcp.test"


def test_metrics_endpoint_is_exposed_without_auth():
    sys.modules.pop("cerebro_mcp.server", None)
    server = importlib.import_module("cerebro_mcp.server")
    client = TestClient(server.build_sse_app(auth_token="secret"))

    metrics_response = client.get("/metrics")
    sse_response = client.get("/sse")
    messages_response = client.get("/messages/test")

    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith(
        "text/plain; version=0.0.4"
    )
    assert "cerebro_http_requests_total" in metrics_response.text
    assert "cerebro_mcp_tool_calls_total" in metrics_response.text
    assert "cerebro_clickhouse_query_duration_seconds" in metrics_response.text
    assert sse_response.status_code == 401
    assert messages_response.status_code == 401


def test_tool_call_metrics_record_success_and_error():
    mcp = _build_test_mcp()

    success_before = cerebro_mcp_tool_calls_total.labels(
        tool_name="echo",
        status="success",
    )._value.get()
    success_duration_before = _histogram_count(
        cerebro_mcp_tool_duration_seconds,
        tool_name="echo",
    )
    error_before = cerebro_mcp_tool_calls_total.labels(
        tool_name="explode",
        status="error",
    )._value.get()
    error_duration_before = _histogram_count(
        cerebro_mcp_tool_duration_seconds,
        tool_name="explode",
    )

    _call_tool(mcp, "echo", {"value": "ok"})
    with pytest.raises(Exception):
        _call_tool(mcp, "explode", {})

    assert (
        cerebro_mcp_tool_calls_total.labels(
            tool_name="echo",
            status="success",
        )._value.get()
        == success_before + 1
    )
    assert (
        _histogram_count(
            cerebro_mcp_tool_duration_seconds,
            tool_name="echo",
        )
        == success_duration_before + 1
    )
    assert (
        cerebro_mcp_tool_calls_total.labels(
            tool_name="explode",
            status="error",
        )._value.get()
        == error_before + 1
    )
    assert (
        _histogram_count(
            cerebro_mcp_tool_duration_seconds,
            tool_name="explode",
        )
        == error_duration_before + 1
    )


def test_request_metrics_record_success_and_error():
    mcp = _build_test_mcp()
    success_request = types.ListToolsRequest()
    error_request = types.CallToolRequest(
        params=types.CallToolRequestParams(name="explode", arguments={})
    )

    success_before = cerebro_mcp_requests_total.labels(
        method="tools/list",
        status="success",
    )._value.get()
    success_duration_before = _histogram_count(
        cerebro_mcp_request_duration_seconds,
        method="tools/list",
    )
    error_before = cerebro_mcp_requests_total.labels(
        method="tools/call",
        status="error",
    )._value.get()
    error_duration_before = _histogram_count(
        cerebro_mcp_request_duration_seconds,
        method="tools/call",
    )

    _call_request(mcp, success_request)
    _call_request(mcp, error_request)

    assert (
        cerebro_mcp_requests_total.labels(
            method="tools/list",
            status="success",
        )._value.get()
        == success_before + 1
    )
    assert (
        _histogram_count(
            cerebro_mcp_request_duration_seconds,
            method="tools/list",
        )
        == success_duration_before + 1
    )
    assert (
        cerebro_mcp_requests_total.labels(
            method="tools/call",
            status="error",
        )._value.get()
        == error_before + 1
    )
    assert (
        _histogram_count(
            cerebro_mcp_request_duration_seconds,
            method="tools/call",
        )
        == error_duration_before + 1
    )


def test_clickhouse_query_metrics_record_success_and_error():
    manager = ClickHouseManager()
    rows = [[idx] for idx in range(3)]
    fake_result = SimpleNamespace(column_names=["id"], result_rows=rows)
    success_client = SimpleNamespace(
        query_arrow=lambda sql: (_ for _ in ()).throw(RuntimeError("no arrow")),
        query=lambda sql: fake_result,
    )
    error_client = SimpleNamespace(
        query_arrow=lambda sql: (_ for _ in ()).throw(RuntimeError("arrow failed")),
        query=lambda sql: (_ for _ in ()).throw(RuntimeError("query failed")),
    )

    success_duration_before = _histogram_count(
        cerebro_clickhouse_query_duration_seconds,
        database="dbt",
        audience="tool",
        fetch_mode="rows",
        status="success",
    )
    success_rows_before = _histogram_count(
        cerebro_clickhouse_rows_returned,
        database="dbt",
        audience="tool",
    )
    error_duration_before = _histogram_count(
        cerebro_clickhouse_query_duration_seconds,
        database="dbt",
        audience="tool",
        fetch_mode="auto",
        status="error",
    )
    error_before = cerebro_clickhouse_query_errors_total.labels(
        database="dbt",
        audience="tool",
    )._value.get()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(manager, "get_client", lambda database: success_client)
        manager.run_query("SELECT * FROM t", database="dbt")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(manager, "get_client", lambda database: error_client)
        with pytest.raises(RuntimeError, match="query failed"):
            manager.run_query("SELECT * FROM t", database="dbt")

    assert (
        _histogram_count(
            cerebro_clickhouse_query_duration_seconds,
            database="dbt",
            audience="tool",
            fetch_mode="rows",
            status="success",
        )
        == success_duration_before + 1
    )
    assert (
        _histogram_count(
            cerebro_clickhouse_rows_returned,
            database="dbt",
            audience="tool",
        )
        == success_rows_before + 1
    )
    assert (
        _histogram_count(
            cerebro_clickhouse_query_duration_seconds,
            database="dbt",
            audience="tool",
            fetch_mode="auto",
            status="error",
        )
        == error_duration_before + 1
    )
    assert (
        cerebro_clickhouse_query_errors_total.labels(
            database="dbt",
            audience="tool",
        )._value.get()
        == error_before + 1
    )
    assert _histogram_sum(
        cerebro_clickhouse_rows_returned,
        database="dbt",
        audience="tool",
    ) >= 3


def test_semantic_metrics_record_tool_attempt_repair_and_fallback():
    tool_before = semantic_tool_calls_total.labels(
        tool_name="query_metrics",
        status="success",
        agent_role="research_analyst",
        entrypoint="semantic",
    )._value.get()
    attempt_before = semantic_query_attempts_total.labels(
        planner_mode="single_model",
        attempt="1",
        result="error",
        agent_role="research_analyst",
    )._value.get()
    repair_before = semantic_query_repairs_total.labels(
        repair_action="qualify_identifiers",
        error_class="unknown_identifier",
        agent_role="research_analyst",
    )._value.get()
    fallback_before = semantic_fallback_total.labels(
        fallback_target="raw_sql",
        reason="semantic_repair_failed",
        agent_role="research_analyst",
    )._value.get()

    observe_semantic_tool_call(
        tool_name="query_metrics",
        status="success",
        agent_role="research_analyst",
        entrypoint="semantic",
    )
    observe_semantic_query_attempt(
        planner_mode="single_model",
        attempt=1,
        result="error",
        agent_role="research_analyst",
    )
    observe_semantic_query_repair(
        repair_action="qualify_identifiers",
        error_class="unknown_identifier",
        agent_role="research_analyst",
    )
    observe_semantic_fallback(
        fallback_target="raw_sql",
        reason="semantic_repair_failed",
        agent_role="research_analyst",
    )

    assert (
        semantic_tool_calls_total.labels(
            tool_name="query_metrics",
            status="success",
            agent_role="research_analyst",
            entrypoint="semantic",
        )._value.get()
        == tool_before + 1
    )
    assert (
        semantic_query_attempts_total.labels(
            planner_mode="single_model",
            attempt="1",
            result="error",
            agent_role="research_analyst",
        )._value.get()
        == attempt_before + 1
    )
    assert (
        semantic_query_repairs_total.labels(
            repair_action="qualify_identifiers",
            error_class="unknown_identifier",
            agent_role="research_analyst",
        )._value.get()
        == repair_before + 1
    )
    assert (
        semantic_fallback_total.labels(
            fallback_target="raw_sql",
            reason="semantic_repair_failed",
            agent_role="research_analyst",
        )._value.get()
        == fallback_before + 1
    )


def test_semantic_route_and_bypass_metrics_increment():
    route_before = semantic_route_total.labels(
        route="semantic_ready",
        mode="chart",
    )._value.get()
    bypass_before = semantic_bypass_total.labels(
        stage="quick_chart",
        reason="Semantic preflight required",
    )._value.get()

    observe_semantic_route(route="semantic_ready", mode="chart")
    observe_semantic_bypass(
        stage="quick_chart",
        reason="Semantic preflight required",
    )

    assert (
        semantic_route_total.labels(
            route="semantic_ready",
            mode="chart",
        )._value.get()
        == route_before + 1
    )
    assert (
        semantic_bypass_total.labels(
            stage="quick_chart",
            reason="Semantic preflight required",
        )._value.get()
        == bypass_before + 1
    )
