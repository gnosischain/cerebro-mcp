"""Tests for the Metric Lab mini-app launcher and chart-update delta tool."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools.visualization import metric_lab as metric_lab_module
from cerebro_mcp.tools.visualization import mini_apps
from cerebro_mcp.tools.visualization.metric_lab import (
    ALLOWED_AGGREGATIONS,
    ALLOWED_CHART_TYPES,
    register_metric_lab_tools,
)


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


class StubCH:
    """Configurable stub returning either a small or large dataset."""

    def __init__(self, total=60):
        self.total = total

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None):
        if "count()" in sql:
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=["c"],
                rows=[[self.total]], row_count=1, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        n = min(requested_max_rows, self.total)
        rows = [
            [f"2026-04-{(i % 28) + 1:02d}", i, float(i) * 1.5]
            for i in range(n)
        ]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database,
            columns=["day", "count_val", "avg_gas"],
            rows=rows, row_count=n, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )


def _build_server(total=60):
    server = FastMCP("test")
    ch = StubCH(total=total)
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)
    return server


def _get_tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


# ---------------------------------------------------------------------------
# open_metric_lab_from_sql
# ---------------------------------------------------------------------------


def test_open_metric_lab_from_sql_small_dataset_is_exact_bounded():
    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab_from_sql")
    result = fn(sql="SELECT * FROM tiny")
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "metric_lab"
    primary = sc["datasets"]["primary"]
    assert primary["stats"]["mode"] == "exact_bounded"
    assert sc["view_state"]["analytics_disabled"] is False


def test_open_metric_lab_from_sql_large_dataset_is_random_sample():
    # SAMPLE_TARGET (10_000) is the inclusive ceiling for exact_bounded;
    # need total > SAMPLE_TARGET to take the random_sample path.
    from cerebro_mcp.tools.visualization.mini_apps import SAMPLE_TARGET
    server = _build_server(total=SAMPLE_TARGET + 1)
    fn = _get_tool(server, "open_metric_lab_from_sql")
    result = fn(sql="SELECT * FROM big")
    sc = result.structuredContent
    assert sc["datasets"]["primary"]["stats"]["mode"] == "random_sample"
    assert sc["view_state"]["estimates"] is True
    assert sc["view_state"]["analytics_disabled"] is False
    assert any("approximate random sample" in w for w in sc["warnings"])


def test_default_chart_inferred_from_schema():
    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab_from_sql")
    result = fn(sql="SELECT * FROM tiny")
    chart = result.structuredContent["view_state"]["chart"]
    # Schema is (day, count_val, avg_gas) — temporal column should be x
    assert chart["xField"] == "day"
    # First numeric (non-x) becomes y
    assert chart["yField"] in {"count_val", "avg_gas"}
    assert chart["chartType"] in {"line", "bar"}


# ---------------------------------------------------------------------------
# update_metric_lab_chart
# ---------------------------------------------------------------------------


def test_update_chart_returns_patch_payload():
    server = _build_server(total=60)
    open_fn = _get_tool(server, "open_metric_lab_from_sql")
    opened = open_fn(sql="SELECT * FROM tiny")
    view_id = opened.structuredContent["view_id"]

    update_fn = _get_tool(server, "update_metric_lab_chart")
    result = update_fn(
        view_id=view_id, x_field="day", y_field="avg_gas",
        chart_type="bar", aggregation="avg",
    )
    sc = result.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["chart"]["chartType"] == "bar"
    assert sc["patch"]["chart"]["aggregation"] == "avg"


def test_update_chart_rejects_unknown_chart_type():
    server = _build_server(total=60)
    open_fn = _get_tool(server, "open_metric_lab_from_sql")
    opened = open_fn(sql="SELECT * FROM tiny")
    view_id = opened.structuredContent["view_id"]

    update_fn = _get_tool(server, "update_metric_lab_chart")
    result = update_fn(
        view_id=view_id, x_field="day", y_field="count_val",
        chart_type="parallel_coordinates",
    )
    assert result.isError is True


def test_update_chart_rejects_unknown_aggregation():
    server = _build_server(total=60)
    open_fn = _get_tool(server, "open_metric_lab_from_sql")
    opened = open_fn(sql="SELECT * FROM tiny")
    view_id = opened.structuredContent["view_id"]

    update_fn = _get_tool(server, "update_metric_lab_chart")
    result = update_fn(
        view_id=view_id, x_field="day", y_field="count_val",
        chart_type="line", aggregation="bogus_agg",
    )
    assert result.isError is True


def test_preview_only_mode_locks_chart_to_table():
    """When the dataset is preview_only, only chart_type='table' is allowed."""
    # Force preview_only by making the bucket sample raise
    class BadCH:
        def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None):
            if "count()" in sql:
                return ExecutedQuery(
                    sql=sql, executed_sql=sql, database=database, columns=["c"],
                    rows=[[50_000]], row_count=1, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
                )
            if "_sample" in sql:
                raise RuntimeError("hash sampling unavailable")
            n = min(requested_max_rows, 200)
            rows = [["2026-04-01", i, float(i)] for i in range(n)]
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database,
                columns=["day", "count_val", "avg_gas"],
                rows=rows, row_count=n, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )

    server = FastMCP("test")
    ch = BadCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)

    open_fn = _get_tool(server, "open_metric_lab_from_sql")
    opened = open_fn(sql="SELECT * FROM hostile")
    sc = opened.structuredContent
    assert sc["datasets"]["primary"]["stats"]["mode"] == "preview_only"
    assert sc["view_state"]["analytics_disabled"] is True
    view_id = sc["view_id"]

    update_fn = _get_tool(server, "update_metric_lab_chart")
    blocked = update_fn(
        view_id=view_id, x_field="day", y_field="count_val", chart_type="line",
    )
    assert blocked.isError is True

    allowed = update_fn(
        view_id=view_id, x_field="day", y_field="count_val", chart_type="table",
    )
    assert allowed.isError is None or allowed.isError is False
    assert allowed.structuredContent["type"] == "PATCH_VIEW_STATE"


# ---------------------------------------------------------------------------
# open_metric_lab_from_metrics (semantic compiler bridge)
# ---------------------------------------------------------------------------


def test_open_metric_lab_from_metrics_runs_semantic_compiler():
    """Patches compile_metric_query_sql so the test does not require live semantic registry."""

    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab_from_metrics")

    with patch.object(
        metric_lab_module,
        "compile_metric_query_sql",
        return_value=("SELECT * FROM compiled_view", "dbt"),
    ) as mocked:
        result = fn(metrics=["execution_tx_count"], dimensions=["day"])
        mocked.assert_called_once()

    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["provenance"]["source"] == "semantic"
    assert sc["provenance"]["metrics"] == ["execution_tx_count"]


def test_open_metric_lab_from_metrics_surfaces_compiler_errors():
    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab_from_metrics")

    with patch.object(
        metric_lab_module,
        "compile_metric_query_sql",
        side_effect=ValueError("metric not found"),
    ):
        result = fn(metrics=["nonexistent"])
    assert result.isError is True


# ---------------------------------------------------------------------------
# Allowed sets sanity
# ---------------------------------------------------------------------------


def test_allowed_chart_types_match_spec():
    assert ALLOWED_CHART_TYPES == {
        "table", "line", "bar", "scatter", "heatmap", "pie", "numberDisplay",
    }


def test_allowed_aggregations_match_spec():
    assert ALLOWED_AGGREGATIONS == {"count", "sum", "avg", "min", "max", "median"}


# ---------------------------------------------------------------------------
# New flow: open_metric_lab (catalog-only launch) + load_metric_lab_metric
# ---------------------------------------------------------------------------


def test_open_metric_lab_returns_catalog_when_available():
    """Zero-arg open_metric_lab must return an empty view with the metric
    catalog bundled. We patch get_metric_catalog so the test doesn't
    depend on a live semantic snapshot."""
    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab")

    fake_catalog = [
        {
            "name": "execution_tx_count",
            "label": "Execution transactions",
            "description": "Daily transaction count.",
            "module": "execution",
            "root_model": "int_execution_transactions_daily",
            "quality_tier": "approved",
            "unit": "count",
            "allowed_dimensions": ["day", "week"],
            "default_dimensions": ["day"],
        },
        {
            "name": "bridge_volume_usd",
            "label": "Bridge volume (USD)",
            "description": "Bridge flow in USD.",
            "module": "bridges",
            "root_model": "int_bridges_flows_daily",
            "quality_tier": "approved",
            "unit": "USD",
            "allowed_dimensions": ["day", "bridge", "direction"],
            "default_dimensions": ["day"],
        },
    ]

    with patch.object(metric_lab_module, "get_metric_catalog", return_value=fake_catalog):
        result = fn()

    assert result.isError is None or result.isError is False
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_state"]["mode"] == "empty"
    assert len(sc["view_state"]["metric_catalog"]) == 2
    assert sc["datasets"] == {}
    assert sc["view_state"]["analytics_disabled"] is True


def test_open_metric_lab_errors_when_catalog_is_empty():
    """If the semantic snapshot has no executable metrics, the launcher
    must return an explicit error, not an empty silent view."""
    server = _build_server(total=60)
    fn = _get_tool(server, "open_metric_lab")

    with patch.object(metric_lab_module, "get_metric_catalog", return_value=[]):
        result = fn()

    assert result.isError is True


def test_load_metric_lab_metric_swaps_dataset_in_place():
    """load_metric_lab_metric should compile via the semantic pipeline,
    load the bounded dataset, and re-emit INITIAL_LOAD for the same view."""
    server = _build_server(total=60)

    # Seed an empty view via open_metric_lab with a fake catalog
    fake_catalog = [
        {
            "name": "execution_tx_count",
            "label": "Execution transactions",
            "description": "",
            "module": "execution",
            "root_model": "int_execution_transactions_daily",
            "quality_tier": "approved",
            "unit": "count",
            "allowed_dimensions": ["day"],
            "default_dimensions": ["day"],
        },
    ]

    open_fn = _get_tool(server, "open_metric_lab")
    with patch.object(metric_lab_module, "get_metric_catalog", return_value=fake_catalog):
        opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_metric_lab_metric")
    with patch.object(
        metric_lab_module,
        "compile_metric_query_sql",
        return_value=("SELECT * FROM compiled_view", "dbt"),
    ) as compiled:
        result = load_fn(
            view_id=view_id, metric="execution_tx_count", dimensions=["day"], limit=500,
        )
        compiled.assert_called_once()

    assert result.isError is None or result.isError is False
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_id"] == view_id
    assert sc["view_state"]["mode"] == "loaded"
    assert sc["view_state"]["selected_metric"] == "execution_tx_count"
    assert sc["view_state"]["selected_dimensions"] == ["day"]
    assert "primary" in sc["datasets"]
    # catalog survives so the user can swap metrics without re-opening
    assert len(sc["view_state"]["metric_catalog"]) == 1


def test_load_metric_lab_metric_surfaces_compiler_error():
    server = _build_server(total=60)

    open_fn = _get_tool(server, "open_metric_lab")
    with patch.object(metric_lab_module, "get_metric_catalog", return_value=[
        {"name": "x", "label": "x", "description": "", "module": "m",
         "root_model": "r", "quality_tier": "approved", "unit": "",
         "allowed_dimensions": [], "default_dimensions": []},
    ]):
        opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_metric_lab_metric")
    with patch.object(
        metric_lab_module,
        "compile_metric_query_sql",
        side_effect=ValueError("Metric 'bogus' not found."),
    ):
        result = load_fn(view_id=view_id, metric="bogus")

    assert result.isError is True


def test_load_metric_lab_metric_rejects_unknown_view():
    server = _build_server(total=60)
    load_fn = _get_tool(server, "load_metric_lab_metric")
    with patch.object(
        metric_lab_module,
        "compile_metric_query_sql",
        return_value=("SELECT 1", "dbt"),
    ):
        result = load_fn(view_id="deadbeef", metric="any")
    assert result.isError is True


# ---------------------------------------------------------------------------
# Bug fix regression: open_metric_lab_from_sql must surface ClickHouse errors
# ---------------------------------------------------------------------------


def test_open_metric_lab_from_sql_returns_error_on_broken_sql():
    """Broken SQL must produce an isError=True CallToolResult with the
    ClickHouse error text, not a silent preview_only empty dataset.
    Regression for the bug seen in session_20260409_231148."""

    class BrokenCH:
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            raise RuntimeError(
                "Code: 47. DB::Exception: Unknown expression identifier `week_start`"
            )

    server = FastMCP("test")
    ch = BrokenCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)

    fn = _get_tool(server, "open_metric_lab_from_sql")
    result = fn(
        sql="SELECT * FROM dbt.fct_execution_gpay_activity_weekly ORDER BY week_start DESC LIMIT 52",
    )
    assert result.isError is True
    error_text = result.content[0].text if result.content else ""
    assert "Unknown expression identifier" in error_text
    assert "week_start" in error_text
