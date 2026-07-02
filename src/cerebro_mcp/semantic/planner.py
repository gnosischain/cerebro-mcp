from __future__ import annotations

import copy
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


def _metric_is_candidate(snapshot, metric: dict[str, Any]) -> bool:
    """Metric is structurally executable but not yet promoted to approved.

    Mirrors the tool-layer ``_metric_is_candidate`` in
    ``tools/semantic/semantic.py``: the ``allow_candidate=True`` opt-in may
    only bypass the QUALITY gate — the root model must itself be approved
    (authorization is not negotiable) and the metric must declare at least
    one dimension so the planner has something to group by.
    """
    if not metric:
        return False
    if metric.get("quality_tier") == "approved":
        return False  # already executable via the normal path
    root_model = snapshot.models.get(metric.get("root_model", ""), {})
    if root_model.get("semantic_status") != "approved":
        return False  # root not even queryable — can't bypass that
    return bool(metric.get("allowed_dimensions"))


# Metric types computed FROM other metrics post-aggregation rather than from
# a measure. MVP scope: same-root only — every input metric must live on the
# SAME root_model so the inputs aggregate on one branch and the derived value
# is a computed column over that branch's output aliases.
_DERIVED_METRIC_TYPES = ("ratio", "derived")


def _metric_type(metric: dict[str, Any]) -> str:
    return str(metric.get("type", "") or "").lower()


def _derived_input_names(metric: dict[str, Any]) -> list[str]:
    """Input metric names for a ratio/derived metric.

    Accepts both the bare-string and the MetricFlow ``{name: ...}`` mapping
    forms. Ratio order is numerator first, denominator second (the compiler
    relies on it); empty entries are preserved for ratio so the caller can
    flag a missing numerator/denominator precisely.
    """

    def _name(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return str(value.get("name", "") or "")
        return ""

    type_params = metric.get("type_params") or {}
    if _metric_type(metric) == "ratio":
        return [_name(type_params.get(key)) for key in ("numerator", "denominator")]
    return [_name(item) for item in (type_params.get("metrics") or [])]


def _expand_derived_metric(
    snapshot, metric: dict[str, Any], *, allow_candidate: bool = False
) -> dict[str, Any]:
    """Resolve a ratio/derived metric into its same-root input metrics.

    Returns ``{"name", "kind", "inputs", "expr", "root_model"}``. Every input
    must itself be an executable simple metric (candidate inputs allowed only
    under ``allow_candidate`` — a derived metric is executable iff ALL inputs
    are). Raises PlanningError for missing/unknown/non-executable inputs,
    nested derived inputs, and cross-root input sets (out of MVP scope).
    """
    name = metric.get("name", "")
    kind = _metric_type(metric)
    input_names = _derived_input_names(metric)
    if kind == "ratio" and not all(input_names):
        raise PlanningError(
            f"Ratio metric '{name}' must declare both type_params.numerator "
            "and type_params.denominator"
        )
    input_names = [input_name for input_name in input_names if input_name]
    if not input_names:
        raise PlanningError(
            f"Derived metric '{name}' declares no input metrics in "
            "type_params.metrics"
        )
    expr = ""
    if kind == "derived":
        expr = str((metric.get("type_params") or {}).get("expr") or "")
        if not expr:
            raise PlanningError(
                f"Derived metric '{name}' is missing type_params.expr"
            )
    resolved_inputs: list[str] = []
    for input_name in input_names:
        resolved = _resolve_metric_name(
            snapshot, input_name, allow_candidate=allow_candidate
        )
        if _metric_type(snapshot.metrics[resolved]) in _DERIVED_METRIC_TYPES:
            raise PlanningError(
                f"Metric '{name}' uses derived metric '{resolved}' as an "
                "input; nested ratio/derived metrics are not supported"
            )
        resolved_inputs.append(resolved)
    roots = sorted(
        {snapshot.metrics[resolved].get("root_model", "") for resolved in resolved_inputs}
    )
    if len(roots) > 1:
        raise PlanningError(
            f"Metric '{name}' combines inputs from multiple root models "
            f"({', '.join(roots)}). Cross-root derived metrics are not "
            "supported yet — query each input metric separately and combine "
            "the results."
        )
    return {
        "name": name,
        "kind": kind,
        "inputs": resolved_inputs,
        "expr": expr,
        "root_model": roots[0],
    }


def _resolve_metric_name(
    snapshot, requested_name: str, *, allow_candidate: bool = False
) -> str:
    normalized = normalize(requested_name)
    if requested_name in snapshot.metrics:
        metric = snapshot.metrics[requested_name]
        if _metric_is_executable(snapshot, metric):
            return requested_name
        if allow_candidate and _metric_is_candidate(snapshot, metric):
            return requested_name
        raise PlanningError(f"Metric {requested_name} is not approved for semantic execution")
    if normalized in snapshot.synonym_index:
        resolved_name = snapshot.synonym_index[normalized]
        metric = snapshot.metrics.get(resolved_name, {})
        if _metric_is_executable(snapshot, metric):
            return resolved_name
        if allow_candidate and _metric_is_candidate(snapshot, metric):
            return resolved_name
        raise PlanningError(f"Metric {requested_name} is not approved for semantic execution")
    raise PlanningError(f"Unknown metric: {requested_name}")


# ──────────────────────────────────────────────────────────────────────
# Dimension-binding cache
# ──────────────────────────────────────────────────────────────────────
# A dimension binding is a pure function of the snapshot content, so it can
# be memoized per (registry_hash, root_model, dimension_name). Values are the
# full `(binding, first_rejected_or_None)` result tuple. Cached values are
# stored AND served as deep copies so callers can freely mutate what they get
# back (`_apply_repair` in tools/semantic/semantic.py deep-copies the whole
# plan anyway, but the compiler also builds per-branch dicts off bindings —
# copies make the cache immune to either). Failed resolutions (PlanningError)
# are NOT cached: they're cheap to recompute relative to their frequency and
# caching them would freeze error messages across authoring iterations that
# share a hash prefix. Bounded like _TOKEN_IDF_CACHE: once more than 4
# distinct registry hashes accumulate, the cache is dropped wholesale.
_BINDING_CACHE: dict[
    tuple[str, str, str], tuple[dict[str, Any], dict[str, Any] | None]
] = {}
_BINDING_CACHE_MAX_REGISTRY_HASHES = 4


def _resolve_dimension_binding(
    snapshot, root_model: str, dimension_name: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    registry_hash = getattr(snapshot, "registry_hash", "") or ""
    cache_key = (registry_hash, root_model, dimension_name)
    if registry_hash:
        cached = _BINDING_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
    result = _resolve_dimension_binding_uncached(snapshot, root_model, dimension_name)
    # Empty hash means "snapshot identity unknown" — never cache under it,
    # two different registries could collide on the sentinel key.
    if registry_hash:
        if (
            len({key[0] for key in _BINDING_CACHE})
            > _BINDING_CACHE_MAX_REGISTRY_HASHES
        ):
            _BINDING_CACHE.clear()
        _BINDING_CACHE[cache_key] = copy.deepcopy(result)
    return result


def _enrichment_block_reason(edges: tuple | list) -> str | None:
    """Dimension enrichment compiles to a ROW-LEVEL join chain hanging off
    the metric's root model, so every edge on the path must be safe to
    apply per-row. Returns a human-readable rejection reason when any edge
    would fan out root rows (and thereby inflate sum/count measures), or
    None when the whole path is safe.

    Rules, per edge (traversal direction — `edge['cardinality']` is
    already inverted for reverse traversal):
    - relationship flagged `aggregate_then_join_only: true` -> reject;
    - relationship flagged `safe_for_dimension_enrichment: false`
      (explicitly False, missing defaults permissive) -> reject;
    - cardinality one_to_many / many_to_many -> reject REGARDLESS of
      flags, because fan-out is structural, not a matter of opinion.
    """
    for edge in edges:
        relationship = edge.get("relationship") or {}
        edge_name = relationship.get("name") or edge.get("name") or "<unnamed>"
        if relationship.get("aggregate_then_join_only"):
            return (
                f"edge {edge_name} is aggregate_then_join_only — row-level "
                "enrichment through it would fan out rows and inflate measures"
            )
        if relationship.get("safe_for_dimension_enrichment") is False:
            return (
                f"edge {edge_name} is marked safe_for_dimension_enrichment: "
                "false — row-level enrichment through it is not allowed"
            )
        cardinality = edge.get("cardinality", "")
        if cardinality in ("one_to_many", "many_to_many"):
            return (
                f"edge {edge_name} has {cardinality} cardinality — row-level "
                "enrichment through it would fan out rows and inflate measures"
            )
    return None


def _resolve_dimension_binding_uncached(snapshot, root_model: str, dimension_name: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
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
        block_reason = _enrichment_block_reason(path.edges)
        if block_reason is not None:
            rejected.append(
                {
                    "dimension": dimension_name,
                    "provider_model": provider_model,
                    "reason": block_reason,
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


def _branch_available_axes(snapshot, root_model_name: str) -> list[str]:
    """List the dimension names actually queryable on a root model:
    its declared dimensions plus any time-spine grains reachable via
    an upcast (e.g. `week`/`month` synthesised from a daily column).
    Used to build actionable multi-root PlanningError messages."""
    root = snapshot.models.get(root_model_name, {})
    axes = [dim["name"] for dim in root.get("dimensions", [])]
    seen = set(axes)
    for model_name, model in snapshot.models.items():
        if not _is_time_spine_model(model_name):
            continue
        for dim in model.get("dimensions", []):
            grain_name = dim.get("name")
            if not grain_name or grain_name in seen:
                continue
            if _try_time_spine_upcast(snapshot, root_model_name, grain_name) is not None:
                axes.append(grain_name)
                seen.add(grain_name)
    return axes


def _raise_multi_root_axis_error(
    snapshot,
    *,
    branches_by_root: dict[str, list[dict[str, Any]]],
    failed_dimensions: list[str],
) -> None:
    """Raise ONE structured PlanningError describing every root's usable
    axes when a multi-root plan cannot bind a requested dimension on
    every branch. Raised at planning time — before any SQL executes."""
    axes_by_root = {
        root_model: _branch_available_axes(snapshot, root_model)
        for root_model in branches_by_root
    }
    lines: list[str] = []
    for root_model, branch_metrics in branches_by_root.items():
        axes = axes_by_root[root_model]
        axes_text = ", ".join(axes) if axes else "none"
        for metric in branch_metrics:
            lines.append(
                f"  - {metric['name']} (root: {root_model}): available axes = {axes_text}"
            )
    shared: set[str] | None = None
    for axes in axes_by_root.values():
        shared = set(axes) if shared is None else shared & set(axes)
    shared_text = ", ".join(sorted(shared)) if shared else "none"
    dims_text = ", ".join(f"'{dim}'" for dim in failed_dimensions)
    raise PlanningError(
        f"Metrics span multiple root models with no shared axis for {dims_text}:\n"
        + "\n".join(lines)
        + "\nOptions: (1) query each metric separately; "
        + f"(2) use a dimension available on every root (shared: {shared_text}); "
        + "(3) drop dimensions for a scalar comparison."
    )


def plan_metric_query(
    snapshot,
    *,
    requested_metrics: list[str],
    requested_dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    agent_role: str = "unknown",
    allow_candidate: bool = False,
) -> dict[str, Any]:
    # `allow_candidate=True` mirrors the tool-layer opt-in: candidate-tier
    # metrics plan iff their root model is approved and they declare at
    # least one dimension (see `_metric_is_candidate`). Plans are never
    # memoized (only dimension bindings are, and those don't depend on the
    # flag), so no cache key needs to carry it.
    started = time.perf_counter()
    requested_dimensions = requested_dimensions or []
    filters = filters or []
    planner_mode = "unsupported"
    try:
        resolved_metric_names = [
            _resolve_metric_name(snapshot, name, allow_candidate=allow_candidate)
            for name in requested_metrics
        ]
        metrics = [snapshot.metrics[name] for name in resolved_metric_names]
        branches_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
        derived_specs: list[dict[str, Any]] = []
        seen_by_root: dict[str, set[str]] = defaultdict(set)

        def _add_branch_metric(root_model: str, branch_metric: dict[str, Any]) -> None:
            if branch_metric["name"] in seen_by_root[root_model]:
                return
            seen_by_root[root_model].add(branch_metric["name"])
            branches_by_root[root_model].append(branch_metric)

        for metric in metrics:
            if _metric_type(metric) in _DERIVED_METRIC_TYPES:
                # Expand the ratio/derived metric onto the SAME branch as its
                # inputs: the inputs aggregate as normal branch metrics and
                # the derived value becomes a computed column the compiler
                # appends to the outer select (see plan["derived_metrics"]).
                spec = _expand_derived_metric(
                    snapshot, metric, allow_candidate=allow_candidate
                )
                for input_name in spec["inputs"]:
                    _add_branch_metric(spec["root_model"], snapshot.metrics[input_name])
                derived_specs.append(
                    {key: spec[key] for key in ("name", "kind", "inputs", "expr")}
                )
            else:
                _add_branch_metric(metric["root_model"], metric)

        branches: list[dict[str, Any]] = []
        selected_paths: list[dict[str, Any]] = []
        rejected_paths: list[dict[str, Any]] = []
        is_multi_root = len(branches_by_root) > 1
        failed_dimensions: list[str] = []
        for root_model, branch_metrics in branches_by_root.items():
            dimension_bindings = {}
            for dimension_name in requested_dimensions:
                try:
                    binding, rejected = _resolve_dimension_binding(snapshot, root_model, dimension_name)
                except PlanningError:
                    # Single-root plans keep the detailed per-dimension
                    # error. Multi-root plans collect EVERY branch's
                    # failure so we can raise one structured error that
                    # shows all roots and their usable axes.
                    if not is_multi_root:
                        raise
                    if dimension_name not in failed_dimensions:
                        failed_dimensions.append(dimension_name)
                    continue
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

        if failed_dimensions:
            _raise_multi_root_axis_error(
                snapshot,
                branches_by_root=branches_by_root,
                failed_dimensions=failed_dimensions,
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
        if derived_specs:
            plan["derived_metrics"] = derived_specs
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
