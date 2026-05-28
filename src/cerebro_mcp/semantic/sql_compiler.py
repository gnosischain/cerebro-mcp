from __future__ import annotations

import json
import time
from typing import Any

from cerebro_mcp.runtime.observability import observe_semantic_sql_compile_latency


# ──────────────────────────────────────────────────────────────────────
# Aggregation function translation
# ──────────────────────────────────────────────────────────────────────
# dbt MetricFlow agg names → ClickHouse function names. The semantic
# layer authoring spec accepts a small set of agg types; ClickHouse
# names some of them differently (notably `uniqExact` for
# `count_distinct` and `avg` for `average`). We translate here so
# authors can keep using the standard MetricFlow vocabulary in
# semantic_models.yml without hitting opaque
# `Function with name 'count_distinct' does not exist` errors at
# query time.
_AGG_TO_CLICKHOUSE: dict[str, str] = {
    "sum":            "sum",
    "min":            "min",
    "max":            "max",
    "count":          "count",
    "count_distinct": "uniqExact",
    "average":        "avg",
    "avg":            "avg",
    "median":         "median",
}


def _agg_call(agg: str, expr: str) -> str:
    """Render `<agg>(<expr>)` translated to the ClickHouse function name.

    Raises ValueError for unknown agg types so authoring mistakes surface at
    compile time with a clear error rather than as opaque ClickHouse syntax
    errors at query time. Add new aggs to ``_AGG_TO_CLICKHOUSE``.
    """
    fn = _AGG_TO_CLICKHOUSE.get(agg)
    if fn is None:
        raise ValueError(
            f"Unsupported aggregation type: '{agg}'. "
            f"Supported: {sorted(_AGG_TO_CLICKHOUSE)}"
        )
    return f"{fn}({expr})"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _find_dimension(model: dict[str, Any], name: str) -> dict[str, Any]:
    for dimension in model.get("dimensions", []):
        if dimension["name"] == name:
            return dimension
    raise KeyError(name)


def _find_measure(model: dict[str, Any], name: str) -> dict[str, Any]:
    for measure in model.get("measures", []):
        if measure["name"] == name:
            return measure
    raise KeyError(name)


def _qualify(expr: str, alias: str, force_qualified: bool) -> str:
    if not expr:
        return expr
    if "." in expr or "(" in expr or " " in expr or not force_qualified:
        return expr
    return f"{alias}.{expr}"


def _compile_join_chain(snapshot, binding: dict[str, Any], root_alias: str, warnings: list[str]) -> tuple[list[str], str]:
    join_sql: list[str] = []
    current_alias = root_alias
    alias_index = 0
    for edge in binding.get("edges", []):
        alias_index += 1
        next_alias = f"{root_alias}_j{alias_index}"
        right_model = snapshot.models[edge["target"]]
        join_type = "LEFT JOIN"
        relationship = edge["relationship"]
        if relationship.get("allow_any_join"):
            join_type = "ANY LEFT JOIN"
            warnings.append(
                f"ANY LEFT JOIN used on relationship {relationship.get('name', '')}; right-side duplicates are intentionally collapsed"
            )
        on_clause = " AND ".join(
            f"{current_alias}.{left_key} = {next_alias}.{right_key}"
            for left_key, right_key in zip(edge.get("left_keys", []), edge.get("right_keys", []))
        )
        join_sql.append(
            f"{join_type} {right_model['relation_name'] or ('dbt.' + right_model['name'])} AS {next_alias} ON {on_clause}"
        )
        current_alias = next_alias
    dimension_expr = _find_dimension(snapshot.models[binding["provider_model"]], binding["name"]).get("expr", binding["name"])
    return join_sql, current_alias + "." + dimension_expr if "." not in dimension_expr and "(" not in dimension_expr else dimension_expr


def _compile_filters(
    *,
    filters: list[dict[str, Any]],
    branch_dimensions: dict[str, str],
    metric_aliases: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Split a filter list into WHERE (dimension filters) and HAVING
    (metric filters) clauses for the current branch.

    A filter ``field`` MUST match either a metric output alias or a
    dimension registered on this branch. The previous implementation
    silently fell through to emitting ``f"{field} {op} {value}"`` when
    the field name didn't match anything — producing malformed SQL such
    as ``WHERE = 'DEX'`` when the field key was missing from both
    lookups. Now an unknown field raises a clear ValueError listing the
    valid options.

    Also accepts the public-API key names (``column`` / ``operator``)
    in addition to the internal short names (``field`` / ``op``) so the
    same filter shape works whether the caller comes through the MCP
    tool layer or constructs filters in-process.
    """
    where_clauses: list[str] = []
    having_clauses: list[str] = []
    valid_fields = sorted(set(branch_dimensions) | set(metric_aliases))
    for filter_item in filters:
        field = filter_item.get("field") or filter_item.get("column")
        if not field:
            raise ValueError(
                f"Filter is missing 'field' / 'column' key: {filter_item}"
            )
        op = filter_item.get("op") or filter_item.get("operator") or "="
        value = _sql_literal(filter_item.get("value"))
        if field in metric_aliases:
            having_clauses.append(f"{metric_aliases[field]} {op} {value}")
        elif field in branch_dimensions:
            where_clauses.append(f"{branch_dimensions[field]} {op} {value}")
        else:
            raise ValueError(
                f"Filter field '{field}' is not a dimension or metric on this branch. "
                f"Valid fields: {valid_fields}"
            )
    return where_clauses, having_clauses


def _compile_branch_cte(
    snapshot,
    branch: dict[str, Any],
    *,
    branch_index: int,
    request_filters: list[dict[str, Any]],
    force_qualified: bool = False,
) -> tuple[str, list[str], list[str], list[str]]:
    warnings: list[str] = []
    root_model = snapshot.models[branch["root_model"]]
    root_alias = f"b{branch_index}_root"
    select_dimensions: list[str] = []
    group_dimensions: list[str] = []
    branch_dimension_map: dict[str, str] = {}
    joins: list[str] = []

    for dimension_name, binding in branch["dimension_bindings"].items():
        if binding["local"]:
            # Prefer the dimension carried on the binding (so synthesised
            # bindings — e.g. time-spine upcasts emitted by the planner —
            # don't need an entry in the root model's `dimensions` list).
            dimension = binding.get("dimension") or _find_dimension(root_model, dimension_name)
            upcast_template = dimension.get("_upcast_template")
            if upcast_template:
                # Render the planner's upcast directive under this branch's
                # alias. e.g. `toMonday(b1_root.date) AS week`. The source
                # column comes from `_upcast_from_col` rather than `expr`
                # so we don't have to parse the template back out.
                source_col = dimension["_upcast_from_col"]
                expr = upcast_template.format(col=f"{root_alias}.{source_col}")
            else:
                expr = _qualify(dimension.get("expr", dimension_name), root_alias, force_qualified)
        else:
            join_sql, expr = _compile_join_chain(snapshot, binding, root_alias, warnings)
            joins.extend(join_sql)
        alias = dimension_name
        select_dimensions.append(f"{expr} AS {alias}")
        group_dimensions.append(alias if force_qualified else expr)
        branch_dimension_map[dimension_name] = alias if force_qualified else expr

    metric_selects: list[str] = []
    metric_alias_map: dict[str, str] = {}
    default_filters: list[dict[str, Any]] = []
    for metric_name in branch["metrics"]:
        metric = snapshot.metrics[metric_name]
        measure = _find_measure(root_model, metric["measure"])
        measure_expr = _qualify(measure.get("expr", metric["measure"]), root_alias, force_qualified)
        agg = measure.get("agg", "sum")
        metric_selects.append(f"{_agg_call(agg, measure_expr)} AS {metric_name}")
        metric_alias_map[metric_name] = metric_name
        default_filters.extend(metric.get("default_filters", []))

    where_filters, having_filters = _compile_filters(
        filters=[*default_filters, *request_filters],
        branch_dimensions=branch_dimension_map,
        metric_aliases=metric_alias_map,
    )

    select_clause = ",\n    ".join([*select_dimensions, *metric_selects]) or "1 AS one"
    relation_name = root_model["relation_name"] or f"dbt.{root_model['name']}"
    cte_name = f"branch_{branch_index}"
    sql = [
        f"{cte_name} AS (",
        "  SELECT",
        f"    {select_clause}",
        f"  FROM {relation_name} AS {root_alias}",
    ]
    sql.extend(f"  {join_clause}" for join_clause in joins)
    if where_filters:
        sql.append("  WHERE " + " AND ".join(where_filters))
    if select_dimensions:
        sql.append("  GROUP BY " + ", ".join(group_dimensions))
    if having_filters:
        sql.append("  HAVING " + " AND ".join(having_filters))
    sql.append(")")
    return "\n".join(sql), list(branch["dimension_bindings"].keys()), branch["metrics"], warnings


def _compile_single_branch_select(
    snapshot,
    branch: dict[str, Any],
    *,
    request_filters: list[dict[str, Any]],
    force_qualified: bool = False,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    root_model = snapshot.models[branch["root_model"]]
    root_alias = "b1_root"
    select_dimensions: list[str] = []
    group_dimensions: list[str] = []
    branch_dimension_map: dict[str, str] = {}

    for dimension_name, binding in branch["dimension_bindings"].items():
        if not binding["local"]:
            raise ValueError("Direct branch SQL only supports local dimensions")
        # See `_compile_branch_cte` for the upcast-template path. Same
        # logic in the single-branch optimisation; do not let the two
        # diverge.
        dimension = binding.get("dimension") or _find_dimension(root_model, dimension_name)
        upcast_template = dimension.get("_upcast_template")
        if upcast_template:
            source_col = dimension["_upcast_from_col"]
            expr = upcast_template.format(col=f"{root_alias}.{source_col}")
        else:
            expr = _qualify(
                dimension.get("expr", dimension_name),
                root_alias,
                force_qualified,
            )
        select_dimensions.append(f"{expr} AS {dimension_name}")
        group_dimensions.append(dimension_name if force_qualified else expr)
        branch_dimension_map[dimension_name] = dimension_name if force_qualified else expr

    metric_selects: list[str] = []
    metric_alias_map: dict[str, str] = {}
    default_filters: list[dict[str, Any]] = []
    for metric_name in branch["metrics"]:
        metric = snapshot.metrics[metric_name]
        measure = _find_measure(root_model, metric["measure"])
        measure_expr = _qualify(
            measure.get("expr", metric["measure"]),
            root_alias,
            force_qualified,
        )
        agg = measure.get("agg", "sum")
        metric_selects.append(f"{_agg_call(agg, measure_expr)} AS {metric_name}")
        metric_alias_map[metric_name] = metric_name
        default_filters.extend(metric.get("default_filters", []))

    where_filters, having_filters = _compile_filters(
        filters=[*default_filters, *request_filters],
        branch_dimensions=branch_dimension_map,
        metric_aliases=metric_alias_map,
    )
    if having_filters:
        raise ValueError("Direct branch SQL cannot inline HAVING filters safely")

    relation_name = root_model["relation_name"] or f"dbt.{root_model['name']}"
    select_clause = ",\n  ".join([*select_dimensions, *metric_selects]) or "1 AS one"
    sql = [
        "SELECT",
        f"  {select_clause}",
        f"FROM {relation_name} AS {root_alias}",
    ]
    if where_filters:
        sql.append("WHERE " + " AND ".join(where_filters))
    if select_dimensions:
        sql.append("GROUP BY " + ", ".join(group_dimensions))
    return "\n".join(sql), warnings


def _can_inline_single_branch(snapshot, branch: dict[str, Any]) -> bool:
    if any(not binding["local"] for binding in branch["dimension_bindings"].values()):
        return False

    root_model = snapshot.models[branch["root_model"]]
    for metric_name in branch["metrics"]:
        metric = snapshot.metrics[metric_name]
        measure = _find_measure(root_model, metric["measure"])
        agg = str(measure.get("agg", "sum")).lower()
        if agg not in {"sum", "count", "min", "max", "avg", "average"}:
            return False

    return True


def _order_by(dimensions: list[str], metrics: list[str], requested_order_by: list[str] | None = None) -> str:
    if requested_order_by:
        return ", ".join(requested_order_by)
    if "day" in dimensions:
        return "day DESC"
    if dimensions:
        return f"{dimensions[0]} ASC"
    if metrics:
        return f"{metrics[0]} DESC"
    return "1"


def compile_metric_plan(
    snapshot,
    plan: dict[str, Any],
    *,
    force_qualified: bool = False,
) -> tuple[str, list[str]]:
    started = time.perf_counter()
    warnings: list[str] = []
    branches = plan["branches"]
    ctes: list[str] = []
    branch_dimensions: list[str] = plan["resolved_dimensions"]
    all_metrics = plan["resolved_metrics"]
    limit = int(plan.get("limit", 100))
    requested_order_by = plan.get("order_by", [])

    if len(branches) == 1:
        branch = branches[0]
        if _can_inline_single_branch(snapshot, branch):
            try:
                sql, branch_warnings = _compile_single_branch_select(
                    snapshot,
                    branch,
                    request_filters=plan.get("filters", []),
                    force_qualified=force_qualified,
                )
                warnings.extend(branch_warnings)
                sql += (
                    f"\nORDER BY {_order_by(branch_dimensions, all_metrics, requested_order_by)}\n"
                    f"LIMIT {limit}"
                )
            except ValueError:
                cte_sql, _dims, _metrics, branch_warnings = _compile_branch_cte(
                    snapshot,
                    branch,
                    branch_index=1,
                    request_filters=plan.get("filters", []),
                    force_qualified=force_qualified,
                )
                ctes.append(cte_sql)
                warnings.extend(branch_warnings)
                branch_name = "branch_1"
                select_parts = [*branch_dimensions, *all_metrics]
                sql = (
                    "WITH\n"
                    + ",\n".join(ctes)
                    + "\nSELECT\n  "
                    + ",\n  ".join(select_parts)
                    + f"\nFROM {branch_name}\nORDER BY {_order_by(branch_dimensions, all_metrics, requested_order_by)}\nLIMIT {limit}"
                )
        else:
            cte_sql, _dims, _metrics, branch_warnings = _compile_branch_cte(
                snapshot,
                branch,
                branch_index=1,
                request_filters=plan.get("filters", []),
                force_qualified=force_qualified,
            )
            ctes.append(cte_sql)
            warnings.extend(branch_warnings)
            branch_name = "branch_1"
            select_parts = [*branch_dimensions, *all_metrics]
            sql = (
                "WITH\n"
                + ",\n".join(ctes)
                + "\nSELECT\n  "
                + ",\n  ".join(select_parts)
                + f"\nFROM {branch_name}\nORDER BY {_order_by(branch_dimensions, all_metrics, requested_order_by)}\nLIMIT {limit}"
            )
    else:
        for index, branch in enumerate(branches, start=1):
            cte_sql, _dims, _metrics, branch_warnings = _compile_branch_cte(
                snapshot,
                branch,
                branch_index=index,
                request_filters=plan.get("filters", []),
                force_qualified=force_qualified,
            )
            ctes.append(cte_sql)
            warnings.extend(branch_warnings)
        if branch_dimensions:
            union_keys = "\n  UNION DISTINCT\n".join(
                "  SELECT " + ", ".join(branch_dimensions) + f" FROM branch_{idx}"
                for idx in range(1, len(branches) + 1)
            )
            keys_projection = ", ".join(branch_dimensions)
        else:
            union_keys = "\n  UNION DISTINCT\n".join(
                f"  SELECT 1 AS join_key FROM branch_{idx}"
                for idx in range(1, len(branches) + 1)
            )
            keys_projection = "join_key"
        ctes.append("keys AS (\n" + union_keys + "\n)")
        select_parts = (
            [f"keys.{dimension} AS {dimension}" for dimension in branch_dimensions]
            if branch_dimensions
            else []
        )
        for index, branch in enumerate(branches, start=1):
            for metric_name in branch["metrics"]:
                select_parts.append(f"branch_{index}.{metric_name} AS {metric_name}")
        join_sql = []
        for index, _branch in enumerate(branches, start=1):
            if branch_dimensions:
                on_clause = " AND ".join(
                    f"keys.{dimension} = branch_{index}.{dimension}"
                    for dimension in branch_dimensions
                )
            else:
                on_clause = f"keys.{keys_projection} = 1"
            join_sql.append(f"LEFT JOIN branch_{index} ON {on_clause}")
        sql = (
            "WITH\n"
            + ",\n".join(ctes)
            + "\nSELECT\n  "
            + ",\n  ".join(select_parts)
            + "\nFROM keys\n"
            + "\n".join(join_sql)
            + f"\nORDER BY {_order_by(branch_dimensions, all_metrics, requested_order_by)}\nLIMIT {limit}"
        )

    observe_semantic_sql_compile_latency(
        planner_mode=plan["planner_mode"],
        elapsed_seconds=time.perf_counter() - started,
    )
    return sql, warnings
