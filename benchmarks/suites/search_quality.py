"""Suite 4: search/routing quality (`--suite search`).

Fully deterministic and in-process — no ClickHouse in any tier, so this suite
never emits skips. Three sections:

1. Golden search quality: the ~30 (query -> expected model) pairs from
   ``tests/test_search_quality.py`` scored on all four unified search surfaces
   (canonical ``ModelSearchIndex``, ``catalog_search``, Metric Lab catalog,
   ``manifest.search_models``) with rank/hit@k/MRR per query plus one
   aggregate per surface. A hit@5 miss is an ERROR — the same gate the pytest
   suite enforces.
2. Routing quality: pinned ``RoutingCase``s driven through BOTH front doors
   (``find`` and ``preflight_analytics_request``) on a bench server under the
   deterministic semantic runtime, asserting route, recommended action,
   metric coverage, and the find/preflight route invariant.
3. Discovery precision: pinned ``DiscoveryPrecisionCase``s over
   ``manifest.search_models`` — off-domain leaks into the top-k are errors;
   an expected model slipping below k but staying inside the top-10 is only
   a warning.

No latency is measured here (that is Suite 1/5 territory): every case carries
``samples_ms=[]``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from benchmarks.core.results import OK, CaseResult
from benchmarks.core.runner import BenchContext
from benchmarks.core.semantic_env import (
    deterministic_semantic_runtime,
    fixture_fingerprint,
    snapshot_from_fixture,
)
from tests.eval.corpus_fixtures import (
    build_manifest_loader,
    build_snapshot,
    install_fixture_manifest,
    load_search_corpus,
)
from tests.eval.search_routing_fixtures import (
    CATEGORY_BY_QUERY,
    DISCOVERY_PRECISION_CASES,
    ROUTING_CASES,
    DiscoveryPrecisionCase,
    RoutingCase,
)

SUPPORTED_MODES = frozenset({"inprocess"})

# Search deeper than the pytest gate (top-5) so per-query rank/MRR still
# resolves for near-misses in positions 6-10.
SEARCH_LIMIT = 10

SURFACES = ("core_index", "catalog_search", "metric_lab", "manifest_search")

DEFAULT_CATEGORY = "plain_language"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "query"


def _flatten(result: Any) -> str:
    """Tool result -> text (CallToolResult content, pydantic JSON, or str)."""
    try:
        from mcp.types import CallToolResult

        if isinstance(result, CallToolResult):
            return "\n".join(getattr(c, "text", "") or "" for c in result.content)
    except Exception:
        pass
    try:
        from pydantic import BaseModel

        if isinstance(result, BaseModel):
            return result.model_dump_json()
    except Exception:
        pass
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    return str(result)


def _structured(result: Any) -> dict[str, Any]:
    """Best-effort dict view of a tool result (routing cases need fields, not
    prose). Non-JSON text lands under ``_text`` so error paths stay visible."""
    if isinstance(result, dict):
        return result
    try:
        from pydantic import BaseModel

        if isinstance(result, BaseModel):
            return result.model_dump()
    except Exception:
        pass
    text = _flatten(result)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"_text": text}


# ---------------------------------------------------------------------------
# Section 1: golden search quality over the four unified surfaces
# ---------------------------------------------------------------------------


def _run_core_index(corpus: dict[str, dict], queries: list[str]) -> dict[str, list[str]]:
    from cerebro_mcp.semantic.search import ModelSearchIndex, reset_search_cache_for_tests

    reset_search_cache_for_tests()
    try:
        idx = ModelSearchIndex.for_snapshot(build_snapshot(corpus))
        return {q: [h.name for h in idx.search(q, limit=SEARCH_LIMIT)] for q in queries}
    finally:
        reset_search_cache_for_tests()


def _run_catalog_search(corpus: dict[str, dict], queries: list[str]) -> dict[str, list[str]]:
    # The golden suite scores catalog_search against the SEARCH corpus
    # snapshot, not the routing registry — patch dc.current_snapshot directly
    # (deterministic_semantic_runtime is NOT active here) and clear the index
    # cache on both sides.
    from unittest import mock

    import cerebro_mcp.tools.semantic.data_catalog as dc
    from cerebro_mcp.semantic.search import reset_search_cache_for_tests

    snapshot = build_snapshot(corpus)
    reset_search_cache_for_tests()
    dc._INDEX_CACHE.clear()
    try:
        with mock.patch.object(dc, "current_snapshot", lambda: snapshot):
            out: dict[str, list[str]] = {}
            for q in queries:
                r = dc.catalog_search(q, entity_types=["model"], limit=SEARCH_LIMIT)
                out[q] = [h["name"] for h in r["hits"][:SEARCH_LIMIT]]
            return out
    finally:
        dc._INDEX_CACHE.clear()
        reset_search_cache_for_tests()


def _run_metric_lab(corpus: dict[str, dict], queries: list[str]) -> dict[str, list[str]]:
    # Mirror of test_metric_lab_catalog_hits_at_5's monkeypatch set, applied
    # via mock.patch.object outside pytest.
    import contextlib
    from unittest import mock

    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.semantic.search import reset_search_cache_for_tests
    from cerebro_mcp.tools.semantic import semantic as semantic_tools
    from cerebro_mcp.tools.visualization import metric_lab as ml

    snapshot = build_snapshot(corpus)
    reset_search_cache_for_tests()
    ml._BASE_CATALOG_CACHE.clear()
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(semantic_runtime, "_snapshot", snapshot))
            stack.enter_context(
                mock.patch.object(semantic_runtime, "_execution_available", True)
            )
            stack.enter_context(mock.patch.object(semantic_runtime, "_stale_reason", None))
            stack.enter_context(
                mock.patch.object(
                    semantic_tools.manifest, "reload_if_changed", lambda: (False, None)
                )
            )
            stack.enter_context(
                mock.patch.object(
                    semantic_tools.catalog, "reload_if_changed", lambda: (False, None)
                )
            )
            stack.enter_context(
                mock.patch.object(semantic_runtime, "refresh_if_changed", lambda: (False, None))
            )
            out: dict[str, list[str]] = {}
            for q in queries:
                r = ml.build_metric_catalog(query=q, limit=SEARCH_LIMIT)
                out[q] = [e["name"] for e in r["entries"][:SEARCH_LIMIT]]
            return out
    finally:
        ml._BASE_CATALOG_CACHE.clear()
        reset_search_cache_for_tests()


def _run_manifest_search(corpus: dict[str, dict], queries: list[str]) -> dict[str, list[str]]:
    from cerebro_mcp.semantic.search import reset_search_cache_for_tests

    reset_search_cache_for_tests()
    try:
        loader = build_manifest_loader(corpus)
        return {
            q: [r["name"] for r in loader.search_models(query=q, limit=SEARCH_LIMIT)]
            for q in queries
        }
    finally:
        reset_search_cache_for_tests()


_SURFACE_RUNNERS: dict[str, Callable[[dict[str, dict], list[str]], dict[str, list[str]]]] = {
    "core_index": _run_core_index,
    "catalog_search": _run_catalog_search,
    "metric_lab": _run_metric_lab,
    "manifest_search": _run_manifest_search,
}

_SURFACE_TOOL = {
    "core_index": "ModelSearchIndex.search",
    "catalog_search": "catalog_search",
    "metric_lab": "build_metric_catalog",
    "manifest_search": "search_models",
}


def _score_query(hits: list[str], expected: str) -> dict[str, Any]:
    rank = hits.index(expected) + 1 if expected in hits else None
    rr = (1.0 / rank) if rank else 0.0
    return {
        "rank": rank,
        "hit1": rank is not None and rank <= 1,
        "hit3": rank is not None and rank <= 3,
        "hit5": rank is not None and rank <= 5,
        "rr": round(rr, 4),
    }


def _aggregate(measures: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(measures)
    if not n:
        return {"hit1": 0.0, "hit3": 0.0, "hit5": 0.0, "mrr": 0.0, "n": 0}
    return {
        "hit1": round(sum(1 for m in measures if m["hit1"]) / n, 4),
        "hit3": round(sum(1 for m in measures if m["hit3"]) / n, 4),
        "hit5": round(sum(1 for m in measures if m["hit5"]) / n, 4),
        "mrr": round(sum(m["rr"] for m in measures) / n, 4),
        "n": n,
    }


def _golden_section(
    ctx: BenchContext,
    corpus: dict[str, dict],
    golden: list[tuple[str, str]],
    top_k: int,
) -> tuple[list[CaseResult], list[dict[str, Any]]]:
    """Per-(surface x query) cases + per-surface aggregates. Returns the
    emitted cases and ALL measures (for the suite-wide aggregate)."""
    results: list[CaseResult] = []
    all_measures: list[dict[str, Any]] = []
    queries = [q for q, _expect in golden]

    for surface in SURFACES:
        case_ids = [f"search/{surface}/{_slug(q)}" for q in queries]
        agg_id = f"search/agg/{surface}"
        if not any(ctx.should_run(cid) for cid in [*case_ids, agg_id, "search/agg/all"]):
            continue

        hits_by_query = _SURFACE_RUNNERS[surface](corpus, queries)
        measures: list[dict[str, Any]] = []
        for (query, expected), case_id in zip(golden, case_ids):
            hits = hits_by_query[query]
            score = _score_query(hits, expected)
            category = CATEGORY_BY_QUERY.get(query, DEFAULT_CATEGORY)
            score["category"] = category
            measures.append(score)
            if not ctx.should_run(case_id):
                continue
            meta = {**score, "expected": expected, "top5": hits[:top_k]}
            if score["hit5"]:
                results.append(
                    CaseResult(id=case_id, tool=_SURFACE_TOOL[surface], status=OK, meta=meta)
                )
            else:
                results.append(
                    CaseResult.error_case(
                        case_id,
                        f"{query!r}: expected {expected} in top-{top_k}, got {hits[:top_k]}",
                        tool=_SURFACE_TOOL[surface],
                        meta=meta,
                    )
                )

        all_measures.extend(measures)
        if ctx.should_run(agg_id):
            per_category: dict[str, dict[str, Any]] = {}
            for cat in sorted({m["category"] for m in measures}):
                cat_measures = [m for m in measures if m["category"] == cat]
                cat_agg = _aggregate(cat_measures)
                per_category[cat] = {
                    "hit5": cat_agg["hit5"],
                    "mrr": cat_agg["mrr"],
                    "n": cat_agg["n"],
                }
            meta = {**_aggregate(measures), "per_category": per_category}
            results.append(
                CaseResult(id=agg_id, tool=_SURFACE_TOOL[surface], status=OK, meta=meta)
            )

    return results, all_measures


# ---------------------------------------------------------------------------
# Section 2: routing quality through both front doors
# ---------------------------------------------------------------------------


async def _eval_routing_case(mcp, case: RoutingCase) -> CaseResult:
    find_raw = await mcp._tool_manager.call_tool(
        "find", {"query": case.query, "mode": case.mode, "limit": 8}
    )
    find_payload = _structured(find_raw)
    # preflight only accepts answer|chart|report; the route itself is
    # mode-independent (shared _route core), so "auto" maps to "answer".
    pf_mode = case.mode if case.mode in ("answer", "chart", "report") else "answer"
    pf_raw = await mcp._tool_manager.call_tool(
        "preflight_analytics_request",
        {"query": case.query, "mode": pf_mode, "detail": "slim"},
    )
    pf_payload = _structured(pf_raw)

    find_route = find_payload.get("route")
    pf_route = pf_payload.get("route")
    action = find_payload.get("recommended_action") or {}
    action_tool = action.get("tool")
    top_metrics = find_payload.get("top_metrics") or []
    metric_names = [m.get("name") for m in top_metrics if isinstance(m, dict)]

    meta = {
        "find_route": find_route,
        "preflight_route": pf_route,
        "action_tool": action_tool,
        "top_metrics": metric_names,
    }

    problems: list[str] = []
    for label, payload in (("find", find_payload), ("preflight", pf_payload)):
        text = payload.get("_text")
        if text is not None:
            problems.append(f"{label} returned non-structured payload: {text[:200]}")
    if find_route != case.expected_route:
        problems.append(f"find.route={find_route!r}, expected {case.expected_route!r}")
    if action_tool != case.expected_action_tool:
        problems.append(
            f"recommended_action.tool={action_tool!r}, expected {case.expected_action_tool!r}"
        )
    missing = [m for m in case.expected_metrics_contains if m not in metric_names]
    if missing:
        problems.append(f"top_metrics missing {missing}; got {metric_names}")
    if case.expect_low_confidence and not any(
        m.get("low_confidence") for m in top_metrics if isinstance(m, dict)
    ):
        problems.append(f"expected low_confidence-flagged metrics, got {top_metrics}")
    if find_route != pf_route:
        problems.append(
            f"route invariant broken: find={find_route!r} preflight={pf_route!r}"
        )

    if problems:
        return CaseResult.error_case(case.id, "; ".join(problems), tool="find", meta=meta)
    return CaseResult(id=case.id, tool="find", status=OK, meta=meta)


async def _routing_section(
    ctx: BenchContext, corpus: dict[str, dict], snapshot
) -> list[CaseResult]:
    from benchmarks.core.fakes import (
        bench_clickhouse_from_corpus,
        build_bench_server,
        reset_server_state,
    )

    install_fixture_manifest(corpus)
    results: list[CaseResult] = []
    with deterministic_semantic_runtime(snapshot):
        mcp = build_bench_server(bench_clickhouse_from_corpus(corpus))
        for case in ROUTING_CASES:
            if not ctx.should_run(case.id):
                continue
            # preflight caches by (query, mode) on the session-state singleton
            # — reset between cases so every case exercises a cold route.
            reset_server_state()
            try:
                results.append(await _eval_routing_case(mcp, case))
            except Exception as exc:  # tool-invocation failure, not a mismatch
                results.append(
                    CaseResult.error_case(
                        case.id, f"{type(exc).__name__}: {exc}", tool="find"
                    )
                )
    reset_server_state()
    return results


# ---------------------------------------------------------------------------
# Section 3: discovery precision over manifest.search_models
# ---------------------------------------------------------------------------


def _eval_discovery_case(loader, case: DiscoveryPrecisionCase) -> CaseResult:
    top10 = [r["name"] for r in loader.search_models(query=case.query, limit=SEARCH_LIMIT)]
    topk = top10[: case.k]

    problems: list[str] = []
    warns: list[str] = []
    leaked = [m for m in case.must_exclude if m in topk]
    if leaked:
        problems.append(f"must_exclude leaked into top-{case.k}: {leaked}")
    for name in case.must_include:
        if name in topk:
            continue
        if name in top10:
            # Findable but demoted — a ranking WARN, not a failure.
            warns.append(f"{name} slipped to rank {top10.index(name) + 1} (k={case.k})")
        else:
            problems.append(f"{name} absent from top-{SEARCH_LIMIT}: {top10}")

    meta: dict[str, Any] = {"k": case.k, "top_k": topk}
    if warns:
        meta["warn"] = "; ".join(warns)
    if problems:
        return CaseResult.error_case(
            case.id, "; ".join(problems), tool="search_models", meta=meta
        )
    return CaseResult(id=case.id, tool="search_models", status=OK, meta=meta)


def _discovery_section(ctx: BenchContext, corpus: dict[str, dict]) -> list[CaseResult]:
    wanted = [c for c in DISCOVERY_PRECISION_CASES if ctx.should_run(c.id)]
    if not wanted:
        return []
    loader = build_manifest_loader(corpus)
    results: list[CaseResult] = []
    for case in wanted:
        try:
            results.append(_eval_discovery_case(loader, case))
        except Exception as exc:
            results.append(
                CaseResult.error_case(
                    case.id, f"{type(exc).__name__}: {exc}", tool="search_models"
                )
            )
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(ctx: BenchContext) -> list[CaseResult]:
    # Lazy: the golden list lives with the pytest gate so the two can never
    # drift, and importing it pulls cerebro_mcp (env-first discipline).
    from tests.test_search_quality import GOLDEN, TOP_K

    corpus = load_search_corpus()
    results: list[CaseResult] = []

    results_golden, all_measures = _golden_section(ctx, corpus, GOLDEN, TOP_K)
    results.extend(results_golden)

    routing_wanted = any(ctx.should_run(c.id) for c in ROUTING_CASES)
    snapshot = snapshot_from_fixture() if routing_wanted else None
    ctx.extra.setdefault("environment", {}).update(fixture_fingerprint(snapshot))
    if routing_wanted:
        results.extend(asyncio.run(_routing_section(ctx, corpus, snapshot)))

    results.extend(_discovery_section(ctx, corpus))

    if all_measures and ctx.should_run("search/agg/all"):
        results.append(
            CaseResult(id="search/agg/all", status=OK, meta=_aggregate(all_measures))
        )

    return [c.finalize() for c in results]
