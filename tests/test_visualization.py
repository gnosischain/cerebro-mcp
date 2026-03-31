"""Tests for the visualization pipeline: MCP App, report cache, chart pruning, nudges."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import cerebro_mcp.tools.visualization as viz
import cerebro_mcp.tools.query as query_mod
import cerebro_mcp.tools.dbt as dbt_mod
import cerebro_mcp.tools.session_state as session_state_mod
from cerebro_mcp.tools.session_state import state
from cerebro_mcp.tool_models import QueryResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_visualization_state(monkeypatch):
    """Reset module-level state between tests."""
    # Chart registry
    monkeypatch.setattr(viz, "_chart_registry", {})
    monkeypatch.setattr(viz, "_chart_counter", 0)

    # Report cache
    monkeypatch.setattr(viz, "_REPORT_CACHE", {})

    # Query nudge state
    monkeypatch.setattr(query_mod, "_query_count", 0)
    monkeypatch.setattr(query_mod, "_last_nudge_time", 0.0)
    state.reset()

    yield


# ---------------------------------------------------------------------------
# Chart registry pruning
# ---------------------------------------------------------------------------

class TestChartRegistryPruning:
    def test_fresh_charts_survive_pruning(self):
        """Charts within TTL are not pruned."""
        viz._chart_registry["chart_1"] = {
            "option": {"type": "line"},
            "title": "Test",
            "chart_type": "line",
            "data_points": 10,
            "created_at": datetime.now(),
        }
        with viz._chart_lock:
            viz._prune_chart_registry()
        assert "chart_1" in viz._chart_registry

    def test_expired_charts_are_pruned(self):
        """Charts older than _CHART_TTL are removed."""
        viz._chart_registry["chart_old"] = {
            "option": {"type": "line"},
            "title": "Old",
            "chart_type": "line",
            "data_points": 5,
            "created_at": datetime.now() - viz._CHART_TTL - timedelta(minutes=1),
        }
        viz._chart_registry["chart_new"] = {
            "option": {"type": "bar"},
            "title": "New",
            "chart_type": "bar",
            "data_points": 3,
            "created_at": datetime.now(),
        }
        with viz._chart_lock:
            viz._prune_chart_registry()
        assert "chart_old" not in viz._chart_registry
        assert "chart_new" in viz._chart_registry

    def test_charts_without_created_at_not_pruned(self):
        """Legacy entries without created_at default to now() and survive."""
        viz._chart_registry["chart_legacy"] = {
            "option": {"type": "pie"},
            "title": "Legacy",
            "chart_type": "pie",
            "data_points": 7,
        }
        with viz._chart_lock:
            viz._prune_chart_registry()
        assert "chart_legacy" in viz._chart_registry


# ---------------------------------------------------------------------------
# Report cache pruning
# ---------------------------------------------------------------------------

class TestReportCachePruning:
    def test_expired_reports_are_pruned(self):
        """Reports past TTL are removed."""
        viz._REPORT_CACHE["expired-id"] = {
            "html": "<html>old</html>",
            "expires": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
        viz._REPORT_CACHE["fresh-id"] = {
            "html": "<html>fresh</html>",
            "expires": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        with viz._REPORT_LOCK:
            viz._prune_report_cache()
        assert "expired-id" not in viz._REPORT_CACHE
        assert "fresh-id" in viz._REPORT_CACHE

    def test_cache_bounded_at_max_entries(self):
        """Excess entries are evicted when over MAX limit."""
        for i in range(viz._REPORT_MAX_ENTRIES + 5):
            viz._REPORT_CACHE[f"report-{i}"] = {
                "html": f"<html>{i}</html>",
                "expires": datetime.now(timezone.utc) + timedelta(minutes=i + 1),
            }
        with viz._REPORT_LOCK:
            viz._prune_report_cache()
        assert len(viz._REPORT_CACHE) <= viz._REPORT_MAX_ENTRIES


# ---------------------------------------------------------------------------
# generate_report returns CallToolResult with structuredContent
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def _setup_chart(self, chart_id="chart_1"):
        viz._chart_registry[chart_id] = {
            "option": {"xAxis": {"data": ["Mon"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }

    def test_returns_call_tool_result_with_structured_content(self, tmp_path, monkeypatch):
        """generate_report returns CallToolResult with structuredContent."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult, TextContent

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-viz")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        result = fn(
            title="Test Report",
            content_markdown="## Section\n\n{{chart:chart_1}}\n\nSome text.",
        )
        assert isinstance(result, CallToolResult)
        # 2 blocks: link (annotated) + metadata
        assert len(result.content) == 2
        # First content: link block, annotated for assistant
        assert isinstance(result.content[0], TextContent)
        assert "Test Report" in result.content[0].text
        assert "[Open Report](file://" in result.content[0].text
        assert result.content[0].annotations is not None
        assert result.content[0].annotations.audience == ["assistant"]
        assert result.content[0].annotations.priority == 1.0
        # Last content: metadata
        assert isinstance(result.content[-1], TextContent)
        assert "Report ID:" in result.content[-1].text

        # Structured content has charts and sections
        sc = result.structuredContent
        assert sc is not None
        assert sc["title"] == "Test Report"
        assert "chart_1" in sc["charts"]
        assert "sections_html" in sc
        assert "timestamp" in sc
        assert "queries" in sc

    def test_caches_report_with_path_and_title(self, tmp_path, monkeypatch):
        """generate_report caches report with path, title, and structured data."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-viz-cache")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Cached Report", content_markdown="{{chart:chart_1}}")
        assert len(viz._REPORT_CACHE) == 1
        cached = list(viz._REPORT_CACHE.values())[0]
        assert "<html" in cached["html"].lower()
        assert "expires" in cached
        assert "path" in cached
        assert "title" in cached
        assert cached["title"] == "Cached Report"
        assert "structured" in cached
        assert cached["structured"]["title"] == "Cached Report"

    def test_does_not_open_browser(self, tmp_path, monkeypatch):
        """generate_report does NOT call webbrowser.open (removed)."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-viz-no-browser")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        # This should succeed without any webbrowser import/call
        result = fn(title="No Browser", content_markdown="{{chart:chart_1}}")
        assert isinstance(result, CallToolResult)

    def test_filename_convention(self, tmp_path, monkeypatch):
        """Report filename contains UTC timestamp, slug, and full UUID."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-viz-fname")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Weekly Gnosis Report", content_markdown="{{chart:chart_1}}")

        files = list(tmp_path.glob("cerebro_report_*.html"))
        assert len(files) == 1
        name = files[0].name
        assert "T" in name and "Z" in name
        assert "weekly-gnosis-report" in name
        stem = files[0].stem
        uuid_part = stem.split("_")[-1]
        assert len(uuid_part) == 36

    def test_chart_specs_have_empty_title_and_grid_top(self):
        """Chart builders produce specs with empty title and grid.top='40'."""
        line = viz._build_line_chart(
            rows=[["Mon", 10], ["Tue", 20]],
            col_index={"day": 0, "val": 1},
            x_field="day", y_field="val", series_field="", title="Ignored Title",
            area=False,
        )
        assert line["title"] == {}
        assert line["grid"]["top"] == "40"
        assert line["legend"]["type"] == "scroll"

        bar = viz._build_bar_chart(
            rows=[["Mon", 5], ["Tue", 8]],
            col_index={"day": 0, "val": 1},
            x_field="day", y_field="val", series_field="", title="Ignored Title",
        )
        assert bar["title"] == {}
        assert bar["grid"]["top"] == "40"
        assert bar["legend"]["type"] == "scroll"

        pie = viz._build_pie_chart(
            rows=[["A", 30], ["B", 70]],
            col_index={"name": 0, "val": 1},
            x_field="name", y_field="val", title="Ignored Title",
        )
        assert pie["title"] == {}
        assert pie["legend"]["type"] == "scroll"

    def test_chart_html_includes_chart_title_div(self, tmp_path, monkeypatch):
        """Report HTML renders chart titles as HTML divs."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-viz-title")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Title Test", content_markdown="{{chart:chart_1}}")
        cached = list(viz._REPORT_CACHE.values())[0]
        html = cached["html"]
        assert 'class="chart-title"' in html
        assert "Test Chart" in html

    def test_missing_charts_returns_error(self):
        """Missing chart IDs return CallToolResult with isError=True."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult

        mcp = FastMCP("test-viz-error")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["generate_report"].fn
        result = fn(
            title="Error Report",
            content_markdown="{{chart:nonexistent}}",
        )
        assert isinstance(result, CallToolResult)
        assert result.isError is True
        assert "nonexistent" in result.content[0].text

    def test_standalone_html_has_embedded_data(self, tmp_path, monkeypatch):
        """Saved HTML file contains embedded JSON data for standalone viewing."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-standalone")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Standalone Test", content_markdown="{{chart:chart_1}}")

        files = list(tmp_path.glob("cerebro_report_*.html"))
        assert len(files) == 1
        html = files[0].read_text()
        assert 'id="report-data"' in html
        assert 'type="application/json"' in html

        # Extract and verify embedded data
        extracted = viz._extract_structured_from_html(html)
        assert extracted is not None
        assert extracted["title"] == "Standalone Test"
        assert "chart_1" in extracted["charts"]

    def test_tool_has_ui_metadata(self):
        """generate_report tool has meta.ui.resourceUri for MCP App."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-meta")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        tool = mcp._tool_manager._tools["generate_report"]
        assert tool.meta is not None
        assert tool.meta.get("ui", {}).get("resourceUri") == viz.REPORT_URI

    def test_answer_mode_can_render_lightweight_visualization(self, monkeypatch, tmp_path):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(route="semantic_ready", mode="answer")
        state.record_semantic_tool_call("query_metrics", execution=True)

        mcp = FastMCP("test-answer-mode-visual")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)
        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        result = fn(
            title="Mode Gate",
            content_markdown="{{chart:chart_1}}",
        )

        assert result.isError is False
        assert "Visualization:" in result.content[0].text
        assert result.structuredContent["presentation_mode"] == "visual_answer"


# ---------------------------------------------------------------------------
# Time series ordering
# ---------------------------------------------------------------------------

class TestTimeSeriesOrdering:
    def test_line_chart_sorts_by_x_field(self):
        """Line chart x-axis values are sorted chronologically."""
        rows = [
            ["2026-01-03", 30],
            ["2026-01-01", 10],
            ["2026-01-02", 20],
        ]
        result = viz._build_line_chart(
            rows=rows,
            col_index={"date": 0, "value": 1},
            x_field="date", y_field="value", series_field="", title="Test",
        )
        assert result["xAxis"]["data"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert result["series"][0]["data"] == [10, 20, 30]

    def test_line_chart_multi_series_sorts_x(self):
        """Multi-series line chart sorts x-axis."""
        rows = [
            ["2026-01-03", "A", 30],
            ["2026-01-01", "A", 10],
            ["2026-01-02", "B", 25],
            ["2026-01-01", "B", 15],
        ]
        result = viz._build_line_chart(
            rows=rows,
            col_index={"date": 0, "series": 1, "value": 2},
            x_field="date", y_field="value", series_field="series", title="Test",
        )
        assert result["xAxis"]["data"] == ["2026-01-01", "2026-01-02", "2026-01-03"]

    def test_bar_chart_preserves_order(self):
        """Bar chart preserves original row order (no sorting)."""
        rows = [
            ["Bridges", 500],
            ["DEX", 300],
            ["Tokens", 100],
        ]
        result = viz._build_bar_chart(
            rows=rows,
            col_index={"category": 0, "count": 1},
            x_field="category", y_field="count", series_field="", title="Test",
        )
        assert result["xAxis"]["data"] == ["Bridges", "DEX", "Tokens"]


# ---------------------------------------------------------------------------
# Chart input shape validation
# ---------------------------------------------------------------------------

class TestChartInputShapeValidation:
    def _make_mcp(self, executed_result):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-chart-validation")
        ch = MagicMock()
        ch.run_query.return_value = executed_result
        viz.register_visualization_tools(mcp, ch)
        return mcp

    def test_number_display_rejects_time_series_input(self):
        executed = SimpleNamespace(
            sql="SELECT month, active_users FROM analytics.monthly_kpis ORDER BY month",
            database="dbt",
            columns=["month", "active_users"],
            rows=[["2026-01-01", 148], ["2026-02-01", 9065]],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            x_field="month",
            y_field="active_users",
            title="Current Active Users",
        )

        assert "single-row query" in result
        assert "ORDER BY month DESC LIMIT 1" in result
        assert viz._chart_registry == {}

    def test_number_display_latest_row_query_succeeds(self):
        executed = SimpleNamespace(
            sql="SELECT month, active_users FROM analytics.monthly_kpis ORDER BY month DESC LIMIT 1",
            database="dbt",
            columns=["month", "active_users"],
            rows=[["2026-02-01", 9065]],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            x_field="month",
            y_field="active_users",
            title="Current Active Users",
        )

        assert "Chart ID" in result
        assert viz._chart_registry["chart_1"]["option"]["value"] == 9065
        assert viz._chart_registry["chart_1"]["input_shape"] == "scalar_kpi_input"

    def test_number_display_single_value_query_auto_detects_metric_column(self):
        executed = SimpleNamespace(
            sql="SELECT 59 AS value",
            database="dbt",
            columns=["value"],
            rows=[[59]],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            title="Validator-Owned Wallets",
        )

        assert "Chart ID" in result
        assert viz._chart_registry["chart_1"]["option"]["value"] == 59
        assert viz._chart_registry["chart_1"]["input_shape"] == "scalar_kpi_input"

    def test_number_display_explicit_change_field_preserves_value_and_delta(self):
        executed = SimpleNamespace(
            sql="SELECT 59 AS wallet_count, -4.5 AS change_pct",
            database="dbt",
            columns=["wallet_count", "change_pct"],
            rows=[[59, -4.5]],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            y_field="wallet_count",
            change_field="change_pct",
            title="Validator-Owned Wallets",
        )

        assert "Chart ID" in result
        option = viz._chart_registry["chart_1"]["option"]
        assert option["value"] == 59
        assert option["change"]["value"] == -4.5
        assert option["change"]["direction"] == "negative"

    def test_number_display_rejects_ambiguous_multi_value_query_without_change_field(self):
        executed = SimpleNamespace(
            sql="SELECT 59 AS wallet_count, -4.5 AS change_pct",
            database="dbt",
            columns=["wallet_count", "change_pct"],
            rows=[[59, -4.5]],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            title="Validator-Owned Wallets",
        )

        assert "multiple numeric columns" in result
        assert "change_field" in result
        assert viz._chart_registry == {}

    def test_markdown_to_html_renders_number_display_change_inline(self):
        viz._chart_registry["chart_1"] = {
            "option": {
                "type": "numberDisplay",
                "title": "Validator-Owned Wallets",
                "value": 59,
                "format": "formatNumber",
                "change": {
                    "value": -4.5,
                    "direction": "negative",
                },
            },
            "title": "Validator-Owned Wallets",
            "chart_type": "numberDisplay",
            "data_points": 1,
            "created_at": datetime.now(),
        }

        html = viz._markdown_to_html(
            "| KPI |\n| --- |\n| {{chart:chart_1}} |"
        )

        assert 'class="kpi-value">59<' in html
        assert 'class="kpi-change number-change negative"' in html
        assert '-4.5' in html

    def test_line_chart_rejects_ambiguous_wide_query(self):
        executed = SimpleNamespace(
            sql="SELECT month, active_users, paying_users, retained_users FROM analytics.monthly_kpis ORDER BY month",
            database="dbt",
            columns=["month", "active_users", "paying_users", "retained_users"],
            rows=[
                ["2026-01-01", 148, 120, 90],
                ["2026-02-01", 9065, 8700, 5400],
            ],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="line",
            x_field="month",
            y_field="active_users",
            title="Monthly Users by Segment",
        )

        assert "do not auto-plot extra numeric columns" in result
        assert 'y_field="active_users,paying_users,retained_users"' in result
        assert viz._chart_registry == {}

    def test_line_chart_accepts_comma_separated_y_fields(self):
        executed = SimpleNamespace(
            sql="SELECT month, active_users, paying_users FROM analytics.monthly_kpis ORDER BY month",
            database="dbt",
            columns=["month", "active_users", "paying_users"],
            rows=[
                ["2026-01-01", 148, 120],
                ["2026-02-01", 9065, 8700],
            ],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="line",
            x_field="month",
            y_field="active_users,paying_users",
            title="Monthly Users by Segment",
        )

        assert "Chart ID" in result
        assert viz._chart_registry["chart_1"]["input_shape"] == "multi_series_wide_input"
        assert [series["name"] for series in viz._chart_registry["chart_1"]["option"]["series"]] == [
            "active_users",
            "paying_users",
        ]

    def test_line_chart_accepts_long_format_series_field(self):
        executed = SimpleNamespace(
            sql="SELECT month, series, value FROM some_long_table ORDER BY month",
            database="dbt",
            columns=["month", "series", "value"],
            rows=[
                ["2026-01-01", "active_users", 148],
                ["2026-01-01", "paying_users", 120],
                ["2026-02-01", "active_users", 9065],
                ["2026-02-01", "paying_users", 8700],
            ],
            elapsed_seconds=0.01,
        )
        mcp = self._make_mcp(executed)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="line",
            x_field="month",
            y_field="value",
            series_field="series",
            title="Monthly Users by Segment",
        )

        assert "Chart ID" in result
        assert viz._chart_registry["chart_1"]["input_shape"] == "long_format_series_input"


class TestSemanticChartRouting:
    def _semantic_result(self):
        return SimpleNamespace(
            sql="SELECT day, validators_active FROM semantic_query",
            database="dbt",
            columns=["day", "validators_active"],
            rows=[["2026-03-01", 101], ["2026-03-02", 104]],
            row_count=2,
            rows_returned=2,
            truncated=False,
            fetch_mode="rows",
            elapsed_seconds=0.02,
            warnings=[],
            requested_metrics=["validators_active"],
            resolved_metrics=["validators_active"],
            requested_dimensions=["day"],
            resolved_dimensions=["day"],
            planner_mode="single_model",
            root_models=["api_consensus_validators_active_daily"],
            repair_traces=[],
            semantic_plan={},
            result_ref_id=None,
            summary_markdown="semantic summary",
        )

    def test_quick_chart_requires_preflight_when_semantic_enabled(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        mcp = FastMCP("test-semantic-gate")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["quick_chart"].fn
        result = fn(
            sql="SELECT day, cnt FROM dbt.api_consensus_validators_active_daily",
            chart_type="line",
            x_field="day",
            y_field="cnt",
        )

        assert "Semantic preflight required" in result

    def test_quick_chart_uses_non_semantic_wording_after_explicit_fallback(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(
            route="semantic_unavailable",
            mode="chart",
            fallback_reason="manifest_hash_mismatch",
        )
        state.record_search_models("active validators", 1)
        state.record_get_model_details("api_consensus_validators_active_daily")
        state.record_get_model_details("api_consensus_validators_active_weekly")
        state.record_get_model_details("api_consensus_validators_active_monthly")

        mcp = FastMCP("test-semantic-fallback-wording")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["quick_chart"].fn
        result = fn(
            sql="SELECT day, cnt FROM dbt.api_consensus_validators_active_daily",
            chart_type="line",
            x_field="day",
            y_field="cnt",
        )

        assert "Chart workflow check failed" in result
        assert "Insufficient schema verification" in result
        assert "Semantic routing check failed" not in result

    def test_quick_metric_chart_uses_semantic_result(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP
        import cerebro_mcp.tools.semantic as semantic_tools

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(route="semantic_ready", mode="chart")
        monkeypatch.setattr(
            semantic_tools,
            "execute_metric_query",
            lambda **kwargs: self._semantic_result(),
        )

        mcp = FastMCP("test-quick-metric-chart")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["quick_metric_chart"].fn
        result = fn(
            metrics=["validators_active"],
            dimensions=["day"],
            chart_type="line",
            x_field="day",
            y_field="validators_active",
            title="Active Validators",
        )

        assert "Chart ID" in result
        assert viz._chart_registry["chart_1"]["chart_type"] == "line"
        assert state.semantic_path_used == "semantic"

    def test_quick_metric_chart_requires_dimension_for_line_chart(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP
        import cerebro_mcp.tools.semantic as semantic_tools

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(route="semantic_ready", mode="chart")
        monkeypatch.setattr(
            semantic_tools,
            "execute_metric_query",
            lambda **kwargs: self._semantic_result(),
        )

        mcp = FastMCP("test-quick-metric-chart-dimension-check")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["quick_metric_chart"].fn
        result = fn(
            metrics=["validators_active"],
            chart_type="line",
            title="Active Validators",
        )

        assert "require at least one dimension" in result
        assert "numberDisplay" in result

    def test_generate_metric_charts_requires_common_depth(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP
        import cerebro_mcp.tools.semantic as semantic_tools

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(route="semantic_ready", mode="report")
        monkeypatch.setattr(
            semantic_tools,
            "execute_metric_query",
            lambda **kwargs: self._semantic_result(),
        )

        mcp = FastMCP("test-generate-metric-charts")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)
        fn = mcp._tool_manager._tools["generate_metric_charts"].fn

        blocked = fn(
            charts=[
                {
                    "metrics": ["validators_active"],
                    "dimensions": ["day"],
                    "chart_type": "line",
                    "x_field": "day",
                    "y_field": "validators_active",
                    "title": "Trend",
                }
            ]
        )
        assert "Discovery skipped" in blocked

        state.record_search_models("active validators", 1, source="semantic")
        state.record_get_model_details(
            "api_consensus_validators_active_daily",
            source="semantic",
        )
        state.record_get_model_details(
            "api_consensus_validators_active_weekly",
            source="semantic",
        )
        state.record_get_model_details(
            "api_consensus_validators_active_monthly",
            source="semantic",
        )
        state.record_describe_table(
            "api_consensus_validators_active_daily",
            source="semantic",
        )

        result = fn(
            charts=[
                {
                    "metrics": ["validators_active"],
                    "dimensions": ["day"],
                    "chart_type": "line",
                    "x_field": "day",
                    "y_field": "validators_active",
                    "title": "Trend",
                }
            ]
        )

        assert "Generated 1/1 semantic charts" in result
        assert [series["name"] for series in viz._chart_registry["chart_1"]["option"]["series"]] == [
            "validators_active",
        ]

    def test_generate_metric_charts_normalizes_date_alias(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP
        import cerebro_mcp.tools.semantic as semantic_tools

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(route="semantic_ready", mode="report")
        state.record_search_models("active validators", 1, source="semantic")
        state.record_get_model_details(
            "api_consensus_validators_active_daily",
            source="semantic",
        )
        state.record_get_model_details(
            "api_consensus_validators_active_weekly",
            source="semantic",
        )
        state.record_get_model_details(
            "api_consensus_validators_active_monthly",
            source="semantic",
        )
        state.record_describe_table(
            "api_consensus_validators_active_daily",
            source="semantic",
        )
        monkeypatch.setattr(
            semantic_tools,
            "execute_metric_query",
            lambda **kwargs: self._semantic_result(),
        )

        mcp = FastMCP("test-generate-metric-charts-date-alias")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)
        fn = mcp._tool_manager._tools["generate_metric_charts"].fn

        result = fn(
            charts=[
                {
                    "metrics": ["validators_active"],
                    "dimensions": ["date"],
                    "chart_type": "line",
                    "x_field": "date",
                    "title": "Trend",
                }
            ]
        )

        assert "Generated 1/1 semantic charts" in result
        assert "chart_1" in viz._chart_registry
        assert viz._chart_registry["chart_1"]["chart_type"] == "line"

    def test_generate_chart_uses_global_state_without_name_error(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setattr(session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", False)

        executed = SimpleNamespace(
            sql="SELECT day, cnt FROM dbt.api_consensus_validators_active_daily ORDER BY day",
            database="dbt",
            columns=["day", "cnt"],
            rows=[["2026-03-01", 101], ["2026-03-02", 104]],
            elapsed_seconds=0.01,
        )
        ch = MagicMock()
        ch.run_query.return_value = executed

        mcp = FastMCP("test-generate-chart-state")
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["generate_chart"].fn
        result = fn(
            sql=executed.sql,
            chart_type="line",
            x_field="day",
            y_field="cnt",
            title="Validators",
        )

        assert "Chart ID" in result
        assert "name 'state' is not defined" not in result

    def test_generate_charts_accepts_successful_raw_query_as_schema_verification(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        state.record_semantic_preflight(
            route="semantic_coverage_gap",
            mode="report",
            fallback_reason="semantic_coverage_gap",
        )
        state.record_search_models("gnosis pay wallet", 1)
        state.record_get_model_details("int_execution_gpay_wallet_owners")
        state.record_get_model_details("int_consensus_validators_labels")
        state.record_get_model_details("fct_execution_gpay_user_lifetime_metrics")
        state.record_execute_query(
            "SELECT * FROM dbt.fct_execution_gpay_user_lifetime_metrics LIMIT 5"
        )

        executed = SimpleNamespace(
            sql="SELECT wallet_address, total_payment_volume_usd FROM dbt.fct_execution_gpay_user_lifetime_metrics ORDER BY total_payment_volume_usd DESC LIMIT 5",
            database="dbt",
            columns=["wallet_address", "total_payment_volume_usd"],
            rows=[["0x1", 10.0], ["0x2", 9.0]],
            elapsed_seconds=0.01,
        )
        ch = MagicMock()
        ch.run_query.return_value = executed

        mcp = FastMCP("test-generate-charts-raw-query-verifies-schema")
        viz.register_visualization_tools(mcp, ch)
        fn = mcp._tool_manager._tools["generate_charts"].fn

        result = fn(
            charts=[
                {
                    "sql": executed.sql,
                    "chart_type": "bar",
                    "x_field": "wallet_address",
                    "y_field": "total_payment_volume_usd",
                    "title": "Top Wallets",
                }
            ]
        )

        assert "Generated 1/1 charts" in result


# ---------------------------------------------------------------------------
# open_report
# ---------------------------------------------------------------------------

class TestOpenReport:
    def _generate_report(self, mcp, tmp_path):
        """Helper: generate a report and return its ID."""
        viz._chart_registry["chart_1"] = {
            "option": {"xAxis": {"data": ["Mon"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }
        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Test Report", content_markdown="{{chart:chart_1}}")
        report_id = list(viz._REPORT_CACHE.keys())[0]
        return report_id

    def test_open_by_short_id(self, tmp_path, monkeypatch):
        """open_report with 8-char prefix returns CallToolResult."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult, TextContent

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-open")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        report_id = self._generate_report(mcp, tmp_path)
        short_id = report_id[:8]

        fn = mcp._tool_manager._tools["open_report"].fn
        result = fn(report_ref=short_id)
        assert isinstance(result, CallToolResult)
        assert len(result.content) >= 1
        assert isinstance(result.content[0], TextContent)
        assert result.structuredContent is not None

    def test_open_disk_fallback(self, tmp_path, monkeypatch):
        """open_report loads from disk when cache is empty."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult, TextContent

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-open-disk")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        report_id = self._generate_report(mcp, tmp_path)
        short_id = report_id[:8]

        # Evict from cache
        viz._REPORT_CACHE.clear()

        fn = mcp._tool_manager._tools["open_report"].fn
        result = fn(report_ref=short_id)
        assert isinstance(result, CallToolResult)
        assert isinstance(result.content[0], TextContent)
        assert "file://" in result.content[0].text
        # Structured content should be extracted from embedded HTML data
        assert result.structuredContent is not None

    def test_open_missing_report(self, tmp_path, monkeypatch):
        """open_report with nonexistent ref returns CallToolResult."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-open-missing")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["open_report"].fn
        result = fn(report_ref="nonexist")
        assert isinstance(result, CallToolResult)
        assert "not found" in result.content[0].text

    def test_open_report_has_ui_metadata(self):
        """open_report tool has meta.ui.resourceUri for MCP App."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-meta")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        tool = mcp._tool_manager._tools["open_report"]
        assert tool.meta is not None
        assert tool.meta.get("ui", {}).get("resourceUri") == viz.REPORT_URI


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------

class TestListReports:
    def test_list_shows_generated_report(self, tmp_path, monkeypatch):
        """list_reports shows a previously generated report."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-list")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        viz._chart_registry["chart_1"] = {
            "option": {"xAxis": {"data": ["Mon"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }
        gen_fn = mcp._tool_manager._tools["generate_report"].fn
        gen_fn(title="Listed Report", content_markdown="{{chart:chart_1}}")

        list_fn = mcp._tool_manager._tools["list_reports"].fn
        result = list_fn()
        assert "cerebro_report_" in result
        assert "file://" in result
        assert "KB" in result
        assert "open_report" in result

    def test_list_empty_dir(self, tmp_path, monkeypatch):
        """list_reports with empty dir returns helpful message."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        mcp = FastMCP("test-list-empty")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["list_reports"].fn
        result = fn()
        assert "No saved reports" in result

    def test_list_no_dir(self, monkeypatch):
        """list_reports when dir doesn't exist returns helpful message."""
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", "/nonexistent/path/reports")

        mcp = FastMCP("test-list-nodir")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        fn = mcp._tool_manager._tools["list_reports"].fn
        result = fn()
        assert "No saved reports" in result or "No report directory" in result


# ---------------------------------------------------------------------------
# MCP App resource
# ---------------------------------------------------------------------------

class TestMCPAppResource:
    def test_resource_serves_static_html(self):
        """The MCP App resource serves the Vite-built React app."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-resource")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        # Find the resource handler
        resources = mcp._resource_manager._resources
        found = False
        for key, res in resources.items():
            if "cerebro/report" in key:
                result = res.fn()
                assert "<!DOCTYPE html>" in result
                assert 'id="root"' in result
                found = True
                break
        assert found, "MCP App resource not registered"

    def test_resource_has_mcp_app_mime_type(self):
        """The MCP App resource has the correct MIME type."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-mime")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        resources = mcp._resource_manager._resources
        for key, res in resources.items():
            if "cerebro/report" in key:
                assert res.mime_type == "text/html;profile=mcp-app"
                break


# ---------------------------------------------------------------------------
# execute_query nudge logic
# ---------------------------------------------------------------------------

class TestExecuteQueryNudge:
    def _make_mock_ch(self):
        ch = MagicMock()
        ch.run_query.return_value = object()
        ch.build_query_result.return_value = QueryResult(
            sql="SELECT 1",
            database="dbt",
            columns=["date", "value"],
            rows=[["2026-01-01", 42]],
            row_count=1,
            rows_returned=1,
            truncated=False,
            fetch_mode="rows",
            elapsed_seconds=0.1,
            warnings=[],
            summary_markdown="",
        )
        return ch

    def test_nudge_fires_without_charts_after_3_queries(self):
        """Nudge appears after 3+ queries even when chart registry is empty."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-nudge")
        ch = self._make_mock_ch()
        query_mod.register_query_tools(mcp, ch)

        fn = mcp._tool_manager._tools["execute_query"].fn

        r1 = fn(sql="SELECT 1", database="dbt", max_rows=10)
        r2 = fn(sql="SELECT 2", database="dbt", max_rows=10)
        assert "generate_charts([...])" not in r1.summary_markdown
        assert "generate_charts([...])" not in r2.summary_markdown

        r3 = fn(sql="SELECT 3", database="dbt", max_rows=10)
        assert "generate_charts([...])" in r3.summary_markdown

    def test_nudge_with_charts_shows_reminder(self):
        """Nudge shows chart count when charts exist in registry."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-nudge-charts")
        ch = self._make_mock_ch()
        query_mod.register_query_tools(mcp, ch)

        viz._chart_registry["chart_1"] = {
            "option": {},
            "title": "Test",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }

        fn = mcp._tool_manager._tools["execute_query"].fn
        query_mod._query_count = 2

        r3 = fn(sql="SELECT 3", database="dbt", max_rows=10)
        assert "1 chart(s) registered" in r3.summary_markdown
        assert "generate_report" in r3.summary_markdown

    def test_nudge_cooldown_prevents_spam(self):
        """Nudge does not fire again within cooldown window."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-nudge-cooldown")
        ch = self._make_mock_ch()
        query_mod.register_query_tools(mcp, ch)

        fn = mcp._tool_manager._tools["execute_query"].fn

        fn(sql="SELECT 1", database="dbt", max_rows=10)
        fn(sql="SELECT 2", database="dbt", max_rows=10)
        r3 = fn(sql="SELECT 3", database="dbt", max_rows=10)
        assert "generate_charts([...])" in r3.summary_markdown

        r4 = fn(sql="SELECT 4", database="dbt", max_rows=10)
        assert "generate_charts([...])" not in r4.summary_markdown


# ---------------------------------------------------------------------------
# search_models workflow hint
# ---------------------------------------------------------------------------

class TestSearchModelsHint:
    def test_search_models_requires_preflight_before_raw_discovery(self, monkeypatch):
        """search_models is blocked until semantic preflight runs."""
        from mcp.server.fastmcp import FastMCP
        from cerebro_mcp.manifest_loader import ManifestLoader, manifest

        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(dbt_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(
            ManifestLoader, "is_loaded", property(lambda self: True)
        )
        monkeypatch.setattr(
            manifest, "search_models",
            lambda **kwargs: [
                {"name": "api_consensus_validators_active_daily", "description": "Test model",
                 "materialized": "view", "tags": ["consensus"], "path": "test.sql"}
            ],
        )
        monkeypatch.setattr(
            dbt_mod,
            "_semantic_discovery_gate",
            lambda query: "Semantic preflight required: call `preflight_analytics_request(query, mode=\"answer\")` before raw model discovery when semantic is enabled. Approved metrics: `validators_active`.",
        )

        mcp = FastMCP("test-search-preflight-gate")
        dbt_mod.register_dbt_tools(mcp)
        fn = mcp._tool_manager._tools["search_models"].fn

        result = fn(query="active validators over time")
        assert "Semantic preflight required" in result
        assert "validators_active" in result

    def test_report_keyword_appends_workflow_hint(self, monkeypatch):
        """search_models adds workflow hint for report-related queries."""
        from mcp.server.fastmcp import FastMCP
        from cerebro_mcp.manifest_loader import ManifestLoader, manifest

        mcp = FastMCP("test-hint")

        monkeypatch.setattr(
            ManifestLoader, "is_loaded", property(lambda self: True)
        )
        monkeypatch.setattr(
            manifest, "search_models",
            lambda **kwargs: [
                {"name": "api_test", "description": "Test model",
                 "materialized": "view", "tags": ["test"], "path": "test.sql"}
            ],
        )

        dbt_mod.register_dbt_tools(mcp)
        fn = mcp._tool_manager._tools["search_models"].fn

        result = fn(query="weekly report trends")
        assert "generate_charts" in result
        assert "generate_report" in result
        assert "single-row SQL" in result

    def test_non_report_query_no_hint(self, monkeypatch):
        """search_models does NOT add workflow hint for non-report queries."""
        from mcp.server.fastmcp import FastMCP
        from cerebro_mcp.manifest_loader import ManifestLoader, manifest

        mcp = FastMCP("test-no-hint")

        monkeypatch.setattr(
            ManifestLoader, "is_loaded", property(lambda self: True)
        )
        monkeypatch.setattr(
            manifest, "search_models",
            lambda **kwargs: [
                {"name": "api_test", "description": "Test model",
                 "materialized": "view", "tags": ["test"], "path": "test.sql"}
            ],
        )

        dbt_mod.register_dbt_tools(mcp)
        fn = mcp._tool_manager._tools["search_models"].fn

        result = fn(query="validator performance")
        assert "generate_charts" not in result


class TestReportPrompts:
    def test_report_prompt_uses_batch_chart_workflow(self):
        from mcp.server.fastmcp import FastMCP
        from cerebro_mcp.prompts.templates import register_prompts

        mcp = FastMCP("test-report-prompts")
        register_prompts(mcp)

        fn = mcp._prompt_manager._prompts["report"].fn
        text = fn(period="March 2026")

        assert "generate_charts([...])" in text
        assert "single-row SQL" in text
        assert "ORDER BY month DESC LIMIT 1" in text
        assert "generate_chart` for each metric" not in text

    def test_frontend_agent_prompt_explains_chart_query_shapes(self):
        from mcp.server.fastmcp import FastMCP
        from cerebro_mcp.prompts.templates import register_prompts

        mcp = FastMCP("test-frontend-prompt")
        register_prompts(mcp)

        fn = mcp._prompt_manager._prompts["frontend_agent"].fn
        text = fn(task="Build a monthly activity report")

        assert "generate_charts([...])" in text
        assert "numberDisplay` charts require single-row SQL" in text
        assert "comma-separated `y_field` values" in text
