"""Visualization standards for AI-generated Grafana dashboards.

Single source of truth for the panel catalog, data-shape vocabulary, the
role x viz x shape compatibility matrix, layout policy, palette, and unit
allowlist. Pure constants — no I/O, no imports from other cerebro modules —
so the compiler and the tests can import it freely.

Design intent: the LLM declares *role* (the audience narrative slot) plus an
optional *viz* (the geometry) and a required *data_shape* (what the SQL
returns). Role gives non-technical readers a stable KPI-first narrative; viz
+ data_shape give the data the right geometry. The compiler turns these into
fully-styled Grafana panel JSON so the LLM never has to learn panel quirks.
"""
from __future__ import annotations

from typing import Literal

PanelRole = Literal["kpi", "trend", "breakdown", "detail"]

DataShape = Literal[
    "single_value",
    "single_value_bounded",
    "time_series_single",
    "time_series_multi",
    "category_value",
    "category_value_multi",
    "share_of_total",
    "distribution_1d",
    "distribution_2d",
    "category_state_over_time",
    "tabular",
]

Viz = Literal[
    "stat",
    "gauge",
    "bargauge",
    "timeseries_line",
    "timeseries_area",
    "timeseries_bar",
    "barchart_vertical",
    "barchart_horizontal",
    "piechart",
    "histogram",
    "heatmap",
    "state_timeline",
    "status_history",
    "table",
]

# "auto" keeps the shape-based default: multi-series shapes stack, single-series
# do not. Explicit values exist because stacking is a SEMANTIC choice the shape
# alone cannot decide: cumulative or mixed-measure series double-count when
# stacked (a top-10/20/50 tier chart rendered a 250% bar on WL-042) and need
# "none" (grouped bars); true compositions may want "percent".
Stacking = Literal["auto", "normal", "none", "percent"]

# Viz families that draw stacked series. An explicit (non-"auto") stacking on
# any other viz would be silently ignored, so the panel model rejects it at
# parse time instead.
STACKABLE_VIZ: frozenset[str] = frozenset({
    "barchart_vertical",
    "barchart_horizontal",
    "timeseries_area",
    "timeseries_bar",
})

# Constrained unit vocabulary. Wrong unit (percent vs percentunit) is the #1
# marketing-dashboard footgun, so the schema validates against this set.
ALLOWED_UNITS: frozenset[str] = frozenset({
    "short", "none", "percent", "percentunit",
    "currencyUSD", "currencyEUR",
    "s", "ms", "dateTimeAsIso",
    "decbytes", "bytes",
    "reqps", "ops",
})

# Section ordering: KPI row first (the headline), then trends, then
# breakdowns, then detail tables. Drives the compiler's grid packing.
ROLE_ORDER: list[PanelRole] = ["kpi", "trend", "breakdown", "detail"]

# Refined Indigo/Slate palette instead of Grafana's harsh primaries.
PALETTE: dict[str, str] = {
    "good": "#10B981",     # emerald-500
    "watch": "#F59E0B",    # amber-500
    "bad": "#EF4444",      # red-500
    "neutral": "#6366F1",  # indigo-500
    "muted": "#64748B",    # slate-500
}

# Replaces Grafana's default green/yellow/red KPI threshold colors so stat
# panels read as polished rather than stoplight-y.
KPI_THRESHOLDS_REFINED: dict = {
    "mode": "absolute",
    "steps": [
        {"color": PALETTE["good"], "value": None},
        {"color": PALETTE["watch"], "value": 70},
        {"color": PALETTE["bad"], "value": 90},
    ],
}

# Map the schema's threshold color names onto the refined palette so a
# `ThresholdStep(color="green")` renders as emerald, not Grafana green.
THRESHOLD_COLOR_MAP: dict[str, str] = {
    "green": PALETTE["good"],
    "yellow": PALETTE["watch"],
    "orange": PALETTE["watch"],
    "red": PALETTE["bad"],
    "blue": PALETTE["neutral"],
    "purple": "#A855F7",
}

# Dashboard-level overrides applied in the compiler.
DASHBOARD_STYLE: dict = {
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,   # shared crosshair across panels
    "liveNow": False,
    "weekStart": "",
    "style": "dark",
}

# --- Panel catalog -------------------------------------------------------
# Each viz pins its Grafana panel type, the ClickHouse target `format`
# ("time_series" | "table"), default size (24-col grid), and the data shapes
# it accepts. The compiler reads this table instead of branching on role.

PANEL_CATALOG: dict[str, dict] = {
    "stat": {
        "grafana_type": "stat",
        "format": "table",
        "width": 6, "height": 4,
        "accepts": {"single_value", "single_value_bounded"},
    },
    "gauge": {
        "grafana_type": "gauge",
        "format": "table",
        "width": 6, "height": 6,
        "accepts": {"single_value_bounded"},
    },
    "bargauge": {
        "grafana_type": "bargauge",
        "format": "table",
        "width": 8, "height": 6,
        "accepts": {"category_value", "single_value_bounded"},
    },
    "timeseries_line": {
        "grafana_type": "timeseries",
        "format": "time_series",
        "width": 12, "height": 8,
        "accepts": {"time_series_single", "time_series_multi"},
    },
    "timeseries_area": {
        "grafana_type": "timeseries",
        "format": "time_series",
        "width": 12, "height": 8,
        "accepts": {"time_series_single", "time_series_multi"},
    },
    "timeseries_bar": {
        "grafana_type": "timeseries",
        "format": "time_series",
        "width": 12, "height": 8,
        "accepts": {"time_series_single", "time_series_multi"},
    },
    "barchart_vertical": {
        "grafana_type": "barchart",
        "format": "table",
        "width": 12, "height": 8,
        "accepts": {"category_value", "category_value_multi"},
    },
    "barchart_horizontal": {
        "grafana_type": "barchart",
        "format": "table",
        "width": 12, "height": 10,
        "accepts": {"category_value", "category_value_multi"},
    },
    "piechart": {
        "grafana_type": "piechart",
        "format": "table",
        "width": 8, "height": 8,
        "accepts": {"share_of_total"},
    },
    "histogram": {
        "grafana_type": "histogram",
        "format": "table",
        "width": 12, "height": 8,
        "accepts": {"distribution_1d"},
    },
    "heatmap": {
        "grafana_type": "heatmap",
        "format": "time_series",
        "width": 12, "height": 8,
        "accepts": {"distribution_2d", "time_series_multi"},
    },
    "state_timeline": {
        "grafana_type": "state-timeline",
        "format": "time_series",
        "width": 24, "height": 6,
        "accepts": {"category_state_over_time"},
    },
    "status_history": {
        "grafana_type": "status-history",
        "format": "time_series",
        "width": 24, "height": 4,
        "accepts": {"category_state_over_time"},
    },
    "table": {
        "grafana_type": "table",
        "format": "table",
        "width": 24, "height": 10,
        "accepts": {"tabular", "category_value", "category_value_multi"},
    },
}

# Role -> allowed viz set. Compiler rejects a viz outside its role's set.
ROLE_ALLOWED_VIZ: dict[str, set[str]] = {
    "kpi": {"stat", "gauge", "bargauge"},
    "trend": {
        "timeseries_line", "timeseries_area", "timeseries_bar",
        "state_timeline", "status_history",
    },
    "breakdown": {
        "barchart_vertical", "barchart_horizontal",
        "piechart", "heatmap", "histogram",
    },
    "detail": {"table"},
}

ROLE_DEFAULT_VIZ: dict[str, str] = {
    "kpi": "stat",
    "trend": "timeseries_line",
    "breakdown": "barchart_vertical",
    "detail": "table",
}

# Convenience: viz -> accepted shapes (mirrors PANEL_CATALOG[viz]["accepts"]).
VIZ_ACCEPTS_SHAPE: dict[str, set[str]] = {
    viz: set(spec["accepts"]) for viz, spec in PANEL_CATALOG.items()
}

# --- Long-format pivot contract ------------------------------------------
# Every ClickHouse target ships as TABLE format (see compiler._FORMAT_TABLE),
# so the panel itself never pivots long-format rows into series: a
# (time, label, value) result draws as one garbled series while the SQL-level
# gates (validate/verify) still report healthy row counts. The compiler
# therefore auto-appends the pivot transformation for these (viz, data_shape)
# pairs. The transformation references columns BY NAME, so the SQL must
# expose exactly these aliases — enforced at parse time in models.py unless
# the panel supplies its own `transformations` (which always win).
# Lesson: grafana-table-format-needs-pivot-transform.

AUTO_TRANSFORM_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("timeseries_line", "time_series_multi"): ("label",),
    ("timeseries_area", "time_series_multi"): ("label",),
    ("timeseries_bar", "time_series_multi"): ("label",),
    ("barchart_vertical", "category_value_multi"): ("category", "series", "value"),
    ("barchart_horizontal", "category_value_multi"): ("category", "series", "value"),
    ("heatmap", "distribution_2d"): ("x", "y", "value"),
}

# Max pie slices before the persona should switch to barchart_horizontal.
MAX_PIE_SLICES = 6


def grafana_type(viz: str) -> str:
    return PANEL_CATALOG[viz]["grafana_type"]


def target_format(viz: str) -> str:
    return PANEL_CATALOG[viz]["format"]


def default_size(viz: str) -> tuple[int, int]:
    spec = PANEL_CATALOG[viz]
    return spec["width"], spec["height"]


def bounds_for_unit(unit: str) -> tuple[float, float] | None:
    """Return (min, max) implied by a unit, or None if unbounded.

    Used to auto-derive gauge min/max so the LLM rarely has to supply them.
    """
    if unit == "percentunit":
        return (0.0, 1.0)
    if unit == "percent":
        return (0.0, 100.0)
    return None
