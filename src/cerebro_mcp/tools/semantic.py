from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from cerebro_mcp import runtime_state
from cerebro_mcp.catalog_loader import catalog
from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.observability import (
    log_event,
    observe_cache_hit,
    observe_cache_miss,
    observe_research_semantic_evidence,
    observe_semantic_bypass,
    observe_semantic_docs_read,
    observe_semantic_fallback,
    observe_semantic_route,
    observe_semantic_query_attempt,
    observe_semantic_query_latency,
    observe_semantic_query_repair,
    observe_semantic_tool_call,
)
from cerebro_mcp.research_store import ResearchStore
from cerebro_mcp.semantic_index import (
    build_token_idf,
    normalize,
    score_metric,
    token_overlap,
)
from cerebro_mcp.semantic_loader import semantic_runtime
from cerebro_mcp.semantic_models import (
    AnalyticsPreflightResult,
    MetricDetailsResult,
    MetricDiscoveryHit,
    MetricDiscoveryResult,
    MetricQueryExplanation,
    SemanticQueryResult,
    SemanticRetryTrace,
)
from cerebro_mcp.semantic_planner import PlanningError, plan_metric_query
from cerebro_mcp.semantic_sql_compiler import compile_metric_plan
from cerebro_mcp.tool_output import build_query_summary, format_results_table, normalize_rows, truncate_response
from cerebro_mcp.tools.session_state import state


logger = logging.getLogger(__name__)
_last_semantic_refresh = 0.0
BUNDLE_MANIFEST_FILENAME = "bundle_manifest.json"
_TIME_DIMENSION_HINTS = (
    (("over time", "trend", "daily", "by day"), "day"),
    (("weekly", "by week"), "week"),
    (("monthly", "by month"), "month"),
    (("hourly", "by hour"), "hour"),
)
_DIMENSION_HINTS = (
    (("by sector", "sector"), "sector"),
)
_DIMENSION_ALIASES = {
    "date": "day",
}
_PREFLIGHT_MIN_SCORE = 30
_QUERY_SPECIFICITY_TOKENS = {
    "wallet",
    "wallets",
    "owner",
    "owners",
    "gpay",
    "payment",
    "payments",
    "spend",
    "spending",
    "cashback",
    "overlap",
    "staking",
    "stake",
    "withdrawal",
    "withdrawals",
    "address",
    "addresses",
    "linked",
    "link",
    "behavior",
    "behaviour",
}


def _trace_semantic_event(
    action: str,
    content: str,
    payload: dict[str, Any],
    *,
    event_kind: str = "semantic_trace",
) -> None:
    try:
        from cerebro_mcp.tools.reasoning import record_trace_event

        record_trace_event(
            action,
            content=content,
            payload=payload,
            event_kind=event_kind,
        )
    except Exception:
        pass


def _resolve_agent_role(explicit_role: str = "") -> str:
    return explicit_role or runtime_state.current_agent_role or "unknown"


# Cache the per-snapshot idf table so we don't recompute it on every
# `discover_metrics` call. Keyed on the snapshot's registry hash so a
# semantic reload (PR 4) invalidates automatically.
_TOKEN_IDF_CACHE: dict[str, dict[str, float]] = {}


def _token_idf_for_snapshot(snapshot) -> dict[str, float]:
    """Return (and cache) the idf weight table for the snapshot's metrics.

    Computing idf is O(N_metrics × |blob|) — cheap (a few hundred
    metrics) but not free per-call. The cache is keyed on
    ``registry_hash`` so:
      * Same snapshot, multiple discover calls -> shared computation.
      * Registry reload (PR 4 force_reload) -> fresh hash, fresh table.
    """
    key = getattr(snapshot, "registry_hash", "") or ""
    cached = _TOKEN_IDF_CACHE.get(key)
    if cached is not None:
        return cached
    table = build_token_idf(snapshot.metrics.values())
    # Bound the cache to a few entries so stale snapshots don't leak
    # memory on long-running servers that survive many reloads.
    if len(_TOKEN_IDF_CACHE) > 4:
        _TOKEN_IDF_CACHE.clear()
    _TOKEN_IDF_CACHE[key] = table
    return table


def _metric_is_executable(snapshot, metric: dict[str, Any] | None) -> bool:
    if not metric:
        return False
    root_model = snapshot.models.get(metric.get("root_model", ""), {})
    return (
        metric.get("quality_tier") == "approved"
        and metric.get("semantic_status") == "approved"
        and root_model.get("semantic_status") == "approved"
    )


def _metric_is_candidate(snapshot, metric: dict[str, Any] | None) -> bool:
    """Metric is structurally executable but not yet promoted to approved.

    Used to gate the `allow_candidate=True` opt-in path: a candidate metric
    can be force-run for authoring/testing iff its root model is itself
    approved AND it has at least one allowed_dimension (so the planner has
    something to group by — see `_metric_is_scalar_kpi` for the no-dim case).
    """
    if not metric:
        return False
    if metric.get("quality_tier") == "approved":
        return False  # already executable via the normal path
    root_model = snapshot.models.get(metric.get("root_model", ""), {})
    if root_model.get("semantic_status") != "approved":
        return False  # root not even queryable — can't bypass that
    return bool(metric.get("allowed_dimensions"))


def _metric_is_scalar_kpi(metric: dict[str, Any] | None) -> bool:
    """Metric has no dimensions to group by — typically a single-row KPI
    view backing a dashboard card. The semantic planner can't aggregate
    these, so we surface a dedicated error directing the caller at
    execute_query rather than the generic "not approved" message.
    """
    if not metric:
        return False
    return not metric.get("allowed_dimensions")


def _lookup_metric(snapshot, metric_name: str) -> tuple[str, dict[str, Any] | None]:
    normalized = normalize(metric_name)
    resolved_name = snapshot.synonym_index.get(normalized, metric_name)
    return resolved_name, snapshot.metrics.get(resolved_name)


def _snapshot_or_error(require_execution: bool = False):
    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return None, "Semantic snapshot unavailable."
    if require_execution and not semantic_runtime.is_execution_available:
        reason = semantic_runtime.stale_reason or "semantic execution unavailable"
        return None, f"Semantic execution unavailable: {reason}"
    return snapshot, None


def _maybe_refresh_semantic() -> None:
    global _last_semantic_refresh
    now = time.time()
    if now - _last_semantic_refresh < settings.SEMANTIC_REFRESH_INTERVAL_SECONDS:
        return
    _last_semantic_refresh = now
    manifest.reload_if_changed()
    catalog.reload_if_changed()
    semantic_runtime.refresh_if_changed()


def _classify_repairable_error(error: Exception) -> dict[str, str] | None:
    message = str(error)
    lowered = message.lower()
    if (
        "unknown_identifier" in lowered
        or "unknown expression" in lowered
        or "ambiguous column" in lowered
    ):
        return {"action": "qualify_identifiers", "error_class": "unknown_identifier"}
    if "not in group by" in lowered or "not under aggregate function" in lowered:
        return {"action": "group_by_aliases", "error_class": "aggregate_boundary"}
    return None


def _apply_repair(plan: dict[str, Any], repair: dict[str, str]) -> dict[str, Any]:
    updated = copy.deepcopy(plan)
    compiler_options = dict(updated.get("compiler_options", {}))
    if repair["action"] == "qualify_identifiers":
        compiler_options["force_qualified"] = True
    elif repair["action"] == "group_by_aliases":
        compiler_options["force_qualified"] = True
    updated["compiler_options"] = compiler_options
    return updated


def _build_explanation(plan: dict[str, Any], sql: str, warnings: list[str], repair_traces: list[SemanticRetryTrace]) -> MetricQueryExplanation:
    summary = [
        f"Planner mode: `{plan['planner_mode']}`",
        f"Resolved metrics: {', '.join(plan['resolved_metrics']) or 'none'}",
        f"Resolved dimensions: {', '.join(plan['resolved_dimensions']) or 'none'}",
    ]
    if warnings:
        summary.append("Warnings: " + "; ".join(warnings))
    return MetricQueryExplanation(
        requested_metrics=plan["requested_metrics"],
        resolved_metrics=plan["resolved_metrics"],
        requested_dimensions=plan["requested_dimensions"],
        resolved_dimensions=plan["resolved_dimensions"],
        planner_mode=plan["planner_mode"],
        root_models=plan["root_models"],
        selected_paths=plan["selected_paths"],
        rejected_paths=plan["rejected_paths"],
        compiled_sql=sql,
        warnings=warnings,
        repair_traces=repair_traces,
        summary_markdown=truncate_response("\n".join(summary)),
    )


def _recommended_semantic_next_tool(route: str, mode: str, hit_count: int) -> str:
    if route not in ("semantic_ready", "hybrid_ready"):
        return "execute_query"
    if mode == "chart":
        return "quick_metric_chart" if hit_count <= 1 else "discover_metrics"
    if mode == "report":
        if route == "hybrid_ready":
            return "discover_metrics"
        return "generate_metric_charts" if hit_count <= 1 else "discover_metrics"
    return "query_metrics" if hit_count <= 1 else "discover_metrics"


def _extract_topic_labels(
    query: str,
    accepted: list[tuple[int, str, dict[str, Any]]],
    scored_but_rejected: list[tuple[int, str, dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Extract covered and uncovered topic labels from the query.

    Covered topics are metric labels/names that matched.
    Uncovered topics are significant query token clusters that didn't match
    any accepted metric.
    """
    covered: list[str] = []
    covered_tokens: set[str] = set()

    for _score, metric_name, metric in accepted:
        label = metric.get("label") or metric_name.replace("_", " ")
        covered.append(label)
        covered_tokens.update(_metric_tokens(metric))
        # Dimensions are also covered by the metric
        for dim in metric.get("allowed_dimensions", []):
            covered_tokens.update(_query_tokens(dim))

    query_toks = {t.rstrip("?.,!;:") for t in _query_tokens(query)}
    # Remove stop words and very short tokens from uncovered detection
    _STOP_WORDS = {
        "the", "a", "an", "and", "or", "for", "in", "on", "of", "to",
        "by", "is", "it", "at", "as", "be", "do", "so", "if", "up",
        "with", "from", "this", "that", "what", "how", "when", "where",
        "are", "was", "were", "been", "being", "have", "has", "had",
        "not", "but", "all", "each", "every", "both", "few", "more",
        "most", "other", "some", "such", "than", "too", "very", "can",
        "will", "just", "should", "now", "also", "into", "only",
        "many", "much", "any", "our", "its", "their", "there", "here",
        "report", "show", "give", "me", "please", "want", "need",
        "last", "past", "recent", "current", "latest", "over", "time",
        "weekly", "daily", "monthly", "trend", "trends", "vs",
        "about", "between", "during", "since", "until", "after",
        "before", "top", "total", "overall", "summary", "breakdown",
        "analysis", "compare", "comparison", "across", "per",
    }
    significant_uncovered = query_toks - covered_tokens - _STOP_WORDS
    significant_uncovered = {t for t in significant_uncovered if len(t) > 2}

    uncovered: list[str] = sorted(significant_uncovered) if significant_uncovered else []
    return covered, uncovered


def _query_tokens(text: str) -> set[str]:
    return {token for token in normalize(text).replace("_", " ").split() if token}


def _metric_tokens(metric: dict[str, Any]) -> set[str]:
    blob = " ".join(
        filter(
            None,
            [
                metric.get("name", ""),
                metric.get("label", ""),
                metric.get("description", ""),
                metric.get("search_blob", ""),
                *metric.get("all_synonyms", []),
            ],
        )
    )
    return _query_tokens(blob)


def _has_strong_metric_phrase_match(query: str, metric: dict[str, Any]) -> bool:
    normalized_query = normalize(query)
    if normalized_query == metric.get("name", ""):
        return True
    if normalized_query in metric.get("all_synonyms", []):
        return True
    return any(
        synonym
        and len(synonym.split()) >= 2
        and synonym in normalized_query
        for synonym in metric.get("all_synonyms", [])
    )


def _has_query_intent_fit(query: str, metric: dict[str, Any]) -> bool:
    specific_tokens = _query_tokens(query) & _QUERY_SPECIFICITY_TOKENS
    if not specific_tokens:
        return True
    metric_tokens = _metric_tokens(metric)
    return specific_tokens.issubset(metric_tokens)


def _is_preflight_ready_match(query: str, metric: dict[str, Any], score: int) -> bool:
    if not _has_query_intent_fit(query, metric):
        return False
    if _has_strong_metric_phrase_match(query, metric):
        return True
    return score >= _PREFLIGHT_MIN_SCORE and token_overlap(query, metric.get("search_blob", "")) >= 2


def _infer_dimensions_from_query(
    query: str,
    metric: dict[str, Any],
) -> list[str]:
    normalized_query = normalize(query)
    allowed_dimensions = metric.get("allowed_dimensions", [])
    inferred: list[str] = []

    def add_dimension(name: str) -> None:
        if name in allowed_dimensions and name not in inferred:
            inferred.append(name)

    saw_time_hint = False
    for phrases, dimension in _TIME_DIMENSION_HINTS:
        if any(phrase in normalized_query for phrase in phrases):
            saw_time_hint = True
            add_dimension(dimension)

    if saw_time_hint and not inferred:
        for candidate in ("day", "week", "month", "hour"):
            if candidate in allowed_dimensions:
                inferred.append(candidate)
                break

    for phrases, dimension in _DIMENSION_HINTS:
        if any(phrase in normalized_query for phrase in phrases):
            add_dimension(dimension)

    return inferred


def _normalize_dimension_name(name: str) -> str:
    normalized_name = normalize(name).replace(" ", "_")
    return _DIMENSION_ALIASES.get(normalized_name, normalized_name)


def _time_spine_upcast_targets(snapshot, metric: dict[str, Any]) -> list[str]:
    """Return the time-spine grain names this metric's root can be upcast
    to (e.g. a metric with a daily `date` column can be grouped to
    `week` / `month`).

    Mirrors `cerebro_mcp.semantic_planner._try_time_spine_upcast` so the
    tool-layer dimension check doesn't reject upcasts the planner WOULD
    successfully synthesise. Kept as a separate helper to avoid an
    import cycle.
    """
    from cerebro_mcp.semantic_planner import (  # local import: avoids cycle
        _TIME_UPCAST_TEMPLATES,
        _is_time_spine_model,
    )

    root_model = snapshot.models.get(metric.get("root_model", ""), {})
    source_grains = {
        (dim.get("type_params") or {}).get("time_granularity")
        for dim in root_model.get("dimensions", [])
        if dim.get("type") == "time"
    }
    source_grains.discard(None)
    if not source_grains:
        return []

    reachable_target_grains = {
        target
        for (target, source) in _TIME_UPCAST_TEMPLATES
        if source in source_grains
    }
    if not reachable_target_grains:
        return []

    # Map target grains back to the spine dimension names exposed in the
    # registry (e.g. `week` from `dim_time_spine_weekly`). A grain might
    # be advertised by multiple spine models; collect all matching names.
    targets: set[str] = set()
    for model_name, model in snapshot.models.items():
        if not _is_time_spine_model(model_name):
            continue
        for dim in model.get("dimensions", []):
            grain = (dim.get("type_params") or {}).get("time_granularity")
            if grain in reachable_target_grains and dim.get("name"):
                targets.add(dim["name"])
    return sorted(targets)


def _metric_supported_dimensions(
    snapshot,
    metric: dict[str, Any],
) -> list[str]:
    allowed = [
        normalize(value).replace(" ", "_")
        for value in metric.get("allowed_dimensions", [])
        if value
    ]
    if not allowed:
        root_model = snapshot.models.get(metric.get("root_model", ""), {})
        allowed = [
            normalize(dimension.get("name", "")).replace(" ", "_")
            for dimension in root_model.get("dimensions", [])
            if dimension.get("name")
        ]

    # Extend with time-spine upcast targets so cross-grain queries (e.g.
    # asking for `week` from a daily metric) survive the pre-planner
    # dimension gate. The planner synthesises the actual `toMonday(...)`
    # projection downstream.
    for upcast_target in _time_spine_upcast_targets(snapshot, metric):
        normalised = normalize(upcast_target).replace(" ", "_")
        if normalised and normalised not in allowed:
            allowed.append(normalised)

    return allowed


def _resolve_executable_metrics(
    snapshot,
    requested_metrics: list[str],
    *,
    allow_candidate: bool = False,
) -> tuple[list[str], list[dict[str, Any]], str]:
    """Resolve metric names to their definitions, enforcing the
    quality-tier gate.

    By default rejects non-approved metrics with a generic error. Two
    refinements:

    * If the metric has no `allowed_dimensions` (a scalar / single-row
      KPI view), surface a dedicated message pointing at execute_query —
      these can't be semantically planned regardless of quality tier.
    * If `allow_candidate=True`, accept structurally-valid candidate
      metrics (their root model is itself approved AND they declare at
      least one dimension). This is the authoring/testing opt-in path;
      never use in production dashboards.
    """
    resolved_metric_names: list[str] = []
    metrics: list[dict[str, Any]] = []
    for requested_name in requested_metrics:
        resolved_name, metric = _lookup_metric(snapshot, requested_name)
        if not metric:
            return [], [], f"Error: Metric '{requested_name}' not found."
        if _metric_is_executable(snapshot, metric):
            pass  # ok
        elif _metric_is_scalar_kpi(metric):
            root = metric.get("root_model", "<unknown>")
            return [], [], (
                f"Error: Metric '{resolved_name}' is a scalar / single-row KPI "
                f"(no `allowed_dimensions` declared). The semantic planner has "
                f"nothing to group by. Query the underlying view directly with "
                f"`execute_query` on `{root}`."
            )
        elif allow_candidate and _metric_is_candidate(snapshot, metric):
            # NB: explicit opt-in for authoring / testing only. Quality is
            # not vetted — never use in dashboards.
            pass
        else:
            return [], [], (
                "Error: Semantic coverage gap. "
                f"Metric '{resolved_name}' exists, but it is not approved for "
                f"semantic execution yet. Pass `allow_candidate=true` to run "
                f"it anyway for authoring/testing, or use `execute_query`."
            )
        resolved_metric_names.append(resolved_name)
        metrics.append(metric)
    return resolved_metric_names, metrics, ""


def _normalize_requested_dimensions(
    snapshot,
    metrics: list[dict[str, Any]],
    requested_dimensions: list[str] | None,
) -> tuple[list[str], str]:
    if not requested_dimensions:
        return [], ""

    normalized_dimensions: list[str] = []
    requested_pairs: list[tuple[str, str]] = []
    supported_sets = [set(_metric_supported_dimensions(snapshot, metric)) for metric in metrics]
    shared_supported = set.intersection(*supported_sets) if supported_sets else set()

    for original_name in requested_dimensions:
        canonical_name = _normalize_dimension_name(original_name)
        requested_pairs.append((original_name, canonical_name))
        if canonical_name not in normalized_dimensions:
            normalized_dimensions.append(canonical_name)

    unsupported = [
        (original_name, canonical_name)
        for original_name, canonical_name in requested_pairs
        if canonical_name not in shared_supported
    ]
    if not unsupported:
        return normalized_dimensions, ""

    allowed = ", ".join(f"`{name}`" for name in sorted(shared_supported)) or "none"
    original_name, canonical_name = unsupported[0]
    alias_note = (
        f" (normalized to `{canonical_name}`)"
        if canonical_name != _normalize_dimension_name(original_name) or canonical_name != original_name
        else ""
    )
    return [], (
        f"Error: Dimension `{original_name}`{alias_note} is not supported for the requested metrics. "
        f"Allowed dimensions: {allowed}."
    )


def get_executable_metrics_for_model(model_name: str) -> list[dict[str, Any]]:
    _maybe_refresh_semantic()
    snapshot, error = _snapshot_or_error()
    if snapshot is None:
        return []
    return [
        metric
        for metric in snapshot.metrics.values()
        if metric.get("root_model") == model_name
        and _metric_is_executable(snapshot, metric)
    ]


def get_semantic_preflight(
    query: str,
    mode: str = "answer",
    *,
    agent_role: str = "",
) -> AnalyticsPreflightResult:
    normalized_mode = normalize(mode) or "answer"
    if normalized_mode not in {"answer", "chart", "report"}:
        normalized_mode = "answer"

    if not settings.SEMANTIC_ENABLED:
        return AnalyticsPreflightResult(
            query=query,
            mode=normalized_mode,
            route="semantic_disabled",
            recommended_next_tool="discover_models",
            fallback_reason="semantic_disabled",
            summary_markdown=truncate_response(
                "Semantic routing is disabled. Continue with raw model discovery."
            ),
        )

    _maybe_refresh_semantic()
    snapshot, error = _snapshot_or_error()
    if snapshot is None:
        return AnalyticsPreflightResult(
            query=query,
            mode=normalized_mode,
            route="semantic_unavailable",
            recommended_next_tool="discover_models",
            fallback_reason=(error or "semantic_unavailable").replace("Semantic ", "").rstrip("."),
            summary_markdown=truncate_response(
                f"Semantic routing unavailable: {error or 'snapshot unavailable'}"
            ),
        )

    if not semantic_runtime.is_execution_available:
        reason = semantic_runtime.stale_reason or "semantic execution unavailable"
        return AnalyticsPreflightResult(
            query=query,
            mode=normalized_mode,
            route="semantic_unavailable",
            recommended_next_tool="discover_models",
            fallback_reason=reason,
            summary_markdown=truncate_response(
                f"Semantic execution unavailable: {reason}. Continue with raw discovery."
            ),
        )

    scored: list[tuple[int, str, dict[str, Any]]] = []
    token_idf = _token_idf_for_snapshot(snapshot)
    for metric_name, metric in snapshot.metrics.items():
        if not _metric_is_executable(snapshot, metric):
            continue
        score = score_metric(query, metric, token_idf=token_idf)
        if score > 0:
            scored.append((score, metric_name, metric))
    scored.sort(key=lambda item: (-item[0], item[1]))

    accepted = [
        (score, metric_name, metric)
        for score, metric_name, metric in scored
        if _is_preflight_ready_match(query, metric, score)
    ]
    rejected = [
        (score, metric_name, metric)
        for score, metric_name, metric in scored
        if not _is_preflight_ready_match(query, metric, score)
    ]

    recommended_metrics = [metric_name for _score, metric_name, _metric in accepted[:5]]
    recommended_dimensions: list[str] = []
    if accepted:
        recommended_dimensions = _infer_dimensions_from_query(query, accepted[0][2])

    # Topic-level hybrid routing
    covered_topics, uncovered_topics = _extract_topic_labels(query, accepted, rejected)
    hybrid_ready = False

    if accepted and uncovered_topics:
        route = "hybrid_ready"
        hybrid_ready = True
        fallback_reason = ""
    elif accepted:
        route = "semantic_ready"
        fallback_reason = ""
    else:
        route = "semantic_coverage_gap"
        fallback_reason = "semantic_coverage_gap"

    next_tool = _recommended_semantic_next_tool(route, normalized_mode, len(accepted))

    lines = [
        f"Route: `{route}`",
        f"Recommended metrics: {', '.join(recommended_metrics) or 'none'}",
        f"Recommended dimensions: {', '.join(recommended_dimensions) or 'none'}",
        f"Recommended next tool: `{next_tool}`",
    ]
    if hybrid_ready:
        lines.append(f"Covered topics (semantic): {', '.join(covered_topics)}")
        lines.append(f"Uncovered topics (use raw): {', '.join(uncovered_topics)}")
    if fallback_reason:
        lines.append(f"Fallback reason: `{fallback_reason}`")

    return AnalyticsPreflightResult(
        query=query,
        mode=normalized_mode,
        route=route,
        hybrid_ready=hybrid_ready,
        covered_topics=covered_topics,
        uncovered_topics=uncovered_topics,
        recommended_metrics=recommended_metrics,
        recommended_dimensions=recommended_dimensions,
        recommended_next_tool=next_tool,
        fallback_reason=fallback_reason,
        summary_markdown=truncate_response("\n".join(lines)),
    )


def execute_metric_query(
    *,
    ch: ClickHouseManager,
    research_store: ResearchStore | None,
    metrics: list[str],
    dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    order_by: list[str] | None = None,
    limit: int = 100,
    research_project_id: str = "",
    persist_result: bool = False,
    evidence_title: str = "",
    agent_role: str = "",
    allow_candidate: bool = False,
) -> SemanticQueryResult | str:
    role = _resolve_agent_role(agent_role)
    started = time.perf_counter()
    repair_traces: list[SemanticRetryTrace] = []
    try:
        _maybe_refresh_semantic()
        if persist_result and not research_project_id:
            return "Error: `persist_result=True` requires `research_project_id`."
        snapshot, error = _snapshot_or_error(require_execution=True)
        if snapshot is None:
            return error or "Semantic execution unavailable."
        resolved_metric_names, metric_definitions, metric_error = _resolve_executable_metrics(
            snapshot,
            metrics,
            allow_candidate=allow_candidate,
        )
        if metric_error:
            return metric_error
        normalized_dimensions, dimension_error = _normalize_requested_dimensions(
            snapshot,
            metric_definitions,
            dimensions,
        )
        if dimension_error:
            return dimension_error
        plan = plan_metric_query(
            snapshot,
            requested_metrics=metrics,
            requested_dimensions=normalized_dimensions,
            filters=filters,
            agent_role=role,
        )
        plan["requested_dimensions"] = list(dimensions or [])
        plan["resolved_dimensions"] = normalized_dimensions
        plan["resolved_metrics"] = resolved_metric_names
        plan["limit"] = limit
        plan["order_by"] = order_by or []

        for branch in plan["branches"]:
            state.record_describe_table(branch["root_model"], source="semantic")

        compiler_options = plan.get("compiler_options", {})
        sql, warnings = compile_metric_plan(
            snapshot,
            plan,
            force_qualified=compiler_options.get("force_qualified", False),
        )
        log_event(
            logger,
            "semantic_query_compiled",
            planner_mode=plan["planner_mode"],
            agent_role=role,
            sql_hash=hash(sql),
            resolved_metrics=plan["resolved_metrics"],
            resolved_dimensions=plan["resolved_dimensions"],
            selected_paths=plan["selected_paths"],
            rejected_paths=plan["rejected_paths"],
        )

        attempt = 1
        while True:
            try:
                executed = ch.run_query(
                    sql,
                    "dbt",
                    requested_max_rows=limit,
                    audience="internal" if persist_result else "tool",
                    fetch_mode="auto",
                )
                result = ch.build_query_result(executed, max_rows=limit)
                repair_traces.append(
                    SemanticRetryTrace(
                        attempt=attempt,
                        sql=sql,
                        success=True,
                    )
                )
                observe_semantic_query_attempt(
                    planner_mode=plan["planner_mode"],
                    attempt=attempt,
                    result="success",
                    agent_role=role,
                )
                break
            except Exception as exc:
                observe_semantic_query_attempt(
                    planner_mode=plan["planner_mode"],
                    attempt=attempt,
                    result="error",
                    agent_role=role,
                )
                repair = _classify_repairable_error(exc) if attempt == 1 else None
                if not repair:
                    state.record_semantic_fallback("semantic_repair_failed")
                    observe_semantic_fallback(
                        fallback_target="raw_sql",
                        reason="semantic_repair_failed",
                        agent_role=role,
                    )
                    log_event(
                        logger,
                        "semantic_fallback",
                        reason="semantic_repair_failed",
                        fallback_target="raw_sql",
                        agent_role=role,
                        clickhouse_error=str(exc),
                    )
                    _trace_semantic_event(
                        "semantic_fallback",
                        "Semantic execution fell back to raw SQL after repair failure.",
                        {
                            "reason": "semantic_repair_failed",
                            "fallback_target": "raw_sql",
                            "agent_role": role,
                            "clickhouse_error": str(exc),
                        },
                        event_kind="semantic_fallback",
                    )
                    raise
                observe_semantic_query_repair(
                    repair_action=repair["action"],
                    error_class=repair["error_class"],
                    agent_role=role,
                )
                repair_traces.append(
                    SemanticRetryTrace(
                        attempt=attempt,
                        sql=sql,
                        clickhouse_error=str(exc),
                        repair_action=repair["action"],
                        success=False,
                    )
                )
                log_event(
                    logger,
                    "semantic_query_repair",
                    error_class=repair["error_class"],
                    repair_action=repair["action"],
                    agent_role=role,
                    clickhouse_error=str(exc),
                )
                plan = _apply_repair(plan, repair)
                compiler_options = plan.get("compiler_options", {})
                sql, new_warnings = compile_metric_plan(
                    snapshot,
                    plan,
                    force_qualified=compiler_options.get("force_qualified", False),
                )
                warnings = list(dict.fromkeys([*warnings, *new_warnings]))
                attempt += 1

        result_ref_id = None
        if persist_result and research_project_id and research_store is not None:
            artifact_rows = normalize_rows(executed.rows)
            result_ref_id = research_store.save_semantic_query_result_artifact(
                project_id=research_project_id,
                title=evidence_title.strip() or ", ".join(plan["resolved_metrics"]),
                sql=executed.sql,
                database=executed.database,
                columns=executed.columns,
                rows=artifact_rows,
                row_count=executed.row_count,
                semantic_plan={
                    "planner_mode": plan["planner_mode"],
                    "selected_paths": plan["selected_paths"],
                    "rejected_paths": plan["rejected_paths"],
                },
            )
            observe_research_semantic_evidence(
                phase="execution",
                agent_role=role,
            )

        state.record_execute_query(sql, source="semantic")
        repair_state = "repaired" if len(repair_traces) > 1 else "clean"
        observe_semantic_query_latency(
            planner_mode=plan["planner_mode"],
            repair_state=repair_state,
            elapsed_seconds=time.perf_counter() - started,
        )
        _trace_semantic_event(
            "semantic_path_used",
            "Semantic execution path was used for this request.",
            {
                "path": "semantic",
                "planner_mode": plan["planner_mode"],
                "resolved_metrics": plan["resolved_metrics"],
                "resolved_dimensions": plan["resolved_dimensions"],
            },
            event_kind="semantic_routing",
        )
        summary = build_query_summary(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            rows_returned=result.rows_returned,
            elapsed_seconds=result.elapsed_seconds,
            database=result.database,
            sql=result.sql,
            warnings=[*result.warnings, *warnings],
            extra_notes=[
                f"Planner mode: `{plan['planner_mode']}`",
                f"Resolved metrics: {', '.join(plan['resolved_metrics'])}",
                f"Resolved dimensions: {', '.join(plan['resolved_dimensions']) or 'none'}",
            ],
        )
        return SemanticQueryResult(
            sql=result.sql,
            database=result.database,
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            rows_returned=result.rows_returned,
            truncated=result.truncated,
            fetch_mode=result.fetch_mode,
            elapsed_seconds=result.elapsed_seconds,
            requested_metrics=plan["requested_metrics"],
            resolved_metrics=plan["resolved_metrics"],
            requested_dimensions=plan["requested_dimensions"],
            resolved_dimensions=plan["resolved_dimensions"],
            planner_mode=plan["planner_mode"],
            root_models=plan["root_models"],
            warnings=[*result.warnings, *warnings],
            repair_traces=repair_traces,
            semantic_plan={
                "selected_paths": plan["selected_paths"],
                "rejected_paths": plan["rejected_paths"],
            },
            result_ref_id=result_ref_id,
            summary_markdown=summary,
        )
    except PlanningError as exc:
        state.record_semantic_fallback("semantic_coverage_gap")
        observe_semantic_fallback(
            fallback_target="raw_sql",
            reason="semantic_coverage_gap",
            agent_role=role,
        )
        log_event(
            logger,
            "semantic_fallback",
            reason="semantic_coverage_gap",
            fallback_target="raw_sql",
            agent_role=role,
            planner_error=str(exc),
        )
        _trace_semantic_event(
            "semantic_fallback",
            "Semantic execution fell back to raw SQL because approved coverage was missing.",
            {
                "reason": "semantic_coverage_gap",
                "fallback_target": "raw_sql",
                "agent_role": role,
                "planner_error": str(exc),
            },
            event_kind="semantic_fallback",
        )
        return (
            "Error: Fallback reason: semantic_coverage_gap. "
            f"{exc}. Use `execute_query` with `get_clickhouse_query_rules` for raw fallback."
        )
    except Exception as exc:
        if repair_traces:
            return (
                "Error: Semantic execution failed after deterministic repair retry. "
                f"Fallback reason: semantic_repair_failed. ClickHouse error: {exc}. "
                "Use `execute_query` with `get_clickhouse_query_rules` for raw fallback."
            )
        return f"Error: {exc}"


def _semantic_docs_page(uri: str, fallback_payload: dict[str, Any]) -> str:
    snapshot = semantic_runtime.snapshot
    if snapshot is None:
        return json.dumps(fallback_payload, indent=2, ensure_ascii=False)

    entry = snapshot.docs_index.get(uri, {})
    relative_path = entry.get("path", "")
    if relative_path:
        if settings.SEMANTIC_DOCS_INDEX_URL:
            try:
                response = requests.get(
                    urljoin(settings.SEMANTIC_DOCS_INDEX_URL, relative_path),
                    timeout=15,
                )
                if response.status_code == 200:
                    return response.text
            except Exception:
                pass
        if settings.SEMANTIC_DOCS_INDEX_PATH:
            local_path = Path(settings.SEMANTIC_DOCS_INDEX_PATH).expanduser().resolve().parent / relative_path
            if local_path.exists():
                return local_path.read_text(encoding="utf-8")

    return json.dumps(fallback_payload, indent=2, ensure_ascii=False)


def _render_metric_details(metric: dict[str, Any]) -> MetricDetailsResult:
    summary = format_results_table(
        ["field", "value"],
        [
            ["root_model", metric.get("root_model", "")],
            ["module", metric.get("module", "")],
            ["allowed_dimensions", ", ".join(metric.get("allowed_dimensions", []))],
            ["supported_time_grains", ", ".join(metric.get("supported_time_grains", []))],
            ["quality_tier", metric.get("quality_tier", "")],
        ],
    )
    return MetricDetailsResult(
        name=metric["name"],
        label=metric.get("label", ""),
        description=metric.get("description", ""),
        module=metric.get("module", ""),
        root_model=metric.get("root_model", ""),
        allowed_dimensions=metric.get("allowed_dimensions", []),
        supported_time_grains=metric.get("supported_time_grains", []),
        default_filters=metric.get("default_filters", []),
        question_synonyms=metric.get("question_synonyms", []),
        semantic_status=metric.get("semantic_status", ""),
        summary_markdown=truncate_response(summary),
    )


def _load_clickhouse_bundle_manifest() -> tuple[Path, dict[str, Any]]:
    base = Path(settings.CLICKHOUSE_AGENT_SKILLS_PATH)
    manifest_path = base / BUNDLE_MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Vendored ClickHouse agent skills bundle manifest is not available."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return base, manifest


def _clickhouse_bundle_is_valid() -> bool:
    try:
        base, manifest = _load_clickhouse_bundle_manifest()
    except Exception:
        return False
    compiled_rules = base / str(manifest.get("compiled_rules_path", ""))
    required_files = [base / str(path) for path in manifest.get("required_files", [])]
    if not compiled_rules.exists():
        return False
    return all(path.exists() for path in required_files)


def _get_clickhouse_rules_text() -> str:
    base, manifest = _load_clickhouse_bundle_manifest()
    compiled_rules_path = base / str(manifest.get("compiled_rules_path", ""))
    if not compiled_rules_path.exists():
        raise FileNotFoundError(
            "Vendored ClickHouse agent skills compiled rules file is missing."
        )
    header = [
        "# ClickHouse Query Rules",
        "",
        "Source bundle: %s" % manifest.get("source_repo_url", ""),
        "Pinned ref: %s" % manifest.get("source_ref", ""),
        "",
    ]
    return "\n".join(header) + compiled_rules_path.read_text(encoding="utf-8")


def register_semantic_tools(mcp, ch: ClickHouseManager, research_store: ResearchStore):
    if not settings.SEMANTIC_ENABLED:
        return

    state.set_semantic_tools_available(True)

    @mcp.tool()
    def preflight_analytics_request(
        query: str,
        mode: str = "answer",
        agent_role: str = "",
    ) -> AnalyticsPreflightResult | str:
        role = _resolve_agent_role(agent_role)
        normalized_mode = normalize(mode) or "answer"
        if normalized_mode not in {"answer", "chart", "report"}:
            normalized_mode = "answer"
        try:
            # Each preflight marks the start of a new analysis cycle. Reset
            # per-cycle discovery and execution accumulators so prior-session
            # state cannot pollute the discovered-model coverage gate or the
            # chart preconditions. Preserves semantic preflight cache.
            state.begin_analysis_cycle()
            state.record_semantic_tool_call("preflight_analytics_request")
            cached = state.get_cached_semantic_preflight(
                query=query,
                mode=normalized_mode,
            )
            cache_hit = cached is not None
            if cache_hit:
                observe_cache_hit("semantic_preflight")
            else:
                observe_cache_miss("semantic_preflight")
            result = (
                AnalyticsPreflightResult(**cached)
                if cached is not None
                else get_semantic_preflight(query, mode=normalized_mode, agent_role=role)
            )
            if not cache_hit:
                state.cache_semantic_preflight(
                    query=query,
                    mode=result.mode,
                    result=result.model_dump(),
                )
            state.record_semantic_preflight(
                route=result.route,
                mode=result.mode,
                fallback_reason=result.fallback_reason,
            )
            observe_semantic_route(route=result.route, mode=result.mode)
            observe_semantic_tool_call(
                tool_name="preflight_analytics_request",
                status="success",
                agent_role=role,
                entrypoint="semantic_router",
            )
            if not cache_hit:
                log_event(
                    logger,
                    "semantic_route_decision",
                    route=result.route,
                    mode=result.mode,
                    recommended_metrics=result.recommended_metrics,
                    recommended_dimensions=result.recommended_dimensions,
                    recommended_next_tool=result.recommended_next_tool,
                    fallback_reason=result.fallback_reason,
                    agent_role=role,
                )
                _trace_semantic_event(
                    "semantic_route_decision",
                    "Semantic analytics preflight completed.",
                    {
                        "query": query,
                        "mode": result.mode,
                        "route": result.route,
                        "recommended_metrics": result.recommended_metrics,
                        "recommended_dimensions": result.recommended_dimensions,
                        "recommended_next_tool": result.recommended_next_tool,
                        "fallback_reason": result.fallback_reason,
                        "cached": False,
                    },
                    event_kind="semantic_routing",
                )
            if result.route != "semantic_ready":
                state.record_semantic_fallback(result.fallback_reason or result.route)
                if not cache_hit:
                    observe_semantic_fallback(
                        fallback_target="raw_sql",
                        reason=result.fallback_reason or result.route,
                        agent_role=role,
                    )
                    _trace_semantic_event(
                        "semantic_fallback",
                        "Semantic preflight recommended raw fallback.",
                        {
                            "reason": result.fallback_reason or result.route,
                            "fallback_target": "raw_sql",
                            "agent_role": role,
                            "mode": result.mode,
                            "cached": False,
                        },
                        event_kind="semantic_fallback",
                    )
            return result
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="preflight_analytics_request",
                status="error",
                agent_role=role,
                entrypoint="semantic_router",
            )
            return f"Error: {exc}"

    @mcp.tool()
    def discover_metrics(
        query: str,
        limit: int = 10,
        agent_role: str = "",
    ) -> MetricDiscoveryResult | str:
        role = _resolve_agent_role(agent_role)
        try:
            state.record_semantic_tool_call("discover_metrics")
            _maybe_refresh_semantic()
            snapshot, error = _snapshot_or_error()
            if snapshot is None:
                return error or "Semantic snapshot unavailable."
            scored = []
            token_idf = _token_idf_for_snapshot(snapshot)
            for metric_name, metric in snapshot.metrics.items():
                if not _metric_is_executable(snapshot, metric):
                    continue
                score = score_metric(query, metric, token_idf=token_idf)
                if score > 0:
                    scored.append((score, metric_name, metric))
            scored.sort(key=lambda item: (-item[0], item[1]))
            hits = [
                MetricDiscoveryHit(
                    name=metric_name,
                    label=metric.get("label", ""),
                    module=metric.get("module", ""),
                    root_model=metric.get("root_model", ""),
                    score=score,
                    quality_tier=metric.get("quality_tier", ""),
                )
                for score, metric_name, metric in scored[: max(1, min(limit, 20))]
            ]
            state.record_search_models(query, len(hits), source="semantic")
            observe_semantic_tool_call(
                tool_name="discover_metrics",
                status="success",
                agent_role=role,
                entrypoint="semantic",
            )
            return MetricDiscoveryResult(
                query=query,
                results=hits,
                summary_markdown=truncate_response(
                    format_results_table(
                        ["name", "module", "root_model", "score"],
                        [[hit.name, hit.module, hit.root_model, hit.score] for hit in hits],
                    )
                ),
            )
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="discover_metrics",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return f"Error: {exc}"

    @mcp.tool()
    def get_metric_details(metric_name: str, agent_role: str = "") -> MetricDetailsResult | str:
        role = _resolve_agent_role(agent_role)
        try:
            state.record_semantic_tool_call("get_metric_details")
            _maybe_refresh_semantic()
            snapshot, error = _snapshot_or_error()
            if snapshot is None:
                return error or "Semantic snapshot unavailable."
            resolved_name, metric = _lookup_metric(snapshot, metric_name)
            if not metric:
                return f"Metric '{metric_name}' not found."
            if not _metric_is_executable(snapshot, metric):
                return (
                    f"Metric '{resolved_name}' exists, but it is not approved for semantic execution yet. "
                    "Use `discover_metrics` for approved metrics or fall back to `execute_query`."
                )
            state.record_get_model_details(metric.get("root_model", ""), source="semantic")
            observe_semantic_tool_call(
                tool_name="get_metric_details",
                status="success",
                agent_role=role,
                entrypoint="semantic",
            )
            return _render_metric_details(metric)
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="get_metric_details",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return f"Error: {exc}"

    @mcp.tool()
    def explain_metric_query(
        metrics: list[str],
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        limit: int = 100,
        order_by: list[str] | None = None,
        agent_role: str = "",
        allow_candidate: bool = False,
    ) -> MetricQueryExplanation | str:
        # See `query_metrics` for the `allow_candidate` semantics.
        role = _resolve_agent_role(agent_role)
        try:
            state.record_semantic_tool_call("explain_metric_query", execution=True)
            _maybe_refresh_semantic()
            snapshot, error = _snapshot_or_error(require_execution=True)
            if snapshot is None:
                return error or "Semantic execution unavailable."
            resolved_metric_names, metric_definitions, metric_error = _resolve_executable_metrics(
                snapshot,
                metrics,
                allow_candidate=allow_candidate,
            )
            if metric_error:
                return metric_error
            normalized_dimensions, dimension_error = _normalize_requested_dimensions(
                snapshot,
                metric_definitions,
                dimensions,
            )
            if dimension_error:
                return dimension_error
            plan = plan_metric_query(
                snapshot,
                requested_metrics=metrics,
                requested_dimensions=normalized_dimensions,
                filters=filters,
                agent_role=role,
            )
            plan["requested_dimensions"] = list(dimensions or [])
            plan["resolved_dimensions"] = normalized_dimensions
            plan["resolved_metrics"] = resolved_metric_names
            plan["limit"] = limit
            plan["order_by"] = order_by or []
            compiler_options = plan.get("compiler_options", {})
            sql, warnings = compile_metric_plan(
                snapshot,
                plan,
                force_qualified=compiler_options.get("force_qualified", False),
            )
            observe_semantic_tool_call(
                tool_name="explain_metric_query",
                status="success",
                agent_role=role,
                entrypoint="semantic",
            )
            _trace_semantic_event(
                "semantic_path_used",
                "Semantic explain path was used for this request.",
                {
                    "path": "semantic",
                    "resolved_metrics": plan["resolved_metrics"],
                    "resolved_dimensions": plan["resolved_dimensions"],
                },
                event_kind="semantic_routing",
            )
            return _build_explanation(plan, sql, warnings, [])
        except PlanningError as exc:
            state.record_semantic_fallback("semantic_coverage_gap")
            observe_semantic_fallback(
                fallback_target="raw_sql",
                reason="semantic_coverage_gap",
                agent_role=role,
            )
            log_event(
                logger,
                "semantic_fallback",
                reason="semantic_coverage_gap",
                fallback_target="raw_sql",
                agent_role=role,
                planner_error=str(exc),
            )
            _trace_semantic_event(
                "semantic_fallback",
                "Semantic explanation fell back to raw SQL because approved coverage was missing.",
                {
                    "reason": "semantic_coverage_gap",
                    "fallback_target": "raw_sql",
                    "planner_error": str(exc),
                    "agent_role": role,
                },
                event_kind="semantic_fallback",
            )
            observe_semantic_tool_call(
                tool_name="explain_metric_query",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return (
                "Error: Fallback reason: semantic_coverage_gap. "
                f"{exc}. Use `discover_metrics` for approved coverage or `execute_query` for raw fallback."
            )
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="explain_metric_query",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return f"Error: {exc}"

    @mcp.tool()
    def query_metrics(
        metrics: list[str],
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
        research_project_id: str = "",
        persist_result: bool = False,
        evidence_title: str = "",
        agent_role: str = "",
        allow_candidate: bool = False,
    ) -> SemanticQueryResult | str:
        # `allow_candidate=True` is an authoring/testing escape hatch that
        # lets you run a metric whose quality_tier is still "candidate" so
        # long as its root model is approved and at least one dimension is
        # declared. NEVER use in production dashboards — candidate metrics
        # haven't passed analyst review.
        role = _resolve_agent_role(agent_role)
        try:
            state.record_semantic_tool_call("query_metrics", execution=True)
            result = execute_metric_query(
                ch=ch,
                research_store=research_store,
                metrics=metrics,
                dimensions=dimensions,
                filters=filters,
                order_by=order_by,
                limit=limit,
                research_project_id=research_project_id,
                persist_result=persist_result,
                evidence_title=evidence_title,
                agent_role=role,
                allow_candidate=allow_candidate,
            )
            observe_semantic_tool_call(
                tool_name="query_metrics",
                status="error" if isinstance(result, str) else "success",
                agent_role=role,
                entrypoint="semantic",
            )
            return result
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="query_metrics",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return f"Error: {exc}"

    @mcp.tool()
    def reload_semantic_registry(agent_role: str = "") -> dict[str, Any]:
        """Force an immediate refresh of the semantic registry, bypassing
        the ETag-based polling and the SEMANTIC_REFRESH_INTERVAL_SECONDS
        TTL (default 300s).

        Useful during semantic-layer authoring loops where you've just
        rebuilt the registry locally (or just redeployed to GitHub Pages)
        and need to see changes — particularly metric **promotions**
        (candidate → approved) and **agg-type changes** — propagate
        without waiting for the next poll cycle.

        Returns counts and content-hash before / after so callers can
        confirm the refresh actually picked up new content.
        """
        role = _resolve_agent_role(agent_role)
        try:
            before_snapshot = semantic_runtime.snapshot
            before_hash = before_snapshot.registry_hash if before_snapshot else ""
            changed, error = semantic_runtime.force_reload()
            after_snapshot = semantic_runtime.snapshot
            after_hash = after_snapshot.registry_hash if after_snapshot else ""
            metric_count = len(after_snapshot.metrics) if after_snapshot else 0
            model_count = len(after_snapshot.models) if after_snapshot else 0
            approved_metrics = (
                sum(
                    1
                    for m in after_snapshot.metrics.values()
                    if m.get("quality_tier") == "approved"
                )
                if after_snapshot
                else 0
            )
            observe_semantic_tool_call(
                tool_name="reload_semantic_registry",
                status="error" if error else "success",
                agent_role=role,
                entrypoint="semantic",
            )
            return {
                "changed": bool(changed),
                "before_hash": before_hash,
                "after_hash": after_hash,
                "metric_count": metric_count,
                "model_count": model_count,
                "approved_metric_count": approved_metrics,
                "error": error or "",
            }
        except Exception as exc:
            observe_semantic_tool_call(
                tool_name="reload_semantic_registry",
                status="error",
                agent_role=role,
                entrypoint="semantic",
            )
            return {"changed": False, "error": f"{exc}"}

    if _clickhouse_bundle_is_valid():
        @mcp.tool()
        def get_clickhouse_query_rules(agent_role: str = "") -> str:
            role = _resolve_agent_role(agent_role)
            try:
                observe_semantic_tool_call(
                    tool_name="get_clickhouse_query_rules",
                    status="success",
                    agent_role=role,
                    entrypoint="clickhouse_agent",
                )
                return truncate_response(_get_clickhouse_rules_text())
            except Exception as exc:
                observe_semantic_tool_call(
                    tool_name="get_clickhouse_query_rules",
                    status="error",
                    agent_role=role,
                    entrypoint="clickhouse_agent",
                )
                return f"Error: {exc}"

    @mcp.resource("gnosis://semantic-model/{name}")
    def semantic_model_resource(name: str) -> str:
        snapshot, error = _snapshot_or_error()
        if snapshot is None:
            return error or "Semantic snapshot unavailable."
        observe_semantic_docs_read(resource_type="model", agent_role=_resolve_agent_role())
        model = snapshot.models.get(name)
        if not model:
            return f"Semantic model '{name}' not found."
        return _semantic_docs_page(
            f"gnosis://semantic-model/{name}",
            model,
        )

    @mcp.resource("gnosis://semantic-metric/{name}")
    def semantic_metric_resource(name: str) -> str:
        snapshot, error = _snapshot_or_error()
        if snapshot is None:
            return error or "Semantic snapshot unavailable."
        observe_semantic_docs_read(resource_type="metric", agent_role=_resolve_agent_role())
        metric = snapshot.metrics.get(name)
        if not metric:
            return f"Semantic metric '{name}' not found."
        return _semantic_docs_page(
            f"gnosis://semantic-metric/{name}",
            metric,
        )

    @mcp.resource("gnosis://semantic-relationship/{name}")
    def semantic_relationship_resource(name: str) -> str:
        snapshot, error = _snapshot_or_error()
        if snapshot is None:
            return error or "Semantic snapshot unavailable."
        observe_semantic_docs_read(resource_type="relationship", agent_role=_resolve_agent_role())
        relationship = next(
            (item for item in snapshot.relationships if item.get("name") == name),
            None,
        )
        if relationship is None:
            return f"Semantic relationship '{name}' not found."
        return _semantic_docs_page(
            f"gnosis://semantic-relationship/{name}",
            relationship,
        )

    @mcp.resource("gnosis://semantic-module/{module_name}")
    def semantic_module_resource(module_name: str) -> str:
        snapshot, error = _snapshot_or_error()
        if snapshot is None:
            return error or "Semantic snapshot unavailable."
        observe_semantic_docs_read(resource_type="module", agent_role=_resolve_agent_role())
        payload = {
            "module": module_name,
            "models": [model for model in snapshot.models.values() if model.get("module") == module_name],
            "metrics": [metric for metric in snapshot.metrics.values() if metric.get("module") == module_name],
        }
        return _semantic_docs_page(
            f"gnosis://semantic-module/{module_name}",
            payload,
        )

    @mcp.resource("gnosis://semantic-graph-overview")
    def semantic_graph_overview() -> str:
        snapshot, error = _snapshot_or_error()
        if snapshot is None:
            return error or "Semantic snapshot unavailable."
        observe_semantic_docs_read(resource_type="overview", agent_role=_resolve_agent_role())
        payload = {
            "model_count": len(snapshot.models),
            "metric_count": len(snapshot.metrics),
            "relationship_count": len(snapshot.relationships),
            "docs_count": len(snapshot.docs_index),
        }
        return _semantic_docs_page(
            "gnosis://semantic-graph-overview",
            payload,
        )
