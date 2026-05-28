from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from cerebro_mcp.runtime.observability import observe_semantic_planner_failure, observe_semantic_planner_latency
from cerebro_mcp.semantic.graph import MAX_JOIN_HOPS, PlanningError, find_safest_path
from cerebro_mcp.semantic.index import normalize


# ──────────────────────────────────────────────────────────────────────
# Time-spine grain upcasts
# ──────────────────────────────────────────────────────────────────────
# When a metric is defined at a finer grain (e.g. `day`) than the
# dimension a caller asks for (e.g. `week`), the planner can synthesise
# a derived `GROUP BY` if a time-spine model declares the coarser grain
# AND we know how to derive it from the finer one.
#
# Templates are ClickHouse expression strings parameterised on `{col}`
# (the qualified source column reference). Add more pairs here if you
# extend the spine vocabulary (e.g. hour → day, day → quarter).
#
# Downcast (week → day, month → week, ...) is intentionally not
# supported: you can't synthesise daily values from weekly aggregates.
_TIME_UPCAST_TEMPLATES: dict[tuple[str, str], str] = {
    ("week",  "day"):    "toMonday({col})",
    ("month", "day"):    "toStartOfMonth({col})",
    ("month", "week"):   "toStartOfMonth({col})",
}


def _is_time_spine_model(model_name: str) -> bool:
    return model_name.startswith("dim_time_spine_")


def _find_time_spine_grain(snapshot, dimension_name: str) -> str | None:
    """Return the granularity (`day` / `week` / `month`) of `dimension_name`
    iff it's the primary time column on some `dim_time_spine_*` model.

    This is how the planner knows "the user said `week` and means the
    weekly spine" without requiring an explicit relationship traversal —
    we treat the spine vocabulary as a globally-resolvable grain.
    """
    for model_name, model in snapshot.models.items():
        if not _is_time_spine_model(model_name):
            continue
        for dim in model.get("dimensions", []):
            if dim.get("name") != dimension_name:
                continue
            return (dim.get("type_params") or {}).get("time_granularity")
    return None


def _try_time_spine_upcast(
    snapshot,
    root_model_name: str,
    dimension_name: str,
) -> dict[str, Any] | None:
    """Synthesise a derived dimension binding when ``dimension_name`` is a
    time-spine grain that's coarser than a time column on the root model.

    Returns a ``local=True`` binding with ``_upcast_template`` /
    ``_upcast_from_col`` fields the SQL compiler reads to emit a
    ``<template>(<root_alias>.<col>) AS <dimension>`` projection. Returns
    ``None`` when no upcast pair applies — caller falls back to the
    existing dimension-index resolution.
    """
    target_grain = _find_time_spine_grain(snapshot, dimension_name)
    if target_grain is None:
        return None
    root = snapshot.models.get(root_model_name, {})
    for dim in root.get("dimensions", []):
        if dim.get("type") != "time":
            continue
        source_grain = (dim.get("type_params") or {}).get("time_granularity")
        if not source_grain:
            continue
        template = _TIME_UPCAST_TEMPLATES.get((target_grain, source_grain))
        if template is None:
            continue
        source_col = dim.get("expr") or dim["name"]
        return {
            "name": dimension_name,
            "provider_model": root_model_name,
            "local": True,
            "path": [root_model_name],
            "edges": [],
            # Derived dimension carries the metadata the compiler needs to
            # render `template(b<i>_root.<source_col>)` under the right
            # branch alias. expr is informational; the compiler reads
            # _upcast_template / _upcast_from_col directly.
            "dimension": {
                "name": dimension_name,
                "type": "time",
                "expr": template.format(col=source_col),
                "type_params": {"time_granularity": target_grain},
                "_upcast_template": template,
                "_upcast_from_col": source_col,
                "_upcast_source_grain": source_grain,
            },
            "_synthesised": "time_spine_upcast",
        }
    return None


def _metric_is_executable(snapshot, metric: dict[str, Any]) -> bool:
    root_model = snapshot.models.get(metric.get("root_model", ""), {})
    return (
        metric.get("quality_tier") == "approved"
        and metric.get("semantic_status") == "approved"
        and root_model.get("semantic_status") == "approved"
    )


def _resolve_metric_name(snapshot, requested_name: str) -> str:
    normalized = normalize(requested_name)
    if requested_name in snapshot.metrics:
        metric = snapshot.metrics[requested_name]
        if _metric_is_executable(snapshot, metric):
            return requested_name
        raise PlanningError(f"Metric {requested_name} is not approved for semantic execution")
    if normalized in snapshot.synonym_index:
        resolved_name = snapshot.synonym_index[normalized]
        metric = snapshot.metrics.get(resolved_name, {})
        if _metric_is_executable(snapshot, metric):
            return resolved_name
        raise PlanningError(f"Metric {requested_name} is not approved for semantic execution")
    raise PlanningError(f"Unknown metric: {requested_name}")


def _resolve_dimension_binding(snapshot, root_model: str, dimension_name: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    root = snapshot.models[root_model]
    for dimension in root.get("dimensions", []):
        if dimension["name"] == dimension_name:
            return (
                {
                    "name": dimension_name,
                    "provider_model": root_model,
                    "local": True,
                    "path": [root_model],
                    "edges": [],
                    "dimension": dimension,
                },
                None,
            )

    candidates = []
    rejected: list[dict[str, Any]] = []
    for provider in snapshot.dimension_index.get(dimension_name, []):
        if provider.get("semantic_status") != "approved":
            continue
        provider_model = provider["provider_model"]
        try:
            path = find_safest_path(
                snapshot.registry_hash,
                snapshot.graph,
                root_model,
                provider_model,
                max_hops=MAX_JOIN_HOPS,
            )
        except PlanningError as exc:
            rejected.append(
                {
                    "dimension": dimension_name,
                    "provider_model": provider_model,
                    "reason": str(exc),
                }
            )
            continue
        candidates.append(
            {
                "name": dimension_name,
                "provider_model": provider_model,
                "local": False,
                "path": list(path.models),
                "edges": [dict(edge) for edge in path.edges],
                "hop_count": len(path.models) - 1,
                "cost": path.cost,
                "dimension": provider["dimension"],
            }
        )

    if not candidates:
        # Last-chance: time-spine grain upcast. Lets the planner answer
        # "I want `week` from a metric with only a `date` column" by
        # synthesising `toMonday(date)` rather than failing.
        upcast_binding = _try_time_spine_upcast(snapshot, root_model, dimension_name)
        if upcast_binding is not None:
            return upcast_binding, None

        local_dims = sorted(d["name"] for d in root.get("dimensions", []))
        hints: list[str] = [f"Dimension '{dimension_name}' is not reachable from '{root_model}'."]

        if rejected:
            reasons = "; ".join(
                f"{r['provider_model']}: {r['reason']}" for r in rejected[:3]
            )
            hints.append(f"Rejected paths: {reasons}.")

        all_providers = snapshot.dimension_index.get(dimension_name, [])
        unapproved = [
            p["provider_model"]
            for p in all_providers
            if p.get("semantic_status") != "approved"
        ]
        if unapproved and not rejected:
            hints.append(
                f"Providers exist but are not approved: {', '.join(unapproved[:3])}."
            )

        if local_dims:
            hints.append(
                f"Available dimensions on '{root_model}': {', '.join(local_dims[:10])}."
            )
        else:
            hints.append(f"'{root_model}' has no local dimensions.")

        raise PlanningError(" ".join(hints))

    candidates.sort(key=lambda candidate: (candidate["cost"], candidate["hop_count"], candidate["provider_model"]))
    best = candidates[0]
    if len(candidates) > 1:
        peer = candidates[1]
        if peer["cost"] == best["cost"] and peer["path"] != best["path"]:
            raise PlanningError(
                f"Ambiguous semantic provider for dimension '{dimension_name}' from '{root_model}': "
                f"'{best['provider_model']}' and '{peer['provider_model']}' have equal cost. "
                f"Approve a preferred path or remove one provider."
            )

    return best, rejected[0] if rejected else None


def plan_metric_query(
    snapshot,
    *,
    requested_metrics: list[str],
    requested_dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    agent_role: str = "unknown",
) -> dict[str, Any]:
    started = time.perf_counter()
    requested_dimensions = requested_dimensions or []
    filters = filters or []
    planner_mode = "unsupported"
    try:
        resolved_metric_names = [_resolve_metric_name(snapshot, name) for name in requested_metrics]
        metrics = [snapshot.metrics[name] for name in resolved_metric_names]
        branches_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for metric in metrics:
            branches_by_root[metric["root_model"]].append(metric)

        branches: list[dict[str, Any]] = []
        selected_paths: list[dict[str, Any]] = []
        rejected_paths: list[dict[str, Any]] = []
        for root_model, branch_metrics in branches_by_root.items():
            dimension_bindings = {}
            for dimension_name in requested_dimensions:
                binding, rejected = _resolve_dimension_binding(snapshot, root_model, dimension_name)
                dimension_bindings[dimension_name] = binding
                selected_paths.append(
                    {
                        "dimension": dimension_name,
                        "root_model": root_model,
                        "provider_model": binding["provider_model"],
                        "path": binding["path"],
                    }
                )
                if rejected:
                    rejected_paths.append(rejected)

            branches.append(
                {
                    "root_model": root_model,
                    "metrics": [metric["name"] for metric in branch_metrics],
                    "dimension_bindings": dimension_bindings,
                }
            )

        if len(branches) == 1:
            has_remote_dimension = any(
                not binding["local"]
                for binding in branches[0]["dimension_bindings"].values()
            )
            planner_mode = "enriched_single_model" if has_remote_dimension else "single_model"
        else:
            planner_mode = "multi_branch_aggregate_join"
            for branch in branches:
                if set(branch["dimension_bindings"]) != set(requested_dimensions):
                    raise PlanningError("Requested dimensions must be reachable from every branch")

        plan = {
            "requested_metrics": requested_metrics,
            "resolved_metrics": resolved_metric_names,
            "requested_dimensions": requested_dimensions,
            "resolved_dimensions": requested_dimensions,
            "planner_mode": planner_mode,
            "root_models": sorted(branches_by_root.keys()),
            "branches": branches,
            "filters": filters,
            "selected_paths": selected_paths,
            "rejected_paths": rejected_paths,
        }
        observe_semantic_planner_latency(
            planner_mode=planner_mode,
            elapsed_seconds=time.perf_counter() - started,
        )
        return plan
    except PlanningError as exc:
        observe_semantic_planner_failure(
            reason=str(exc).split(":")[0].replace(" ", "_").lower(),
            planner_mode=planner_mode,
            agent_role=agent_role,
        )
        raise
