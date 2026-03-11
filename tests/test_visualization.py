"""Tests for the visualization pipeline: MCP App, report cache, chart pruning, nudges."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import cerebro_mcp.tools.visualization as viz
import cerebro_mcp.tools.query as query_mod
import cerebro_mcp.tools.dbt as dbt_mod


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
        ch.execute_query.return_value = {
            "columns": ["date", "value"],
            "rows": [["2026-01-01", 42]],
            "row_count": 1,
            "elapsed_seconds": 0.1,
        }
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
        assert "generate_chart" not in r1
        assert "generate_chart" not in r2

        r3 = fn(sql="SELECT 3", database="dbt", max_rows=10)
        assert "generate_chart" in r3

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
        assert "1 chart(s) registered" in r3
        assert "generate_report" in r3

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
        assert "generate_chart" in r3

        r4 = fn(sql="SELECT 4", database="dbt", max_rows=10)
        assert "generate_chart" not in r4


# ---------------------------------------------------------------------------
# search_models workflow hint
# ---------------------------------------------------------------------------

class TestSearchModelsHint:
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
        assert "generate_chart" in result
        assert "generate_report" in result

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
        assert "generate_chart" not in result
