"""Dashboard Tab Factory — MCP tools for discovering metrics and scaffolding dashboard tabs."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def register_dashboard_tools(mcp):
    """Register dashboard builder tools. Feature-gated by DASHBOARD_BUILDER_ENABLED."""
    from cerebro_mcp.config import settings

    if not settings.DASHBOARD_BUILDER_ENABLED:
        return

    @mcp.tool()
    def discover_dashboard_metrics(
        query: str = "",
        module: str = "",
        quality_tier: str = "",
        limit: int = 20,
    ) -> str:
        """Discover dbt models suitable for dashboard metrics.

        Searches api_* models from the semantic registry, applies optional
        filters, and suggests chart types based on column heuristics.

        Args:
            query: Text search against model name and description (case-insensitive).
            module: Filter by module (e.g. 'execution', 'consensus', 'bridges').
            quality_tier: Filter by quality tier (e.g. 'production', 'staging').
            limit: Maximum results to return. Default: 20.

        Returns:
            Markdown table of matching models with suggested chart types.
        """
        try:
            from cerebro_mcp.loaders.semantic import semantic_runtime

            snapshot = semantic_runtime.snapshot
            if snapshot is None:
                return "Error: Semantic registry not loaded. Is SEMANTIC_ENABLED=true?"

            models = snapshot.models
            candidates = []

            for model_name, model in models.items():
                if not model_name.startswith("api_"):
                    continue

                # Module filter
                if module and model.get("module", "").lower() != module.lower():
                    continue

                # Quality tier filter
                if quality_tier and model.get("quality_tier", "").lower() != quality_tier.lower():
                    continue

                # Text search
                if query:
                    query_lower = query.lower()
                    name_match = query_lower in model_name.lower()
                    desc_match = query_lower in (model.get("description") or "").lower()
                    if not name_match and not desc_match:
                        continue

                candidates.append((model_name, model))

            total_available = len(candidates)
            candidates = candidates[:limit]

            if not candidates:
                return (
                    f"No api_* models found matching query={query!r}, "
                    f"module={module!r}, quality_tier={quality_tier!r}."
                )

            lines = ["# Dashboard Metric Candidates\n"]
            lines.append(
                "| Model | Module | Chart Type | Columns | Description |"
            )
            lines.append(
                "|-------|--------|------------|---------|-------------|"
            )

            for model_name, model in candidates:
                columns = model.get("columns", [])
                chart_type = _suggest_chart_type(model_name, columns)
                col_names = ", ".join(c.get("name", "") for c in columns[:6])
                if len(columns) > 6:
                    col_names += f" (+{len(columns) - 6})"
                desc = (model.get("description") or "")[:80]
                mod = model.get("module", "")
                lines.append(
                    f"| {model_name} | {mod} | {chart_type} | {col_names} | {desc} |"
                )

            lines.append(f"\nShowing {len(candidates)} of {total_available} matches.")
            lines.append(
                "\nUse `get_model_details(model_name)` to inspect columns, "
                "then `scaffold_dashboard_tab` to generate the tab."
            )
            return "\n".join(lines)

        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def scaffold_dashboard_tab(blueprint_json: str) -> str:
        """Scaffold a dashboard tab from a JSON blueprint.

        Parses the blueprint into a DashboardBlueprint, generates JS query
        files, and merges the tab definition into the dashboard YAML.

        Args:
            blueprint_json: JSON string matching the DashboardBlueprint schema.

        Returns:
            Summary of actions taken (or preview if dry_run=true).
        """
        try:
            from cerebro_mcp.models.dashboard import DashboardBlueprint

            try:
                blueprint = DashboardBlueprint(**json.loads(blueprint_json))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                return f"Error: Invalid blueprint JSON: {e}"

            dashboard_path = settings.METRICS_DASHBOARD_PATH
            if not dashboard_path:
                return "Error: METRICS_DASHBOARD_PATH is not configured."

            dashboard_yml_path = os.path.join(dashboard_path, "public", "dashboard.yml")
            if not os.path.isfile(dashboard_yml_path):
                return (
                    f"Error: dashboard.yml not found at {dashboard_yml_path}. "
                    f"Check METRICS_DASHBOARD_PATH setting."
                )

            actions: list[str] = []

            # --- Generate JS query files ---
            queries_dir = os.path.join(dashboard_path, "src", "queries")
            os.makedirs(queries_dir, exist_ok=True)

            for spec in blueprint.queries:
                js_content = _render_query_js(spec)
                js_path = os.path.join(queries_dir, f"{spec.id}.js")
                existed = os.path.isfile(js_path)

                if blueprint.dry_run:
                    verb = "overwrite" if existed else "create"
                    actions.append(f"[dry-run] Would {verb}: {js_path}")
                else:
                    with open(js_path, "w") as f:
                        f.write(js_content)
                    verb = "Overwrote" if existed else "Created"
                    actions.append(f"{verb}: {js_path}")

            # --- Merge tab into YAML ---
            try:
                import yaml
            except ImportError:
                return "Error: PyYAML is required. Install with: pip install pyyaml"

            with open(dashboard_yml_path) as f:
                dashboard_config = yaml.safe_load(f) or {}

            # Find dashboard entry (case-insensitive key search)
            source_path = None
            for key, value in dashboard_config.items():
                if key.lower() == blueprint.dashboard_id.lower():
                    if isinstance(value, dict):
                        source_path = value.get("source")
                    break

            if not source_path:
                return (
                    f"Error: Dashboard '{blueprint.dashboard_id}' not found "
                    f"in dashboard.yml. Available: {', '.join(dashboard_config.keys())}"
                )

            abs_source = os.path.join(dashboard_path, "public", source_path.lstrip("/"))
            if not os.path.isfile(abs_source):
                return f"Error: Dashboard source file not found: {abs_source}"

            with open(abs_source) as f:
                source_data = yaml.safe_load(f) or {}

            if "tabs" not in source_data:
                source_data["tabs"] = []

            tab_dict = _tab_spec_to_yaml_dict(blueprint.tab, blueprint.tab.metrics)

            # Search existing tabs by name match -> replace, or insert at position
            replaced = False
            for i, existing_tab in enumerate(source_data["tabs"]):
                if existing_tab.get("name", "").lower() == blueprint.tab.name.lower():
                    if blueprint.dry_run:
                        actions.append(
                            f"[dry-run] Would replace tab '{blueprint.tab.name}' "
                            f"at position {i} in {abs_source}"
                        )
                    else:
                        source_data["tabs"][i] = tab_dict
                        actions.append(
                            f"Replaced tab '{blueprint.tab.name}' at position {i} "
                            f"in {abs_source}"
                        )
                    replaced = True
                    break

            if not replaced:
                # Insert at correct position by order
                insert_idx = len(source_data["tabs"])
                for i, existing_tab in enumerate(source_data["tabs"]):
                    if existing_tab.get("order", 0) > blueprint.tab.order:
                        insert_idx = i
                        break

                if blueprint.dry_run:
                    actions.append(
                        f"[dry-run] Would insert tab '{blueprint.tab.name}' "
                        f"at position {insert_idx} in {abs_source}"
                    )
                else:
                    source_data["tabs"].insert(insert_idx, tab_dict)
                    actions.append(
                        f"Inserted tab '{blueprint.tab.name}' at position "
                        f"{insert_idx} in {abs_source}"
                    )

            if not blueprint.dry_run:
                with open(abs_source, "w") as f:
                    yaml.dump(
                        source_data,
                        f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )

            # --- Optional build check ---
            if blueprint.run_build_check and not blueprint.dry_run:
                actions.append("Running pnpm build check...")
                try:
                    result = subprocess.run(
                        ["pnpm", "build"],
                        cwd=dashboard_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode == 0:
                        actions.append("Build check: PASSED")
                    else:
                        actions.append(
                            f"Build check: FAILED (exit {result.returncode})\n"
                            f"stderr: {result.stderr[:500]}"
                        )
                except FileNotFoundError:
                    actions.append("Build check: SKIPPED (pnpm not found)")
                except subprocess.TimeoutExpired:
                    actions.append("Build check: TIMED OUT (120s)")

            # --- Summary ---
            header = "Dry Run Preview" if blueprint.dry_run else "Scaffold Complete"
            summary_lines = [f"# {header}\n"]
            summary_lines.append(f"**Dashboard:** {blueprint.dashboard_id}")
            summary_lines.append(f"**Tab:** {blueprint.tab.name} (order: {blueprint.tab.order})")
            summary_lines.append(f"**Queries:** {len(blueprint.queries)}")
            summary_lines.append(f"**Metrics:** {len(blueprint.tab.metrics)}\n")
            summary_lines.append("## Actions\n")
            for action in actions:
                summary_lines.append(f"- {action}")

            return "\n".join(summary_lines)

        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _suggest_chart_type(model_name: str, columns: list[dict]) -> str:
    """Heuristically suggest a chart type based on model name and columns."""
    name_lower = model_name.lower()
    col_names = [c.get("name", "").lower() for c in columns]

    # Name-based heuristics
    if any(kw in name_lower for kw in ("kpi", "latest", "total")):
        return "numberDisplay"
    if "sankey" in name_lower:
        return "sankey"
    if "dist" in name_lower and any(
        q in cn for cn in col_names for q in ("q10", "q25", "q50", "q75", "q90")
    ):
        return "boxplot"

    # Column-based heuristics
    time_cols = {"date", "day", "month", "week"}
    series_cols = {"label", "client", "bridge", "token", "sector"}
    has_time = bool(time_cols & set(col_names))
    has_series = bool(series_cols & set(col_names))

    if has_time and has_series:
        return "area"
    if has_time:
        return "line"

    return "bar"


def _js_value(v: Any) -> str:
    """Convert a Python value to a JavaScript literal string."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        items = ", ".join(_js_value(item) for item in v)
        return f"[{items}]"
    if isinstance(v, dict):
        entries = ", ".join(f"{k}: {_js_value(val)}" for k, val in v.items())
        return f"{{ {entries} }}"
    return repr(v)


def _render_query_js(spec) -> str:
    """Generate JS content for a dashboard query file.

    Args:
        spec: A QuerySpec instance from dashboard_models.
    """
    lines = ["const metric = {"]

    # Always emit identity fields
    lines.append(f"  id: '{spec.id}',")
    lines.append(f"  name: {_js_value(spec.name)},")

    if spec.description:
        lines.append(f"  description: {_js_value(spec.description)},")
    if spec.metric_description:
        lines.append(f"  metricDescription: {_js_value(spec.metric_description)},")

    # Chart type
    lines.append(f"  chartType: '{spec.chart_type}',")

    # Booleans — only emit when True
    if spec.is_time_series:
        lines.append("  isTimeSeries: true,")
    if spec.enable_zoom:
        lines.append("  enableZoom: true,")
    if spec.enable_filtering:
        lines.append("  enableFiltering: true,")
    if spec.stacked:
        lines.append("  stacked: true,")
    if spec.show_total:
        lines.append("  showTotal: true,")

    # Format
    if spec.format:
        lines.append(f"  format: '{spec.format}',")

    # Field mappings — only emit non-empty
    if spec.x_field:
        lines.append(f"  xField: '{spec.x_field}',")
    if spec.y_field:
        lines.append(f"  yField: '{spec.y_field}',")
    if spec.series_field:
        lines.append(f"  seriesField: '{spec.series_field}',")
    if spec.value_field:
        lines.append(f"  valueField: '{spec.value_field}',")
    if spec.source_field:
        lines.append(f"  sourceField: '{spec.source_field}',")
    if spec.target_field:
        lines.append(f"  targetField: '{spec.target_field}',")
    if spec.label_field:
        lines.append(f"  labelField: '{spec.label_field}',")

    # Resolutions
    if spec.resolutions:
        lines.append(f"  resolutions: {_js_value(spec.resolutions)},")
    if spec.default_resolution:
        lines.append(f"  defaultResolution: '{spec.default_resolution}',")

    # Text content
    if spec.content:
        lines.append(f"  content: {_js_value(spec.content)},")

    # Extra properties
    for key, val in spec.extra_properties.items():
        lines.append(f"  {key}: {_js_value(val)},")

    # SQL query — backtick template literal
    lines.append(f"  query: `{spec.query}`,")

    lines.append("};")
    lines.append("")
    lines.append("export default metric;")
    lines.append("")

    return "\n".join(lines)


def _tab_spec_to_yaml_dict(tab, metrics: list) -> dict:
    """Convert a TabSpec and its MetricPlacement list to the camelCase dict
    format expected by the metrics-dashboard YAML files.
    """
    result: dict[str, Any] = {"name": tab.name, "order": tab.order}

    if tab.icon:
        result["icon"] = tab.icon
    if tab.icon_class:
        result["iconClass"] = tab.icon_class
    if tab.time_ranges:
        result["timeRanges"] = True
    if tab.default_time_range:
        result["defaultTimeRange"] = tab.default_time_range
    if tab.resolution_toggle:
        result["resolutionToggle"] = True
    if tab.default_resolution:
        result["defaultResolution"] = tab.default_resolution
    if tab.global_filter_field:
        result["globalFilterField"] = tab.global_filter_field
    if tab.global_filter_label:
        result["globalFilterLabel"] = tab.global_filter_label
    if tab.searchable:
        result["searchable"] = True
    if tab.search_placeholder:
        result["searchPlaceholder"] = tab.search_placeholder
    if tab.unit_toggle:
        result["unitToggle"] = True
    if tab.default_unit:
        result["defaultUnit"] = tab.default_unit

    result["metrics"] = [
        {
            "id": m.id,
            "gridRow": m.grid_row,
            "gridColumn": m.grid_column,
            "minHeight": m.min_height,
        }
        for m in metrics
    ]

    return result
