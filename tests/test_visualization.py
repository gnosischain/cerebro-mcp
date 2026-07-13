"""Tests for the visualization pipeline: MCP App, report cache, chart pruning, nudges."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import CallToolResult

import importlib.resources

import cerebro_mcp.tools.visualization.charts as viz
import cerebro_mcp.tools.analytics.query as query_mod
import cerebro_mcp.tools.analytics.dbt as dbt_mod
import cerebro_mcp.tools.governance.session_state as session_state_mod
from cerebro_mcp.tools.governance.session_state import state
from cerebro_mcp.models.tool import QueryResult

# The Vite build artifact (make build-ui-report -> static/report.html) is
# gitignored; a few tests assert on markup that only exists inside that built
# bundle. Skip them when it is absent (a source checkout / CI without a UI
# build) — report generation itself degrades to a minimal shell either way.
_REPORT_BUNDLE_PRESENT = (
    importlib.resources.files("cerebro_mcp").joinpath("static/report.html").is_file()
)
_needs_report_bundle = pytest.mark.skipif(
    not _REPORT_BUNDLE_PRESENT,
    reason="asserts on built UI bundle markup; run `make build-ui-report`",
)


def _tool_text(result):
    """Flatten a chart tool's CallToolResult into its text (tools that used
    to return str now return CallToolResult)."""
    if isinstance(result, CallToolResult):
        return "\n".join(
            c.text for c in result.content if getattr(c, "text", None)
        )
    return result


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

    # Last rendered visualization pointer
    monkeypatch.setitem(viz._LAST_VISUAL, "report_id", None)
    monkeypatch.setitem(viz._LAST_VISUAL, "created_at", None)

    # Query nudge state
    monkeypatch.setattr(query_mod, "_query_count", 0)
    monkeypatch.setattr(query_mod, "_last_nudge_time", 0.0)
    state.reset()

    # The deployed env may set SEMANTIC_ENABLED=True; force to False as the
    # test default. Individual tests that exercise the semantic path opt back
    # in via `monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)`.
    monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", False)
    monkeypatch.setattr(dbt_mod.settings, "SEMANTIC_ENABLED", False)

    # Disable the chart-precondition / report-precondition gates by default
    # so tests can exercise the underlying machinery without having to set
    # up full discovery + EDA flows. Tests that exercise the gates themselves
    # explicitly re-enable via monkeypatch.
    monkeypatch.setattr(
        session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", False
    )

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

    def test_does_not_open_browser_when_disabled(self, tmp_path, monkeypatch):
        """With REPORT_AUTO_OPEN off (the test default via conftest),
        generate_report never calls webbrowser.open."""
        import webbrowser
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

        mcp = FastMCP("test-viz-no-browser")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        result = fn(title="No Browser", content_markdown="{{chart:chart_1}}")
        assert isinstance(result, CallToolResult)
        assert opened == []

    def test_auto_opens_browser_on_local_stdio(self, tmp_path, monkeypatch):
        """With REPORT_AUTO_OPEN on and a non-SSE transport, the rendered
        artifact pops in the default browser — the guaranteed-visible path
        for clients that can't render UI resources or click file:// links."""
        import webbrowser
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        monkeypatch.delenv("CEREBRO_TRANSPORT", raising=False)
        monkeypatch.setattr(session_state_mod.settings, "REPORT_AUTO_OPEN", True)
        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

        mcp = FastMCP("test-viz-auto-open")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="Auto Open", content_markdown="{{chart:chart_1}}")
        assert len(opened) == 1
        assert opened[0].startswith("file://")
        assert opened[0].endswith(".html")

    def test_no_auto_open_on_sse(self, tmp_path, monkeypatch):
        """On SSE the browser would open on the SERVER host — never do that."""
        import webbrowser
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        monkeypatch.setenv("CEREBRO_TRANSPORT", "sse")
        monkeypatch.setattr(session_state_mod.settings, "REPORT_AUTO_OPEN", True)
        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

        mcp = FastMCP("test-viz-no-open-sse")
        ch = MagicMock()
        viz.register_visualization_tools(mcp, ch)

        self._setup_chart("chart_1")

        fn = mcp._tool_manager._tools["generate_report"].fn
        fn(title="SSE No Open", content_markdown="{{chart:chart_1}}")
        assert opened == []

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

    @_needs_report_bundle
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

    def test_tool_has_ui_metadata(self, monkeypatch):
        """generate_report tool's meta.ui.resourceUri is gated by
        MCP_UI_INLINE_ENABLED: absent by default, present when enabled."""
        from mcp.server.fastmcp import FastMCP

        ch = MagicMock()

        monkeypatch.setattr(
            "cerebro_mcp.config.settings.MCP_UI_INLINE_ENABLED", False
        )
        mcp_off = FastMCP("test-meta-off")
        viz.register_visualization_tools(mcp_off, ch)
        assert mcp_off._tool_manager._tools["generate_report"].meta is None

        monkeypatch.setattr(
            "cerebro_mcp.config.settings.MCP_UI_INLINE_ENABLED", True
        )
        mcp_on = FastMCP("test-meta-on")
        viz.register_visualization_tools(mcp_on, ch)
        tool = mcp_on._tool_manager._tools["generate_report"]
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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

        result = fn(
            sql=executed.sql,
            database="dbt",
            chart_type="numberDisplay",
            title="Validator-Owned Wallets",
        )

        # Error message wording was updated to "requires an explicit main KPI
        # column when the query returns multiple fields"; the original test
        # was checking for the older "multiple numeric columns" phrasing.
        assert "explicit main KPI column" in result
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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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
    @pytest.fixture(autouse=True)
    def _enable_chart_gate(self, monkeypatch):
        """The class-wide outer autouse disables ENFORCE_CHART_PRECONDITIONS
        for test convenience; this class actually exercises the gate, so
        re-enable it for every test in this class."""
        monkeypatch.setattr(
            session_state_mod.settings,
            "ENFORCE_CHART_PRECONDITIONS",
            True,
        )
        yield

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

        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731
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

        _raw_fn = mcp._tool_manager._tools["quick_chart"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731
        result = fn(
            sql="SELECT day, cnt FROM dbt.api_consensus_validators_active_daily",
            chart_type="line",
            x_field="day",
            y_field="cnt",
        )

        assert "Chart workflow check failed" in result
        assert "Schema: verify" in result
        assert "Semantic routing check failed" not in result

    def test_quick_metric_chart_uses_semantic_result(self, monkeypatch):
        from mcp.server.fastmcp import FastMCP
        import cerebro_mcp.tools.semantic.semantic as semantic_tools

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
        import cerebro_mcp.tools.semantic.semantic as semantic_tools

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
        import cerebro_mcp.tools.semantic.semantic as semantic_tools

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
        assert "Discovery:" in blocked

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
        import cerebro_mcp.tools.semantic.semantic as semantic_tools

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
        _raw_fn = mcp._tool_manager._tools["generate_charts"].fn
        fn = lambda **kw: _tool_text(_raw_fn(**kw))  # noqa: E731

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

    def test_open_report_has_ui_metadata(self, monkeypatch):
        """open_report tool's meta.ui.resourceUri is gated by
        MCP_UI_INLINE_ENABLED: absent by default, present when enabled."""
        from mcp.server.fastmcp import FastMCP

        ch = MagicMock()

        monkeypatch.setattr(
            "cerebro_mcp.config.settings.MCP_UI_INLINE_ENABLED", False
        )
        mcp_off = FastMCP("test-meta-off")
        viz.register_visualization_tools(mcp_off, ch)
        assert mcp_off._tool_manager._tools["open_report"].meta is None

        monkeypatch.setattr(
            "cerebro_mcp.config.settings.MCP_UI_INLINE_ENABLED", True
        )
        mcp_on = FastMCP("test-meta-on")
        viz.register_visualization_tools(mcp_on, ch)
        tool = mcp_on._tool_manager._tools["open_report"]
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
        from cerebro_mcp.loaders.manifest import ManifestLoader, manifest

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
        from cerebro_mcp.loaders.manifest import ManifestLoader, manifest

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
        from cerebro_mcp.loaders.manifest import ManifestLoader, manifest

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


class TestHybridRouting:
    """Tests for hybrid_ready routing and mixed chart registries."""

    def test_raw_chart_allowed_in_hybrid_mode(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True)
        state.record_semantic_preflight(route="hybrid_ready", mode="report")
        assert state.analysis_path == "hybrid"

        # Provide minimum discovery depth
        state.record_search_models("bridge volume", 1)
        state.record_get_model_details("model_a")
        state.record_get_model_details("model_b")
        state.record_get_model_details("model_c")
        state.record_describe_table("table_a")

        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert passed, f"Raw chart should be allowed in hybrid mode: {reason}"

    def test_semantic_chart_allowed_in_hybrid_mode(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True)
        state.record_semantic_preflight(route="hybrid_ready", mode="report")

        # Semantic chart gate only needs preflight + route
        passed, reason = state.check_chart_preconditions(raw_path=False)
        # Should pass the semantic route check (may still fail common depth)
        # The important thing is it doesn't fail with "Semantic charting requires semantic_ready"
        if not passed:
            assert "semantic_ready" not in reason.lower() or "hybrid_ready" in reason.lower()

    def test_chart_registry_carries_source_field(self):
        viz._chart_registry["chart_1"] = {
            "option": {"xAxis": {"data": ["Mon"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": "SELECT 1",
            "database": "dbt",
            "series_field": "",
            "change_field": "",
            "input_shape": {},
            "source": "raw",
        }
        assert viz._chart_registry["chart_1"]["source"] == "raw"

        viz._chart_registry["chart_2"] = {
            "option": {"xAxis": {"data": ["Mon"]}, "series": [{"data": [2]}]},
            "title": "Semantic Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": "SELECT 1",
            "database": "dbt",
            "series_field": "",
            "change_field": "",
            "input_shape": {},
            "source": "semantic",
        }
        assert viz._chart_registry["chart_2"]["source"] == "semantic"

    def test_analysis_path_reset_on_state_reset(self):
        state.record_semantic_preflight(route="hybrid_ready", mode="report")
        assert state.analysis_path == "hybrid"
        state.reset()
        assert state.analysis_path == "undecided"

class TestResearchReport:
    """Research-report style layout: markdown directives + artifact builder."""

    def _setup_chart(self, chart_id="chart_1"):
        viz._chart_registry[chart_id] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }

    def test_markdown_heading_gets_anchor_in_research_mode(self):
        html = viz._markdown_to_html("## Methodology\n", research_mode=True)
        assert 'id="methodology"' in html
        assert "rr-section-heading" in html

    def test_markdown_heading_no_anchor_in_default_mode(self):
        html = viz._markdown_to_html("## Methodology\n", research_mode=False)
        assert "<h2>Methodology</h2>" in html
        assert "rr-section-heading" not in html

    def test_pullquote_directive(self):
        md = "{{pullquote}}\nThe default is uncertainty.\n{{/pullquote}}\n"
        html = viz._markdown_to_html(md, research_mode=True)
        assert 'class="rr-pullquote"' in html
        assert "uncertainty" in html

    def test_callout_directive_captures_kind(self):
        md = "{{callout kind=key_takeaway}}\nBig result.\n{{/callout}}\n"
        html = viz._markdown_to_html(md, research_mode=True)
        assert "rr-callout--key_takeaway" in html
        assert "Big result" in html

    def test_sidebar_directive_with_title(self):
        md = '{{sidebar title="Methods"}}\nDetails here.\n{{/sidebar}}\n'
        html = viz._markdown_to_html(md, research_mode=True)
        assert 'class="rr-sidebar"' in html
        assert "Methods" in html
        assert "Details here" in html

    def test_figure_directive_renders_chart_with_caption(self):
        self._setup_chart("chart_7")
        md = '{{figure:chart_7 caption="TVL trend" source="Dune"}}\n'
        html = viz._markdown_to_html(md, research_mode=True)
        assert 'class="rr-figure"' in html
        assert 'id="chart-chart_7"' in html
        assert "TVL trend" in html
        assert "Source:" in html
        assert "Dune" in html

    def test_figure_directive_missing_chart_emits_placeholder(self):
        md = "{{figure:missing_chart caption=\"x\"}}\n"
        html = viz._markdown_to_html(md, research_mode=True)
        assert "rr-figure--missing" in html
        assert "missing_chart" in html

    def test_footnotes_ref_and_definition(self):
        md = (
            "Some claim[^1] and another[^two].\n\n"
            "[^1]: First note.\n"
            "[^two]: Second note.\n"
        )
        html = viz._markdown_to_html(md, research_mode=True)
        assert 'id="fnref-1"' in html
        assert 'id="fnref-two"' in html
        assert 'id="fn-1"' in html
        assert 'id="fn-two"' in html
        assert "First note" in html
        assert "Second note" in html
        assert "rr-footnotes" in html

    def test_footnotes_ignored_when_not_research_mode(self):
        md = "Claim[^1].\n\n[^1]: note.\n"
        html = viz._markdown_to_html(md, research_mode=False)
        assert "rr-footnotes" not in html
        assert "[^1]" in html  # passes through unchanged

    def test_chart_placeholder_still_works_in_research_mode(self):
        self._setup_chart("chart_9")
        html = viz._markdown_to_html(
            "{{chart:chart_9}}\n", research_mode=True
        )
        assert 'id="chart-chart_9"' in html

    def test_report_filename_research_prefix(self):
        name = viz._report_filename("abc-123", "Q2 2026 TVL Review", kind="research")
        assert name.startswith("cerebro_research_")
        assert name.endswith("_abc-123.html")

    def test_report_filename_default_prefix_unchanged(self):
        name = viz._report_filename("abc-123", "My Report")
        assert name.startswith("cerebro_report_")

    def test_find_report_on_disk_matches_research(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        fn = tmp_path / "cerebro_research_20260101T000000Z_q2-review_abc12345-def6-7890-1234-567890abcdef.html"
        fn.write_text("<html></html>")
        found = viz._find_report_on_disk("abc12345")
        assert found == fn
        assert viz._report_kind_from_path(fn) == "research"

    def test_create_research_report_artifact_happy_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._setup_chart("chart_1")
        out = viz.create_research_report_artifact(
            title="Stablecoin Yield Landscape",
            deck="A tour of on-chain yield across the five largest stablecoin pools.",
            content_markdown=(
                "## Overview\n\n"
                "Intro paragraph[^1].\n\n"
                "{{callout kind=key_takeaway}}\nThings happened.\n{{/callout}}\n\n"
                "{{figure:chart_1 caption=\"TVL\" source=\"Dune\"}}\n\n"
                "## Methods\n\nMethods body.\n\n"
                "[^1]: Data through 2026-04-21.\n"
            ),
            authors=["Jane D.", "John S."],
            category="DeFi Research",
            key_takeaways=[
                "Yields compressed ~30% YoY",
                "Curve remains dominant venue",
                "Ethena inflows accelerating",
            ],
            footnotes=[{"id": "note2", "text": "Excludes CEX data."}],
            enforce_quality_gate=False,
            reset_session_state=False,
        )
        assert out["report_path"].name.startswith("cerebro_research_")
        structured = out["structured"]
        assert structured["presentation_mode"] == "research"
        meta = structured["research_metadata"]
        assert meta["deck"].startswith("A tour")
        assert meta["category"] == "DeFi Research"
        assert meta["reading_minutes"] >= 1
        assert len(meta["key_takeaways"]) == 3
        # Normalized footnote list contains the extra meta footnote
        assert any(f["id"] == "note2" for f in meta["footnotes"])
        # Rendered sections HTML contains the research structures and anchors
        sections = structured["sections_html"]
        assert "rr-callout" in sections
        assert "rr-figure" in sections
        assert 'id="overview"' in sections
        assert 'id="methods"' in sections

    def test_create_research_report_requires_deck(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._setup_chart("chart_1")
        with pytest.raises(ValueError, match="deck"):
            viz.create_research_report_artifact(
                title="X",
                deck="",
                content_markdown="{{chart:chart_1}}\n",
                key_takeaways=["a", "b", "c"],
                enforce_quality_gate=False,
                reset_session_state=False,
            )

    def test_create_research_report_requires_3_to_6_takeaways(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._setup_chart("chart_1")
        with pytest.raises(ValueError, match="key_takeaways"):
            viz.create_research_report_artifact(
                title="X",
                deck="A deck.",
                content_markdown="{{chart:chart_1}}\n",
                key_takeaways=["only one"],
                enforce_quality_gate=False,
                reset_session_state=False,
            )


    def test_quality_gate_scoped_to_referenced_charts(
        self, tmp_path, monkeypatch
    ):
        """Regression: legacy/unreferenced charts in the global registry must
        not block a clean report. The residual_bucket_disclosure gate (and
        peers) should evaluate only charts cited via {{chart:}} / {{figure:}}
        in the report's content_markdown.
        """
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))

        # Re-enable the gate that the autouse fixture disables, and turn on
        # only the residual-bucket heuristic so the test stays focused.
        s = session_state_mod.settings
        monkeypatch.setattr(s, "ENFORCE_CHART_PRECONDITIONS", True)
        monkeypatch.setattr(s, "ENFORCE_RESIDUAL_BUCKET_DISCLOSURE", True)
        monkeypatch.setattr(s, "ENFORCE_STOCK_FLOW_DISCIPLINE", False)
        monkeypatch.setattr(s, "ENFORCE_STATIONARITY_ON_CORRELATIONS", False)
        monkeypatch.setattr(s, "ENFORCE_AGGREGATOR_VOLUME_DEDUP", False)
        monkeypatch.setattr(s, "ENFORCE_DISCOVERED_MODEL_COVERAGE", False)
        monkeypatch.setattr(s, "REQUIRE_CHART_DIVERSITY", False)
        monkeypatch.setattr(s, "REQUIRE_DIMENSIONAL_BREAKDOWN", False)
        monkeypatch.setattr(s, "REQUIRE_RELATIONAL_CHART", False)
        monkeypatch.setattr(s, "MIN_CHARTS_FOR_REPORT", 1)
        monkeypatch.setattr(s, "MIN_EXPLORATORY_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_STATISTICAL_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_CORRELATION_QUERIES", 0)

        # Clean chart — no residual-bucket filter, will be referenced by the
        # report's markdown.
        viz._chart_registry["chart_clean"] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Clean trend",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": "SELECT toDate(ts) AS d, count() AS c FROM t GROUP BY d",
            "description": "",
        }
        # Polluting legacy chart — residual-bucket filter without metadata
        # acknowledgment. Must NOT be referenced by the report. With the bug
        # present, this charts blocks generate_report. With the fix, it does
        # not, because the gate is scoped to referenced charts.
        viz._chart_registry["chart_legacy"] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Legacy chart with hidden residual bucket",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": (
                "SELECT label, count() AS c FROM t "
                "WHERE label != '' GROUP BY label"
            ),
            "description": "",
        }

        out = viz.create_report_artifact(
            title="Scoped Gate Regression",
            content_markdown="## Section\n\n{{chart:chart_clean}}\n",
            enforce_quality_gate=True,
            reset_session_state=False,
        )
        # Success path: artifact built, structured content present, and the
        # legacy chart did NOT leak into the rendered report.
        assert out["structured"]["title"] == "Scoped Gate Regression"
        assert "chart_clean" in out["structured"]["charts"]
        assert "chart_legacy" not in out["structured"]["charts"]

    def test_begin_analysis_cycle_preserves_chart_gate_evidence(self):
        """begin_analysis_cycle() (run by every preflight) must NOT wipe the
        discovery/lineage/schema evidence the chart gate reads — wiping it is
        what caused the discover -> preflight -> 0/0/0 redo loop. It still
        resets the per-report accumulators (coverage set + chart/stat/corr
        counters) and preserves the preflight cache."""
        # Data-surface evidence the chart gate reads.
        state.explored_models.add("explored_x")
        state.explored_tables.add("table_y")
        state.verified_query_surfaces.add("dbt.fct_z")
        state.search_models_count = 3
        state.execute_query_count = 5
        # Per-report accumulators + coverage set.
        state.discovered_models.update({"old_model_a", "old_model_b"})
        state.excluded_models.add("excluded_w")
        state.generate_chart_count = 4
        state.statistical_query_count = 2
        state.correlation_query_count = 1
        state.chart_types_generated.update({"line", "scatter"})
        state.semantic_preflight_cache["key1"] = {"foo": "bar"}

        state.begin_analysis_cycle()

        # PRESERVED: chart-gate evidence survives a preflight.
        assert state.explored_models == {"explored_x"}
        assert state.explored_tables == {"table_y"}
        assert state.verified_query_surfaces == {"dbt.fct_z"}
        assert state.search_models_count == 3
        assert state.execute_query_count == 5
        # RESET: coverage set + report-quality accumulators.
        assert state.discovered_models == set()
        assert state.excluded_models == set()
        assert state.generate_chart_count == 0
        assert state.statistical_query_count == 0
        assert state.correlation_query_count == 0
        assert state.chart_types_generated == set()
        # Preflight cache preserved across cycles.
        assert "key1" in state.semantic_preflight_cache

    def test_preflight_after_discovery_does_not_reset_chart_gate(self, monkeypatch):
        """Regression for the redo loop: discover + explore + verify, THEN
        preflight (which runs begin_analysis_cycle), must leave the chart gate
        satisfiable instead of bouncing with discovery/lineage/schema at zero."""
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(
            session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True
        )
        # Discovery / lineage / schema done BEFORE preflight (the failing order).
        state.record_search_models("active validators", 1)
        state.record_get_model_details("api_consensus_validators_active_daily")
        state.record_describe_table("api_consensus_validators_active_daily")
        # A preflight resets the cycle (begin_analysis_cycle) then records itself.
        state.begin_analysis_cycle()
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")

        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert passed, reason

    def test_record_model_exclusion_satisfies_coverage_gate(self, monkeypatch):
        """record_model_exclusion marks a discovered model as excluded so it
        no longer counts toward the coverage gate."""
        s = session_state_mod.settings
        monkeypatch.setattr(s, "ENFORCE_CHART_PRECONDITIONS", True)
        monkeypatch.setattr(s, "ENFORCE_DISCOVERED_MODEL_COVERAGE", True)
        monkeypatch.setattr(s, "ENFORCE_RESIDUAL_BUCKET_DISCLOSURE", False)
        monkeypatch.setattr(s, "ENFORCE_STOCK_FLOW_DISCIPLINE", False)
        monkeypatch.setattr(s, "ENFORCE_STATIONARITY_ON_CORRELATIONS", False)
        monkeypatch.setattr(s, "ENFORCE_AGGREGATOR_VOLUME_DEDUP", False)
        monkeypatch.setattr(s, "REQUIRE_CHART_DIVERSITY", False)
        monkeypatch.setattr(s, "REQUIRE_DIMENSIONAL_BREAKDOWN", False)
        monkeypatch.setattr(s, "REQUIRE_RELATIONAL_CHART", False)
        monkeypatch.setattr(s, "MIN_CHARTS_FOR_REPORT", 1)
        monkeypatch.setattr(s, "MIN_EXPLORATORY_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_STATISTICAL_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_CORRELATION_QUERIES", 0)

        # One discovered, unused model. Without exclusion → gate fails.
        state.discovered_models.add("fct_some_unused_model")
        viz._chart_registry["chart_1"] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Trend",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": "SELECT 1",
            "description": "",
        }

        passed, reason, _ = state.check_report_preconditions(viz._chart_registry)
        assert not passed
        assert "Discovered-but-unused" in reason

        # After exclusion → gate passes.
        state.record_model_exclusion("fct_some_unused_model", "out of scope")
        passed, reason, _ = state.check_report_preconditions(viz._chart_registry)
        assert passed, f"Expected pass after exclusion, got: {reason}"

    def test_quality_gate_still_fires_on_referenced_polluting_chart(
        self, tmp_path, monkeypatch
    ):
        """Counterpart: when the polluting chart IS referenced, the gate
        must still reject. Confirms the scoping fix didn't silently disable
        enforcement.
        """
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        s = session_state_mod.settings
        monkeypatch.setattr(s, "ENFORCE_CHART_PRECONDITIONS", True)
        monkeypatch.setattr(s, "ENFORCE_RESIDUAL_BUCKET_DISCLOSURE", True)
        monkeypatch.setattr(s, "ENFORCE_STOCK_FLOW_DISCIPLINE", False)
        monkeypatch.setattr(s, "ENFORCE_STATIONARITY_ON_CORRELATIONS", False)
        monkeypatch.setattr(s, "ENFORCE_AGGREGATOR_VOLUME_DEDUP", False)
        monkeypatch.setattr(s, "ENFORCE_DISCOVERED_MODEL_COVERAGE", False)
        monkeypatch.setattr(s, "REQUIRE_CHART_DIVERSITY", False)
        monkeypatch.setattr(s, "REQUIRE_DIMENSIONAL_BREAKDOWN", False)
        monkeypatch.setattr(s, "REQUIRE_RELATIONAL_CHART", False)
        monkeypatch.setattr(s, "MIN_CHARTS_FOR_REPORT", 1)
        monkeypatch.setattr(s, "MIN_EXPLORATORY_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_STATISTICAL_QUERIES", 0)
        monkeypatch.setattr(s, "MIN_CORRELATION_QUERIES", 0)

        viz._chart_registry["chart_dirty"] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Chart with hidden residual bucket",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
            "sql": (
                "SELECT label, count() AS c FROM t "
                "WHERE label != '' GROUP BY label"
            ),
            "description": "",
        }

        with pytest.raises(ValueError, match="Quality"):
            viz.create_report_artifact(
                title="Should Reject",
                content_markdown="{{chart:chart_dirty}}\n",
                enforce_quality_gate=True,
                reset_session_state=False,
            )


    def test_raw_chart_blocked_in_pure_semantic_before_execution(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True)
        state.record_semantic_preflight(route="semantic_ready", mode="report")
        assert state.analysis_path == "semantic_only"

        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert not passed
        assert "semantic coverage" in reason.lower()


class TestReportDownloadUrl:
    """A1: report links must resolve (auth token + public base, no dead loopback)."""

    def test_base_url_appends_auth_token(self, monkeypatch):
        monkeypatch.setattr(
            session_state_mod.settings,
            "REPORT_BASE_URL",
            "https://mcp.example.com/reports",
        )
        monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-123")
        url = viz._get_report_download_url("abcd1234")
        assert url == "https://mcp.example.com/reports/abcd1234?token=secret-123"

    def test_base_url_without_token_has_no_query(self, monkeypatch):
        monkeypatch.setattr(
            session_state_mod.settings,
            "REPORT_BASE_URL",
            "https://mcp.example.com/reports",
        )
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        url = viz._get_report_download_url("abcd1234")
        assert url == "https://mcp.example.com/reports/abcd1234"
        assert "token=" not in url

    def test_sse_loopback_without_base_returns_none(self, monkeypatch):
        # Loopback bind host + no public base -> emit no dead http link; the
        # caller (`_get_report_link`) falls back to a working file:// path.
        monkeypatch.setattr(session_state_mod.settings, "REPORT_BASE_URL", "")
        monkeypatch.setenv("CEREBRO_TRANSPORT", "sse")
        monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        assert viz._get_report_download_url("abcd1234") is None

    def test_sse_public_host_gets_token(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "REPORT_BASE_URL", "")
        monkeypatch.setenv("CEREBRO_TRANSPORT", "sse")
        monkeypatch.setenv("FASTMCP_HOST", "reports.example.com")
        monkeypatch.setenv("FASTMCP_PORT", "9000")
        monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
        url = viz._get_report_download_url("abcd1234")
        assert url == "http://reports.example.com:9000/reports/abcd1234?token=tok"


class TestChartGateAggregationAndTiering:
    """B1 (one combined message) + B2 (tier-scaled lineage depth)."""

    def _enable(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(
            session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True
        )

    def test_all_prerequisites_reported_in_one_message(self, monkeypatch):
        self._enable(monkeypatch)
        # Cold state: nothing done yet. One call must surface EVERY remaining
        # prerequisite, not just the first, so the caller fixes them in one pass.
        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert not passed
        assert "Semantic preflight required" in reason
        assert "Discovery:" in reason
        assert "Lineage:" in reason
        assert "Schema:" in reason

    def test_lite_tier_passes_with_one_model_detail(self, monkeypatch):
        self._enable(monkeypatch)
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        state.record_search_models("active validators", 1)
        state.record_get_model_details("api_consensus_validators_active_daily")
        state.record_describe_table("api_consensus_validators_active_daily")

        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert passed, reason

    def test_report_tier_still_requires_three_model_details(self, monkeypatch):
        self._enable(monkeypatch)
        state.record_semantic_preflight(route="hybrid_ready", mode="report")
        state.record_search_models("active validators", 1)
        state.record_get_model_details("api_consensus_validators_active_daily")
        state.record_describe_table("api_consensus_validators_active_daily")

        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert not passed
        assert "Lineage:" in reason and "at least 3" in reason

        state.record_get_model_details("api_consensus_validators_active_weekly")
        state.record_get_model_details("api_consensus_validators_active_monthly")
        passed, reason = state.check_chart_preconditions(raw_path=True)
        assert passed, reason


class TestReportModeGate:
    """generate_report is hard-blocked unless the request was routed mode='report'."""

    def _enable(self, monkeypatch):
        monkeypatch.setattr(session_state_mod.settings, "SEMANTIC_ENABLED", True)
        monkeypatch.setattr(
            session_state_mod.settings, "ENFORCE_CHART_PRECONDITIONS", True
        )
        monkeypatch.setattr(
            session_state_mod.settings, "REPORT_REQUIRES_EXPLICIT_MODE", True
        )

    def test_chart_mode_report_blocked(self, monkeypatch):
        self._enable(monkeypatch)
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        passed, reason, _ = state.check_report_preconditions(
            {"chart_1": {"chart_type": "bar"}}
        )
        assert not passed
        assert "not routed as a report" in reason
        assert "STOP" in reason

    def test_answer_mode_report_blocked(self, monkeypatch):
        self._enable(monkeypatch)
        state.record_semantic_preflight(route="hybrid_ready", mode="answer")
        passed, reason, _ = state.check_report_preconditions(
            {"chart_1": {"chart_type": "bar"}}
        )
        assert not passed
        assert "not routed as a report" in reason

    def test_report_mode_not_blocked_by_explicit_mode_gate(self, monkeypatch):
        # mode="report" must get PAST the explicit-mode block into the normal
        # report-quality gates. With only 1 chart it still fails the min-charts
        # gate, but the failure must NOT be the explicit-mode block.
        self._enable(monkeypatch)
        state.record_semantic_preflight(route="hybrid_ready", mode="report")
        passed, reason, _ = state.check_report_preconditions(
            {"chart_1": {"chart_type": "bar"}}
        )
        assert "not routed as a report" not in reason

    def test_toggle_off_restores_lite_bypass(self, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(
            session_state_mod.settings, "REPORT_REQUIRES_EXPLICIT_MODE", False
        )
        state.record_semantic_preflight(route="hybrid_ready", mode="answer")
        passed, reason, _ = state.check_report_preconditions(
            {"chart_1": {"chart_type": "bar"}}
        )
        assert passed, reason


class TestChartModeAutoRender:
    """In chart/answer mode the chart tools RENDER the charts (visual_answer
    artifact + per-report UI resource with embedded data) — the fix for
    'charts generated but no visual output'."""

    def _mcp(self, tmp_path, monkeypatch, ui_inline=False):
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        monkeypatch.delenv("CEREBRO_TRANSPORT", raising=False)
        # UI-resource meta is gated (default off). Set before register so
        # tool-level meta reflects the flag; it also covers result-level meta.
        monkeypatch.setattr(
            "cerebro_mcp.config.settings.MCP_UI_INLINE_ENABLED", ui_inline
        )
        executed = SimpleNamespace(
            sql="SELECT day, cnt FROM dbt.t",
            database="dbt",
            columns=["day", "cnt"],
            rows=[["2026-01-01", 1], ["2026-01-02", 2]],
            elapsed_seconds=0.01,
        )
        ch = MagicMock()
        ch.run_query.return_value = executed
        mcp = FastMCP("test-chart-autorender")
        viz.register_visualization_tools(mcp, ch)
        return mcp

    def _spec(self):
        return {
            "sql": "SELECT day, cnt FROM dbt.t",
            "chart_type": "line",
            "x_field": "day",
            "y_field": "cnt",
            "title": "Trend",
        }

    def test_generate_charts_chart_mode_renders_visualization(
        self, tmp_path, monkeypatch
    ):
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        mcp = self._mcp(tmp_path, monkeypatch)
        fn = mcp._tool_manager._tools["generate_charts"].fn

        result = fn(charts=[self._spec()])

        assert isinstance(result, CallToolResult)
        assert result.structuredContent["presentation_mode"] == "visual_answer"
        # Default (MCP_UI_INLINE_ENABLED off): no server UI panel meta — the
        # model renders inline instead, from the model-inline payload.
        assert result.meta is None
        text = _tool_text(result)
        assert "RENDER THESE CHARTS INLINE" in text
        assert "palette_dark" in text
        assert "SELECT day, cnt" in text  # per-chart SQL is handed to the model
        assert "file://" in text  # full-fidelity report still linked
        # Session NOT reset: charts stay registered for a follow-up report.
        assert len(viz._chart_registry) == 1

    def test_generate_charts_report_mode_returns_summary_only(
        self, tmp_path, monkeypatch
    ):
        state.record_semantic_preflight(route="hybrid_ready", mode="report")
        mcp = self._mcp(tmp_path, monkeypatch)
        fn = mcp._tool_manager._tools["generate_charts"].fn

        result = fn(charts=[self._spec()])

        assert isinstance(result, CallToolResult)
        assert result.structuredContent is None
        assert result.meta is None
        assert "Registered charts" in _tool_text(result)

    def test_quick_chart_chart_mode_renders_visualization(
        self, tmp_path, monkeypatch
    ):
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        mcp = self._mcp(tmp_path, monkeypatch)
        fn = mcp._tool_manager._tools["quick_chart"].fn

        result = fn(
            sql="SELECT day, cnt FROM dbt.t",
            chart_type="line",
            x_field="day",
            y_field="cnt",
            title="Trend",
        )

        assert isinstance(result, CallToolResult)
        assert result.structuredContent["presentation_mode"] == "visual_answer"
        # Default: model renders inline; no server UI panel meta.
        assert result.meta is None
        assert "RENDER THESE CHARTS INLINE" in _tool_text(result)

    def test_report_instance_resource_serves_embedded_data(
        self, tmp_path, monkeypatch
    ):
        """The per-report ui:// resource returns standalone HTML with the
        report data EMBEDDED — renders with zero ext-apps handshake."""
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        mcp = self._mcp(tmp_path, monkeypatch, ui_inline=True)
        fn = mcp._tool_manager._tools["generate_charts"].fn
        result = fn(charts=[self._spec()])
        report_id = result.meta["ui"]["resourceUri"].rsplit("/", 1)[-1]

        templates = mcp._resource_manager._templates
        tpl_fn = None
        for key, tpl in templates.items():
            if "cerebro/report/" in str(key):
                tpl_fn = tpl.fn
                break
        assert tpl_fn is not None, "per-report resource template not registered"

        html = tpl_fn(report_id=report_id)
        assert 'id="report-data"' in html
        assert "visual_answer" in html

    def _static_resource(self, mcp, uri_fragment):
        for key, res in mcp._resource_manager._resources.items():
            if uri_fragment in str(key):
                return res.fn
        return None

    def test_chart_tools_carry_visualization_tool_meta(
        self, tmp_path, monkeypatch
    ):
        """UI-resource meta is gated by MCP_UI_INLINE_ENABLED: absent by default
        (so Claude Desktop shows no broken panel), present when enabled."""
        mcp_off = self._mcp(tmp_path, monkeypatch)
        for name in ("generate_charts", "quick_chart"):
            assert mcp_off._tool_manager._tools[name].meta is None

        mcp_on = self._mcp(tmp_path, monkeypatch, ui_inline=True)
        for name in ("generate_charts", "quick_chart"):
            meta = mcp_on._tool_manager._tools[name].meta
            assert meta["ui"]["resourceUri"] == viz.VISUALIZATION_URI

    def test_latest_visualization_resource_serves_embedded_data(
        self, tmp_path, monkeypatch
    ):
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        mcp = self._mcp(tmp_path, monkeypatch)
        fn = mcp._tool_manager._tools["generate_charts"].fn
        fn(charts=[self._spec()])

        res_fn = self._static_resource(mcp, "cerebro/visualization")
        assert res_fn is not None, "visualization resource not registered"
        html = res_fn()
        assert 'id="report-data"' in html
        assert "visual_answer" in html

    def test_latest_visualization_resource_placeholder_when_none(
        self, tmp_path, monkeypatch
    ):
        mcp = self._mcp(tmp_path, monkeypatch)
        res_fn = self._static_resource(mcp, "cerebro/visualization")
        html = res_fn()
        assert "no standalone visualization" in html

    def test_report_resource_serves_latest_embedded_report(
        self, tmp_path, monkeypatch
    ):
        """ui://cerebro/report now prefers the latest report's standalone
        HTML (data embedded) over the data-less bundle."""
        state.record_semantic_preflight(route="hybrid_ready", mode="chart")
        mcp = self._mcp(tmp_path, monkeypatch)
        fn = mcp._tool_manager._tools["generate_charts"].fn
        fn(charts=[self._spec()])

        res_fn = self._static_resource(mcp, "cerebro/report")
        html = res_fn()
        assert 'id="report-data"' in html


# ---------------------------------------------------------------------------
# Off-loop hardening: tools run on worker threads without blocking the loop
# ---------------------------------------------------------------------------

class TestOffloadHardening:
    """The `@_offloaded` wrapper and the non-blocking browser-open keep the
    single asyncio event loop responsive under a slow tool / cold browser."""

    def test_offloaded_runs_on_worker_thread(self):
        import asyncio
        import threading

        from cerebro_mcp.runtime.offload import offloaded

        main = threading.current_thread().name

        @offloaded
        def body(x):
            return (x, threading.current_thread().name)

        got, thread_name = asyncio.run(body(7))
        assert got == 7
        assert thread_name != main  # ran off the calling (event-loop) thread

    def test_offloaded_preserves_signature_and_doc(self):
        import inspect

        from cerebro_mcp.runtime.offload import offloaded

        @offloaded
        def body(a: int, b: str = "x") -> str:
            """Body doc."""
            return f"{a}{b}"

        sig = inspect.signature(body)
        assert list(sig.parameters) == ["a", "b"]
        assert body.__doc__ == "Body doc."

    def test_open_in_browser_async_does_not_block(self, monkeypatch):
        """A blocking webbrowser.open must not block the caller (it would
        freeze the event loop and time out every concurrent tool)."""
        import threading
        import time

        started = threading.Event()
        release = threading.Event()

        def blocking_open(url):
            started.set()
            release.wait(5)

        monkeypatch.setattr("webbrowser.open", blocking_open)

        t0 = time.monotonic()
        viz._open_in_browser_async("file:///tmp/x.html")
        elapsed = time.monotonic() - t0
        try:
            assert elapsed < 0.5  # returned immediately despite the 5s open
            assert started.wait(2)  # the open actually ran on a thread
        finally:
            release.set()
