"""Suite 1 — per-tool latency over the in-process bench server.

Measures each tool through the PRODUCTION dispatch path
(``mcp._tool_manager.call_tool``) against the deterministic fixture stack:
recorded corpus manifest, routing-registry semantic snapshot, canned
ClickHouse. Warm-up + N timed iterations per case (BIRD-style), VES scored
against the per-tool budgets in ``benchmarks/cases/latency_cases.py``.

Gate preambles (chart/report preconditions) mutate the session-state
singleton directly so the timed region contains ONLY the tool call under
measurement. ``generate_report`` resets session state on success, so its case
re-arms the gates before every iteration (``setup_each``), still outside the
timed region.

Real mode (``CEREBRO_EVAL_CLICKHOUSE=1``): every case runs against the
PRODUCTION module-global server (``build_inprocess_real`` — real manifest,
live semantic registry, real ClickHouse), so the ``inprocess-real`` result
label reflects genuine warehouse numbers. Cases carrying ``real_args`` swap
their SQL for queries pinned to the actual warehouse columns; the rest use
the same args in both modes. ClickHouse-touching cases default to 5
iterations there (7 in fake mode). All SQL is read-only, date-bounded, and
LIMITed — see benchmarks/README.md ClickHouse-safety notes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

from benchmarks.cases.latency_cases import (
    BUDGETS_MS,
    CASES,
    PINNED_MODELS,
    REAL_STATS_SQL,
    STATS_SQL,
    ToolLatencyCase,
)
from benchmarks.core.results import CaseResult
from benchmarks.core.runner import BenchContext
from benchmarks.core.stats import measure_latency_async

SUPPORTED_MODES = frozenset({"inprocess"})

_CHART_ID_RE = re.compile(r"\bchart_\d+\b")


# ── result flattening / validation ──────────────────────────────────


def _flatten_result(result: Any) -> str:
    """Tool results to comparable text: CallToolResult -> joined content
    text, pydantic -> JSON, dict/list -> JSON, else str()."""
    from mcp.types import CallToolResult

    if isinstance(result, CallToolResult):
        parts = [
            block.text
            for block in (result.content or [])
            if getattr(block, "text", None)
        ]
        text = "\n".join(parts)
        if result.isError and "Error" not in text:
            text = f"Error: {text}"
        return text
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    return str(result)


def _validate_text(case: ToolLatencyCase, text: str, checks: tuple) -> str | None:
    """Substring contract on the LAST result. Returns an error message or None."""
    for needle in case.forbid_substrings:
        if needle and needle in text:
            idx = text.find(needle)
            return f"forbidden substring {needle!r} in result: {text[idx:idx + 300]!r}"
    for needle in checks:
        if needle and needle not in text:
            return f"expected substring {needle!r} missing from result: {text[:300]!r}"
    return None


# ── args templating ──────────────────────────────────────────────────


def _case_args(case: ToolLatencyCase, env: dict) -> dict:
    """Mode-appropriate raw args: ``real_args`` in real mode when pinned."""
    if env.get("real_mode") and case.real_args is not None:
        return case.real_args
    return case.args


def _render(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        for key, sub in variables.items():
            value = value.replace("{" + key + "}", sub)
        return value
    if isinstance(value, dict):
        return {k: _render(v, variables) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_render(v, variables) for v in value]
    return value


# ── fixture metric discovery ─────────────────────────────────────────


def _discover_day_metric(snapshot) -> str:
    """First approved, executable, day-grain metric in the fixture registry.

    Mirrors the semantic layer's executability rules (approved metric +
    approved root model) and its dimension fallback (``allowed_dimensions``,
    else the root model's declared dimensions) without importing the private
    helpers. Deterministic: preferred well-known names first, then sorted.
    """

    def _supported_dims(metric: dict) -> set[str]:
        allowed = [
            str(d).strip().lower().replace(" ", "_")
            for d in metric.get("allowed_dimensions", [])
            if d
        ]
        if not allowed:
            root = snapshot.models.get(metric.get("root_model", ""), {})
            allowed = [
                str(d.get("name", "")).strip().lower().replace(" ", "_")
                for d in root.get("dimensions", [])
                if d.get("name")
            ]
        return set(allowed)

    candidates = []
    for name in sorted(snapshot.metrics):
        metric = snapshot.metrics[name]
        root = snapshot.models.get(metric.get("root_model", ""), {})
        if (
            metric.get("quality_tier") == "approved"
            and metric.get("semantic_status") == "approved"
            and root.get("semantic_status") == "approved"
            and str(metric.get("type", "")).lower() == "simple"
            and "day" in _supported_dims(metric)
        ):
            candidates.append(name)
    for preferred in ("transaction_count", "validators_active"):
        if preferred in candidates:
            return preferred
    if candidates:
        return candidates[0]
    raise RuntimeError(
        "fixture registry contains no approved simple metric with a `day` "
        "dimension — semantic-metric latency cases cannot be pinned"
    )


# ── gate setups (session-state preambles, run OUTSIDE the timed region) ──


def _session_state():
    from cerebro_mcp.tools.governance.session_state import state

    return state


def _arm_common_depth(state) -> None:
    """Discovery + lineage + schema evidence the chart gate reads."""
    state.record_search_models("bench: bridge flows", len(PINNED_MODELS), source="raw")
    for model in PINNED_MODELS:
        state.record_get_model_details(model, source="raw")
    state.record_describe_table(PINNED_MODELS[0], source="raw")


async def _setup_chart_gate(env: dict) -> dict[str, str]:
    """Open the RAW chart gate in the light (chart) tier. ``hybrid_ready``
    keeps raw SQL charting allowed alongside semantic coverage."""
    state = _session_state()
    state.record_semantic_preflight(route="hybrid_ready", mode="chart")
    _arm_common_depth(state)
    return {}


async def _setup_semantic_preflight(env: dict) -> dict[str, str]:
    """Minimal gate for ``quick_metric_chart``: routed + semantic_ready."""
    state = _session_state()
    state.record_semantic_preflight(route="semantic_ready", mode="chart")
    return {}


async def _setup_semantic_chart_gate(env: dict) -> dict[str, str]:
    """``generate_metric_charts`` checks the common depth too (raw_path=False)."""
    state = _session_state()
    state.record_semantic_preflight(route="semantic_ready", mode="chart")
    _arm_common_depth(state)
    return {}


async def _setup_report_gate(env: dict) -> dict[str, str]:
    """Full report-tier gate + chart registry population.

    Re-armed before every ``generate_report`` iteration (the tool resets the
    session state on success). The three gate charts are created ONCE by an
    UNTIMED ``generate_charts`` call and reused across iterations — a
    successful report clears session state but not the chart registry.
    """
    state = _session_state()
    state.record_semantic_preflight(route="hybrid_ready", mode="report")
    _arm_common_depth(state)
    state.record_execute_query(
        "SELECT date, net_usd FROM dbt.int_bridges_flows_daily LIMIT 100",
        source="raw",
    )
    # quantiles( + corr( — the classifier reads the recorded SQL text only
    state.record_execute_query(
        REAL_STATS_SQL if env.get("real_mode") else STATS_SQL, source="raw"
    )

    from cerebro_mcp.tools.visualization import charts as viz

    chart_ids = env.get("report_chart_ids") or []
    if not chart_ids or any(cid not in viz._chart_registry for cid in chart_ids):
        report_case = next(c for c in CASES if c.id == "latency/generate_charts")
        result = await env["tm"].call_tool("generate_charts", _case_args(report_case, env))
        text = _flatten_result(result)
        chart_ids = list(dict.fromkeys(_CHART_ID_RE.findall(text)))
        if len(chart_ids) < 3 or "Error:" in text:
            raise RuntimeError(f"report_gate setup: chart batch failed: {text[:400]}")
        env["report_chart_ids"] = chart_ids[:3]
        chart_ids = env["report_chart_ids"]

    body = "\n\n".join(f"{{{{chart:{cid}}}}}" for cid in chart_ids)
    markdown = (
        "## Bench Bridge Flows\n\n"
        "Deterministic canned data over the pinned bridge-flow model.\n\n"
        f"{body}\n\n"
        "Commentary between chart groups keeps the layout gate satisfied."
    )
    return {"report_markdown": markdown}


SETUPS: dict[str, Callable[[dict], Awaitable[dict[str, str]]]] = {
    "chart_gate": _setup_chart_gate,
    "semantic_preflight": _setup_semantic_preflight,
    "semantic_chart_gate": _setup_semantic_chart_gate,
    "report_gate": _setup_report_gate,
}


# ── measurement ──────────────────────────────────────────────────────


async def _measure_with_rearm(
    tm,
    case: ToolLatencyCase,
    setup_fn: Callable[[dict], Awaitable[dict[str, str]]],
    env: dict,
    base_vars: dict[str, str],
    *,
    iters: int,
    warmup: int,
) -> tuple[Any, list[float]]:
    """Custom sample loop for ``setup_each`` cases: re-arm the gates before
    EVERY call, but time ONLY the tool call itself."""
    result: Any = None
    samples: list[float] = []
    raw_args = _case_args(case, env)
    for _ in range(max(0, warmup)):
        variables = {**base_vars, **(await setup_fn(env) or {})}
        args = _render(raw_args, variables)
        result = await tm.call_tool(case.tool, args)
    for _ in range(max(1, iters)):
        variables = {**base_vars, **(await setup_fn(env) or {})}
        args = _render(raw_args, variables)
        start = time.perf_counter()
        result = await tm.call_tool(case.tool, args)
        samples.append((time.perf_counter() - start) * 1000.0)
    return result, samples


async def _run_case(
    ctx: BenchContext,
    tm,
    case: ToolLatencyCase,
    env: dict,
) -> CaseResult:
    from benchmarks.core.fakes import reset_server_state

    reset_server_state()

    base_vars = {"metric": env["metric"]}
    setup_fn = SETUPS[case.setup] if case.setup else None
    default_iters = 5 if (env.get("real_mode") and case.needs_clickhouse) else 7
    iters = ctx.iters if ctx.iters is not None else (case.iters if case.iters is not None else default_iters)
    warmup = ctx.warmup if ctx.warmup is not None else (case.warmup if case.warmup is not None else 1)

    raw_args = _case_args(case, env)
    meta = dict(case.meta)
    if "{metric}" in json.dumps(raw_args):
        meta["metric"] = env["metric"]

    try:
        if setup_fn is not None and case.setup_each:
            result, samples = await _measure_with_rearm(
                tm, case, setup_fn, env, base_vars, iters=iters, warmup=warmup
            )
            variables = dict(base_vars)  # checks with base vars only
        else:
            variables = dict(base_vars)
            if setup_fn is not None:
                variables.update(await setup_fn(env) or {})
            args = _render(raw_args, variables)
            result, samples = await measure_latency_async(
                lambda: tm.call_tool(case.tool, args),
                iters=iters,
                warmup=warmup,
            )
    except Exception as exc:  # tool dispatch/validation blew up
        return CaseResult.error_case(
            case.id, f"{type(exc).__name__}: {exc}", tool=case.tool, meta=meta
        )
    finally:
        if case.teardown:
            reset_server_state()

    text = _flatten_result(result)
    checks = tuple(_render(s, variables) for s in case.check_substrings)
    violation = _validate_text(case, text, checks)
    if violation:
        return CaseResult.error_case(case.id, violation, tool=case.tool, meta=meta)

    return CaseResult(
        id=case.id,
        tool=case.tool,
        samples_ms=samples,
        budget_ms=case.budget_ms if case.budget_ms is not None else BUDGETS_MS.get(case.tool),
        meta=meta,
    ).finalize()


async def _run_all(ctx: BenchContext, tm, env: dict, cases: list[ToolLatencyCase]) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        results.append(await _run_case(ctx, tm, case, env))
    return results


# ── suite entry point ────────────────────────────────────────────────


def _lint_core_coverage() -> None:
    """Every lean-core tool must have a latency case — fail the run loudly."""
    from cerebro_mcp.tools.tool_meta import CORE_TOOL_NAMES

    covered = {case.tool for case in CASES}
    missing = sorted(CORE_TOOL_NAMES - covered)
    if missing:
        raise RuntimeError(
            f"latency suite is missing cases for core tools: {', '.join(missing)}"
        )


def _run_fake(ctx: BenchContext, runnable: list[ToolLatencyCase]) -> list[CaseResult]:
    from benchmarks.core.inprocess import build_inprocess_fake
    from benchmarks.core.semantic_env import (
        deterministic_semantic_runtime,
        fixture_fingerprint,
    )

    mcp, ch, snapshot, _corpus = build_inprocess_fake()
    ctx.extra.setdefault("environment", {})["fixture"] = fixture_fingerprint(snapshot)
    env: dict[str, Any] = {
        "mcp": mcp,
        "tm": mcp._tool_manager,
        "ch": ch,
        "snapshot": snapshot,
        "real_mode": False,
        "metric": _discover_day_metric(snapshot),
    }
    with deterministic_semantic_runtime(snapshot):
        return asyncio.run(_run_all(ctx, env["tm"], env, runnable))


def _run_real(ctx: BenchContext, runnable: list[ToolLatencyCase]) -> list[CaseResult]:
    """Every case against the production module-global server: real manifest,
    live semantic registry, real ClickHouse. No deterministic-runtime patching
    — this measures the deployed configuration (``SEMANTIC_AUTOLOAD_ON_LOCAL_
    MTIME`` is already forced off by ``run.py`` so a mid-run registry reload
    cannot pollute the samples)."""
    from benchmarks.core.inprocess import build_inprocess_real

    mcp = build_inprocess_real()

    from cerebro_mcp.config import settings
    from cerebro_mcp.loaders.semantic import semantic_runtime

    snapshot = getattr(semantic_runtime, "_snapshot", None)
    # Semantic cases need EXECUTABLE semantic coverage, not just a loaded
    # snapshot: a stale local registry (manifest_hash_mismatch) loads fine but
    # routes everything to `semantic_unavailable` and refuses query_metrics.
    execution_available = bool(getattr(semantic_runtime, "_execution_available", False))
    stale_reason = getattr(semantic_runtime, "_stale_reason", None)
    semantic_ok = bool(settings.SEMANTIC_ENABLED and snapshot is not None and execution_available)
    if not semantic_ok:
        skip_reason = (
            "semantic execution unavailable"
            + (f": {stale_reason}" if stale_reason else "")
            + (" (SEMANTIC_ENABLED off)" if not settings.SEMANTIC_ENABLED else "")
            + (" — rebuild the local registry (build_registry.py) for live semantic numbers"
               if stale_reason else "")
        )

    results: list[CaseResult] = []
    live_cases: list[ToolLatencyCase] = []
    for case in runnable:
        if case.needs_semantic and not semantic_ok:
            results.append(CaseResult.skipped_case(case.id, skip_reason, tool=case.tool))
            continue
        live_cases.append(case)

    env: dict[str, Any] = {
        "mcp": mcp,
        "tm": mcp._tool_manager,
        "snapshot": snapshot,
        "real_mode": True,
        "metric": _discover_day_metric(snapshot) if semantic_ok else "",
    }
    ctx.extra.setdefault("environment", {})["fixture"] = {
        "source": "live",
        "registry_hash": getattr(snapshot, "registry_hash", None),
        "n_models": len(getattr(snapshot, "models", {}) or {}),
        "n_metrics": len(getattr(snapshot, "metrics", {}) or {}),
    }
    results.extend(asyncio.run(_run_all(ctx, env["tm"], env, live_cases)))
    return results


def run(ctx: BenchContext) -> list[CaseResult]:
    _lint_core_coverage()

    results: list[CaseResult] = []
    runnable: list[ToolLatencyCase] = []
    for case in CASES:
        if not ctx.should_run(case.id):
            continue
        if not ctx.real_clickhouse and not case.fake_ok:
            results.append(CaseResult.skipped_case(
                case.id,
                "requires real ClickHouse (set CEREBRO_EVAL_CLICKHOUSE=1)",
                tool=case.tool,
            ))
            continue
        runnable.append(case)

    if runnable:
        if ctx.real_clickhouse:
            results.extend(_run_real(ctx, runnable))
        else:
            results.extend(_run_fake(ctx, runnable))

    order = {case.id: i for i, case in enumerate(CASES)}
    results.sort(key=lambda r: order.get(r.id, len(order)))
    return results
