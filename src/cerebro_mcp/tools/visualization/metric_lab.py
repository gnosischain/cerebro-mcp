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
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager
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

# Soft cap on how many metrics ship in the initial catalog payload.
# Keeps the launch payload light; the user can search to find more.
METRIC_CATALOG_LIMIT = 200


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
) -> MiniAppPayload:
    """Catalog-driven launch payload with no attached dataset.

    Used by the zero-arg ``open_metric_lab`` so the user lands in the app
    and picks a metric from a dropdown before any ClickHouse call runs.
    """
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=METRIC_LAB_APP_ID,
        title=title,
        status="ready",
        summary_cards=[
            SummaryCard(
                label="Metrics available",
                value=f"{len(catalog):,}",
                tone="neutral",
            ),
            SummaryCard(
                label="Status",
                value="Pick a metric",
                tone="neutral",
            ),
        ],
        datasets={},
        view_state={
            "mode": "empty",
            "metric_catalog": catalog,
            "catalog_query": query,
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
        },
        provenance={
            "source": "catalog",
            "catalog_size": len(catalog),
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
) -> MiniAppPayload:
    chart_defaults = _infer_default_chart(dataset)
    view_state: dict[str, Any] = {
        "mode": "loaded",
        "metric_catalog": catalog or [],
        "selected_metric": selected_metric,
        "selected_metrics": selected_metrics or ([selected_metric] if selected_metric else []),
        "selected_dimensions": selected_dimensions or [],
        "selected_limit": selected_limit,
        "selected_order_by": selected_order_by or [],
        "chart": chart_defaults,
        "sort": {"field": chart_defaults["xField"], "direction": "desc"},
        "filters": [],
        "analytics_disabled": dataset.stats.mode == "preview_only",
        "estimates": dataset.stats.mode == "random_sample",
        "dataset_mode": dataset.stats.mode,
        "sample_source_rows": dataset.stats.sample_source_rows,
    }
    descriptors = {
        "primary": mini_apps.build_dataset_descriptor(
            key="primary", dataset=dataset, title="Primary dataset"
        )
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


# ---------------------------------------------------------------------------
# Metric catalog (reused by the UI picker)
# ---------------------------------------------------------------------------


def get_metric_catalog(query: str = "", limit: int = METRIC_CATALOG_LIMIT) -> list[dict[str, Any]]:
    """Build a lightweight metric catalog from the semantic snapshot.

    Reuses the existing validation helpers from ``tools.semantic``:
      * ``_metric_is_executable`` — only include metrics approved for
        semantic execution (same filter as ``execute_metric_query``).
      * ``_metric_supported_dimensions`` — populate per-metric dimension
        lists so the UI can render a scoped dropdown.

    When ``query`` is non-empty, metrics are scored with
    ``semantic_index.score_metric`` and returned in relevance order.
    """
    from cerebro_mcp.semantic.index import score_metric
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic.semantic import (
        _metric_is_executable,
        _metric_supported_dimensions,
        _maybe_refresh_semantic,
    )

    _maybe_refresh_semantic()
    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return []

    entries: list[tuple[int, dict[str, Any]]] = []

    # 1. Curated semantic metrics — kind="metric"
    for name, metric in snapshot.metrics.items():
        if metric.get("quality_tier") != "approved":
            continue
        executable = _metric_is_executable(snapshot, metric)
        score = score_metric(query, metric) if query else 0
        entry = {
            "kind": "metric",
            "name": name,
            "label": metric.get("label") or name.replace("_", " "),
            "description": (metric.get("description") or "")[:280],
            "module": metric.get("module") or "",
            "sector": metric.get("module") or "other",
            "subsector": "",
            "root_model": metric.get("root_model") or "",
            "quality_tier": metric.get("quality_tier") or "",
            "unit": metric.get("unit") or "",
            "allowed_dimensions": _metric_supported_dimensions(snapshot, metric),
            "default_dimensions": metric.get("default_dimensions") or [],
            "executable": executable,
            "columns": [],
        }
        entries.append((score, entry))

    # 2. api_* dbt models — kind="model" — grouped by sector. Lets the user
    # browse 200+ analytics tables by sector and chart any of them directly.
    for model_name, model in snapshot.models.items():
        if not model_name.startswith("api_"):
            continue
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

        # Sector / subsector heuristic from api_<sector>_<subsector>_*
        # e.g. api_execution_gpay_active_users → sector=execution, subsector=gpay
        # e.g. api_consensus_validators_active_daily → sector=consensus, subsector=validators
        parts = model_name.split("_")
        sector = model.get("module") or (parts[1] if len(parts) > 1 else "other")
        subsector = parts[2] if len(parts) > 2 else ""

        score = 0
        if query:
            q = query.lower()
            haystack = f"{model_name} {model.get('description') or ''}".lower()
            if q in haystack:
                score = 10
            elif any(part in haystack for part in q.split()):
                score = 3

        entries.append((score, {
            "kind": "model",
            "name": model_name,
            "label": model_name.replace("api_", "").replace("_", " "),
            "description": (model.get("description") or "")[:280],
            "module": model.get("module") or "",
            "sector": sector,
            "subsector": subsector,
            "root_model": model_name,
            "quality_tier": model.get("quality_tier") or "",
            "unit": "",
            "allowed_dimensions": [],
            "default_dimensions": [],
            "executable": True,  # api_ tables are always queryable
            "columns": cols,
        }))

    if query:
        entries.sort(key=lambda e: (-e[0], e[1]["name"]))
    else:
        entries.sort(key=lambda e: e[1]["name"])

    return [entry for _, entry in entries[:limit]]


# ---------------------------------------------------------------------------
# Dual-table comparison helper
# ---------------------------------------------------------------------------


def _load_dual_tables(
    ch: ClickHouseManager,
    *,
    view_id: str,
    record: "mini_apps.ViewRecord",
    tables: list[str],
    limit: int,
) -> CallToolResult:
    """Load two api_* tables as primary + secondary datasets for dual-axis chart."""
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic
    _maybe_refresh_semantic()
    snapshot = semantic_runtime.snapshot

    datasets: dict[str, "mini_apps.CachedDataset"] = {}
    keys = ["primary", "secondary"]

    for i, table_name in enumerate(tables[:2]):
        model_def = (snapshot.models.get(table_name) if snapshot else None) or {}
        cols_field = model_def.get("columns") or {}
        col_names = list(cols_field.keys()) if isinstance(cols_field, dict) else [
            c.get("name", "") for c in cols_field if isinstance(c, dict)
        ]
        date_col = next(
            (c for c in col_names if c.lower() in ("date", "day", "week", "month", "ts", "timestamp", "block_date")),
            None,
        )
        order_clause = f" ORDER BY {date_col} DESC" if date_col else ""
        sql = f"SELECT * FROM dbt.{table_name}{order_clause} LIMIT {int(limit)}"

        try:
            ds = mini_apps.load_bounded_dataset(ch, sql, database="dbt")
        except mini_apps.MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(
                f"Failed to load {table_name}: {exc}"
            )

        key = keys[i]
        datasets[key] = ds
        mini_apps.attach_dataset(view_id, key, ds)

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

    view_state: dict[str, Any] = {
        "mode": "loaded",
        "metric_catalog": catalog,
        "selected_metric": tables[0],
        "selected_metrics": tables,
        "selected_dimensions": [],
        "selected_limit": limit,
        "selected_order_by": [],
        "chart": chart_defaults,
        "sort": {"field": chart_defaults["xField"], "direction": "desc"},
        "filters": [],
        "analytics_disabled": False,
        "estimates": False,
        "dataset_mode": primary_ds.stats.mode,
        "sample_source_rows": primary_ds.stats.sample_source_rows,
    }
    mini_apps.patch_view_state(view_id, view_state)

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
        """Open Metric Lab with a metric catalog but no pre-loaded data.

        Use this as the default entry point. The app renders a searchable
        metric picker sourced from the semantic registry; once the user
        picks a metric the frontend calls ``load_metric_lab_metric`` to
        run the query and attach the dataset to the same view.

        Args:
            query: Optional search hint — reorders the catalog by
                relevance so recommended metrics appear first.
            title: Optional override for the view title.

        Returns:
            Interactive UI resource for ``ui://cerebro/metric_lab``.
        """
        catalog = get_metric_catalog(query=query)
        if not catalog:
            return mini_apps.error_call_tool_result(
                "No executable metrics found in the semantic registry. "
                "Check that the semantic snapshot is loaded."
            )

        view_id = mini_apps.create_view(
            METRIC_LAB_APP_ID, title or "Metric Lab"
        )
        payload = _build_empty_payload(
            view_id=view_id,
            title=title or "Metric Lab",
            catalog=catalog,
            query=query,
        )
        mini_apps.patch_view_state(view_id, payload.view_state)

        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Metric Lab ready — {len(catalog)} metrics available. "
                f"view_id={view_id[:8]}"
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
    ) -> CallToolResult:
        """Load a semantic metric into an existing Metric Lab view.

        Delegates to the semantic planner/compiler (``compile_metric_query_sql``)
        which enforces metric and dimension validation before any SQL runs.
        On success, re-emits ``INITIAL_LOAD`` for the same ``view_id`` so
        the frontend swaps in the fresh dataset while the catalog remains
        visible for the user to pick another metric.

        Args:
            view_id: Target view (from ``open_metric_lab``).
            metric: Metric name (e.g. ``execution_tx_count``). Must exist
                in the semantic registry.
            dimensions: Optional dimension breakdowns (e.g. ``["day"]``).
            filters: Optional filter expressions.
            order_by: Optional ORDER BY clauses.
            limit: Row cap before the mini-app sampler kicks in.
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

        # Detect kind: api_* names load directly via SELECT, semantic
        # metrics route through the compiler. Multi-metric is only
        # supported for semantic metrics (compiler joins them).
        is_model = primary.startswith("api_")
        is_all_models = is_model and all(m.startswith("api_") for m in metrics)
        if is_model and is_multi and len(metrics) > 2:
            return mini_apps.error_call_tool_result(
                "At most 2 analytics tables can be compared side by side."
            )
        if is_all_models and is_multi and len(metrics) == 2:
            # Dual-table comparison: run two queries, return as primary + secondary.
            return _load_dual_tables(
                ch, view_id=view_id, record=record,
                tables=metrics, limit=limit,
            )
        if is_model and is_multi:
            return mini_apps.error_call_tool_result(
                "Cannot mix api_* tables with semantic metrics."
            )
        if is_model:
            # Find a date-like column for ORDER BY DESC
            from cerebro_mcp.loaders.semantic import semantic_runtime
            from cerebro_mcp.tools.semantic.semantic import _maybe_refresh_semantic
            _maybe_refresh_semantic()
            snapshot = semantic_runtime.snapshot
            model_def = (snapshot.models.get(metric) if snapshot else None) or {}
            cols_field = model_def.get("columns") or {}
            col_names = list(cols_field.keys()) if isinstance(cols_field, dict) else [
                c.get("name", "") for c in cols_field if isinstance(c, dict)
            ]
            date_col = next(
                (c for c in col_names if c.lower() in ("date", "day", "week", "month", "ts", "timestamp", "block_date")),
                None,
            )
            order_clause = f" ORDER BY {date_col} DESC" if date_col else ""
            sql = f"SELECT * FROM dbt.{primary}{order_clause} LIMIT {int(limit)}"
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
            logger.exception("load_metric_lab_metric failed")
            return mini_apps.error_call_tool_result(str(exc))

        mini_apps.attach_dataset(view_id, "primary", dataset)

        # Keep the catalog visible across reloads so the user can swap
        # metrics without re-opening the app.
        catalog_query = record.view_state.get("catalog_query", "") if record.view_state else ""
        catalog = record.view_state.get("metric_catalog") or get_metric_catalog(
            query=catalog_query
        )

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
        )
        mini_apps.patch_view_state(view_id, payload.view_state)

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
        """Open Metric Lab from a raw SQL query.

        Loads a bounded dataset (≤2,000 rows or a deterministic random
        sample for larger queries) and renders an interactive table-and-chart
        view that the user and the model can reshape together.
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
        mini_apps.patch_view_state(view_id, payload.view_state)

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
        """Open Metric Lab from a semantic metric request.

        Compiles ``metrics`` + ``dimensions`` + ``filters`` to SQL via the
        semantic registry, then funnels through the same bounded loader as
        ``open_metric_lab_from_sql``.
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
        )
        mini_apps.patch_view_state(view_id, payload.view_state)

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
    ) -> CallToolResult:
        """Patch the chart configuration in an open Metric Lab view.

        ``chart_type`` ∈ ``{table, line, bar, scatter, heatmap, pie, numberDisplay}``
        ``aggregation`` ∈ ``{count, sum, avg, min, max, median}``

        When the underlying dataset is in ``preview_only`` mode, only
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
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        primary = record.datasets.get("primary")
        if primary and primary.stats.mode == "preview_only" and chart_type != "table":
            return mini_apps.error_call_tool_result(
                "Dataset is in preview_only mode; only chart_type='table' is "
                "allowed. Reload Metric Lab with a smaller scope to enable "
                "analytics."
            )

        chart_patch = {
            "xField": x_field,
            "yField": y_field,
            "chartType": chart_type,
            "aggregation": aggregation,
            "groupBy": group_by,
        }
        patch = {"chart": chart_patch}
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
            summary_text=f"Metric Lab chart updated → {chart_type} ({x_field}, {y_field})",
        )

    web_apps.register_web_app(
        app_id=METRIC_LAB_APP_ID,
        open_tool="open_metric_lab",
        html_loader=get_metric_lab_html,
        tools={
            "open_metric_lab": open_metric_lab,
            "load_metric_lab_metric": load_metric_lab_metric,
            "open_metric_lab_from_sql": open_metric_lab_from_sql,
            "open_metric_lab_from_metrics": open_metric_lab_from_metrics,
            "update_metric_lab_chart": update_metric_lab_chart,
        },
    )


__all__ = [
    "METRIC_LAB_APP_ID",
    "METRIC_LAB_URI",
    "ALLOWED_CHART_TYPES",
    "ALLOWED_AGGREGATIONS",
    "register_metric_lab_tools",
    "get_metric_lab_html",
    "compile_metric_query_sql",
]
