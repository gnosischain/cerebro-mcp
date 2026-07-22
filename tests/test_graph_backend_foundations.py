"""Focused regression tests for Graph Explorer backend safety foundations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager, QueryBudget
from cerebro_mcp.semantic.flow_queries import build_bridge_safety_gate_sql
from cerebro_mcp.tools.semantic.graph_explorer import forensics, state
from cerebro_mcp.tools.visualization import mini_apps, web_apps


def test_query_budget_rejects_invalid_values_and_clamps_to_global_guards():
    with pytest.raises(ValueError, match="positive integer"):
        QueryBudget(max_execution_time=0)
    with pytest.raises(ValueError, match="positive integer"):
        QueryBudget(max_threads=True)

    budget = QueryBudget(
        max_execution_time=10**6,
        max_memory_usage=10**15,
        max_result_rows=10**9,
        max_threads=64,
    )
    applied = ClickHouseManager._query_budget_settings(budget)
    session = ClickHouseManager._session_settings()
    assert applied is not None
    assert applied["max_execution_time"] <= session["max_execution_time"]
    if "max_memory_usage" in session:
        assert applied["max_memory_usage"] <= session["max_memory_usage"]
    assert applied["max_result_rows"] <= 10_000
    assert applied["max_threads"] == 4
    assert applied["result_overflow_mode"] == "throw"
    assert "readonly" not in applied


def test_query_budget_reaches_raw_arrow_and_native_fetch_paths(monkeypatch):
    manager = ClickHouseManager()
    calls: list[tuple[str, dict]] = []

    class Client:
        def query(self, sql, **kwargs):
            calls.append(("native", kwargs))
            return SimpleNamespace(column_names=["n"], result_rows=[[1]])

        def query_arrow(self, sql, **kwargs):
            calls.append(("arrow", kwargs))
            raise RuntimeError("client has no Arrow support")

    monkeypatch.setattr(manager, "get_client", lambda _database: Client())
    budget = QueryBudget(max_execution_time=3, max_result_rows=2, max_threads=1)

    manager.execute_raw("SELECT 1", query_budget=budget)
    manager.run_query(
        "SELECT 1",
        requested_max_rows=2,
        audience="internal",
        query_budget=budget,
    )

    assert calls[0][0] == "native"
    assert calls[0][1]["settings"]["max_execution_time"] == 3
    assert calls[1][0] == "arrow"
    assert calls[1][1]["settings"]["max_result_rows"] == 2
    assert calls[2][0] == "native"
    assert calls[2][1]["settings"]["max_threads"] == 1


def test_server_memory_error_does_not_retry_same_query_as_native(monkeypatch):
    manager = ClickHouseManager()
    native_calls = 0

    class Client:
        def query_arrow(self, sql, **kwargs):
            raise RuntimeError("Code: 241. MEMORY_LIMIT_EXCEEDED")

        def query(self, sql, **kwargs):
            nonlocal native_calls
            native_calls += 1
            raise AssertionError("must not retry a server query error")

    monkeypatch.setattr(manager, "get_client", lambda _database: Client())
    with pytest.raises(RuntimeError, match="MEMORY_LIMIT_EXCEEDED"):
        manager.run_query("SELECT 1", audience="internal")
    assert native_calls == 0


def test_source_contract_uses_strict_budget_correct_horizon_and_split_ttls(
    monkeypatch,
):
    forensics.reset_source_contract_cache_for_tests()
    now = [100.0]
    monkeypatch.setattr(forensics.time, "monotonic", lambda: now[0])

    class FakeCH:
        def __init__(self):
            self.calls: list[tuple[str, QueryBudget | None]] = []

        def execute_raw(
            self, sql, database="dbt", parameters=None, *, query_budget=None
        ):
            self.calls.append((sql, query_budget))
            if "system.columns" in sql:
                return {"rows": [["event_time", "DateTime"]]}
            return {"rows": [["2026-07-20 00:00:00"]]}

    ch = FakeCH()
    checked = forensics.validate_source_contract(
        ch,
        "dbt.events",
        ["event_time"],
        probe_horizon=True,
        horizon_column="event_time",
    )
    assert checked["ok"] is True
    assert "toString(max(`event_time`))" in ch.calls[1][0]
    assert all(call[1] is not None for call in ch.calls)

    # Successful contracts survive beyond the old 60-second TTL.
    now[0] += 100
    forensics.validate_source_contract(
        ch,
        "dbt.events",
        ["event_time"],
        probe_horizon=True,
        horizon_column="event_time",
    )
    assert len(ch.calls) == 2

    class FailedCH:
        def __init__(self):
            self.calls = 0

        def execute_raw(self, sql, database="dbt", parameters=None, **kwargs):
            self.calls += 1
            raise RuntimeError("source unavailable")

    failed = FailedCH()
    assert not forensics.validate_source_contract(
        failed, "dbt.missing", ["id"]
    )["ok"]
    now[0] += 20
    assert not forensics.validate_source_contract(
        failed, "dbt.missing", ["id"]
    )["ok"]
    assert failed.calls == 1
    now[0] += 11
    assert not forensics.validate_source_contract(
        failed, "dbt.missing", ["id"]
    )["ok"]
    assert failed.calls == 2
    forensics.reset_source_contract_cache_for_tests()


def test_bridge_gate_has_no_embedded_settings_and_remains_full_relation():
    sql, params = build_bridge_safety_gate_sql()
    assert params == {}
    assert "SETTINGS" not in sql.upper()
    assert "graph_explorer_bridge_safety_gate" in sql
    assert "LIMIT 1" in sql


def test_payload_row_hash_is_cached_per_view_dataset_revision(monkeypatch):
    mini_apps.reset_views_for_tests()
    state.reset_row_hash_cache_for_tests()
    view_id = mini_apps.create_view("graph_explorer", "Graph Explorer")
    record = mini_apps.get_view(view_id)
    assert record is not None
    record.view_state = state.empty_state("Graph Explorer")
    dataset = state.dataset_from_rows(["id"], [["a"]], "nodes")
    mini_apps.attach_dataset(view_id, "nodes", dataset)

    calls = 0
    real_hash = state.canonical_row_hash

    def counted(rows):
        nonlocal calls
        calls += 1
        return real_hash(rows)

    monkeypatch.setattr(state, "canonical_row_hash", counted)
    state.build_payload(record)
    state.build_payload(record)
    assert calls == 1

    mini_apps.attach_dataset(view_id, "nodes", dataset)
    state.build_payload(record)
    assert calls == 2
    mini_apps.reset_views_for_tests()
    state.reset_row_hash_cache_for_tests()


@pytest.mark.asyncio
async def test_web_app_shell_and_gzip_work_are_offloaded(monkeypatch):
    monkeypatch.setattr(web_apps, "WEB_APP_CONFIGS", {})
    monkeypatch.setattr(web_apps, "MINI_APP_TOOL_REGISTRY", {})
    offloaded: list[object] = []
    real_to_thread = web_apps.asyncio.to_thread

    async def tracked_to_thread(func, /, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(web_apps.asyncio, "to_thread", tracked_to_thread)

    def open_tool():
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent={"type": "INITIAL_LOAD", "view_id": "v"},
            isError=False,
        )

    def html_loader():
        return "<html><body><script></script>" + ("x" * 2_000) + "</body></html>"

    web_apps.register_web_app(
        app_id="offload_test",
        open_tool="open_offload_test",
        html_loader=html_loader,
        tools={"open_offload_test": open_tool},
    )

    request = SimpleNamespace(
        path_params={"app_id": "offload_test"},
        query_params={},
        headers={"accept-encoding": "gzip"},
    )
    response = await web_apps.serve_app(request)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert open_tool in offloaded
    assert html_loader in offloaded
    assert web_apps._inject_payload in offloaded
    assert web_apps._gzip_bytes in offloaded
