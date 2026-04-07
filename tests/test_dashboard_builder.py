"""Tests for dashboard_builder.py — JS generation and YAML merging helpers."""

from __future__ import annotations

import os

import pytest
import yaml

from cerebro_mcp.dashboard_models import MetricPlacement, QuerySpec, TabSpec
from cerebro_mcp.tools.dashboard_builder import (
    _js_value,
    _render_query_js,
    _suggest_chart_type,
    _tab_spec_to_yaml_dict,
)


# ---------------------------------------------------------------------------
# _render_query_js
# ---------------------------------------------------------------------------


class TestRenderQueryJs:
    def test_area_chart_with_fields(self):
        spec = QuerySpec(
            id="txs_daily",
            name="Daily Transactions",
            chart_type="area",
            query="SELECT date, value, client FROM t",
            x_field="date",
            y_field="value",
            series_field="client",
        )
        js = _render_query_js(spec)
        assert "chartType: 'area'" in js
        assert "xField: 'date'" in js
        assert "yField: 'value'" in js
        assert "seriesField: 'client'" in js
        assert "export default metric;" in js

    def test_number_display_with_value_field(self):
        spec = QuerySpec(
            id="total_stake",
            name="Total Stake",
            chart_type="numberDisplay",
            query="SELECT sum(value) AS total FROM t",
            value_field="total",
        )
        js = _render_query_js(spec)
        assert "valueField: 'total'" in js
        assert "xField" not in js

    def test_text_chart_content(self):
        spec = QuerySpec(
            id="info_text",
            name="Info",
            chart_type="text",
            query="SELECT 1",
            content="This is **markdown** content.",
        )
        js = _render_query_js(spec)
        assert "content:" in js
        assert "This is **markdown** content." in js

    def test_extra_properties_serialized(self):
        spec = QuerySpec(
            id="custom_chart",
            name="Custom",
            chart_type="bar",
            query="SELECT 1",
            extra_properties={
                "colors": ["#ff0000", "#00ff00"],
                "options": {"smooth": True, "step": 5},
            },
        )
        js = _render_query_js(spec)
        assert "colors: ['#ff0000', '#00ff00']" in js
        assert "options: { smooth: true, step: 5 }" in js


# ---------------------------------------------------------------------------
# _js_value
# ---------------------------------------------------------------------------


class TestJsValue:
    @pytest.mark.parametrize(
        "python_val, expected_js",
        [
            (True, "true"),
            (False, "false"),
            ("hello", "'hello'"),
            (42, "42"),
            ([1, 2], "[1, 2]"),
            ({"a": 1}, "{ a: 1 }"),
            (None, "null"),
        ],
    )
    def test_js_value_conversions(self, python_val, expected_js):
        assert _js_value(python_val) == expected_js


# ---------------------------------------------------------------------------
# _tab_spec_to_yaml_dict
# ---------------------------------------------------------------------------


class TestTabSpecToYamlDict:
    def _make_tab(self, **kwargs):
        defaults = {
            "name": "Test Tab",
            "order": 1,
            "metrics": [
                MetricPlacement(id="m1", grid_row="1", grid_column="1 / span 12")
            ],
        }
        defaults.update(kwargs)
        return TabSpec(**defaults)

    def test_time_ranges_true(self):
        tab = self._make_tab(time_ranges=True)
        d = _tab_spec_to_yaml_dict(tab, tab.metrics)
        assert d["timeRanges"] is True

    def test_time_ranges_false_omits_key(self):
        tab = self._make_tab(time_ranges=False)
        d = _tab_spec_to_yaml_dict(tab, tab.metrics)
        assert "timeRanges" not in d

    def test_metrics_serialized(self):
        tab = self._make_tab()
        d = _tab_spec_to_yaml_dict(tab, tab.metrics)
        assert len(d["metrics"]) == 1
        assert d["metrics"][0]["id"] == "m1"
        assert d["metrics"][0]["gridColumn"] == "1 / span 12"


# ---------------------------------------------------------------------------
# YAML merge with tmpdir
# ---------------------------------------------------------------------------


class TestYamlMerge:
    def test_merge_second_tab(self, tmp_path):
        """Write initial YAML with one tab, merge a second tab, verify both exist."""
        source_file = tmp_path / "source.yml"
        initial_data = {
            "tabs": [
                {
                    "name": "Overview",
                    "order": 0,
                    "metrics": [{"id": "m1", "gridRow": "1", "gridColumn": "1 / span 12"}],
                }
            ]
        }
        source_file.write_text(yaml.dump(initial_data))

        # Load, merge, write
        with open(source_file) as f:
            data = yaml.safe_load(f)

        new_tab = self._make_tab_dict("Validators", order=1)
        data["tabs"].append(new_tab)

        with open(source_file, "w") as f:
            yaml.dump(data, f)

        # Verify
        with open(source_file) as f:
            result = yaml.safe_load(f)
        names = [t["name"] for t in result["tabs"]]
        assert names == ["Overview", "Validators"]

    def test_merge_idempotent(self, tmp_path):
        """Merging the same tab name twice should not duplicate it."""
        source_file = tmp_path / "source.yml"
        initial_data = {
            "tabs": [
                {"name": "Overview", "order": 0, "metrics": []}
            ]
        }
        source_file.write_text(yaml.dump(initial_data))

        for _ in range(2):
            with open(source_file) as f:
                data = yaml.safe_load(f)

            new_tab = self._make_tab_dict("Overview", order=0)
            # Replace existing tab with same name (mirrors scaffold logic)
            replaced = False
            for i, existing in enumerate(data["tabs"]):
                if existing["name"].lower() == new_tab["name"].lower():
                    data["tabs"][i] = new_tab
                    replaced = True
                    break
            if not replaced:
                data["tabs"].append(new_tab)

            with open(source_file, "w") as f:
                yaml.dump(data, f)

        with open(source_file) as f:
            result = yaml.safe_load(f)
        overview_tabs = [t for t in result["tabs"] if t["name"] == "Overview"]
        assert len(overview_tabs) == 1

    @staticmethod
    def _make_tab_dict(name: str, order: int = 0) -> dict:
        return {
            "name": name,
            "order": order,
            "metrics": [{"id": "m_new", "gridRow": "1", "gridColumn": "1 / span 12"}],
        }


# ---------------------------------------------------------------------------
# _suggest_chart_type
# ---------------------------------------------------------------------------


class TestSuggestChartType:
    def test_kpi_in_name(self):
        assert _suggest_chart_type("api_execution_kpi_latest", []) == "numberDisplay"

    def test_sankey_in_name(self):
        assert _suggest_chart_type("api_bridges_sankey_flows", []) == "sankey"

    def test_time_and_series_columns(self):
        cols = [{"name": "date"}, {"name": "client"}, {"name": "value"}]
        assert _suggest_chart_type("api_some_model", cols) == "area"

    def test_time_only(self):
        cols = [{"name": "day"}, {"name": "count"}]
        assert _suggest_chart_type("api_blocks_daily", cols) == "line"

    def test_fallback_bar(self):
        cols = [{"name": "category"}, {"name": "count"}]
        assert _suggest_chart_type("api_something", cols) == "bar"
