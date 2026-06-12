"""Pure, deterministic compiler: GrafanaDashboardDef -> Grafana dashboard JSON.

No I/O. Given the same spec (and the same settings), it produces byte-identical
output, which makes it trivially unit-testable and keeps publishing idempotent.

The compiler:
  * substitutes Grafana SQL macros to neutral SQL for safety validation,
  * packs panels into a 24-column grid grouped by role (KPI -> trend ->
    breakdown -> detail),
  * builds per-viz option/fieldConfig blocks (no shared `options: {}`),
  * injects the ClickHouse datasource UID from settings (never the LLM),
  * compiles template variables into `templating.list`,
  * omits the `id` field so Grafana assigns it on overwrite-by-UID.
"""
from __future__ import annotations

import math
import re

from cerebro_mcp.config import settings
from cerebro_mcp.grafana.models import (
    GrafanaDashboardDef,
    GrafanaPanelDef,
    GrafanaVariableDef,
)
from cerebro_mcp.grafana.styles import (
    DASHBOARD_STYLE,
    KPI_THRESHOLDS_REFINED,
    PALETTE,
    THRESHOLD_COLOR_MAP,
    bounds_for_unit,
    default_size,
    grafana_type,
)

# --- SQL macro handling --------------------------------------------------
# Function-form macros are substituted with neutral SQL purely so the query
# parses for safety validation. The *raw* SQL (with macros intact) is what we
# send to Grafana, which resolves them at query time.

_MACRO_PATTERNS: list[tuple[re.Pattern, "callable"]] = [
    (re.compile(r"\$__timeFilter\(\s*(\w+)\s*\)"),
     lambda m: f"{m.group(1)} >= now() - INTERVAL 30 DAY"),
    (re.compile(r"\$__dateFilter\(\s*(\w+)\s*\)"),
     lambda m: f"toDate({m.group(1)}) >= today() - 30"),
    (re.compile(r"\$__timeInterval\(\s*(\w+)\s*\)"),
     lambda m: "toIntervalMinute(5)"),
]


def sql_for_validation(sql: str) -> str:
    """Return a macro-free, variable-neutralized form of `sql` for validation.

    Raises ValueError on any unsupported `$__*` macro. Single-`$` template
    variables (e.g. `$chain`) and `$__interval` resolve at Grafana time and
    are neutralized to a string literal so the query still parses.
    """
    cleaned = sql
    for pattern, repl in _MACRO_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)

    # Any remaining `$__macro` that isn't an interval variant is unsupported.
    remaining = sorted(set(re.findall(r"\$__\w+", cleaned)))
    unknown = [u for u in remaining if not u.startswith("$__interval")]
    if unknown:
        raise ValueError(f"Unsupported Grafana macros: {', '.join(unknown)}")

    # Neutralize template variable references so the SQL parses standalone.
    return re.sub(r"\$\w+", "'__var__'", cleaned)


# --- datasource ----------------------------------------------------------

def _datasource() -> dict:
    return {
        "type": settings.GRAFANA_CLICKHOUSE_DATASOURCE_TYPE,
        "uid": settings.GRAFANA_CLICKHOUSE_DATASOURCE_UID,
    }


# --- thresholds / mappings ----------------------------------------------

def _build_thresholds(panel: GrafanaPanelDef) -> dict:
    if panel.thresholds:
        return {
            "mode": "absolute",
            "steps": [
                {
                    "color": THRESHOLD_COLOR_MAP.get(step.color, step.color),
                    "value": step.value,
                }
                for step in panel.thresholds
            ],
        }
    if panel.role == "kpi":
        return KPI_THRESHOLDS_REFINED
    # Neutral single-step base for everything else.
    return {"mode": "absolute", "steps": [{"color": PALETTE["neutral"], "value": None}]}


def _build_mappings(panel: GrafanaPanelDef) -> list[dict]:
    if not panel.value_mappings:
        return []
    options = {}
    for i, vm in enumerate(panel.value_mappings):
        options[vm.state] = {
            "text": vm.text or vm.state,
            "color": vm.color,
            "index": i,
        }
    return [{"type": "value", "options": options}]


# --- per-viz option builders --------------------------------------------

_REDUCE_LAST = {"calcs": ["lastNotNull"], "fields": "", "values": False}


def _options_stat(panel: GrafanaPanelDef) -> dict:
    use_spark = bool(
        panel.effective_viz == "stat"
        and panel.data_shape == "single_value"
        and panel.sparkline_sql
    )
    return {
        "colorMode": "background",
        "graphMode": "area" if use_spark else "none",
        "justifyMode": "center",
        # "value" (not "value_and_name") => the card shows just "$177M", not
        # "value $177M". The panel title already names the metric.
        "textMode": "value",
        "reduceOptions": dict(_REDUCE_LAST),
    }


def _options_gauge(panel: GrafanaPanelDef) -> dict:
    return {
        "reduceOptions": dict(_REDUCE_LAST),
        "showThresholdLabels": False,
        "showThresholdMarkers": True,
        "orientation": "auto",
    }


def _options_bargauge(panel: GrafanaPanelDef) -> dict:
    return {
        "displayMode": "gradient",
        "orientation": "horizontal",
        "reduceOptions": dict(_REDUCE_LAST),
        "showUnfilled": True,
        "valueMode": "color",
    }


def _options_timeseries(panel: GrafanaPanelDef) -> dict:
    return {
        "legend": {
            "displayMode": "table",
            "placement": "bottom",
            "calcs": ["lastNotNull", "mean", "max"],
        },
        "tooltip": {"mode": "multi", "sort": "desc"},
    }


def _options_barchart(panel: GrafanaPanelDef) -> dict:
    horizontal = panel.effective_viz == "barchart_horizontal"
    return {
        "legend": {"displayMode": "list", "placement": "bottom"},
        "orientation": "horizontal" if horizontal else "vertical",
        "showValue": "auto",
        "stacking": "normal" if panel.data_shape == "category_value_multi" else "none",
        "xTickLabelRotation": 0 if horizontal else -30,
    }


def _options_piechart(panel: GrafanaPanelDef) -> dict:
    return {
        "legend": {"displayMode": "table", "placement": "right",
                   "values": ["value", "percent"]},
        "pieType": "donut",
        "displayLabels": ["name", "percent"],
        # values=True => one slice per ROW (per category), not one slice per
        # field. A category breakdown (label col + value col) is meaningless
        # with values=False: it reduces the value column to a single slice.
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
        "tooltip": {"mode": "single", "sort": "desc"},
    }


def _options_histogram(panel: GrafanaPanelDef) -> dict:
    return {"legend": {"displayMode": "list", "placement": "bottom"}}


def _options_heatmap(panel: GrafanaPanelDef) -> dict:
    calculate = panel.data_shape == "time_series_multi"
    return {
        "calculate": calculate,
        "color": {"mode": "scheme", "scheme": "Inferno", "steps": 64},
        "legend": {"show": True},
        "tooltip": {"show": True, "yHistogram": False},
    }


def _options_state(panel: GrafanaPanelDef) -> dict:
    return {
        "legend": {"displayMode": "list", "placement": "bottom"},
        "showValue": "auto",
        "rowHeight": 0.9,
        "mergeValues": True,
    }


def _options_table(panel: GrafanaPanelDef) -> dict:
    return {
        "cellHeight": "sm",
        "footer": {"countRows": False, "reducer": ["sum"], "show": False},
        "showHeader": True,
    }


_OPTION_BUILDERS = {
    "stat": _options_stat,
    "gauge": _options_gauge,
    "bargauge": _options_bargauge,
    "timeseries_line": _options_timeseries,
    "timeseries_area": _options_timeseries,
    "timeseries_bar": _options_timeseries,
    "barchart_vertical": _options_barchart,
    "barchart_horizontal": _options_barchart,
    "piechart": _options_piechart,
    "histogram": _options_histogram,
    "heatmap": _options_heatmap,
    "state_timeline": _options_state,
    "status_history": _options_state,
    "table": _options_table,
}


def _custom_field_config(panel: GrafanaPanelDef) -> dict:
    """The `custom` block of fieldConfig.defaults, per viz family."""
    viz = panel.effective_viz
    if viz == "timeseries_line":
        return {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10,
                "spanNulls": False, "showPoints": "never"}
    if viz == "timeseries_area":
        multi = panel.data_shape == "time_series_multi"
        return {"drawStyle": "line", "lineWidth": 1,
                "fillOpacity": 40 if multi else 20,
                "stacking": {"mode": "normal" if multi else "none", "group": "A"},
                "spanNulls": False, "showPoints": "never"}
    if viz == "timeseries_bar":
        multi = panel.data_shape == "time_series_multi"
        return {"drawStyle": "bars", "fillOpacity": 70,
                "stacking": {"mode": "normal" if multi else "none", "group": "A"}}
    if viz == "table":
        return {"align": "auto", "cellOptions": {"type": "auto"}}
    return {}


# --- field config + panel assembly --------------------------------------

def _field_config(panel: GrafanaPanelDef) -> dict:
    defaults: dict = {
        "unit": panel.unit,
        "thresholds": _build_thresholds(panel),
        "color": {"mode": "thresholds"} if panel.role == "kpi" else {"mode": "palette-classic"},
    }
    if panel.decimals is not None:
        defaults["decimals"] = panel.decimals

    mappings = _build_mappings(panel)
    if mappings:
        defaults["mappings"] = mappings

    custom = _custom_field_config(panel)
    if custom:
        defaults["custom"] = custom

    # gauge min/max (explicit or unit-implied)
    if panel.effective_viz == "gauge":
        implied = bounds_for_unit(panel.unit)
        gmin = panel.min if panel.min is not None else (implied[0] if implied else None)
        gmax = panel.max if panel.max is not None else (implied[1] if implied else None)
        if gmin is not None:
            defaults["min"] = gmin
        if gmax is not None:
            defaults["max"] = gmax

    return {"defaults": defaults, "overrides": _field_overrides(panel)}


def _field_overrides(panel: GrafanaPanelDef) -> list[dict]:
    """Per-column overrides. Forces declared text columns (addresses, hashes,
    ids) to render verbatim — otherwise the panel's numeric `unit` leaks onto
    every table column and Grafana formats hex strings as numbers."""
    overrides: list[dict] = []
    for col in panel.text_columns:
        overrides.append({
            "matcher": {"id": "byName", "options": col},
            "properties": [
                {"id": "unit", "value": "string"},
                {"id": "decimals", "value": 0},
                {"id": "custom.cellOptions", "value": {"type": "auto"}},
            ],
        })
    return overrides


# Grafana ClickHouse plugin `format` is a NUMERIC enum (see
# mapQueryTypeToGrafanaFormat in grafana/clickhouse-datasource src/data/utils.ts):
#   TimeSeries = 0, Table = 1, Logs = 2, Traces = 3.
# The plugin rejects the `time_series` format (0) with an unmarshal error on
# every panel — only `table` (1) is accepted regardless of viz type. Timeseries
# panels still render correctly: the plugin builds the series from the returned
# table columns. So every target is emitted as table/`queryType: "table"`.
_FORMAT_TABLE = 1


def _build_target(panel: GrafanaPanelDef) -> dict:
    return {
        "refId": "A",
        "datasource": _datasource(),
        "rawSql": panel.sql_query,
        "format": _FORMAT_TABLE,
        "editorType": "sql",
        "queryType": "table",
    }


def _compile_panel(panel: GrafanaPanelDef, panel_id: int, grid_pos: dict) -> dict:
    builder = _OPTION_BUILDERS[panel.effective_viz]
    return {
        "id": panel_id,
        "type": grafana_type(panel.effective_viz),
        "title": panel.title,
        "description": panel.description,
        "datasource": _datasource(),
        "gridPos": grid_pos,
        "fieldConfig": _field_config(panel),
        "options": builder(panel),
        "targets": [_build_target(panel)],
        "transformations": panel.transformations,
    }


# --- layout (gap-free, section-grouped) ----------------------------------
#
# Two rules keep the dashboard free of empty space and readable:
#   1. Every grid-row's panel widths are auto-fit to sum to exactly 24
#      columns (no horizontal gap), ignoring per-panel width hints.
#   2. All panels in a grid-row share one height (no vertical gap).
# Panels are grouped into role sections (KPI -> trend -> breakdown -> detail)
# and, when more than one section is present, each gets a Grafana "row" panel
# as a titled header.

SECTION_LABELS = {
    "kpi": "Key Metrics",
    "trend": "Trends",
    "breakdown": "Breakdowns",
    "detail": "Detail",
}

# Max panels per grid-row, per role. Actual columns are balanced across rows.
_ROLE_MAX_COLS = {"kpi": 4, "trend": 2, "breakdown": 3, "detail": 1}


def _distribute_widths(n: int) -> list[int]:
    """Split 24 columns across n panels so they sum to exactly 24."""
    base, rem = divmod(24, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def _cols_for(role: str, n: int) -> int:
    """Balanced number of columns per grid-row for a section of n panels."""
    max_cols = _ROLE_MAX_COLS.get(role, 3)
    n_rows = math.ceil(n / max_cols)
    return math.ceil(n / n_rows)


def _use_sections(panels: list[GrafanaPanelDef]) -> bool:
    return len({p.role for p in panels}) >= 2


def plan_sections(panels: list[GrafanaPanelDef], use_sections: bool) -> list[dict]:
    """Pure layout plan: list of sections, each with a label and grid-rows.

    Each grid-row is a list of (panel, width, height) with widths summing to 24
    and a single shared height. Used by both the compiler and the sketch.
    """
    from cerebro_mcp.grafana.styles import ROLE_ORDER

    sections: list[dict] = []
    for role in ROLE_ORDER:
        group = [p for p in panels if p.role == role]
        if not group:
            continue
        cols = _cols_for(role, len(group))
        rows: list[list[tuple]] = []
        for i in range(0, len(group), cols):
            chunk = group[i:i + cols]
            widths = _distribute_widths(len(chunk))
            height = max(
                (p.height if p.height is not None else default_size(p.effective_viz)[1])
                for p in chunk
            )
            rows.append([(p, w, height) for p, w in zip(chunk, widths)])
        sections.append({
            "role": role,
            "label": SECTION_LABELS[role] if use_sections else None,
            "rows": rows,
        })
    return sections


def _compile_row(title: str, y: int, panel_id: int) -> dict:
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


# --- variables -----------------------------------------------------------

def _compile_variable(var: GrafanaVariableDef) -> dict:
    opts = [o.strip() for o in var.options.split(",") if o.strip()]
    entry = {
        "name": var.name,
        "type": var.type,
        "label": var.label,
        "query": var.options,
        "current": {"text": var.default, "value": var.default, "selected": True},
        "options": [
            {"text": o, "value": o, "selected": o == var.default} for o in opts
        ],
        "hide": 0,
    }
    if var.type == "interval":
        entry["refresh"] = 2
        entry["auto"] = False
    return entry


# --- tags ----------------------------------------------------------------

def _compile_tags(tags: list[str]) -> list[str]:
    out: list[str] = ["cerebro-mcp", "ai-generated"]
    seen = set(out)
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


# --- top-level -----------------------------------------------------------

def compile_grafana_dashboard(dashboard: GrafanaDashboardDef) -> dict:
    sections = plan_sections(dashboard.panels, _use_sections(dashboard.panels))
    panels_json: list[dict] = []
    pid = 1
    y = 0
    for sec in sections:
        if sec["label"]:
            panels_json.append(_compile_row(sec["label"], y, pid))
            pid += 1
            y += 1
        for row in sec["rows"]:
            x = 0
            height = row[0][2]
            for panel, w, h in row:
                panels_json.append(
                    _compile_panel(panel, pid, {"x": x, "y": y, "w": w, "h": h})
                )
                pid += 1
                x += w
            y += height

    compiled = {
        "uid": dashboard.uid,
        "title": dashboard.title,
        "tags": _compile_tags(dashboard.tags),
        "schemaVersion": settings.GRAFANA_SCHEMA_VERSION,
        "timezone": "",
        "editable": True,
        "refresh": dashboard.refresh,
        "time": {"from": dashboard.time_from, "to": dashboard.time_to},
        "templating": {"list": [_compile_variable(v) for v in dashboard.variables]},
        "panels": panels_json,
        "version": 0,
        # NOTE: no `id` key — Grafana assigns it on overwrite-by-UID.
        **DASHBOARD_STYLE,
    }
    return compiled


# --- human-readable layout sketch (for the approval step) ----------------

def _unit_tag(unit: str) -> str:
    if unit in ("currencyUSD", "currencyEUR"):
        return " $"
    if unit in ("percent", "percentunit"):
        return " %"
    return ""


def build_layout_sketch(dashboard: GrafanaDashboardDef) -> str:
    """An ASCII sketch of the dashboard layout + the metrics each card shows.

    Presented to the user for approval before publishing — widths are drawn
    proportional to the actual 24-column grid, so the sketch matches the
    published result.
    """
    sections = plan_sections(dashboard.panels, _use_sections(dashboard.panels))
    out: list[str] = [
        f"Dashboard: {dashboard.title}",
        f"  window {dashboard.time_from} -> {dashboard.time_to} | "
        f"refresh {dashboard.refresh} | {len(dashboard.panels)} cards",
        "",
    ]
    for sec in sections:
        out.append(f"=== {sec['label'] or 'Panels'} ===")
        for row in sec["rows"]:
            segments = []
            for panel, w, _h in row:
                label = f"{panel.title} [{panel.effective_viz}{_unit_tag(panel.unit)}]"
                box = max(w * 4, len(label) + 3)
                segments.append("| " + label.ljust(box - 3) + "|")
            out.append("  " + "".join(segments))
        out.append("")

    out.append("Metrics:")
    for p in dashboard.panels:
        sql = " ".join(p.sql_query.split())
        if len(sql) > 90:
            sql = sql[:87] + "..."
        out.append(f"  - {p.title} ({p.role}/{p.effective_viz}, {p.unit}): {sql}")
    out.append("")
    out.append(
        "Approve to publish, or tell me what to change "
        "(metrics, order, units, viz, sections)."
    )
    return "\n".join(out)
