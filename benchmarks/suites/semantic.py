"""Suite 5: semantic layer (`--suite semantic`).

Benchmarks the semantic stack end to end on the frozen routing-registry
fixture (``tests/fixtures/routing_registry.json.gz``), in six sections:

A. Runtime — snapshot / index / token-idf / search-index build latency plus
   the ``refresh_if_changed`` no-op fast path (wired with fake artifact
   loaders exactly like ``tests/test_semantic_runtime_refresh.py``). Live
   registry cases are skipped unless ``CEREBRO_EVAL_LIVE_REGISTRY=1``.
B. Routing — ``preflight_analytics_request`` cold/cached per query class,
   ``find`` cold, the bare ``_route`` core, a preflight-cache correctness
   check, and a route-distribution snapshot over the 40 pinned queries in
   ``benchmarks/queries/semantic_routing_queries.json``.
C. Planner + SQL goldens — ``plan_metric_query`` per planner mode (warm and
   one cold-cache variant) and ``compile_metric_plan`` output pinned via
   normalized-AST hashes in ``tests/fixtures/semantic_sql_golden.json``
   (``--update-golden`` rewrites; a mismatch errors with a unified diff).
D. query_metrics E2E — clean / repaired / terminally-failing executions on
   scriptable ``BenchClickHouse`` fakes; real-ClickHouse pinned metrics are
   skipped unless ``CEREBRO_EVAL_CLICKHOUSE=1``.
E. Coverage — registry health scalars (``meta.kind == "coverage"``) from the
   fixture (or the live registry under ``CEREBRO_EVAL_LIVE_REGISTRY=1``).
F. Chart tools — ``quick_metric_chart`` / ``generate_metric_charts`` /
   ``explain_metric_query`` positives plus the semantic-gate negative.

The fixture registry has no ratio/derived metrics and no metric exposing a
remote (joined) dimension through ``allowed_dimensions``; those paths run on
the deterministic micro-registry in ``benchmarks/cases/semantic_cases.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarks.cases.semantic_cases import (
    BATCH_CHART_SPECS,
    CACHE_CORRECTNESS_CASE_ID,
    CHART_CASES,
    CHART_GATE_ROOT_MODEL,
    COVERAGE_CASES,
    MICRO_REGISTRY,
    PLANNER_CASES,
    QUERY_METRICS_CASES,
    REAL_QUERY_BUDGET_MS,
    REAL_QUERY_METRICS,
    ROUTE_DIRECT_BUDGET_MS,
    ROUTE_DIRECT_CASE_ID,
    ROUTE_DISTRIBUTION_CASE_ID,
    ROUTING_LATENCY_CASES,
    RUNTIME_CASES,
    SCALAR_KPI_OVERRIDE,
    SQL_GOLDEN_CASES,
    WEEKLY_NETFLOW_OVERRIDE,
    ChartCase,
    QueryMetricsCase,
    RoutingCase,
    RuntimeCase,
    SqlGoldenCase,
)
from benchmarks.core.fakes import (
    BenchClickHouse,
    bench_clickhouse_from_corpus,
    build_bench_server,
    reset_server_state,
)
from benchmarks.core.results import ERROR, OK, CaseResult
from benchmarks.core.runner import BenchContext
from benchmarks.core.semantic_env import (
    deterministic_semantic_runtime,
    fixture_fingerprint,
    reset_semantic_process_state,
    snapshot_from_fixture,
)
from benchmarks.core.stats import measure_latency, measure_latency_async
from tests.eval.corpus_fixtures import (
    FIXTURES_DIR,
    install_fixture_manifest,
    load_routing_registry,
    load_search_corpus,
)

SUPPORTED_MODES = frozenset({"inprocess"})

QUERIES_PATH = Path(__file__).resolve().parent.parent / "queries" / "semantic_routing_queries.json"
GOLDEN_PATH = FIXTURES_DIR / "semantic_sql_golden.json"

_ROUTING_QUERY_TIMEOUT_NOTE = "verified against the frozen routing registry fixture"


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def _iters(ctx: BenchContext, default: int) -> int:
    return ctx.iters if ctx.iters is not None else default


def _warmup(ctx: BenchContext, default: int) -> int:
    return ctx.warmup if ctx.warmup is not None else default


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


def _is_tool_error(result: Any) -> bool:
    """Semantic tools return plain ``"Error: ..."`` strings on failure."""
    return isinstance(result, str) and result.lstrip().startswith("Error")


def _registry_body(registry: dict[str, Any]) -> dict[str, Any]:
    """Closest valid raw-registry body reconstructable from the fixture.

    The recorded fixture stores the PROCESSED snapshot surfaces (its
    ``metrics`` already carry the derived ``all_synonyms`` / ``search_blob``
    fields and its models live under ``models_exec``), while
    ``SemanticRuntime._build_snapshot`` expects the published raw registry
    (``models`` / raw ``metrics`` / ``relationships`` / ``metadata``). Strip
    the derived metric fields (``build_indexes`` recomputes them) and remap
    the model key.
    """
    return {
        "metadata": dict(registry.get("metadata") or {}),
        "models": registry["models_exec"],
        "metrics": {
            name: {k: v for k, v in metric.items() if k not in ("all_synonyms", "search_blob")}
            for name, metric in registry["metrics"].items()
        },
        "relationships": registry["relationships"],
    }


def _build_micro_snapshot():
    """Real SemanticSnapshot over the deterministic micro-registry."""
    from cerebro_mcp.models.semantic import SemanticSnapshot
    from cerebro_mcp.semantic.graph import build_semantic_graph
    from cerebro_mcp.semantic.index import build_indexes

    registry = MICRO_REGISTRY
    synonym_index, dimension_index, metrics = build_indexes(registry)
    graph, vertex_ids = build_semantic_graph(
        registry["models"], registry["relationships"]
    )
    return SemanticSnapshot(
        registry_hash="bench-micro-v1",
        manifest_hash="bench-micro",
        catalog_hash="bench-micro",
        docs_hash="",
        graph=graph,
        vertex_ids=vertex_ids,
        synonym_index=synonym_index,
        dimension_index=dimension_index,
        metrics=metrics,
        models=registry["models"],
        relationships=registry["relationships"],
        docs_index={},
        loaded_at=0.0,
        token_idf={},
    )


def _plan_for_case(snapshot, case) -> dict[str, Any]:
    """plan_metric_query with the case's pinned inputs (shared by C/goldens)."""
    from cerebro_mcp.semantic.planner import plan_metric_query

    return plan_metric_query(
        snapshot,
        requested_metrics=list(case.metrics),
        requested_dimensions=list(case.dimensions),
        filters=[dict(f) for f in case.filters],
        allow_candidate=case.allow_candidate,
    )


# ──────────────────────────────────────────────────────────────────────
# Section A — runtime
# ──────────────────────────────────────────────────────────────────────


def _run_runtime_section(
    ctx: BenchContext,
    registry: dict[str, Any],
    snapshot,
    results: list[CaseResult],
) -> None:
    runnable = [c for c in RUNTIME_CASES if ctx.should_run(c.id)]
    if not runnable:
        return

    base_meta = {
        "n_models": len(registry["models_exec"]),
        "n_metrics": len(registry["metrics"]),
        "n_synonyms": len(registry["synonym_index"]),
        "snapshot_json_bytes": len(json.dumps(registry)),
        "registry_body_note": (
            "reconstructed from fixture models_exec + processed metrics "
            "(derived all_synonyms/search_blob stripped)"
        ),
    }
    body = _registry_body(registry)

    for case in runnable:
        if case.needs_live and not ctx.live_registry:
            results.append(CaseResult.skipped_case(
                case.id,
                "live registry not enabled (set CEREBRO_EVAL_LIVE_REGISTRY=1)",
                tool=case.op,
            ))
            continue
        try:
            fn, extra_meta = _runtime_op(case, body, snapshot)
            result, samples = measure_latency(
                fn, iters=_iters(ctx, case.iters), warmup=_warmup(ctx, case.warmup)
            )
            meta = {**base_meta, **extra_meta}
            if case.op == "snapshot_build" and result is not None:
                meta["graph_profiles"] = len(getattr(result, "graph_profiles", ()) or ())
            record = CaseResult(
                id=case.id, tool=case.op, samples_ms=samples,
                budget_ms=case.budget_ms, meta=meta,
            ).finalize()
            if (
                case.hard_error_factor is not None
                and record.stats.get("p50", 0.0) > case.budget_ms * case.hard_error_factor
            ):
                record.status = ERROR
                record.error = (
                    f"{case.op} p50 {record.stats['p50']}ms exceeds "
                    f"{case.hard_error_factor}x the {case.budget_ms}ms budget — "
                    "the no-op refresh path is doing real work"
                )
            results.append(record)
        except Exception as exc:
            results.append(CaseResult.error_case(case.id, f"{type(exc).__name__}: {exc}", tool=case.op))


def _runtime_op(case: RuntimeCase, body: dict[str, Any], snapshot):
    """Return (callable, extra_meta) for a runtime case (lazy cerebro imports)."""
    if case.op == "snapshot_build":
        from cerebro_mcp.loaders.semantic import SemanticRuntime

        runtime = SemanticRuntime()
        return (lambda: runtime._build_snapshot(body, [])), {}
    if case.op == "index_build":
        from cerebro_mcp.semantic.index import build_indexes

        return (lambda: build_indexes(body)), {}
    if case.op == "token_idf_build":
        from cerebro_mcp.semantic.index import build_token_idf

        metric_values = list(snapshot.metrics.values())
        return (lambda: build_token_idf(metric_values)), {}
    if case.op == "search_index_build":
        from cerebro_mcp.semantic.search import (
            ModelSearchIndex,
            reset_search_cache_for_tests,
        )

        def build_cold():
            reset_search_cache_for_tests()
            return ModelSearchIndex.for_snapshot(snapshot)

        return build_cold, {}
    if case.op == "refresh_noop":
        return _make_refresh_noop(), {"loader_wiring": "fake artifact/hash loaders (pure no-op)"}
    if case.op == "cold_load_real":
        from cerebro_mcp.loaders.semantic import SemanticRuntime

        runtime = SemanticRuntime()

        def load_real():
            loaded = runtime.load()
            if loaded is None:
                raise RuntimeError(
                    "SemanticRuntime.load() returned None (registry unavailable "
                    "or SEMANTIC_ENABLED off)"
                )
            return loaded

        return load_real, {"source": "live"}
    if case.op == "refresh_noop_real":
        from cerebro_mcp.loaders.semantic import semantic_runtime

        if semantic_runtime.snapshot is None and semantic_runtime.load() is None:
            raise RuntimeError("live semantic registry unavailable for refresh_noop_real")
        return semantic_runtime.refresh_if_changed, {"source": "live"}
    raise ValueError(f"unknown runtime op {case.op!r}")


def _make_refresh_noop():
    """SemanticRuntime whose refresh_if_changed() is a pure no-op.

    Same fake wiring as ``tests/test_semantic_runtime_refresh.py``: artifact
    loaders report "unchanged" and manifest/catalog hash loaders are already
    at their deployed hash, so ``_refresh`` early-returns without rebuilding.
    The ExitStack stays open for the lifetime of the returned closure (its
    patches are module-level; the section calls the closure synchronously and
    the stack is closed when the closure is garbage collected via the
    contextlib.ExitStack callback pattern below).
    """
    from unittest import mock

    import cerebro_mcp.loaders.semantic as sem

    class _FakeArtifact:
        payload = SimpleNamespace(body=None)
        content_hash = "bench-noop"

        def force_reload(self):
            return False, None

        def reload_if_changed(self):
            return False, None

    class _FakeHashLoader:
        is_loaded = True
        content_hash = "bench-hash"

        def reload_if_changed(self):
            return False, None

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(sem.settings, "SEMANTIC_ENABLED", True))
    stack.enter_context(mock.patch.object(sem, "semantic_registry", _FakeArtifact()))
    stack.enter_context(mock.patch.object(sem, "semantic_docs", _FakeArtifact()))
    stack.enter_context(mock.patch.object(sem, "semantic_graph_catalog", _FakeArtifact()))
    stack.enter_context(mock.patch.object(sem, "manifest", _FakeHashLoader()))
    stack.enter_context(mock.patch.object(sem, "catalog", _FakeHashLoader()))
    runtime = sem.SemanticRuntime()

    def refresh_noop():
        changed, error = runtime.refresh_if_changed()
        if changed or error:
            stack.close()
            raise RuntimeError(
                f"refresh_if_changed was not a no-op (changed={changed}, error={error})"
            )
        return changed

    # Tie the patch lifetime to the closure: when the section drops the
    # callable, the stack unwinds and the module attributes are restored.
    refresh_noop._stack = stack  # type: ignore[attr-defined]
    import weakref

    weakref.finalize(refresh_noop, stack.close)
    return refresh_noop


# ──────────────────────────────────────────────────────────────────────
# Section B — routing latency + cache
# ──────────────────────────────────────────────────────────────────────


def _run_routing_section(ctx: BenchContext, snapshot, results: list[CaseResult]) -> None:
    latency_cases = [c for c in ROUTING_LATENCY_CASES if ctx.should_run(c.id)]
    extras = [
        case_id
        for case_id in (ROUTE_DIRECT_CASE_ID, CACHE_CORRECTNESS_CASE_ID, ROUTE_DISTRIBUTION_CASE_ID)
        if ctx.should_run(case_id)
    ]
    if not latency_cases and not extras:
        return

    corpus = load_search_corpus()
    with deterministic_semantic_runtime(snapshot) as semantic_tools:
        mcp = build_bench_server(bench_clickhouse_from_corpus(corpus))
        asyncio.run(
            _routing_async(ctx, mcp, semantic_tools, latency_cases, extras, results)
        )
    reset_semantic_process_state()


async def _routing_async(
    ctx: BenchContext,
    mcp,
    semantic_tools,
    latency_cases: list[RoutingCase],
    extras: list[str],
    results: list[CaseResult],
) -> None:
    async def call_tool(case: RoutingCase):
        if case.tool == "find":
            return await mcp._tool_manager.call_tool(
                "find", {"query": case.query, "mode": case.mode, "limit": 8}
            )
        return await mcp._tool_manager.call_tool(
            "preflight_analytics_request", {"query": case.query, "mode": case.mode}
        )

    def route_of(result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get("route", ""))
        return str(getattr(result, "route", ""))

    for case in latency_cases:
        try:
            iters = _iters(ctx, case.iters)
            if case.phase == "cold":
                # One untimed priming call absorbs first-touch import costs,
                # then every timed sample starts from a fully cold cache.
                await call_tool(case)
                samples: list[float] = []
                last: Any = None
                for _ in range(iters):
                    reset_semantic_process_state()
                    started = time.perf_counter()
                    last = await call_tool(case)
                    samples.append((time.perf_counter() - started) * 1000.0)
                if _is_tool_error(last):
                    results.append(CaseResult.error_case(case.id, _flatten(last)[:400], tool=case.tool))
                    continue
                results.append(CaseResult(
                    id=case.id, tool=case.tool, samples_ms=samples,
                    budget_ms=case.budget_ms,
                    meta={"phase": "cold", "query": case.query, "mode": case.mode,
                          "route": route_of(last)},
                ).finalize())
            else:
                reset_semantic_process_state()
                prime = await call_tool(case)
                if _is_tool_error(prime):
                    results.append(CaseResult.error_case(case.id, _flatten(prime)[:400], tool=case.tool))
                    continue
                last, samples = await measure_latency_async(
                    lambda: call_tool(case), iters=iters, warmup=0
                )
                stats = semantic_tools.get_semantic_runtime_stats()[
                    "preflight_analytics_request"
                ]
                cache_size = semantic_tools.state.semantic_summary()[
                    "semantic_preflight_cache_size"
                ]
                meta = {
                    "phase": "cached", "query": case.query, "mode": case.mode,
                    "route": route_of(last),
                    "cache_hits": stats["cache_hits"],
                    "cache_misses": stats["cache_misses"],
                    "preflight_cache_size": cache_size,
                }
                if stats["cache_hits"] < iters:
                    results.append(CaseResult.error_case(
                        case.id,
                        f"expected >= {iters} preflight cache hits after priming, "
                        f"got {stats['cache_hits']} (misses={stats['cache_misses']})",
                        tool=case.tool, meta=meta,
                    ))
                    continue
                results.append(CaseResult(
                    id=case.id, tool=case.tool, samples_ms=samples,
                    budget_ms=case.budget_ms, meta=meta,
                ).finalize())
        except Exception as exc:
            results.append(CaseResult.error_case(case.id, f"{type(exc).__name__}: {exc}", tool=case.tool))

    if ROUTE_DIRECT_CASE_ID in extras:
        try:
            reset_semantic_process_state()
            query = "bridge netflow last week"
            routing, samples = measure_latency(
                lambda: semantic_tools._route(query, "answer"),
                iters=_iters(ctx, 10), warmup=_warmup(ctx, 2),
            )
            results.append(CaseResult(
                id=ROUTE_DIRECT_CASE_ID, tool="_route", samples_ms=samples,
                budget_ms=ROUTE_DIRECT_BUDGET_MS,
                meta={"query": query, "route": routing["route"]},
            ).finalize())
        except Exception as exc:
            results.append(CaseResult.error_case(
                ROUTE_DIRECT_CASE_ID, f"{type(exc).__name__}: {exc}", tool="_route"
            ))

    if CACHE_CORRECTNESS_CASE_ID in extras:
        results.append(await _cache_correctness_case(mcp, semantic_tools))

    if ROUTE_DISTRIBUTION_CASE_ID in extras:
        results.append(_route_distribution_case(semantic_tools))


async def _cache_correctness_case(mcp, semantic_tools) -> CaseResult:
    """Two identical preflights -> identical payload, cache size exactly 1."""
    case_id = CACHE_CORRECTNESS_CASE_ID
    tool = "preflight_analytics_request"
    query = "bridge netflow last week"
    try:
        reset_semantic_process_state()
        size_start = semantic_tools.state.semantic_summary()["semantic_preflight_cache_size"]
        if size_start != 0:
            return CaseResult.error_case(
                case_id, f"cold-start preflight cache size {size_start} != 0", tool=tool
            )
        first = await mcp._tool_manager.call_tool(tool, {"query": query, "mode": "answer"})
        size_after_first = semantic_tools.state.semantic_summary()["semantic_preflight_cache_size"]
        second = await mcp._tool_manager.call_tool(tool, {"query": query, "mode": "answer"})
        size_after_second = semantic_tools.state.semantic_summary()["semantic_preflight_cache_size"]
        first_text, second_text = _flatten(first), _flatten(second)
        meta = {
            "query": query,
            "cache_size_after_first": size_after_first,
            "cache_size_after_second": size_after_second,
            "route": str(getattr(first, "route", "")),
        }
        if _is_tool_error(first) or _is_tool_error(second):
            return CaseResult.error_case(case_id, first_text[:400], tool=tool, meta=meta)
        if first_text != second_text:
            diff = "\n".join(difflib.unified_diff(
                first_text.splitlines(), second_text.splitlines(),
                fromfile="first_call", tofile="second_call", lineterm="",
            ))
            return CaseResult.error_case(
                case_id, f"cached preflight diverged from the original:\n{diff[:1500]}",
                tool=tool, meta=meta,
            )
        if size_after_first != 1 or size_after_second != 1:
            return CaseResult.error_case(
                case_id,
                f"preflight cache size drifted (after first={size_after_first}, "
                f"after second={size_after_second}; expected 1 and 1)",
                tool=tool, meta=meta,
            )
        return CaseResult(id=case_id, tool=tool, meta=meta)
    except Exception as exc:
        return CaseResult.error_case(case_id, f"{type(exc).__name__}: {exc}", tool=tool)


def _route_distribution_case(semantic_tools) -> CaseResult:
    """Route every pinned query once (mode=answer); meta only, no assertions."""
    case_id = ROUTE_DISTRIBUTION_CASE_ID
    try:
        payload = json.loads(QUERIES_PATH.read_text())
        queries = payload["queries"]
        reset_semantic_process_state()
        distribution: dict[str, int] = {}
        by_query: dict[str, str] = {}
        by_class: dict[str, dict[str, int]] = {}
        for entry in queries:
            query, q_class = entry["query"], entry.get("class", "")
            route = semantic_tools._route(query, "answer")["route"]
            by_query[query] = route
            distribution[route] = distribution.get(route, 0) + 1
            class_counts = by_class.setdefault(q_class, {})
            class_counts[route] = class_counts.get(route, 0) + 1
        return CaseResult(
            id=case_id, tool="_route",
            meta={
                "kind": "route_distribution",
                "n_queries": len(queries),
                "distribution": distribution,
                "by_class": by_class,
                "by_query": by_query,
                "note": _ROUTING_QUERY_TIMEOUT_NOTE,
            },
        )
    except Exception as exc:
        return CaseResult.error_case(case_id, f"{type(exc).__name__}: {exc}", tool="_route")


# ──────────────────────────────────────────────────────────────────────
# Section C — planner
# ──────────────────────────────────────────────────────────────────────


def _run_planner_section(
    ctx: BenchContext, snapshot, micro_snapshot, results: list[CaseResult]
) -> None:
    runnable = [c for c in PLANNER_CASES if ctx.should_run(c.id)]
    if not runnable:
        return
    import cerebro_mcp.semantic.graph as graph_mod
    import cerebro_mcp.semantic.planner as planner_mod

    for case in runnable:
        snap = micro_snapshot if case.snapshot == "micro" else snapshot
        try:
            if case.cache == "cold":
                samples: list[float] = []
                plan = None
                for _ in range(_iters(ctx, case.iters)):
                    planner_mod._BINDING_CACHE.clear()
                    graph_mod._PATH_CACHE.clear()
                    started = time.perf_counter()
                    plan = _plan_for_case(snap, case)
                    samples.append((time.perf_counter() - started) * 1000.0)
            else:
                plan, samples = measure_latency(
                    lambda: _plan_for_case(snap, case),
                    iters=_iters(ctx, case.iters), warmup=_warmup(ctx, case.warmup),
                )
            meta = {
                "planner_mode": plan["planner_mode"],
                "expected_mode": case.expected_mode,
                "root_models": plan["root_models"],
                "cache": case.cache,
                "snapshot": case.snapshot,
            }
            if plan["planner_mode"] != case.expected_mode:
                results.append(CaseResult.error_case(
                    case.id,
                    f"planner_mode {plan['planner_mode']!r} != expected {case.expected_mode!r}",
                    tool="plan_metric_query", meta=meta,
                ))
                continue
            if case.expect_derived and not plan.get("derived_metrics"):
                results.append(CaseResult.error_case(
                    case.id, "plan is missing the derived_metrics spec",
                    tool="plan_metric_query", meta=meta,
                ))
                continue
            if case.expect_derived:
                meta["derived_metrics"] = plan["derived_metrics"]
            results.append(CaseResult(
                id=case.id, tool="plan_metric_query", samples_ms=samples,
                budget_ms=case.budget_ms, meta=meta,
            ).finalize())
        except Exception as exc:
            results.append(CaseResult.error_case(
                case.id, f"{type(exc).__name__}: {exc}", tool="plan_metric_query"
            ))


# ──────────────────────────────────────────────────────────────────────
# Section C — SQL compiler goldens
# ──────────────────────────────────────────────────────────────────────


def _normalize_sql(sql: str) -> tuple[str, str]:
    """Normalized SQL text for hashing: sqlglot AST round-trip when it parses,
    whitespace-collapse fallback otherwise (recorded as ``meta.hash_kind``)."""
    try:
        import sqlglot

        normalized = sqlglot.parse_one(sql, read="clickhouse").sql(
            normalize=True, comments=False
        )
        return normalized, "sqlglot_ast"
    except Exception:
        return " ".join(sql.split()), "whitespace"


def _run_sql_golden_section(
    ctx: BenchContext, snapshot, micro_snapshot, results: list[CaseResult]
) -> None:
    runnable = [c for c in SQL_GOLDEN_CASES if ctx.should_run(c.id)]
    if not runnable:
        return
    from cerebro_mcp.semantic.sql_compiler import compile_metric_plan

    golden: dict[str, Any] = {}
    if GOLDEN_PATH.exists():
        golden = json.loads(GOLDEN_PATH.read_text())
    updated = dict(golden)
    wrote_any = False

    for case in runnable:
        snap = micro_snapshot if case.snapshot == "micro" else snapshot
        try:
            plan = _plan_for_case(snap, case)
            plan["limit"] = case.limit
            plan["order_by"] = list(case.order_by)
            (sql, warnings), samples = measure_latency(
                lambda: compile_metric_plan(snap, plan, force_qualified=case.force_qualified),
                iters=_iters(ctx, 5), warmup=_warmup(ctx, 1),
            )
        except Exception as exc:
            results.append(CaseResult.error_case(
                case.id, f"{type(exc).__name__}: {exc}", tool="compile_metric_plan"
            ))
            continue

        normalized, hash_kind = _normalize_sql(sql)
        sql_norm_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        params = {
            "metrics": list(case.metrics),
            "dimensions": list(case.dimensions),
            "filters": [dict(f) for f in case.filters],
            "order_by": list(case.order_by),
            "limit": case.limit,
            "force_qualified": case.force_qualified,
            "allow_candidate": case.allow_candidate,
            "snapshot": case.snapshot,
        }
        meta = {
            "hash_kind": hash_kind,
            "sql_norm_hash": sql_norm_hash,
            "planner_mode": plan["planner_mode"],
            "compiler_warnings": list(warnings),
        }

        if ctx.update_golden:
            updated[case.id] = {"sql_norm_hash": sql_norm_hash, "sql": sql, "params": params}
            wrote_any = True
            meta["updated"] = True
            results.append(CaseResult(
                id=case.id, tool="compile_metric_plan", samples_ms=samples,
                budget_ms=case.budget_ms, meta=meta,
            ).finalize())
            continue

        stored = golden.get(case.id)
        if stored is None:
            results.append(CaseResult.error_case(
                case.id,
                "no golden recorded — run with --update-golden",
                tool="compile_metric_plan", meta=meta,
            ))
            continue
        if stored.get("sql_norm_hash") != sql_norm_hash:
            diff = "\n".join(difflib.unified_diff(
                str(stored.get("sql", "")).splitlines(), sql.splitlines(),
                fromfile="golden", tofile="current", lineterm="",
            ))
            results.append(CaseResult.error_case(
                case.id,
                "compiled SQL diverged from the golden "
                f"(stored {stored.get('sql_norm_hash', '')[:12]} != "
                f"current {sql_norm_hash[:12]}):\n{diff}",
                tool="compile_metric_plan", meta=meta,
            ))
            continue
        results.append(CaseResult(
            id=case.id, tool="compile_metric_plan", samples_ms=samples,
            budget_ms=case.budget_ms, meta=meta,
        ).finalize())

    if ctx.update_golden and wrote_any:
        GOLDEN_PATH.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


# ──────────────────────────────────────────────────────────────────────
# Section D — query_metrics end-to-end
# ──────────────────────────────────────────────────────────────────────


def _run_query_metrics_section(
    ctx: BenchContext, snapshot, micro_snapshot, results: list[CaseResult]
) -> None:
    fake_cases = [c for c in QUERY_METRICS_CASES if ctx.should_run(c.id)]
    real_ids = [
        (f"semantic.query.real.{metric}", metric, dims)
        for metric, dims in REAL_QUERY_METRICS
    ]
    real_runnable = [(cid, m, d) for cid, m, d in real_ids if ctx.should_run(cid)]
    if not fake_cases and not real_runnable:
        return

    if fake_cases:
        asyncio.run(_query_metrics_async(ctx, snapshot, micro_snapshot, fake_cases, results))

    if real_runnable:
        if not ctx.real_clickhouse:
            for cid, _metric, _dims in real_runnable:
                results.append(CaseResult.skipped_case(
                    cid, "real ClickHouse not enabled (set CEREBRO_EVAL_CLICKHOUSE=1)",
                    tool="query_metrics",
                ))
        else:
            asyncio.run(_query_metrics_real_async(ctx, snapshot, real_runnable, results))
    reset_semantic_process_state()


async def _query_metrics_async(
    ctx: BenchContext,
    snapshot,
    micro_snapshot,
    cases: list[QueryMetricsCase],
    results: list[CaseResult],
) -> None:
    from mcp.server.fastmcp import FastMCP
    from cerebro_mcp.tools.semantic.semantic import register_semantic_tools

    for case in cases:
        snap = micro_snapshot if case.snapshot == "micro" else snapshot
        reset_semantic_process_state()
        try:
            with deterministic_semantic_runtime(snap):
                # Fresh server + fresh scriptable fake per case: scripted
                # failure counters must not leak across cases.
                ch = BenchClickHouse(fail_error=case.fail_error or "UNKNOWN_IDENTIFIER: bench")
                mcp = FastMCP(f"bench-{case.id.replace('.', '-')}")
                register_semantic_tools(mcp, ch, SimpleNamespace())

                args: dict[str, Any] = {"metrics": list(case.metrics), "limit": 50}
                if case.dimensions:
                    args["dimensions"] = list(case.dimensions)
                if case.allow_candidate:
                    args["allow_candidate"] = True

                async def call():
                    return await mcp._tool_manager.call_tool("query_metrics", dict(args))

                if case.kind == "clean":
                    result, samples = await measure_latency_async(
                        call, iters=_iters(ctx, case.iters), warmup=_warmup(ctx, case.warmup)
                    )
                else:
                    samples = []
                    result = None
                    for _ in range(_iters(ctx, case.iters)):
                        if case.kind == "fail_once":
                            ch.fail_times = 1
                        elif case.kind == "fail_always":
                            ch.fail_times = 1_000_000
                        started = time.perf_counter()
                        result = await call()
                        samples.append((time.perf_counter() - started) * 1000.0)

                record = _assert_query_metrics_case(case, result)
                if record is not None:
                    results.append(record)
                    continue
                results.append(CaseResult(
                    id=case.id, tool="query_metrics", samples_ms=samples,
                    budget_ms=case.budget_ms,
                    meta=_query_case_meta(case, result),
                ).finalize())
        except Exception as exc:
            results.append(CaseResult.error_case(
                case.id, f"{type(exc).__name__}: {exc}", tool="query_metrics"
            ))


def _query_case_meta(case: QueryMetricsCase, result: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"kind": case.kind, "snapshot": case.snapshot}
    if hasattr(result, "repair_traces"):
        traces = result.repair_traces
        meta["repair_state"] = "repaired" if len(traces) > 1 else "clean"
        meta["attempts"] = len(traces)
        meta["planner_mode"] = result.planner_mode
        meta["rows_returned"] = result.rows_returned
    elif case.kind == "fail_always":
        meta["repair_state"] = "failed"
    elif case.kind == "candidate_error":
        meta["repair_state"] = "rejected"
    return meta


def _assert_query_metrics_case(case: QueryMetricsCase, result: Any) -> CaseResult | None:
    """Return an error CaseResult when the pinned expectation is violated."""
    tool = "query_metrics"
    text = _flatten(result)
    if case.kind in ("clean", "fail_once"):
        if isinstance(result, str):
            return CaseResult.error_case(
                case.id, f"expected a SemanticQueryResult, got error: {text[:400]}",
                tool=tool, meta={"kind": case.kind},
            )
        if not result.rows:
            return CaseResult.error_case(case.id, "query returned no rows", tool=tool)
        if case.expected_planner_mode and result.planner_mode != case.expected_planner_mode:
            return CaseResult.error_case(
                case.id,
                f"planner_mode {result.planner_mode!r} != expected "
                f"{case.expected_planner_mode!r}",
                tool=tool,
            )
        traces = result.repair_traces
        if case.kind == "clean":
            if len(traces) != 1 or not traces[0].success:
                return CaseResult.error_case(
                    case.id,
                    f"expected one clean attempt, got {len(traces)} trace(s)",
                    tool=tool,
                )
        else:  # fail_once
            if len(traces) != 2:
                return CaseResult.error_case(
                    case.id, f"expected 2 repair traces, got {len(traces)}", tool=tool
                )
            if traces[0].repair_action != case.expected_repair:
                return CaseResult.error_case(
                    case.id,
                    f"repair_action {traces[0].repair_action!r} != expected "
                    f"{case.expected_repair!r} (error was {traces[0].clickhouse_error!r})",
                    tool=tool,
                )
            if not traces[-1].success:
                return CaseResult.error_case(
                    case.id, "final repair trace did not succeed", tool=tool
                )
        return None
    if case.kind == "fail_always":
        if not isinstance(result, str):
            return CaseResult.error_case(
                case.id, "expected a terminal error string, got a success payload",
                tool=tool,
            )
        if (
            "Semantic execution failed after deterministic repair retry" not in result
            or "semantic_repair_failed" not in result
        ):
            return CaseResult.error_case(
                case.id, f"unexpected terminal error text: {text[:400]}", tool=tool
            )
        return None
    if case.kind == "candidate_error":
        if not isinstance(result, str):
            return CaseResult.error_case(
                case.id, "expected the candidate-tier refusal, got a success payload",
                tool=tool,
            )
        if "Semantic coverage gap" not in result or "allow_candidate" not in result:
            return CaseResult.error_case(
                case.id, f"unexpected refusal text: {text[:400]}", tool=tool
            )
        return None
    return CaseResult.error_case(case.id, f"unknown case kind {case.kind!r}", tool=tool)


async def _query_metrics_real_async(
    ctx: BenchContext,
    snapshot,
    real_cases: list[tuple[str, str, tuple[str, ...]]],
    results: list[CaseResult],
) -> None:
    from mcp.server.fastmcp import FastMCP
    from cerebro_mcp.clients.clickhouse import ClickHouseManager
    from cerebro_mcp.tools.semantic.semantic import register_semantic_tools

    try:
        ch = ClickHouseManager()
    except Exception as exc:
        for cid, _metric, _dims in real_cases:
            results.append(CaseResult.error_case(
                cid, f"ClickHouseManager init failed: {exc}", tool="query_metrics"
            ))
        return

    reset_semantic_process_state()
    with deterministic_semantic_runtime(snapshot):
        mcp = FastMCP("bench-semantic-real")
        register_semantic_tools(mcp, ch, SimpleNamespace())
        for cid, metric, dims in real_cases:
            args: dict[str, Any] = {"metrics": [metric], "limit": 50}
            if dims:
                args["dimensions"] = list(dims)
            try:
                result, samples = await measure_latency_async(
                    lambda: mcp._tool_manager.call_tool("query_metrics", dict(args)),
                    iters=_iters(ctx, 3), warmup=_warmup(ctx, 1),
                )
                if isinstance(result, str):
                    results.append(CaseResult.error_case(
                        cid, _flatten(result)[:400], tool="query_metrics"
                    ))
                    continue
                results.append(CaseResult(
                    id=cid, tool="query_metrics", samples_ms=samples,
                    budget_ms=REAL_QUERY_BUDGET_MS,
                    meta={"kind": "real", "metric": metric,
                          "planner_mode": result.planner_mode,
                          "rows_returned": result.rows_returned},
                ).finalize())
            except Exception as exc:
                results.append(CaseResult.error_case(
                    cid, f"{type(exc).__name__}: {exc}", tool="query_metrics"
                ))


# ──────────────────────────────────────────────────────────────────────
# Section E — coverage scalars
# ──────────────────────────────────────────────────────────────────────


def _run_coverage_section(
    ctx: BenchContext, registry: dict[str, Any], results: list[CaseResult]
) -> None:
    runnable = [c for c in COVERAGE_CASES if ctx.should_run(c.id)]
    if not runnable:
        return

    source = "live" if ctx.live_registry else "fixture"
    try:
        if ctx.live_registry:
            from cerebro_mcp.loaders.semantic import semantic_runtime

            live_snapshot = semantic_runtime.snapshot or semantic_runtime.load()
            if live_snapshot is None:
                raise RuntimeError(
                    "live semantic registry unavailable (SemanticRuntime.load() "
                    "returned None)"
                )
            models = live_snapshot.models
            metrics = live_snapshot.metrics
            dimension_index = live_snapshot.dimension_index
            coverage_summary = None
        else:
            models = registry["models_exec"]
            metrics = registry["metrics"]
            dimension_index = registry["dimension_index"]
            coverage_summary = registry.get("coverage_summary")
        stats = _coverage_stats(models, metrics, dimension_index, coverage_summary)
    except Exception as exc:
        for case in runnable:
            results.append(CaseResult.error_case(
                case.id, f"{type(exc).__name__}: {exc}", tool="coverage"
            ))
        return

    for case in runnable:
        value, extra = stats[case.stat]
        meta = {
            "kind": "coverage",
            "stat": case.stat,
            "value": value,
            "direction": case.direction,
            "source": source,
            **extra,
        }
        record = CaseResult(id=case.id, tool="coverage", samples_ms=[], meta=meta)
        if case.direction == "must_be_zero" and value > 0:
            record.status = ERROR
            record.error = f"{case.stat} = {value} (must be zero); sample: {extra.get('sample')}"
        results.append(record)


def _coverage_stats(
    models: dict[str, Any],
    metrics: dict[str, Any],
    dimension_index: dict[str, Any],
    coverage_summary: dict[str, Any] | None,
) -> dict[str, tuple[float, dict[str, Any]]]:
    """stat name -> (value, extra_meta). Same math for fixture and live."""
    n_models = max(1, len(models))
    n_metrics = max(1, len(metrics))
    tier_counts: dict[str, int] = {}
    for metric in metrics.values():
        tier = str(metric.get("quality_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    status_counts: dict[str, int] = {}
    for model in models.values():
        status = str(model.get("semantic_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    orphans = sorted(
        name for name, metric in metrics.items()
        if metric.get("root_model") not in models
    )
    if coverage_summary and isinstance(coverage_summary.get("modules"), dict):
        per_module = {
            module: int(counts.get("approved", 0))
            for module, counts in coverage_summary["modules"].items()
        }
        per_module_source = "coverage_summary"
    else:
        per_module = {}
        for model in models.values():
            if model.get("semantic_status") == "approved":
                module = str(model.get("module") or "unknown")
                per_module[module] = per_module.get(module, 0) + 1
        per_module_source = "computed_from_models"

    return {
        "metrics_total": (float(len(metrics)), {}),
        "metrics_approved_count": (
            float(tier_counts.get("approved", 0)), {"by_tier": tier_counts}
        ),
        "metrics_candidate_count": (
            float(tier_counts.get("candidate", 0)), {"by_tier": tier_counts}
        ),
        "models_by_semantic_status": (
            float(status_counts.get("approved", 0)), {"breakdown": status_counts}
        ),
        "pct_models_with_entities": (
            round(100.0 * sum(1 for m in models.values() if m.get("entities")) / n_models, 2),
            {"n_models": len(models)},
        ),
        "pct_metrics_with_synonyms": (
            round(
                100.0
                * sum(1 for m in metrics.values() if m.get("question_synonyms"))
                / n_metrics,
                2,
            ),
            {"n_metrics": len(metrics)},
        ),
        "dimension_index_size": (float(len(dimension_index)), {}),
        "pct_metrics_with_allowed_dimensions": (
            round(
                100.0
                * sum(1 for m in metrics.values() if m.get("allowed_dimensions"))
                / n_metrics,
                2,
            ),
            {"n_metrics": len(metrics)},
        ),
        "orphan_metrics": (
            float(len(orphans)), {"sample": orphans[:10]}
        ),
        "per_module_approved": (
            float(sum(per_module.values())),
            {"per_module": per_module, "per_module_source": per_module_source},
        ),
    }


# ──────────────────────────────────────────────────────────────────────
# Section F — semantic chart tools
# ──────────────────────────────────────────────────────────────────────


def _satisfy_semantic_chart_gate() -> None:
    """Record the state a chart-tier workflow legitimately accumulates before
    charting: a chart-mode preflight (route=semantic_ready) plus the lite
    discovery/lineage/schema evidence (MIN_MODELS_DETAILED_LITE=1,
    MIN_TABLES_VERIFIED=1)."""
    from cerebro_mcp.tools.governance.session_state import state

    state.record_semantic_preflight(route="semantic_ready", mode="chart")
    state.record_search_models("bench semantic charts", 3, source="semantic")
    state.record_get_model_details(CHART_GATE_ROOT_MODEL, source="semantic")
    state.record_describe_table(CHART_GATE_ROOT_MODEL, source="semantic")


def _run_chart_section(ctx: BenchContext, snapshot, results: list[CaseResult]) -> None:
    runnable = [c for c in CHART_CASES if ctx.should_run(c.id)]
    if not runnable:
        return

    corpus = load_search_corpus()
    reset_semantic_process_state()
    with deterministic_semantic_runtime(snapshot):
        install_fixture_manifest(corpus)
        ch = bench_clickhouse_from_corpus(
            corpus,
            overrides=[
                (pattern, (list(columns), [list(r) for r in rows]))
                for pattern, (columns, rows) in (SCALAR_KPI_OVERRIDE, WEEKLY_NETFLOW_OVERRIDE)
            ],
        )
        mcp = build_bench_server(ch)
        asyncio.run(_charts_async(ctx, mcp, runnable, results))
    reset_semantic_process_state()


async def _charts_async(
    ctx: BenchContext, mcp, cases: list[ChartCase], results: list[CaseResult]
) -> None:
    import cerebro_mcp.tools.visualization.charts as viz

    for case in cases:
        try:
            reset_server_state()
            if case.kind != "gate_negative":
                _satisfy_semantic_chart_gate()

            async def call():
                return await mcp._tool_manager.call_tool(case.tool, dict(case.args))

            result, samples = await measure_latency_async(
                call, iters=_iters(ctx, case.iters), warmup=_warmup(ctx, case.warmup)
            )
            text = _flatten(result)
            meta: dict[str, Any] = {
                "kind": case.kind,
                "charts_registered": len(viz._chart_registry),
            }

            if case.kind == "gate_negative":
                if case.expect_anchor not in text:
                    results.append(CaseResult.error_case(
                        case.id,
                        "semantic chart gate did NOT block an unprefixed "
                        f"{case.tool} call; got: {text[:400]}",
                        tool=case.tool, meta=meta,
                    ))
                    continue
                if viz._chart_registry:
                    results.append(CaseResult.error_case(
                        case.id, "gate blocked but a chart was still registered",
                        tool=case.tool, meta=meta,
                    ))
                    continue
                meta["blocked"] = True
                results.append(CaseResult(
                    id=case.id, tool=case.tool, samples_ms=samples,
                    budget_ms=case.budget_ms, meta=meta,
                ).finalize())
                continue

            if case.kind == "explain":
                compiled_sql = str(getattr(result, "compiled_sql", "") or "")
                if _is_tool_error(result) or not compiled_sql.strip():
                    results.append(CaseResult.error_case(
                        case.id,
                        f"explain_metric_query returned no compiled_sql: {text[:400]}",
                        tool=case.tool, meta=meta,
                    ))
                    continue
                meta["compiled_sql_chars"] = len(compiled_sql)
                meta["planner_mode"] = str(getattr(result, "planner_mode", ""))
                results.append(CaseResult(
                    id=case.id, tool=case.tool, samples_ms=samples,
                    budget_ms=case.budget_ms, meta=meta,
                ).finalize())
                continue

            # positive chart cases
            if _is_tool_error(result) or case.expect_anchor not in text:
                results.append(CaseResult.error_case(
                    case.id,
                    f"expected {case.expect_anchor!r} in the tool output; "
                    f"got: {text[:400]}",
                    tool=case.tool, meta=meta,
                ))
                continue
            if not viz._chart_registry:
                results.append(CaseResult.error_case(
                    case.id, "tool reported success but the chart registry is empty",
                    tool=case.tool, meta=meta,
                ))
                continue
            results.append(CaseResult(
                id=case.id, tool=case.tool, samples_ms=samples,
                budget_ms=case.budget_ms, meta=meta,
            ).finalize())
        except Exception as exc:
            results.append(CaseResult.error_case(
                case.id, f"{type(exc).__name__}: {exc}", tool=case.tool
            ))


# ──────────────────────────────────────────────────────────────────────
# Suite entrypoint
# ──────────────────────────────────────────────────────────────────────


def run(ctx: BenchContext) -> list[CaseResult]:
    results: list[CaseResult] = []

    registry = load_routing_registry()
    snapshot = snapshot_from_fixture(registry)
    micro_snapshot = _build_micro_snapshot()

    fingerprint = fixture_fingerprint(snapshot)
    fingerprint["source"] = "live" if ctx.live_registry else "fixture"
    ctx.extra.setdefault("environment", {}).update(fingerprint)

    _run_runtime_section(ctx, registry, snapshot, results)
    reset_semantic_process_state()
    _run_routing_section(ctx, snapshot, results)
    _run_planner_section(ctx, snapshot, micro_snapshot, results)
    _run_sql_golden_section(ctx, snapshot, micro_snapshot, results)
    reset_semantic_process_state()
    _run_query_metrics_section(ctx, snapshot, micro_snapshot, results)
    _run_coverage_section(ctx, registry, results)
    _run_chart_section(ctx, snapshot, results)
    reset_semantic_process_state()
    return results
