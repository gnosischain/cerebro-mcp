"""Compiler tests for the Grafana dashboard publisher."""
from __future__ import annotations

import json

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.grafana.compiler import compile_grafana_dashboard, sql_for_validation
from cerebro_mcp.grafana.models import GrafanaDashboardDef, GrafanaVariableDef


@pytest.fixture(autouse=True)
def _ds(monkeypatch):
    monkeypatch.setattr(settings, "GRAFANA_CLICKHOUSE_DATASOURCE_UID", "ch-uid-123")
    monkeypatch.setattr(settings, "GRAFANA_CLICKHOUSE_DATASOURCE_TYPE", "grafana-clickhouse-datasource")
    monkeypatch.setattr(settings, "GRAFANA_SCHEMA_VERSION", 41)


def _full_dashboard():
    return GrafanaDashboardDef(
        uid="growth_x_daily",
        title="Growth",
        tags=["growth", "cerebro-mcp", "growth"],  # dup + already-present
        variables=[
            GrafanaVariableDef(name="chain", type="custom", options="gnosis,ethereum", default="gnosis"),
            GrafanaVariableDef(name="interval", type="interval", options="1m,5m,1h", default="5m"),
        ],
        panels=[
            {"title": "Users", "role": "kpi", "data_shape": "single_value",
             "sql_query": "SELECT count() FROM u", "unit": "short"},
            {"title": "Target", "role": "kpi", "viz": "gauge",
             "data_shape": "single_value_bounded", "sql_query": "SELECT v FROM t", "unit": "percent"},
            {"title": "DAU", "role": "trend", "viz": "timeseries_area",
             "data_shape": "time_series_multi",
             "sql_query": "SELECT toStartOfDay(ts) AS time, chain AS label, count() AS value FROM e WHERE $__timeFilter(ts) GROUP BY time, label",
             "unit": "short"},
            {"title": "Heat", "role": "breakdown", "viz": "heatmap",
             "data_shape": "distribution_2d",
             "sql_query": "SELECT x, y, count() AS value FROM d GROUP BY x, y", "unit": "percent"},
            {"title": "Top users", "role": "detail", "data_shape": "tabular",
             "sql_query": "SELECT user, n FROM top LIMIT 50"},
        ],
    )


def test_deterministic_byte_identical():
    d = _full_dashboard()
    a = json.dumps(compile_grafana_dashboard(d), sort_keys=True)
    b = json.dumps(compile_grafana_dashboard(d), sort_keys=True)
    assert a == b


def test_no_id_key_in_output():
    out = compile_grafana_dashboard(_full_dashboard())
    assert "id" not in out


def _data_panels(out):
    return [p for p in out["panels"] if p.get("type") != "row"]


def test_datasource_from_settings_not_llm():
    out = compile_grafana_dashboard(_full_dashboard())
    for p in _data_panels(out):
        assert p["datasource"]["uid"] == "ch-uid-123"
        assert p["targets"][0]["datasource"]["uid"] == "ch-uid-123"


def test_target_format_is_numeric_enum():
    # Grafana ClickHouse plugin: format is numeric (TimeSeries=0, Table=1).
    # Emitting the string "time_series" => "invalid format value" at query time.
    # Since 24dc207 EVERY target is table (1): the plugin rejects format 0 with
    # an unmarshal error, and timeseries panels build their series from the
    # returned table columns anyway (see _FORMAT_TABLE in grafana/compiler.py).
    out = compile_grafana_dashboard(_full_dashboard())
    by_title = {p["title"]: p for p in out["panels"]}
    assert by_title["DAU"]["targets"][0]["format"] == 1
    assert by_title["DAU"]["targets"][0]["queryType"] == "table"
    assert by_title["Users"]["targets"][0]["format"] == 1
    assert by_title["Top users"]["targets"][0]["format"] == 1
    for p in _data_panels(out):
        assert isinstance(p["targets"][0]["format"], int)


def test_stat_textmode_value_only():
    # KPI stat cards must show just the value ("$177M"), not "value $177M".
    out = compile_grafana_dashboard(_full_dashboard())
    by_title = {p["title"]: p for p in out["panels"]}
    assert by_title["Users"]["options"]["textMode"] == "value"


def test_layout_kpis_first_detail_last():
    out = compile_grafana_dashboard(_full_dashboard())
    data = _data_panels(out)
    # KPI section header row is first, KPI panels sit just below at y=1
    assert out["panels"][0]["type"] == "row"
    assert out["panels"][0]["title"] == "Key Metrics"
    assert data[0]["gridPos"]["y"] == 1
    # detail table is the last data panel and lands lowest
    assert data[-1]["type"] == "table"
    assert data[-1]["gridPos"]["y"] == max(p["gridPos"]["y"] for p in data)


def test_section_headers_present():
    out = compile_grafana_dashboard(_full_dashboard())
    rows = [p for p in out["panels"] if p.get("type") == "row"]
    assert [r["title"] for r in rows] == ["Key Metrics", "Trends", "Breakdowns", "Detail"]


def test_no_section_headers_for_single_role():
    d = GrafanaDashboardDef(uid="u", title="t", panels=[
        {"title": "A", "role": "kpi", "data_shape": "single_value",
         "sql_query": "SELECT 1 AS value", "unit": "short"},
        {"title": "B", "role": "kpi", "data_shape": "single_value",
         "sql_query": "SELECT 2 AS value", "unit": "short"},
    ])
    out = compile_grafana_dashboard(d)
    assert all(p.get("type") != "row" for p in out["panels"])


def test_rows_fill_24_columns_no_horizontal_gap():
    out = compile_grafana_dashboard(_full_dashboard())
    by_y: dict[int, int] = {}
    for p in _data_panels(out):
        g = p["gridPos"]
        by_y[g["y"]] = by_y.get(g["y"], 0) + g["w"]
    for y, total in by_y.items():
        assert total == 24, f"row y={y} sums to {total}, not 24"


def test_uniform_height_per_row_no_vertical_gap():
    out = compile_grafana_dashboard(_full_dashboard())
    heights_by_y: dict[int, set] = {}
    for p in _data_panels(out):
        g = p["gridPos"]
        heights_by_y.setdefault(g["y"], set()).add(g["h"])
    for y, heights in heights_by_y.items():
        assert len(heights) == 1, f"row y={y} has mixed heights {heights}"


def test_tags_dedup_and_prepend():
    out = compile_grafana_dashboard(_full_dashboard())
    assert out["tags"][:2] == ["cerebro-mcp", "ai-generated"]
    assert out["tags"].count("cerebro-mcp") == 1
    assert out["tags"].count("growth") == 1


def test_macro_substitution_resolves():
    cleaned = sql_for_validation("SELECT * FROM t WHERE $__timeFilter(block_time)")
    assert "$__timeFilter" not in cleaned
    assert "block_time >=" in cleaned


def test_unknown_macro_raises():
    with pytest.raises(ValueError):
        sql_for_validation("SELECT $__weirdMacro(x) FROM t")


def test_template_var_neutralized_and_interval_allowed():
    cleaned = sql_for_validation(
        "SELECT toStartOfInterval(ts, INTERVAL $__interval) FROM t WHERE chain = '$chain'"
    )
    assert "$chain" not in cleaned
    assert "'__var__'" in cleaned


def test_variables_compiled_into_templating_list():
    out = compile_grafana_dashboard(_full_dashboard())
    names = [v["name"] for v in out["templating"]["list"]]
    assert names == ["chain", "interval"]
    interval = out["templating"]["list"][1]
    assert interval["type"] == "interval"
    assert interval["refresh"] == 2


def test_per_viz_option_builders():
    out = compile_grafana_dashboard(_full_dashboard())
    by_title = {p["title"]: p for p in out["panels"]}
    # stat has reduceOptions
    assert "reduceOptions" in by_title["Users"]["options"]
    # gauge has min/max in fieldConfig defaults (from percent unit)
    gdefs = by_title["Target"]["fieldConfig"]["defaults"]
    assert gdefs["min"] == 0.0 and gdefs["max"] == 100.0
    # area-multi has stacking normal
    area_custom = by_title["DAU"]["fieldConfig"]["defaults"]["custom"]
    assert area_custom["stacking"]["mode"] == "normal"
    # heatmap with distribution_2d compiles to a color-graded table grid
    assert by_title["Heat"]["type"] == "table"
    # table has cellOptions
    assert "cellOptions" in by_title["Top users"]["fieldConfig"]["defaults"]["custom"]


def test_heatmap_timeseries_multi_calculates_true():
    d = GrafanaDashboardDef(uid="u", title="t", panels=[
        {"title": "H", "role": "breakdown", "viz": "heatmap",
         "data_shape": "time_series_multi",
         "sql_query": "SELECT time, s, v FROM x", "unit": "short"},
    ])
    out = compile_grafana_dashboard(d)
    assert out["panels"][0]["options"]["calculate"] is True
    # calculate-mode heatmaps bucket the raw value column themselves; the
    # auto-pivot applies only to timeseries_* viz, never here.
    assert out["panels"][0]["type"] == "heatmap"
    assert out["panels"][0]["transformations"] == []


# --- long-format pivot: auto transformations + alias contract -------------
# Table-format targets are never pivoted into series by the panel itself
# (lesson: grafana-table-format-needs-pivot-transform) — these tests pin the
# compiler-added pivots and prove the parse-time alias gate rejects exactly
# the SQL it exists to reject.


def test_auto_pivot_added_for_time_series_multi():
    out = compile_grafana_dashboard(_full_dashboard())
    by_title = {p["title"]: p for p in out["panels"]}
    assert by_title["DAU"]["transformations"] == [{
        "id": "partitionByValues",
        "options": {"fields": ["label"], "keepFields": False,
                    "naming": {"asLabels": True}},
    }]
    # shapes without a long-format pivot stay untransformed
    assert by_title["Users"]["transformations"] == []
    assert by_title["Top users"]["transformations"] == []


def test_distribution_2d_compiles_to_color_grid_table():
    out = compile_grafana_dashboard(_full_dashboard())
    heat = {p["title"]: p for p in out["panels"]}["Heat"]
    # native heatmap panel crashes on categorical grids -> table grid instead
    assert heat["type"] == "table"
    assert heat["transformations"] == [{
        "id": "groupingToMatrix",
        "options": {"columnField": "x", "rowField": "y", "valueField": "value"},
    }]
    defaults = heat["fieldConfig"]["defaults"]
    assert defaults["custom"]["cellOptions"] == {"type": "color-background"}
    assert defaults["color"]["mode"] == "continuous-RdYlGr"
    # percent unit implies 0..100 bounds for the gradient
    assert defaults["min"] == 0.0 and defaults["max"] == 100.0


def test_auto_pivot_barchart_category_value_multi():
    d = GrafanaDashboardDef(uid="u", title="t", panels=[
        {"title": "Hourly", "role": "breakdown", "viz": "barchart_vertical",
         "data_shape": "category_value_multi",
         "sql_query": "SELECT hour AS category, chain AS series, count() AS value FROM e GROUP BY category, series",
         "unit": "short"},
    ])
    out = compile_grafana_dashboard(d)
    assert out["panels"][0]["transformations"] == [{
        "id": "groupingToMatrix",
        "options": {"columnField": "series", "rowField": "category",
                    "valueField": "value"},
    }]


def test_user_transformations_win_over_auto_pivot():
    custom = [{"id": "partitionByValues", "options": {"fields": ["chain"]}}]
    d = GrafanaDashboardDef(uid="u", title="t", panels=[
        {"title": "DAU", "role": "trend", "viz": "timeseries_line",
         "data_shape": "time_series_multi",
         # no canonical aliases needed when transformations are supplied
         "sql_query": "SELECT time, chain, v FROM e",
         "unit": "short", "transformations": custom},
    ])
    out = compile_grafana_dashboard(d)
    assert out["panels"][0]["transformations"] == custom


def test_time_series_multi_without_label_alias_rejected():
    # The gate must fail on the exact input it exists to reject: long-format
    # SQL whose series column is not aliased `label` and no explicit
    # transformations to compensate.
    with pytest.raises(ValueError, match="label"):
        GrafanaDashboardDef(uid="u", title="t", panels=[
            {"title": "DAU", "role": "trend", "viz": "timeseries_line",
             "data_shape": "time_series_multi",
             "sql_query": "SELECT toStartOfDay(ts) AS time, chain, count() AS value FROM e GROUP BY time, chain",
             "unit": "short"},
        ])


def test_distribution_2d_without_canonical_aliases_rejected():
    with pytest.raises(ValueError, match="Missing"):
        GrafanaDashboardDef(uid="u", title="t", panels=[
            {"title": "Heat", "role": "breakdown", "viz": "heatmap",
             "data_shape": "distribution_2d",
             "sql_query": "SELECT a, b, count() FROM d GROUP BY a, b",
             "unit": "short"},
        ])


def test_category_value_multi_without_canonical_aliases_rejected():
    with pytest.raises(ValueError, match="Missing"):
        GrafanaDashboardDef(uid="u", title="t", panels=[
            {"title": "Hourly", "role": "breakdown", "viz": "barchart_vertical",
             "data_shape": "category_value_multi",
             "sql_query": "SELECT hour, chain, count() FROM e GROUP BY hour, chain",
             "unit": "short"},
        ])


def test_five_kpis_balance_across_rows_filling_24():
    # 5 KPIs -> two grid-rows, each summing to 24 (3 then 2), no gaps.
    panels = [
        {"title": f"K{i}", "role": "kpi", "data_shape": "single_value",
         "sql_query": f"SELECT {i} AS value", "unit": "short"}
        for i in range(5)
    ]
    out = compile_grafana_dashboard(GrafanaDashboardDef(uid="u", title="t", panels=panels))
    data = [p for p in out["panels"] if p.get("type") != "row"]
    widths_by_y: dict[int, list] = {}
    for p in data:
        g = p["gridPos"]
        widths_by_y.setdefault(g["y"], []).append(g["w"])
    sums = {y: sum(ws) for y, ws in widths_by_y.items()}
    assert all(s == 24 for s in sums.values())
    assert sorted(len(ws) for ws in widths_by_y.values()) == [2, 3]


def test_layout_sketch_lists_sections_and_metrics():
    from cerebro_mcp.grafana.compiler import build_layout_sketch
    sketch = build_layout_sketch(_full_dashboard())
    assert "Dashboard: Growth" in sketch
    assert "Key Metrics" in sketch and "Trends" in sketch
    assert "Metrics:" in sketch
    assert "Users" in sketch and "DAU" in sketch
    assert "Approve to publish" in sketch
