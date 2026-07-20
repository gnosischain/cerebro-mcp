"""Metric Lab mini app — bounded ad-hoc analysis.

The user (or model) hands Metric Lab either a raw SQL query or a semantic
metric request. The launcher loads a *bounded* dataset using the shared
sampler in ``mini_apps.load_bounded_dataset`` and returns a lightweight
``MiniAppPayload`` describing the schema, the first page of rows, and the
sampling stats. The React frontend at ``ui://cerebro/metric_lab`` then
hydrates the rest via the app-only ``get_mini_app_rows`` tool and lets the
user reshape the chart client-side.

The model can also drive chart configuration through
``update_metric_lab_chart``.
"""

from __future__ import annotations

import importlib.resources
import logging
import re
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.mini_apps import MiniAppQueryError

logger = logging.getLogger(__name__)


METRIC_LAB_APP_ID = "metric_lab"
METRIC_LAB_URI = "ui://cerebro/metric_lab"


ALLOWED_CHART_TYPES = {
    "table",
    "line",
    "bar",
    "scatter",
    "heatmap",
    "pie",
    "numberDisplay",
}
ALLOWED_AGGREGATIONS = {"count", "sum", "avg", "min", "max", "median"}

# --- Chart-grid panel contract ---
# `view_state.charts` (a list of panel dicts, camelCase — same schema the
# frontend uses) is the source of truth; `view_state.chart` is only the
# LEGACY SCALAR PROJECTION of charts[0] (xField/yField/chartType/
# aggregation/groupBy) kept for older consumers and the model-facing
# update_metric_lab_chart default.
MAX_CHART_PANELS = 12
MAX_Y_FIELDS = 8
_MAX_PANEL_TITLE = 200
_PANEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_PANEL_SORT_DIRS = {"", "asc", "desc"}
_LEGACY_CHART_KEYS = ("xField", "yField", "chartType", "aggregation", "groupBy")


def _normalize_panel_yfields(panel: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a panel/chart config: yFields authoritative (deduped,
    capped), yField/y2Field re-derived as its mirrors."""
    y_fields_raw = [c for c in (panel.get("yFields") or []) if c]
    if not y_fields_raw:
        y_fields_raw = [
            c
            for c in (panel.get("yField") or "", panel.get("y2Field") or "")
            if c
        ]
    y_fields: list[str] = []
    for c in y_fields_raw:
        if c not in y_fields:
            y_fields.append(c)
    y_fields = y_fields[:MAX_Y_FIELDS]
    out = dict(panel)
    out["yFields"] = y_fields
    out["yField"] = y_fields[0] if y_fields else ""
    if len(y_fields) > 1:
        out["y2Field"] = y_fields[1]
    else:
        out.pop("y2Field", None)
    return out


def _legacy_chart_projection(panel: dict[str, Any]) -> dict[str, Any]:
    return {
        "xField": panel.get("xField", ""),
        "yField": panel.get("yField", ""),
        "chartType": panel.get("chartType", "table"),
        "aggregation": panel.get("aggregation", "sum"),
        "groupBy": panel.get("groupBy", ""),
    }


def _validate_panels(
    record: "mini_apps.ViewRecord", charts: list[Any]
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate + canonicalize a full chart-panel grid.

    Returns ``(canonical_panels, None)`` or ``([], error)``. Fields are
    validated against the panel's OWN dataset (``datasetKey``); repair is a
    client-side concern — the server rejects with specifics.
    """
    if not isinstance(charts, list) or not charts:
        return [], "charts must be a non-empty list of panel objects."
    if len(charts) > MAX_CHART_PANELS:
        return [], f"At most {MAX_CHART_PANELS} chart panels per view."
    canonical: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(charts):
        if not isinstance(raw, dict):
            return [], f"charts[{i}] must be an object."
        panel_id = str(raw.get("id") or "")
        if not _PANEL_ID_RE.match(panel_id):
            return [], (
                f"charts[{i}].id '{panel_id}' is invalid — expected "
                "1-16 chars of [A-Za-z0-9_-]."
            )
        if panel_id in seen_ids:
            return [], f"Duplicate panel id '{panel_id}'."
        seen_ids.add(panel_id)
        dataset_key = str(raw.get("datasetKey") or "primary")
        dataset = record.datasets.get(dataset_key)
        if dataset is None:
            return [], (
                f"charts[{i}].datasetKey '{dataset_key}' does not reference "
                f"an attached dataset. Have: {sorted(record.datasets)}"
            )
        chart_type = str(raw.get("chartType") or "table")
        if chart_type not in ALLOWED_CHART_TYPES:
            return [], (
                f"charts[{i}].chartType must be one of "
                f"{sorted(ALLOWED_CHART_TYPES)}"
            )
        if dataset.stats.mode == "preview_only" and chart_type != "table":
            return [], (
                f"Dataset '{dataset_key}' is preview_only — panel "
                f"'{panel_id}' must use chartType='table'."
            )
        aggregation = str(raw.get("aggregation") or "sum")
        if aggregation not in ALLOWED_AGGREGATIONS:
            return [], (
                f"charts[{i}].aggregation must be one of "
                f"{sorted(ALLOWED_AGGREGATIONS)}"
            )
        title = str(raw.get("title") or "")
        if len(title) > _MAX_PANEL_TITLE:
            return [], f"charts[{i}].title exceeds {_MAX_PANEL_TITLE} chars."
        sort_dir = str(raw.get("sortDir") or "")
        if sort_dir not in _PANEL_SORT_DIRS:
            return [], f"charts[{i}].sortDir must be 'asc' or 'desc'."
        panel = _normalize_panel_yfields(raw)
        valid_columns = set(dataset.columns)
        field_checks = [
            ("xField", panel.get("xField") or ""),
            ("groupBy", panel.get("groupBy") or ""),
            ("colorBy", panel.get("colorBy") or ""),
        ] + [("yFields", c) for c in panel.get("yFields") or []]
        for field_name, value in field_checks:
            if value and value not in valid_columns:
                return [], (
                    f"charts[{i}].{field_name}='{value}' is not a column of "
                    f"dataset '{dataset_key}'. Valid: {sorted(valid_columns)}"
                )
        panel.update(
            {
                "id": panel_id,
                "datasetKey": dataset_key,
                "chartType": chart_type,
                "aggregation": aggregation,
                "groupBy": str(panel.get("groupBy") or ""),
                "xField": str(panel.get("xField") or ""),
                "trendline": bool(raw.get("trendline", False)),
            }
        )
        if title:
            panel["title"] = title
        else:
            panel.pop("title", None)
        if sort_dir:
            panel["sortDir"] = sort_dir
        else:
            panel.pop("sortDir", None)
        canonical.append(panel)
    return canonical, None


# Soft cap on how many metrics ship in the initial catalog payload.
# Keeps the launch payload light; the user can search to find more.
METRIC_CATALOG_LIMIT = 200
# Hard ceiling for a single catalog page (search_metric_catalog paging).
# The full registry is ~2,100 entries / ~1.2 MB — never embed it whole.
METRIC_CATALOG_MAX_LIMIT = 500


# --- Bundled React UI ---

_BUNDLED_METRIC_LAB_HTML: str | None = None


def get_metric_lab_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_METRIC_LAB_HTML
    if _BUNDLED_METRIC_LAB_HTML is None:
        try:
            _BUNDLED_METRIC_LAB_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/metric_lab.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_METRIC_LAB_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>metric_lab.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_METRIC_LAB_HTML


# ---------------------------------------------------------------------------
# Schema heuristics
# ---------------------------------------------------------------------------


_TEMPORAL_TYPE_HINTS = ("date", "time", "timestamp", "datetime")
_NUMERIC_TYPE_HINTS = (
    "int",
    "float",
    "decimal",
    "double",
    "uint",
    "number",
    "long",
)


def _looks_temporal(name: str, type_name: str) -> bool:
    name_l = name.lower()
    type_l = type_name.lower()
    return any(h in type_l for h in _TEMPORAL_TYPE_HINTS) or any(
        token in name_l for token in ("date", "day", "week", "month", "time")
    )


def _looks_numeric(type_name: str) -> bool:
    type_l = type_name.lower()
    return any(h in type_l for h in _NUMERIC_TYPE_HINTS)


def _infer_default_chart(dataset: CachedDataset) -> dict[str, Any]:
    """Pick sensible defaults for x_field/y_field/chart_type from the schema."""
    if not dataset.columns:
        return {
            "xField": "",
            "yField": "",
            "chartType": "table",
            "aggregation": "sum",
            "groupBy": "",
        }

    x_field = dataset.columns[0]
    for idx, name in enumerate(dataset.columns):
        type_name = (
            dataset.column_types[idx]
            if idx < len(dataset.column_types)
            else "Unknown"
        )
        if _looks_temporal(name, type_name):
            x_field = name
            break

    y_field = ""
    for idx, name in enumerate(dataset.columns):
        if name == x_field:
            continue
        type_name = (
            dataset.column_types[idx]
            if idx < len(dataset.column_types)
            else "Unknown"
        )
        if _looks_numeric(type_name):
            y_field = name
            break

    chart_type: str
    if dataset.stats.mode == "preview_only":
        chart_type = "table"
    elif y_field and x_field:
        chart_type = "line" if any(
            _looks_temporal(x_field, t) for t in (
                dataset.column_types[dataset.columns.index(x_field)]
                if dataset.columns.index(x_field) < len(dataset.column_types)
                else "Unknown",
            )
        ) else "bar"
    else:
        chart_type = "table"

    return {
        "xField": x_field,
        "yField": y_field,
        "chartType": chart_type,
        "aggregation": "sum",
        "groupBy": "",
    }


def _build_summary_cards(dataset: CachedDataset) -> list[SummaryCard]:
    mode_label = {
        "exact_bounded": "Exact",
        "random_sample": "Random sample",
        "preview_only": "Preview only",
    }[dataset.stats.mode]

    cards: list[SummaryCard] = [
        SummaryCard(
            label="Rows loaded",
            value=f"{dataset.stats.row_count:,}",
            tone="neutral",
        ),
        SummaryCard(
            label="Source rows",
            value=(
                f"{dataset.stats.sample_source_rows:,}"
                if dataset.stats.sample_source_rows is not None
                else "—"
            ),
            tone="neutral",
        ),
        SummaryCard(
            label="Mode",
            value=mode_label,
            tone="warning"
            if dataset.stats.mode != "exact_bounded"
            else "positive",
        ),
        SummaryCard(
            label="Columns",
            value=str(len(dataset.columns)),
            tone="neutral",
        ),
    ]
    return cards


def _build_empty_payload(
    *,
    view_id: str,
    title: str,
    catalog: list[dict[str, Any]],
    query: str,
    catalog_total: int | None = None,
    catalog_facets: dict[str, Any] | None = None,
) -> MiniAppPayload:
    """Catalog-driven launch payload with no attached dataset.

    Used by the zero-arg ``open_metric_lab`` so the user lands in the app
    and picks a metric from a dropdown before any ClickHouse call runs.
    """
    total = catalog_total if catalog_total is not None else len(catalog)
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=METRIC_LAB_APP_ID,
        title=title,
        status="ready",
        summary_cards=[
            SummaryCard(
                label="Models available",
                value=f"{total:,}",
                tone="neutral",
            ),
            SummaryCard(
                label="Status",
                value="Pick a model",
                tone="neutral",
            ),
        ],
        datasets={},
        view_state={
            "mode": "empty",
            "metric_catalog": catalog,
            "catalog_query": query,
            "catalog_total": total,
            "catalog_facets": catalog_facets or {},
            "catalog_filters": {},
            "selected_metric": "",
            "selected_metrics": [],
            "selected_dimensions": [],
            "selected_limit": 2000,
            "selected_order_by": [],
            "chart": {
                "xField": "",
                "yField": "",
                "chartType": "table",
                "aggregation": "sum",
                "groupBy": "",
            },
            "analytics_disabled": True,
            "estimates": False,
            "dataset_mode": None,
            "sample_source_rows": None,
            "metric_fields": [],
            "chart_suggestions": [],
            "unvalidated_metrics": [],
        },
        provenance={
            "source": "catalog",
            "catalog_size": len(catalog),
            "catalog_total": total,
            "catalog_query": query,
        },
        warnings=[],
    )


def _build_initial_payload(
    *,
    view_id: str,
    title: str,
    dataset: CachedDataset,
    provenance: dict[str, Any],
    catalog: list[dict[str, Any]] | None = None,
    selected_metric: str = "",
    selected_metrics: list[str] | None = None,
    selected_dimensions: list[str] | None = None,
    selected_limit: int = 2000,
    selected_order_by: list[str] | None = None,
    catalog_total: int | None = None,
    catalog_facets: dict[str, Any] | None = None,
    unvalidated_metrics: list[str] | None = None,
    load_mode: str = "raw",
    aggregate_config: dict[str, Any] | None = None,
    raw_config: dict[str, Any] | None = None,
    datasets: dict[str, CachedDataset] | None = None,
    dataset_titles: dict[str, str] | None = None,
) -> MiniAppPayload:
    chart_defaults = _infer_default_chart(dataset)
    if load_mode == "aggregate" and aggregate_config:
        # The aggregated output IS the plot shape: x, [series,] agg_y1..N.
        agg = aggregate_config.get("agg") or "sum"
        x_col = aggregate_config.get("x") or chart_defaults["xField"]
        if agg == "count":
            wanted = ["row_count"]
        else:
            measures = [c for c in (aggregate_config.get("ys") or []) if c]
            if not measures and aggregate_config.get("y"):
                measures = [aggregate_config["y"]]
            wanted = [f"{agg}_{c}" for c in measures]
        y_fields = [f for f in wanted if f in dataset.columns]
        if not y_fields:
            # Fall back to output-shape inference: everything that is not
            # x/series is an aggregated measure.
            agg_cols = [
                c
                for c in dataset.columns
                if c != x_col and c != aggregate_config.get("series")
            ]
            y_fields = agg_cols[-1:] if agg_cols else [chart_defaults["yField"]]
        x_idx = dataset.columns.index(x_col) if x_col in dataset.columns else -1
        x_type = (
            dataset.column_types[x_idx]
            if 0 <= x_idx < len(dataset.column_types)
            else "Unknown"
        )
        chart_defaults = {
            "xField": x_col,
            "yField": y_fields[0] if y_fields else "",
            "yFields": y_fields,
            "chartType": "line" if _looks_temporal(x_col, x_type) else "bar",
            "aggregation": "sum",
            "groupBy": aggregate_config.get("series") or "",
        }
        if len(y_fields) > 1:
            chart_defaults["y2Field"] = y_fields[1]
    metrics_list = selected_metrics or ([selected_metric] if selected_metric else [])

    # Metric-named dataset columns: the semantic compiler AND the N-model
    # join both alias each output column to its metric/model name, so this
    # intersection reliably identifies the value columns of a joined wide
    # table. Empty for raw-SQL / plain model loads.
    metric_name_set = set(metrics_list)
    metric_fields = [c for c in dataset.columns if c in metric_name_set]

    if load_mode == "join":
        # N-model wide table: x is always the grain-bucketed "date" column;
        # EVERY model alias is a plotted value field. The aggregate branch
        # above does NOT cover this shape.
        y_fields = metric_fields or dataset.columns[1:2]
        chart_defaults = {
            "xField": "date",
            "yField": y_fields[0] if y_fields else "",
            "yFields": y_fields,
            "chartType": "line",
            "aggregation": "sum",
            "groupBy": "",
        }
        if len(y_fields) > 1:
            chart_defaults["y2Field"] = y_fields[1]

    # Correlation affordance: with 2+ aligned metric columns, offer a
    # one-click metric-vs-metric scatter. The DEFAULT chart stays line/bar
    # (time series users expect trends); the frontend renders this as a
    # "Correlate" suggestion chip instead of surprising anyone.
    chart_suggestions: list[dict[str, Any]] = []
    if len(metric_fields) >= 2 and dataset.stats.mode != "preview_only":
        chart_suggestions.append(
            {
                "chartType": "scatter",
                "xField": metric_fields[0],
                "yField": metric_fields[1],
                "reason": "correlation",
            }
        )

    view_state: dict[str, Any] = {
        "mode": "loaded",
        "metric_catalog": catalog or [],
        "catalog_total": catalog_total if catalog_total is not None else len(catalog or []),
        "catalog_facets": catalog_facets or {},
        "catalog_filters": {},
        "selected_metric": selected_metric,
        "selected_metrics": metrics_list,
        "selected_dimensions": selected_dimensions or [],
        "selected_limit": selected_limit,
        "selected_order_by": selected_order_by or [],
        # charts[] (full panel schema) is the source of truth; chart is the
        # legacy scalar projection of charts[0].
        "charts": [
            _normalize_panel_yfields(
                {**chart_defaults, "id": "c1", "datasetKey": "primary"}
            )
        ],
        "chart": _legacy_chart_projection(chart_defaults),
        "sort": {"field": chart_defaults["xField"], "direction": "desc"},
        "filters": [],
        "analytics_disabled": dataset.stats.mode == "preview_only",
        "estimates": dataset.stats.mode == "random_sample",
        "dataset_mode": dataset.stats.mode,
        "sample_source_rows": dataset.stats.sample_source_rows,
        "metric_fields": metric_fields,
        "chart_suggestions": chart_suggestions,
        "unvalidated_metrics": unvalidated_metrics or [],
        "load_mode": load_mode,
        "aggregate_config": aggregate_config or {},
        "raw_config": raw_config or {},
    }
    record = mini_apps.get_view(view_id)
    view_state["dataset_revisions"] = (
        dict(record.dataset_revisions) if record else {}
    )
    view_state["allowed_databases"] = list(settings.ALLOWED_DATABASES)
    all_datasets = datasets or {"primary": dataset}
    titles = dataset_titles or {}
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key,
            dataset=ds,
            title=titles.get(
                key, "Primary dataset" if key == "primary" else key
            ),
        )
        for key, ds in all_datasets.items()
    }
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=METRIC_LAB_APP_ID,
        title=title,
        status="ready",
        summary_cards=_build_summary_cards(dataset),
        datasets=descriptors,
        view_state=view_state,
        provenance=provenance,
        warnings=list(dataset.stats.warnings),
    )


# ---------------------------------------------------------------------------
# Semantic compiler bridge
# ---------------------------------------------------------------------------


def compile_metric_query_sql(
    metrics: list[str],
    dimensions: list[str] | None,
    filters: list[dict[str, Any]] | None,
    order_by: list[str] | None,
    limit: int,
) -> tuple[str, str]:
    """Plan + compile a semantic metric query into SQL without executing it.

    Returns ``(sql, database)``. Raises ``ValueError`` with a user-facing
    message on planner / compiler errors.
    """
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.semantic.planner import PlanningError, plan_metric_query
    from cerebro_mcp.semantic.sql_compiler import compile_metric_plan

    snapshot = semantic_runtime.snapshot
    if snapshot is None or not semantic_runtime.is_execution_available:
        raise ValueError("Semantic execution unavailable.")

    try:
        plan = plan_metric_query(
            snapshot,
            requested_metrics=metrics,
            requested_dimensions=dimensions or [],
            filters=filters,
            agent_role="metric_lab",
            # Metric Lab is an exploration surface: candidate-tier metrics
            # run behind an "unvalidated" UI warning (same opt-in
            # `query_metrics` exposes as allow_candidate=true). Truly
            # broken candidates (unapproved root model / no dimensions)
            # are still rejected by the planner.
            allow_candidate=True,
        )
    except PlanningError as exc:
        raise ValueError(f"Semantic planning failed: {exc}") from exc

    plan["limit"] = limit
    plan["order_by"] = order_by or []
    compiler_options = plan.get("compiler_options", {})
    sql, _warnings = compile_metric_plan(
        snapshot,
        plan,
        force_qualified=compiler_options.get("force_qualified", False),
    )
    return sql, "dbt"


def _unvalidated_metric_names(metric_names: list[str]) -> list[str]:
    """Subset of ``metric_names`` that are candidate-tier (not approved).

    Drives the frontend's "unvalidated metric — treat as estimate" banner
    when candidate metrics run via the ``allow_candidate`` opt-in.
    """
    from cerebro_mcp.loaders.semantic import semantic_runtime

    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return []
    out: list[str] = []
    for name in metric_names:
        metric = snapshot.metrics.get(name)
        if metric is not None and metric.get("quality_tier") != "approved":
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Metric catalog (reused by the UI picker)
# ---------------------------------------------------------------------------


_TIME_COLUMN_HINTS = frozenset(
    {"date", "day", "week", "month", "ts", "timestamp", "block_date"}
)

_TIME_TYPES = ("Date", "Date32", "DateTime", "DateTime64")


def _unwrap_ch_type(ch_type: str) -> str:
    """Strip Nullable(...) / LowCardinality(...) wrappers."""
    t = ch_type or ""
    while t.startswith(("Nullable(", "LowCardinality(")) and t.endswith(")"):
        t = t[t.index("(") + 1 : -1]
    return t


def _is_time_type(ch_type: str) -> bool:
    return _unwrap_ch_type(ch_type).startswith(_TIME_TYPES)


def _model_column_items(model_def: dict[str, Any]) -> list[tuple[str, str]]:
    """(name, data_type) pairs from a registry model def (dict or list shape)."""
    cols = model_def.get("columns") or {}
    if isinstance(cols, dict):
        return [
            (n, str(v.get("data_type", "")) if isinstance(v, dict) else "")
            for n, v in cols.items()
        ]
    return [
        (c.get("name", ""), str(c.get("data_type", "")))
        for c in cols
        if isinstance(c, dict)
    ]


def _column_type(model_def: dict[str, Any], name: str) -> str:
    return dict(_model_column_items(model_def)).get(name, "")


def _time_column(model_def: dict[str, Any]) -> str:
    """Best date/time column of a model.

    Priority: typed AND name-hinted > any time-typed column > untyped name
    hint. Type-aware so columns like ``block_timestamp DateTime64`` are found
    even when their names miss ``_TIME_COLUMN_HINTS``.
    """
    items = _model_column_items(model_def)
    typed = [n for n, t in items if _is_time_type(t)]
    hinted = [n for n, _ in items if n.lower() in _TIME_COLUMN_HINTS]
    for name in hinted:
        if name in typed:
            return name
    if typed:
        return typed[0]
    return hinted[0] if hinted else ""


def _entry_is_timeseries(entry: dict[str, Any]) -> bool:
    """True when the entry can plot over time: a metric with time grains or a
    time-like dimension, or a model with a date-like column."""
    if entry.get("supported_time_grains"):
        return True
    for dim in entry.get("allowed_dimensions") or []:
        if str(dim).lower() in _TIME_COLUMN_HINTS:
            return True
    for col in entry.get("columns") or []:
        if str(col.get("name", "")).lower() in _TIME_COLUMN_HINTS:
            return True
    return False


_LAYER_PREFIXES = ("api", "fct", "int", "stg")


def _model_layer(name: str) -> str:
    """dbt layer from the name prefix; everything else (raw sources like
    ``consensus.attestations``) is ``source``."""
    for p in _LAYER_PREFIXES:
        if name.startswith(p + "_"):
            return p
    return "source"


def _model_relation(model: dict[str, Any], name: str) -> str:
    """Qualified FROM target. ``relation_name`` is authoritative — source
    models are NOT under the ``dbt`` database (e.g. `consensus`.`attestations`),
    so a hardcoded ``dbt.`` prefix would break them."""
    return model.get("relation_name") or f"`dbt`.`{name}`"


_BASE_CATALOG_CACHE: dict[str, list[dict[str, Any]]] = {}


def _base_catalog_entries(snapshot) -> list[dict[str, Any]]:
    """Score-free catalog entries for every model, cached per registry_hash
    (single entry) — the per-call cost of the catalog becomes score/filter/
    facet only instead of a full O(N) rebuild."""
    key = getattr(snapshot, "registry_hash", "") or f"id:{id(snapshot)}"
    cached = _BASE_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    entries: list[dict[str, Any]] = []
    for model_name, model in snapshot.models.items():
        cols_field = model.get("columns") or {}
        cols: list[dict[str, str]] = []
        if isinstance(cols_field, dict):
            for col_name, col_def in cols_field.items():
                cols.append({
                    "name": col_name,
                    "type": (col_def or {}).get("data_type", ""),
                })
        elif isinstance(cols_field, list):
            for col in cols_field:
                if isinstance(col, dict):
                    cols.append({"name": col.get("name", ""), "type": col.get("data_type", "")})

        model_layer = _model_layer(model_name)
        # Sector = dbt module (present across all layers); name-split fallback
        # for the api_<sector>_<subsector>_* convention.
        parts = model_name.split("_")
        model_sector = model.get("module") or (
            parts[1] if model_layer == "api" and len(parts) > 1 else "other"
        )
        subsector = parts[2] if model_layer == "api" and len(parts) > 2 else ""

        entries.append({
            "kind": "model",
            "name": model_name,
            # EXACT dbt/DB name — invented display names only confuse.
            "label": model_name,
            "description": (model.get("description") or "")[:280],
            "module": model.get("module") or "",
            "sector": model_sector,
            "subsector": subsector,
            "layer": model_layer,
            "materialized": model.get("materialized") or "",
            "relation_name": _model_relation(model, model_name),
            "root_model": model_name,
            "quality_tier": "",
            "unit": "",
            "tags": list(model.get("tags") or []),
            "allowed_dimensions": [],
            "default_dimensions": [],
            "supported_time_grains": [],
            "executable": True,
            "columns": cols,
        })

    _BASE_CATALOG_CACHE.clear()
    _BASE_CATALOG_CACHE[key] = entries
    return entries


def build_metric_catalog(
    query: str = "",
    sector: str = "",
    layer: str = "",
    tag: str = "",
    timeseries: bool = False,
    limit: int = METRIC_CATALOG_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Build a filtered, paged catalog of ALL dbt models in the snapshot.

    This is a data explorer over the models that actually exist in the
    database — every ``api_`` / ``fct_`` / ``int_`` / ``stg_`` model plus raw
    sources — under their EXACT names. The semantic metric registry is NOT
    part of this catalog (its 1,500+ auto-scaffolded per-measure wrappers
    like ``avatar_count_value`` were noise, not metrics). Internal/private
    models are already stripped upstream by the loader.

    Ranking goes through the canonical ``ModelSearchIndex`` (field-weighted
    BM25 + fuzzy, shared with catalog_search/find/search_models) — the same
    query returns the same models everywhere. Matching entries additionally
    carry ``matched_columns`` when a column name matched the query.

    Returns ``{"entries": [...], "total_matching": int, "facets": {...}}``.
    ``facets`` counts sector/layer/tag over the *query-matched* set (before
    the sector/layer/tag filters are applied) so sidebar counts stay stable
    while the user toggles filters.
    """
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.semantic.search import ModelSearchIndex
    from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic

    _maybe_refresh_semantic()
    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return {"entries": [], "total_matching": 0, "facets": {}}

    limit = max(1, min(int(limit), METRIC_CATALOG_MAX_LIMIT))
    offset = max(0, int(offset))
    sector_l = sector.strip().lower()
    layer_l = layer.strip().lower()
    tag_l = tag.strip().lower()

    base = _base_catalog_entries(snapshot)

    # Query acts as a MATCH filter, ranked by the shared search backend.
    if query:
        index = ModelSearchIndex.for_snapshot(snapshot)
        hits = index.search(
            query, limit=len(index) or 1, include_column_matches=True
        )
        hit_info = {h.name: h for h in hits}
        matched = []
        for e in base:
            h = hit_info.get(e["name"])
            if h is None:
                continue
            if h.matched_columns:
                e = {**e, "matched_columns": h.matched_columns}
            matched.append((h.score, e))
    else:
        matched = [(0.0, e) for e in base]

    # Facets over the query-matched set, BEFORE sector/layer/tag filters —
    # sidebar counts stay stable while the user toggles filters.
    facets: dict[str, dict[str, int]] = {"sector": {}, "layer": {}}
    tag_counts: dict[str, int] = {}
    for _, e in matched:
        for facet_key in ("sector", "layer"):
            value = e.get(facet_key) or ""
            if value:
                facets[facet_key][value] = facets[facet_key].get(value, 0) + 1
        for t in e.get("tags") or []:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    # Tags are unbounded — ship only the top ones for the sidebar.
    facets["tag"] = dict(
        sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:24]
    )

    filtered = [
        (s, e)
        for s, e in matched
        if (not sector_l or (e.get("sector") or "").lower() == sector_l)
        and (not layer_l or e.get("layer") == layer_l)
        and (not tag_l or any(t.lower() == tag_l for t in e.get("tags") or []))
        and (not timeseries or _entry_is_timeseries(e))
    ]

    if query:
        filtered.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    else:
        # Consumer-facing layers first (api, fct), then int/stg/source.
        layer_rank = {"api": 0, "fct": 1, "int": 2, "stg": 3, "source": 4}
        filtered.sort(
            key=lambda pair: (
                layer_rank.get(pair[1].get("layer"), 5),
                pair[1]["name"],
            )
        )

    page = [entry for _, entry in filtered[offset : offset + limit]]
    return {
        "entries": page,
        "total_matching": len(filtered),
        "facets": facets,
    }


def get_metric_catalog(query: str = "", limit: int = METRIC_CATALOG_LIMIT) -> list[dict[str, Any]]:
    """Back-compat wrapper: first page of ``build_metric_catalog`` entries."""
    return build_metric_catalog(query=query, limit=limit)["entries"]


def _model_columns_list(model: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize a model's columns field (dict or list) to [{name, type}]."""
    cols_field = (model or {}).get("columns") or {}
    cols: list[dict[str, str]] = []
    if isinstance(cols_field, dict):
        for col_name, col_def in cols_field.items():
            cols.append({
                "name": col_name,
                "type": (col_def or {}).get("data_type", ""),
                "description": (col_def or {}).get("description", "") or "",
            })
    elif isinstance(cols_field, list):
        for col in cols_field:
            if isinstance(col, dict):
                cols.append({
                    "name": col.get("name", ""),
                    "type": col.get("data_type", ""),
                    "description": col.get("description", "") or "",
                })
    return cols


def get_catalog_entry_detail(name: str) -> dict[str, Any]:
    """Full detail for one model (any dbt layer or raw source).

    Detail-on-demand companion to the truncated list entries: full
    description, column schema with descriptions, tags, layer,
    materialization, and the fully-qualified relation. A registry-metric
    name redirects to its root model. Returns ``{"error": ...,
    "suggestions": [...]}`` for unknown names.
    """
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic

    _maybe_refresh_semantic()
    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return {"error": "Semantic snapshot not loaded.", "suggestions": []}

    model = snapshot.models.get(name)
    if model is not None:
        model_layer = _model_layer(name)
        parts = name.split("_")
        return {
            "kind": "model",
            "name": name,
            "label": name,
            "description": (model.get("description") or "").strip(),
            "module": model.get("module") or "",
            "sector": model.get("module") or (
                parts[1] if model_layer == "api" and len(parts) > 1 else "other"
            ),
            "subsector": parts[2] if model_layer == "api" and len(parts) > 2 else "",
            "layer": model_layer,
            "materialized": model.get("materialized") or "",
            "tags": list(model.get("tags") or []),
            "relation_name": _model_relation(model, name),
            "root_model": name,
            "quality_tier": "",
            "semantic_status": model.get("semantic_status") or "",
            "unit": "",
            "allowed_dimensions": [],
            "default_dimensions": [],
            "supported_time_grains": [],
            "executable": True,
            "columns": _model_columns_list(model),
        }

    # Registry-metric name (legacy) — point at the model that carries the data.
    metric = snapshot.metrics.get(name)
    if metric is not None:
        root = metric.get("root_model") or ""
        return {
            "error": (
                f"'{name}' is a registry metric, not a model. "
                f"The underlying model is '{root}' — open that instead."
            ),
            "suggestions": [root] if root else [],
        }

    # Unknown name — offer nearest model matches by substring.
    needle = name.lower()
    candidates = [
        n for n in snapshot.models.keys() if needle in n.lower() or n.lower() in needle
    ]
    return {
        "error": f"No model named '{name}'.",
        "suggestions": sorted(candidates)[:5],
    }


# ---------------------------------------------------------------------------
# Model load SQL + dual-table comparison helper
# ---------------------------------------------------------------------------


def _model_load_sql(
    model_def: dict[str, Any],
    model_name: str,
    limit: int,
    window_days: int = 0,
    columns: list[str] | None = None,
) -> str:
    """Bounded SELECT for one model, FROM its qualified ``relation_name``
    (sources live outside the ``dbt`` database). When a date/time column
    exists: newest-first ordering, plus an optional trailing time window —
    ClickHouse pushes the predicate into view definitions, which bounds the
    scan on heavy views.

    ``columns`` optionally projects a subset (each whitelisted against the
    registry columns, user order preserved); empty/None keeps ``SELECT *``.
    """
    date_col = _time_column(model_def) or None
    proj = "*"
    if columns:
        valid = set(_model_column_names(model_def))
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate columns in projection.")
        bad = [c for c in columns if c not in valid]
        if bad:
            raise ValueError(
                f"Not columns of {model_name}: {bad}. "
                f"Columns: {sorted(valid)}"
            )
        proj = ", ".join(f"`{c}`" for c in columns)
    relation = _model_relation(model_def, model_name)
    where_clause = (
        f" WHERE `{date_col}` >= today() - {int(window_days)}"
        if date_col and window_days > 0
        else ""
    )
    order_clause = (
        f" ORDER BY `{date_col}` DESC"
        if date_col and (not columns or date_col in columns)
        else ""
    )
    return f"SELECT {proj} FROM {relation}{where_clause}{order_clause} LIMIT {int(limit)}"


_AGGREGATE_FUNCTIONS: dict[str, str] = {
    # UI name -> ClickHouse call template ({col} = backticked column)
    "sum": "sum({col})",
    "avg": "avg({col})",
    "min": "min({col})",
    "max": "max({col})",
    "median": "quantile(0.5)({col})",
    "count": "count()",
    "uniq": "uniqExact({col})",
}
_FILTER_OPS = {"=", "!="}
_AGGREGATE_ROW_CAP = 50_000

_TIME_GRAINS: dict[str, str] = {
    # grain -> ClickHouse expr template ({col} = backticked column).
    # NOTE: "" (no bucketing) is the absence of a grain, and "raw" is a load
    # MODE — neither belongs in this map.
    "day": "toDate({col})",
    "week": "toStartOfWeek({col}, 1)",  # ISO weeks, Monday start
    "month": "toStartOfMonth({col})",
}


def _model_column_names(model_def: dict[str, Any]) -> list[str]:
    cols_field = model_def.get("columns") or {}
    if isinstance(cols_field, dict):
        return list(cols_field.keys())
    return [c.get("name", "") for c in cols_field if isinstance(c, dict)]


def _aggregate_load_sql(
    model_def: dict[str, Any],
    model_name: str,
    *,
    x: str,
    y: str,
    agg: str,
    ys: list[str] | None = None,
    grain: str = "",
    series: str = "",
    series_top_n: int = 8,
    window_days: int = 0,
    filter_col: str = "",
    filter_op: str = "=",
    filter_value: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Compile a server-side GROUP BY chart query for one model.

    Big per-entity panels (balances/transfers per address) cannot be charted
    from a LIMIT'ed raw sample — the aggregation must run in ClickHouse.
    Returns ``(sql, parameters)``.

    ``ys`` aggregates several measures with the same ``agg`` (mutually
    exclusive with ``series``); ``grain`` buckets a date/time ``x`` by
    day/week/month (empty = group by the raw column).

    Safety: every identifier is whitelisted against the model's registry
    columns and backtick-quoted; ``agg``/``grain``/``filter_op`` come from
    fixed whitelists; the only free text (``filter_value``) is passed as a
    bound ClickHouse query parameter, never interpolated.
    """
    columns = _model_column_names(model_def)
    if not columns:
        raise ValueError(
            f"Model '{model_name}' has no column metadata in the registry — "
            "load it in raw mode instead."
        )

    def _check(arg_name: str, value: str) -> None:
        if value not in columns:
            raise ValueError(
                f"{arg_name}='{value}' is not a column of {model_name}. "
                f"Columns: {sorted(columns)}"
            )

    _check("x", x)
    if agg not in _AGGREGATE_FUNCTIONS:
        raise ValueError(
            f"agg='{agg}' is not supported. One of: "
            f"{sorted(_AGGREGATE_FUNCTIONS)}"
        )

    # Measure list: ys (multi) wins over legacy single y.
    y_list = [c for c in (ys or []) if c]
    if y_list:
        if y and y not in y_list:
            raise ValueError("Pass either y or ys, not both.")
        if len(set(y_list)) != len(y_list):
            raise ValueError("Duplicate columns in ys.")
        if agg == "count":
            raise ValueError(
                "agg='count' takes no measure list — use a single y."
            )
        if series and len(y_list) > 1:
            raise ValueError(
                "series and multiple ys are mutually exclusive."
            )
    elif y:
        y_list = [y]
    if agg != "count":
        if not y_list:
            raise ValueError("y (or ys) is required for this aggregation.")
        for col in y_list:
            _check("y", col)

    if series:
        _check("series", series)
        if series == x:
            raise ValueError("series must differ from x.")
    if filter_col:
        _check("filter_col", filter_col)
        if filter_op not in _FILTER_OPS:
            raise ValueError(f"filter_op must be one of {sorted(_FILTER_OPS)}")

    relation = _model_relation(model_def, model_name)

    # Separate collections: bare measures for top-N ranking (ORDER BY cannot
    # take an `AS`-suffixed expression), aliased ones for the SELECT list.
    if agg == "count":
        measure_exprs = ["count()"]
        select_exprs = ["count() AS `row_count`"]
    else:
        measure_exprs = [
            _AGGREGATE_FUNCTIONS[agg].format(col=f"`{c}`") for c in y_list
        ]
        select_exprs = [
            f"{m} AS `{agg}_{c}`" for m, c in zip(measure_exprs, y_list)
        ]

    # Time grain: bucket x, alias back to the original column name so chart
    # defaults and downstream column validation keep working unchanged.
    x_select = f"`{x}`"
    x_group = f"`{x}`"
    if grain:
        if grain not in _TIME_GRAINS:
            raise ValueError(
                f"grain must be one of {sorted(_TIME_GRAINS)} or empty."
            )
        x_type = _column_type(model_def, x)
        if not _is_time_type(x_type) and x.lower() not in _TIME_COLUMN_HINTS:
            raise ValueError(
                f"grain requires a date/time x column; '{x}' is "
                f"'{x_type or 'unknown'}'."
            )
        grain_expr = _TIME_GRAINS[grain].format(col=f"`{x}`")
        x_select = f"{grain_expr} AS `{x}`"
        x_group = grain_expr

    # Shared WHERE pieces (window + filter) — applied to both the main query
    # and the top-N series subselect so they rank on the same slice.
    where_parts: list[str] = []
    date_col = _time_column(model_def) or None
    if window_days > 0 and date_col:
        where_parts.append(f"`{date_col}` >= today() - {int(window_days)}")
    parameters: dict[str, Any] | None = None
    if filter_col:
        where_parts.append(f"`{filter_col}` {filter_op} {{flt:String}}")
        parameters = {"flt": filter_value}
    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

    select_cols = [x_select]
    group_cols = [x_group]
    if series:
        select_cols.append(f"`{series}`")
        group_cols.append(f"`{series}`")
        # Bound series cardinality: keep only the top-N series by the same
        # aggregate over the same filtered slice.
        top_n = max(1, min(int(series_top_n), 50))
        series_where = where_sql
        top_sql = (
            f"SELECT `{series}` FROM {relation}{series_where} "
            f"GROUP BY `{series}` ORDER BY {measure_exprs[0]} DESC LIMIT {top_n}"
        )
        series_guard = f"`{series}` IN ({top_sql})"
        where_sql = (
            f"{where_sql} AND {series_guard}"
            if where_sql
            else f" WHERE {series_guard}"
        )

    sql = (
        f"SELECT {', '.join(select_cols + select_exprs)} "
        f"FROM {relation}{where_sql} "
        f"GROUP BY {', '.join(group_cols)} "
        f"ORDER BY `{x}` ASC "
        f"LIMIT {_AGGREGATE_ROW_CAP}"
    )
    return sql, parameters


def _load_dual_tables(
    ch: ClickHouseManager,
    *,
    view_id: str,
    record: "mini_apps.ViewRecord",
    tables: list[str],
    limit: int,
    window_days: int = 0,
) -> CallToolResult:
    """Load two models as primary + secondary datasets for a dual-axis chart."""
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic
    _maybe_refresh_semantic()
    snapshot = semantic_runtime.snapshot

    datasets: dict[str, "mini_apps.CachedDataset"] = {}
    keys = ["primary", "secondary"]

    for i, table_name in enumerate(tables[:2]):
        model_def = (snapshot.models.get(table_name) if snapshot else None) or {}
        sql = _model_load_sql(model_def, table_name, limit, window_days)

        try:
            ds = mini_apps.load_bounded_dataset(ch, sql, database="dbt")
        except mini_apps.MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(
                f"Failed to load {table_name}: {exc}"
            )

        datasets[keys[i]] = ds

    # Attach BOTH at once — attaching primary before secondary succeeds
    # would leave partial view state when the second load fails.
    mini_apps.replace_view_datasets(view_id, datasets)

    # Build payload with both datasets.
    primary_ds = datasets["primary"]
    chart_defaults = _infer_default_chart(primary_ds)
    catalog_query = record.view_state.get("catalog_query", "") if record.view_state else ""
    catalog = record.view_state.get("metric_catalog") or get_metric_catalog(query=catalog_query)

    descriptors = {}
    for key, ds in datasets.items():
        descriptors[key] = mini_apps.build_dataset_descriptor(
            key=key, dataset=ds, title=tables[keys.index(key)]
        )

    # One grid panel per table (primary + secondary), each with that
    # dataset's own inferred defaults.
    panels = [
        _normalize_panel_yfields(
            {
                **_infer_default_chart(ds),
                "id": f"c{i + 1}",
                "datasetKey": key,
                "title": tables[keys.index(key)],
            }
        )
        for i, (key, ds) in enumerate(datasets.items())
    ]

    view_state: dict[str, Any] = {
        "mode": "loaded",
        "metric_catalog": catalog,
        "catalog_total": record.view_state.get("catalog_total", len(catalog)) if record.view_state else len(catalog),
        "catalog_facets": record.view_state.get("catalog_facets", {}) if record.view_state else {},
        "catalog_filters": {},
        "selected_metric": tables[0],
        "selected_metrics": tables,
        "selected_dimensions": [],
        "selected_limit": limit,
        "selected_order_by": [],
        "charts": panels,
        "chart": _legacy_chart_projection(panels[0] if panels else chart_defaults),
        "sort": {"field": chart_defaults["xField"], "direction": "desc"},
        "filters": [],
        "analytics_disabled": False,
        "estimates": False,
        "dataset_mode": primary_ds.stats.mode,
        "sample_source_rows": primary_ds.stats.sample_source_rows,
        # Dual api_* datasets are separate tables, not a joined wide table —
        # cross-dataset correlation is a frontend concern over the two
        # descriptors, so no metric_fields/suggestions here.
        "metric_fields": [],
        "chart_suggestions": [],
        "unvalidated_metrics": [],
        "load_mode": "raw",
        "aggregate_config": {},
        "raw_config": {},
    }
    view_record = mini_apps.get_view(view_id)
    view_state["dataset_revisions"] = (
        dict(view_record.dataset_revisions) if view_record else {}
    )
    mini_apps.set_view_state(view_id, view_state)

    payload = MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=METRIC_LAB_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=_build_summary_cards(primary_ds),
        datasets=descriptors,
        view_state=view_state,
        provenance={
            "source": "dual_model",
            "metrics": tables,
        },
        warnings=list(primary_ds.stats.warnings) + list(datasets.get("secondary", primary_ds).stats.warnings),
    )

    return mini_apps.payload_to_call_tool_result(
        payload,
        summary_text=(
            f"Metric Lab loaded dual tables: {tables[0]} + {tables[1]} "
            f"({primary_ds.stats.row_count:,} + "
            f"{datasets.get('secondary', primary_ds).stats.row_count:,} rows)."
        ),
    )


# ---------------------------------------------------------------------------
# N-model date-grain join (2-8 models -> ONE wide table)
# ---------------------------------------------------------------------------


_JOIN_MODEL_CAP = 8

_NUMERIC_TYPE_PREFIXES = ("Int", "UInt", "Float", "Decimal")


def _first_numeric_column(model_def: dict[str, Any], model_name: str) -> str:
    """Default Y for a join spec: the model's first numeric registry column."""
    for name, ch_type in _model_column_items(model_def):
        if _unwrap_ch_type(ch_type).startswith(_NUMERIC_TYPE_PREFIXES):
            return name
    raise ValueError(
        f"{model_name} has no numeric column to aggregate — pass an explicit "
        "y in join_specs."
    )


def _multi_metric_join_sql(
    models_map: dict[str, Any],
    specs: list[dict[str, str]],
    *,
    grain: str = "day",
    window_days: int = 0,
) -> str:
    """N-model date-grain wide table (one row per date bucket).

    NULL-safe UNION ALL + outer ``max()`` GROUP BY — avoids ClickHouse FULL
    JOIN USING edge cases; aggregate functions skip NULLs, so ``max()`` per
    column collapses the union correctly. Value columns are aliased to the
    MODEL NAMES so ``_build_initial_payload``'s metric_fields intersection
    and the Correlate suggestion light up unchanged.

    This is a model-TABLE comparison (raw registry columns aggregated per
    date), not a governed semantic-metric join.
    """
    if grain not in _TIME_GRAINS:
        raise ValueError(f"grain must be one of {sorted(_TIME_GRAINS)}")
    aliases = [s.get("model", "") for s in specs]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Duplicate models in join.")
    n = len(specs)
    branches: list[str] = []
    for i, spec in enumerate(specs):
        model_name = spec.get("model", "")
        model_def = models_map.get(model_name) or {}
        cols = _model_column_names(model_def)
        if not cols:
            raise ValueError(
                f"Model '{model_name}' has no column metadata in the registry."
            )
        date_col = _time_column(model_def)
        if not date_col:
            raise ValueError(
                f"{model_name} has no date/time column — cannot join on date."
            )
        y_col = spec.get("y") or _first_numeric_column(model_def, model_name)
        if y_col not in cols:
            raise ValueError(
                f"y='{y_col}' is not a column of {model_name}. "
                f"Columns: {sorted(cols)}"
            )
        agg = spec.get("agg") or "sum"
        if agg not in _AGGREGATE_FUNCTIONS or agg == "count":
            raise ValueError(
                f"agg='{agg}' is not supported for joins. One of: "
                f"{sorted(set(_AGGREGATE_FUNCTIONS) - {'count'})}"
            )
        measure = _AGGREGATE_FUNCTIONS[agg].format(col=f"`{y_col}`")
        cells = ", ".join(
            f"{measure} AS m{j}" if j == i else f"NULL AS m{j}"
            for j in range(n)
        )
        where = (
            f" WHERE `{date_col}` >= today() - {int(window_days)}"
            if window_days > 0
            else ""
        )
        grain_expr = _TIME_GRAINS[grain].format(col=f"`{date_col}`")
        branches.append(
            f"SELECT {grain_expr} AS date, {cells} "
            f"FROM {_model_relation(model_def, model_name)}{where} "
            f"GROUP BY date"
        )
    outer = ", ".join(f"max(m{i}) AS `{a}`" for i, a in enumerate(aliases))
    return (
        f"SELECT date, {outer} FROM ({' UNION ALL '.join(branches)}) "
        f"GROUP BY date ORDER BY date ASC LIMIT {_AGGREGATE_ROW_CAP}"
    )


def _resolve_join_specs(
    metrics: list[str],
    join_specs: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Derive one spec per selected model — ``metrics`` defines identity and
    order; ``join_specs`` is per-model configuration keyed by model name and
    may not reference unselected models."""
    extra = set(join_specs or {}) - set(metrics)
    if extra:
        raise ValueError(
            f"join_specs for unselected models: {sorted(extra)}"
        )
    specs: list[dict[str, str]] = []
    for m in metrics:
        conf = (join_specs or {}).get(m) or {}
        if not isinstance(conf, dict):
            raise ValueError(f"join_specs['{m}'] must be an object.")
        specs.append(
            {
                "model": m,
                "y": str(conf.get("y", "") or ""),
                "agg": str(conf.get("agg", "") or "sum"),
            }
        )
    return specs


def _load_joined_models(
    ch: ClickHouseManager,
    *,
    view_id: str,
    record: "mini_apps.ViewRecord",
    models: list[str],
    models_map: dict[str, Any],
    join_specs: dict[str, Any] | None = None,
    grain: str = "day",
    window_days: int = 0,
) -> CallToolResult:
    """Load 2-8 models as ONE server-joined wide table under ``primary``."""
    try:
        specs = _resolve_join_specs(models, join_specs)
        sql = _multi_metric_join_sql(
            models_map, specs, grain=grain, window_days=window_days
        )
    except ValueError as exc:
        return mini_apps.error_call_tool_result(str(exc))

    parameters = {
        "metrics": models,
        "mode": "join",
        "grain": grain,
        "window_days": window_days,
        "join_specs": specs,
    }
    try:
        dataset = mini_apps.load_bounded_dataset(
            ch, sql, database="dbt", parameters=parameters
        )
    except mini_apps.MiniAppQueryError as exc:
        return mini_apps.error_call_tool_result(f"Join load failed: {exc}")

    mini_apps.replace_view_datasets(view_id, {"primary": dataset})

    prior_state = record.view_state or {}
    catalog_query = prior_state.get("catalog_query", "")
    catalog = prior_state.get("metric_catalog")
    catalog_total = prior_state.get("catalog_total")
    catalog_facets = prior_state.get("catalog_facets")
    if not catalog:
        catalog_result = build_metric_catalog(query=catalog_query)
        catalog = catalog_result["entries"]
        catalog_total = catalog_result["total_matching"]
        catalog_facets = catalog_result["facets"]

    payload = _build_initial_payload(
        view_id=view_id,
        title=record.title,
        dataset=dataset,
        provenance={
            "source": "model_join",
            "metrics": models,
            "grain": grain,
            "join_specs": specs,
        },
        catalog=catalog,
        selected_metric=models[0],
        selected_metrics=models,
        selected_limit=_AGGREGATE_ROW_CAP,
        catalog_total=catalog_total,
        catalog_facets=catalog_facets,
        load_mode="join",
        aggregate_config={"grain": grain, "specs": specs},
    )
    mini_apps.set_view_state(view_id, payload.view_state)

    return mini_apps.payload_to_call_tool_result(
        payload,
        summary_text=(
            f"Metric Lab joined {len(models)} models on date "
            f"({grain} grain, {dataset.stats.row_count:,} rows)."
        ),
    )


def _refresh_view_payload(
    record: "mini_apps.ViewRecord",
    *,
    replaced: dict[str, CachedDataset],
    provenance_patch: dict[str, dict[str, Any]] | None = None,
) -> MiniAppPayload:
    """Rebuild an INITIAL_LOAD payload from the CURRENT view record with
    ``replaced`` datasets overlaid.

    Catalog/builder state and chart panels are PRESERVED; only panels whose
    fields no longer exist in their (possibly replaced) dataset get their
    field selection repaired to that dataset's inferred defaults. Provenance
    is tracked per dataset under ``view_state["provenance"]``.

    Pure with respect to server state — callers attach the replaced
    dataset(s) and ``set_view_state`` afterwards, so a failure mid-build
    mutates nothing.
    """
    prior = dict(record.view_state or {})
    datasets = {**record.datasets, **replaced}
    primary = datasets.get("primary") or next(iter(datasets.values()))

    # Predict post-attach revisions (the caller attaches right after).
    revisions = dict(record.dataset_revisions)
    for key in replaced:
        revisions[key] = revisions.get(key, 0) + 1

    panels: list[dict[str, Any]] = []
    for p in prior.get("charts") or []:
        if not isinstance(p, dict):
            continue
        panel = dict(p)
        ds = datasets.get(str(panel.get("datasetKey") or "primary"))
        if ds is not None:
            cols = set(ds.columns)
            referenced = [
                panel.get("xField") or "",
                panel.get("groupBy") or "",
                panel.get("colorBy") or "",
                *(panel.get("yFields") or [panel.get("yField") or ""]),
            ]
            if any(f and f not in cols for f in referenced):
                defaults = _infer_default_chart(ds)
                panel = _normalize_panel_yfields(
                    {
                        **panel,
                        "xField": defaults["xField"],
                        "yField": defaults["yField"],
                        "yFields": [],
                        "y2Field": "",
                        "groupBy": "",
                    }
                )
                panel.pop("colorBy", None)
            if ds.stats.mode == "preview_only":
                panel["chartType"] = "table"
        panels.append(panel)
    if not panels:
        panels = [
            _normalize_panel_yfields(
                {**_infer_default_chart(primary), "id": "c1", "datasetKey": "primary"}
            )
        ]

    provenance_map = dict(prior.get("provenance") or {})
    for key, prov in (provenance_patch or {}).items():
        provenance_map[key] = prov

    view_state: dict[str, Any] = {
        **prior,
        "charts": panels,
        "chart": _legacy_chart_projection(panels[0]),
        "analytics_disabled": primary.stats.mode == "preview_only",
        "estimates": primary.stats.mode == "random_sample",
        "dataset_mode": primary.stats.mode,
        "sample_source_rows": primary.stats.sample_source_rows,
        "dataset_revisions": revisions,
        "provenance": provenance_map,
        "allowed_databases": list(settings.ALLOWED_DATABASES),
    }
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key,
            dataset=ds,
            title="Primary dataset" if key == "primary" else key,
        )
        for key, ds in datasets.items()
    }
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=METRIC_LAB_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=_build_summary_cards(primary),
        datasets=descriptors,
        view_state=view_state,
        provenance={"source": "dataset_refresh", "replaced": sorted(replaced)},
        warnings=list(primary.stats.warnings),
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_metric_lab_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        METRIC_LAB_APP_ID,
        title="Metric Lab",
        resource_uri=METRIC_LAB_URI,
    )

    @mcp.resource(
        METRIC_LAB_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_metric_lab_app() -> str:
        """Serve the bundled Vite/React single-file app for the Metric Lab URI."""
        return get_metric_lab_html()

    @mcp.tool(
        meta={
            "ui": {"resourceUri": METRIC_LAB_URI},
            "ui/resourceUri": METRIC_LAB_URI,
        }
    )
    def open_metric_lab(query: str = "", title: str = "") -> CallToolResult:
        """Open the interactive Metric Lab app with an empty metric catalog.

        Call ONLY when the user explicitly asks to open, explore, or
        interact with the Metric Lab. This is NOT the default path for
        answering metric questions — for a plain answer use ``find`` ->
        ``query_metrics``; for a chart/report use the preflight -> charts
        -> report pipeline. This tool opens a UI, it does not answer a
        question on its own.

        Once open, the app renders a searchable metric picker sourced from
        the semantic registry; when the user picks a metric the frontend
        calls ``load_metric_lab_metric`` to run the query and attach the
        dataset to the same view.

        Args:
            query: Optional search hint — reorders the catalog by
                relevance so recommended metrics appear first.
            title: Optional override for the view title.

        Returns:
            Interactive UI resource for ``ui://cerebro/metric_lab``.
        """
        catalog_result = build_metric_catalog(query=query)
        catalog = catalog_result["entries"]
        if not catalog:
            return mini_apps.error_call_tool_result(
                "No models found in the semantic snapshot. "
                "Check that the snapshot is loaded."
            )

        view_id = mini_apps.create_view(
            METRIC_LAB_APP_ID, title or "Metric Lab"
        )
        payload = _build_empty_payload(
            view_id=view_id,
            title=title or "Metric Lab",
            catalog=catalog,
            query=query,
            catalog_total=catalog_result["total_matching"],
            catalog_facets=catalog_result["facets"],
        )
        mini_apps.set_view_state(view_id, payload.view_state)

        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab ready — {catalog_result['total_matching']} models "
                f"available ({len(catalog)} embedded). view_id={view_id[:8]}"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": METRIC_LAB_URI},
            "ui/resourceUri": METRIC_LAB_URI,
        }
    )
    def load_metric_lab_metric(
        view_id: str,
        metric: str | list[str],
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        order_by: list[str] | None = None,
        limit: int = 2000,
        window_days: int = 0,
        mode: str = "raw",
        x: str = "",
        y: str = "",
        agg: str = "sum",
        ys: list[str] | None = None,
        grain: str = "",
        columns: list[str] | None = None,
        join_specs: dict[str, Any] | None = None,
        series: str = "",
        series_top_n: int = 8,
        filter_col: str = "",
        filter_op: str = "=",
        filter_value: str = "",
    ) -> CallToolResult:
        """Load a dbt model (or legacy semantic metric) into an open view.

        Operates on an already-open view (requires ``view_id``), so it is
        never a first step — the user must already have a Metric Lab open.
        On success, re-emits ``INITIAL_LOAD`` for the same ``view_id`` so
        the frontend swaps in the fresh dataset while the catalog remains
        visible for the user to pick another model.

        Args:
            view_id: Target view (from ``open_metric_lab``).
            metric: dbt model name from the catalog (any layer — api_/fct_/
                int_/stg_/source; loaded via its qualified relation), or a
                legacy semantic-registry metric name (compiled via the
                semantic planner — agent compatibility). Several models with
                mode='aggregate' (up to 8) are joined on date into ONE wide
                table (one value column per model, aliased to the model
                name); 2 models in raw mode load as primary + secondary
                (legacy dual-axis compare). Metric names and model names
                cannot be mixed.
            dimensions: Optional dimension breakdowns (semantic metrics only).
            filters: Optional filter expressions (semantic metrics only).
            order_by: Optional ORDER BY clauses (semantic metrics only).
            limit: Row cap before the mini-app sampler kicks in (raw mode).
            window_days: For model loads with a date-like column, keep only
                the trailing N days (0 = all history). Bounds the scan on
                heavy views.
            mode: ``"raw"`` (SELECT * sample, default) or ``"aggregate"`` —
                run ``agg(y)`` GROUP BY ``x`` [, ``series``] IN CLICKHOUSE.
                Aggregate is the only correct way to chart big per-entity
                panels (e.g. balances per avatar per day) — a raw LIMIT
                sample covers a fraction of one day.
            x: Aggregate mode — group-by column (usually the date column).
            y: Aggregate mode — the measured column.
            agg: Aggregate mode — sum|avg|min|max|median|count|uniq
                (``uniq`` counts distinct ``y`` per ``x`` bucket, e.g.
                daily active avatars).
            ys: Aggregate mode — several measure columns aggregated with the
                same ``agg`` (mutually exclusive with ``series``; not valid
                with ``agg='count'``). Overrides ``y`` when provided.
            grain: Aggregate mode — bucket a date/time ``x`` by
                ``day``/``week``/``month`` (empty = group by the raw column).
            columns: Raw mode — project only these columns (each must be a
                registry column of the model; empty = ``SELECT *``).
            join_specs: Multi-model aggregate joins — per-model config keyed
                by model name: ``{"model_a": {"y": "col", "agg": "sum"}}``.
                Defaults: first numeric registry column, ``sum``. Keys must
                be a subset of ``metric``; this is a model-TABLE comparison,
                not a governed semantic-metric join.
            series: Aggregate mode — optional breakdown column; output is
                bounded to the top ``series_top_n`` series.
            filter_col/filter_op/filter_value: optional equality filter
                (op ∈ {=, !=}); the value is bound as a query parameter.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        # Normalize to list — frontend may send one name or many.
        metrics = [metric] if isinstance(metric, str) else list(metric)
        if not metrics:
            return mini_apps.error_call_tool_result("No metric(s) provided.")
        primary = metrics[0]
        is_multi = len(metrics) > 1

        # Detect kind by snapshot membership: MODEL names (any layer) load
        # directly via SELECT on their relation; legacy semantic-metric
        # names route through the compiler (agent compatibility).
        from cerebro_mcp.loaders.semantic import semantic_runtime
        from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic
        _maybe_refresh_semantic()
        snapshot = semantic_runtime.snapshot
        models_map = snapshot.models if snapshot else {}

        # Routing matrix (models):
        #   1 model, raw            -> _model_load_sql
        #   1 model, aggregate      -> _aggregate_load_sql
        #   2 models, raw           -> _load_dual_tables (legacy compat)
        #   2-8 models, aggregate   -> _load_joined_models (ONE wide table)
        #   >2 models, raw          -> reject (use aggregate comparison)
        #   >8 / duplicates / mixed -> reject
        is_model = primary in models_map or primary.startswith("api_")
        model_flags = [
            m in models_map or m.startswith("api_") for m in metrics
        ]
        all_models = all(model_flags)
        if is_multi and any(model_flags) and not all_models:
            return mini_apps.error_call_tool_result(
                "Cannot mix model names with semantic metric names in one load."
            )
        if all_models and is_multi:
            if len(set(metrics)) != len(metrics):
                return mini_apps.error_call_tool_result(
                    "Duplicate model names in the comparison."
                )
            if len(metrics) > _JOIN_MODEL_CAP:
                return mini_apps.error_call_tool_result(
                    f"At most {_JOIN_MODEL_CAP} models per comparison."
                )
            if mode == "aggregate":
                return _load_joined_models(
                    ch,
                    view_id=view_id,
                    record=record,
                    models=metrics,
                    models_map=models_map,
                    join_specs=join_specs,
                    grain=grain or "day",
                    window_days=window_days,
                )
            if len(metrics) == 2:
                # Dual-table comparison: two raw queries as primary + secondary.
                return _load_dual_tables(
                    ch, view_id=view_id, record=record,
                    tables=metrics, limit=limit, window_days=window_days,
                )
            return mini_apps.error_call_tool_result(
                "Raw mode compares at most 2 models — use mode='aggregate' "
                "to overlay more on a date-joined wide table."
            )
        query_params: dict[str, Any] | None = None
        if is_model and mode == "aggregate":
            model_def = models_map.get(primary) or {}
            try:
                sql, query_params = _aggregate_load_sql(
                    model_def,
                    primary,
                    x=x,
                    y=y,
                    agg=agg,
                    ys=ys,
                    grain=grain,
                    series=series,
                    series_top_n=series_top_n,
                    window_days=window_days,
                    filter_col=filter_col,
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            except ValueError as exc:
                return mini_apps.error_call_tool_result(str(exc))
            database = "dbt"
        elif is_model:
            model_def = models_map.get(primary) or {}
            try:
                sql = _model_load_sql(
                    model_def, primary, limit, window_days, columns=columns
                )
            except ValueError as exc:
                return mini_apps.error_call_tool_result(str(exc))
            database = "dbt"
        else:
            try:
                sql, database = compile_metric_query_sql(
                    metrics=metrics,
                    dimensions=dimensions,
                    filters=filters,
                    order_by=order_by,
                    limit=limit,
                )
            except ValueError as exc:
                return mini_apps.error_call_tool_result(str(exc))

        aggregate_config = (
            {
                "x": x,
                "y": y,
                "agg": agg,
                "ys": ys or [],
                "grain": grain,
                "series": series,
                "series_top_n": series_top_n,
                "filter_col": filter_col,
                "filter_op": filter_op,
                "filter_value": filter_value,
            }
            if mode == "aggregate"
            else {}
        )
        raw_config = (
            {"columns": columns or []} if mode != "aggregate" else {}
        )
        # Top-level JSON-serializable values only: this dict doubles as the
        # cache key AND the ClickHouse bind-parameter source ({flt:String}
        # needs a top-level "flt" key).
        parameters = {
            "metrics": metrics,
            "dimensions": dimensions or [],
            "filters": filters or [],
            "order_by": order_by or [],
            "limit": limit,
            "window_days": window_days,
            "mode": mode,
            "agg_x": x,
            "agg_y": y,
            "agg_fn": agg,
            "agg_ys": ys or [],
            "agg_grain": grain,
            "agg_series": series,
            "agg_top_n": series_top_n,
            "agg_filter": f"{filter_col}{filter_op}",
            "raw_cols": columns or [],
            **(query_params or {}),
        }
        try:
            dataset = mini_apps.load_bounded_dataset(
                ch, sql, database=database, parameters=parameters
            )
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(
                f"Load failed: {exc}"
            )
        except Exception as exc:
            logger.exception("load_metric_lab_metric failed")
            return mini_apps.error_call_tool_result(str(exc))

        # Replace (not attach): a solo load after a dual/join compare must
        # also drop the stale secondary dataset.
        mini_apps.replace_view_datasets(view_id, {"primary": dataset})

        # Keep the catalog visible across reloads so the user can swap
        # metrics without re-opening the app.
        prior_state = record.view_state or {}
        catalog_query = prior_state.get("catalog_query", "")
        catalog = prior_state.get("metric_catalog")
        catalog_total = prior_state.get("catalog_total")
        catalog_facets = prior_state.get("catalog_facets")
        if not catalog:
            catalog_result = build_metric_catalog(query=catalog_query)
            catalog = catalog_result["entries"]
            catalog_total = catalog_result["total_matching"]
            catalog_facets = catalog_result["facets"]

        payload = _build_initial_payload(
            view_id=view_id,
            title=record.title,
            dataset=dataset,
            provenance={
                "source": "model" if is_model else "semantic",
                "metric": primary,
                "metrics": metrics,
                "dimensions": dimensions or [],
                "filters": filters or [],
                "order_by": order_by or [],
            },
            catalog=catalog,
            selected_metric=primary,
            selected_metrics=metrics,
            selected_dimensions=dimensions or [],
            selected_limit=limit,
            selected_order_by=order_by or [],
            catalog_total=catalog_total,
            catalog_facets=catalog_facets,
            unvalidated_metrics=(
                [] if is_model else _unvalidated_metric_names(metrics)
            ),
            load_mode=mode if is_model else "raw",
            aggregate_config=aggregate_config or None,
            raw_config=raw_config or None,
        )
        # Exact replacement (INITIAL_LOAD): a deep-merge would keep stale
        # keys (cleared aggregate_config, prior provenance) alive.
        mini_apps.set_view_state(view_id, payload.view_state)

        label = primary if not is_multi else f"{primary} + {len(metrics) - 1} more"
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab loaded '{label}' "
                f"({dataset.stats.mode}, {dataset.stats.row_count:,} rows)."
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": METRIC_LAB_URI},
            "ui/resourceUri": METRIC_LAB_URI,
        }
    )
    def open_metric_lab_from_sql(
        sql: str,
        database: str = "dbt",
        title: str = "",
    ) -> CallToolResult:
        """Open the interactive Metric Lab app from a raw SQL query.

        Call only when the user asked to open/explore the Metric Lab
        interactively — not as the default way to answer a question. Loads
        a bounded dataset (≤2,000 rows or a deterministic random sample for
        larger queries) and renders an interactive table-and-chart view that
        the user and the model can reshape together.
        """
        try:
            dataset = mini_apps.load_bounded_dataset(
                ch, sql, database=database, parameters=None
            )
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(
                f"Query failed: {exc}"
            )
        except Exception as exc:
            logger.exception("metric_lab SQL load failed")
            return mini_apps.error_call_tool_result(str(exc))

        view_id = mini_apps.create_view(
            METRIC_LAB_APP_ID, title or "Metric Lab — SQL"
        )
        mini_apps.attach_dataset(view_id, "primary", dataset)

        payload = _build_initial_payload(
            view_id=view_id,
            title=title or "Metric Lab — SQL",
            dataset=dataset,
            provenance={
                "source": "raw_sql",
                "sql": dataset.sql,
                "database": dataset.database,
            },
        )
        mini_apps.set_view_state(view_id, payload.view_state)

        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab ready ({dataset.stats.mode}, "
                f"{dataset.stats.row_count:,} rows). view_id={view_id[:8]}"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": METRIC_LAB_URI},
            "ui/resourceUri": METRIC_LAB_URI,
        }
    )
    def open_metric_lab_from_metrics(
        metrics: list[str],
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        order_by: list[str] | None = None,
        limit: int = 2000,
        title: str = "",
    ) -> CallToolResult:
        """Open the interactive Metric Lab app from a semantic metric request.

        Call only when the user asked to open/explore the Metric Lab
        interactively — not as the default way to answer a metric question
        (use ``query_metrics`` for that). Compiles ``metrics`` +
        ``dimensions`` + ``filters`` to SQL via the semantic registry, then
        funnels through the same bounded loader as ``open_metric_lab_from_sql``.
        """
        try:
            sql, database = compile_metric_query_sql(
                metrics=metrics,
                dimensions=dimensions,
                filters=filters,
                order_by=order_by,
                limit=limit,
            )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        parameters = {
            "metrics": metrics,
            "dimensions": dimensions or [],
            "filters": filters or [],
            "order_by": order_by or [],
            "limit": limit,
        }
        try:
            dataset = mini_apps.load_bounded_dataset(
                ch, sql, database=database, parameters=parameters
            )
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(
                f"Metric query failed: {exc}"
            )
        except Exception as exc:
            logger.exception("metric_lab metrics load failed")
            return mini_apps.error_call_tool_result(str(exc))

        view_id = mini_apps.create_view(
            METRIC_LAB_APP_ID, title or "Metric Lab — Metrics"
        )
        mini_apps.attach_dataset(view_id, "primary", dataset)

        payload = _build_initial_payload(
            view_id=view_id,
            title=title or "Metric Lab — Metrics",
            dataset=dataset,
            provenance={
                "source": "semantic",
                "metrics": metrics,
                "dimensions": dimensions or [],
                "filters": filters or [],
            },
            selected_metric=metrics[0] if metrics else "",
            selected_metrics=list(metrics),
            selected_dimensions=dimensions or [],
            selected_limit=limit,
            selected_order_by=order_by or [],
            unvalidated_metrics=_unvalidated_metric_names(list(metrics)),
        )
        mini_apps.set_view_state(view_id, payload.view_state)

        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab ready ({dataset.stats.mode}, "
                f"{dataset.stats.row_count:,} rows) for "
                f"{', '.join(metrics)}. view_id={view_id[:8]}"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": METRIC_LAB_URI},
            "ui/resourceUri": METRIC_LAB_URI,
        }
    )
    def update_metric_lab_chart(
        view_id: str,
        x_field: str,
        y_field: str,
        chart_type: str,
        aggregation: str = "sum",
        group_by: str = "",
        chart_id: str = "",
    ) -> CallToolResult:
        """Patch one chart panel's configuration in an open Metric Lab view.

        Operates on an already-open view (requires ``view_id``), so it is
        never a first step — the user must already have a Metric Lab open.

        ``chart_type`` ∈ ``{table, line, bar, scatter, heatmap, pie, numberDisplay}``
        ``aggregation`` ∈ ``{count, sum, avg, min, max, median}``
        ``chart_id`` — panel to patch; empty = the first panel (legacy
        behavior; also refreshes the legacy ``chart`` projection).

        When the panel's dataset is in ``preview_only`` mode, only
        ``chart_type="table"`` is permitted.
        """
        if chart_type not in ALLOWED_CHART_TYPES:
            return mini_apps.error_call_tool_result(
                f"chart_type must be one of {sorted(ALLOWED_CHART_TYPES)}"
            )
        if aggregation not in ALLOWED_AGGREGATIONS:
            return mini_apps.error_call_tool_result(
                f"aggregation must be one of {sorted(ALLOWED_AGGREGATIONS)}"
            )

        record = mini_apps.get_view(view_id)
        if record is None or record.app_id != METRIC_LAB_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired Metric Lab view_id: {view_id}"
            )

        panels = [
            dict(p)
            for p in (record.view_state or {}).get("charts") or []
            if isinstance(p, dict)
        ]
        if chart_id:
            index = next(
                (i for i, p in enumerate(panels) if p.get("id") == chart_id),
                None,
            )
            if index is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown chart_id '{chart_id}'. Panels: "
                    f"{[p.get('id') for p in panels] or ['(none)']}"
                )
        else:
            index = 0

        dataset_key = (
            panels[index].get("datasetKey", "primary")
            if index < len(panels)
            else "primary"
        )
        dataset = record.datasets.get(dataset_key)
        if (
            dataset
            and dataset.stats.mode == "preview_only"
            and chart_type != "table"
        ):
            return mini_apps.error_call_tool_result(
                f"Dataset '{dataset_key}' is in preview_only mode; only "
                "chart_type='table' is allowed. Reload Metric Lab with a "
                "smaller scope to enable analytics."
            )

        # Validate field names against the PANEL'S dataset — catches typos
        # that would otherwise silently render an empty chart.
        if dataset is not None:
            valid_columns = set(dataset.columns)
            for arg_name, value in (
                ("x_field", x_field),
                ("y_field", y_field),
                ("group_by", group_by),
            ):
                if value and value not in valid_columns:
                    return mini_apps.error_call_tool_result(
                        f"{arg_name}='{value}' is not a column of dataset "
                        f"'{dataset_key}'. Valid columns: {sorted(valid_columns)}"
                    )

        chart_patch = {
            "xField": x_field,
            "yField": y_field,
            "chartType": chart_type,
            "aggregation": aggregation,
            "groupBy": group_by,
        }
        patch: dict[str, Any] = {}
        if index < len(panels):
            updated = _normalize_panel_yfields(
                {**panels[index], **chart_patch, "yFields": []}
            )
            # A single-Y agent patch replaces the plotted fields outright.
            updated["yFields"] = [y_field] if y_field else []
            updated["yField"] = y_field
            updated.pop("y2Field", None)
            panels[index] = updated
            patch["charts"] = panels
        if index == 0:
            patch["chart"] = chart_patch
        if not patch:
            patch = {"chart": chart_patch}
        mini_apps.patch_view_state(view_id, patch)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=METRIC_LAB_APP_ID,
            title=record.title,
            patch=patch,
        )
        target = chart_id or (panels[0].get("id") if panels else "chart")
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab panel '{target}' updated → {chart_type} "
                f"({x_field}, {y_field})"
            ),
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def set_metric_lab_charts(
        view_id: str, charts: list[dict[str, Any]]
    ) -> CallToolResult:
        """[App-only] Persist the full chart-panel grid for a view.

        Hidden from the model-facing tool list. The Metric Lab UI debounces
        add/remove/duplicate/reorder and per-panel edits into one bulk call.
        Panels are validated against their OWN dataset (``datasetKey``) and
        canonicalized (yFields authoritative, mirrors re-derived) before
        persisting. ``view_state.chart`` is refreshed as the legacy scalar
        projection of ``charts[0]``.
        """
        record = mini_apps.get_view(view_id)
        if record is None or record.app_id != METRIC_LAB_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired Metric Lab view_id: {view_id}"
            )
        canonical, err = _validate_panels(record, charts)
        if err:
            return mini_apps.error_call_tool_result(err)
        patch = {
            "charts": canonical,
            "chart": _legacy_chart_projection(canonical[0]),
        }
        mini_apps.patch_view_state(view_id, patch)
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=METRIC_LAB_APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"{len(canonical)} chart panel(s) saved",
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def run_metric_lab_sql(
        view_id: str,
        sql: str,
        database: str = "",
        dataset_key: str = "primary",
    ) -> CallToolResult:
        """[App-only] Re-run (possibly edited) SQL for one attached dataset.

        Hidden from the model-facing tool list. Backs the Metric Lab SQL
        editor: the full guard stack applies (validate_query SELECT-only
        prefix + deny lists, ALLOWED_DATABASES, readonly session, result
        caps via load_bounded_dataset). Replaces ONLY the requested dataset;
        catalog/builder state and unaffected chart panels are preserved,
        and panels whose fields vanished are repaired.
        """
        record = mini_apps.get_view(view_id)
        if record is None or record.app_id != METRIC_LAB_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired Metric Lab view_id: {view_id}"
            )
        if dataset_key not in record.datasets:
            return mini_apps.error_call_tool_result(
                f"Unknown dataset '{dataset_key}'. "
                f"Have: {sorted(record.datasets)}"
            )
        head = sql.lstrip()[:8].upper()
        if not (head.startswith("SELECT") or head.startswith("WITH")):
            # validate_query also admits SHOW/DESCRIBE/EXPLAIN, but the
            # sampler nests the input in SELECT count() FROM (...) — only
            # nestable query expressions can run here.
            return mini_apps.error_call_tool_result(
                "Editor SQL must start with SELECT or WITH (it runs nested "
                "inside the dataset sampler)."
            )
        prev = record.datasets[dataset_key]
        db = database or prev.database or "dbt"
        unchanged = sql.strip() == (prev.sql or "").strip()
        params = prev.parameters if unchanged else None
        if not unchanged and re.search(r"\{\w+:\w+\}", sql):
            return mini_apps.error_call_tool_result(
                "This SQL contains bound placeholders like {flt:String}. "
                "Parameters are only retained for UNCHANGED generated SQL — "
                "inline literal values instead."
            )
        try:
            # Explicit rerun: bypass the 30-min dataset cache (identical SQL
            # + params would otherwise return the cached rows untouched).
            dataset = mini_apps.load_bounded_dataset(
                ch, sql, database=db, parameters=params, force_refresh=True
            )
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(str(exc))
        except Exception as exc:
            logger.exception("run_metric_lab_sql failed")
            return mini_apps.error_call_tool_result(str(exc))

        # Build the refreshed payload BEFORE mutating server state, then
        # swap atomically (attach bumps the revision the payload predicted).
        payload = _refresh_view_payload(
            record,
            replaced={dataset_key: dataset},
            provenance_patch={
                dataset_key: {"source": "editor_sql", "database": db}
            },
        )
        mini_apps.attach_dataset(view_id, dataset_key, dataset)
        mini_apps.set_view_state(view_id, payload.view_state)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"SQL re-run for '{dataset_key}' "
                f"({dataset.stats.mode}, {dataset.stats.row_count:,} rows)."
            ),
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def search_metric_catalog(
        query: str = "",
        sector: str = "",
        layer: str = "",
        tag: str = "",
        timeseries: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> CallToolResult:
        """[App-only] Search / page the model catalog for the frontend.

        Hidden from the model-facing tool list. Stateless (no view_id) —
        the Metric Lab UI calls this to re-query the catalog (search box,
        facet filters, load-more paging) without reloading any dataset.
        ``layer`` ∈ {api, fct, int, stg, source}; ``timeseries=True`` keeps
        only models with a date-like column; ``tag`` filters by dbt tag.
        Returns entries plus sector/layer/tag facet counts in one call.
        """
        result = build_metric_catalog(
            query=query, sector=sector, layer=layer, tag=tag,
            timeseries=timeseries, limit=limit, offset=offset,
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"catalog page entries={len(result['entries'])} "
                        f"total={result['total_matching']}"
                    ),
                )
            ],
            structuredContent={
                **result,
                "query": query,
                "sector": sector,
                "layer": layer,
                "tag": tag,
                "timeseries": timeseries,
                "limit": limit,
                "offset": offset,
            },
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def get_metric_catalog_entry(name: str) -> CallToolResult:
        """[App-only] Full detail for one catalog entry (metric or model).

        Hidden from the model-facing tool list. The Metric Lab UI calls
        this when the user opens a detail panel: full untruncated
        description, synonyms, time grains, and the root model's column
        schema — data too heavy to embed in every catalog list entry.
        """
        detail = get_catalog_entry_detail(name)
        if "error" in detail:
            suggestions = detail.get("suggestions") or []
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return mini_apps.error_call_tool_result(f"{detail['error']}{hint}")
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"catalog entry {name}")
            ],
            structuredContent=detail,
        )

    mini_apps.mark_app_only("search_metric_catalog")
    mini_apps.mark_app_only("get_metric_catalog_entry")
    mini_apps.mark_app_only("set_metric_lab_charts")
    mini_apps.mark_app_only("run_metric_lab_sql")

    web_apps.register_web_app(
        app_id=METRIC_LAB_APP_ID,
        open_tool="open_metric_lab",
        html_loader=get_metric_lab_html,
        title="Metric Lab",
        description=(
            "Build and compare metrics from the semantic layer. Join models on a "
            "date grain, chart several series side by side, and drop straight to "
            "the generated SQL."
        ),
        icon="◧",
        tools={
            "open_metric_lab": open_metric_lab,
            "load_metric_lab_metric": load_metric_lab_metric,
            "open_metric_lab_from_sql": open_metric_lab_from_sql,
            "open_metric_lab_from_metrics": open_metric_lab_from_metrics,
            "update_metric_lab_chart": update_metric_lab_chart,
            "set_metric_lab_charts": set_metric_lab_charts,
            "run_metric_lab_sql": run_metric_lab_sql,
            "search_metric_catalog": search_metric_catalog,
            "get_metric_catalog_entry": get_metric_catalog_entry,
        },
    )


__all__ = [
    "METRIC_LAB_APP_ID",
    "METRIC_LAB_URI",
    "ALLOWED_CHART_TYPES",
    "ALLOWED_AGGREGATIONS",
    "METRIC_CATALOG_LIMIT",
    "METRIC_CATALOG_MAX_LIMIT",
    "register_metric_lab_tools",
    "get_metric_lab_html",
    "compile_metric_query_sql",
    "build_metric_catalog",
    "get_metric_catalog",
    "get_catalog_entry_detail",
]
