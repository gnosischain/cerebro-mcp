"""BIRD-style scoring for the graph/semantic MCP tools (WS9).

Adapts BIRD's two-metric methodology to our tools:

- **Execution Accuracy (EX)** — binary correctness of a tool's result against an
  expected value, using SET semantics for id/edge collections (order-independent)
  and ``math.isclose`` for numeric (flow) answers.
- **Valid Efficiency Score (VES)** — for *correct* results only, latency vs a
  per-tool millisecond budget, ``100 * sqrt(budget / actual)`` clamped to
  ``[0, 100]``. Latency is measured over several iterations with warm-up runs and
  3-sigma outlier removal (BIRD's statistical rigor) so a cold cache or a GC
  pause doesn't skew the score.

Everything here is pure/deterministic except ``measure_latency`` (which times a
callable); the scoring functions are unit-tested without a database.
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

# Per-tool latency budgets (ms). A result at/under budget scores 100.
VES_BUDGETS_MS: dict[str, float] = {
    "search_graph_catalog": 100.0,
    "explore_neighborhood": 500.0,
    "calculate_flow_efficiency": 1000.0,
}

DEFAULT_FLOAT_REL_TOL = 1e-5
DEFAULT_FLOAT_ABS_TOL = 0.01


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def floats_close(
    expected: float,
    actual: float,
    *,
    rel_tol: float = DEFAULT_FLOAT_REL_TOL,
    abs_tol: float = DEFAULT_FLOAT_ABS_TOL,
) -> bool:
    return math.isclose(float(expected), float(actual), rel_tol=rel_tol, abs_tol=abs_tol)


def set_equal(expected: Iterable[Any], actual: Iterable[Any]) -> bool:
    """Order-independent equality over id/edge collections (stringified)."""
    return frozenset(str(x) for x in expected) == frozenset(str(x) for x in actual)


def execution_accuracy(expected: Any, actual: Any, **float_kwargs: float) -> bool:
    """Binary correctness. Numbers compare with isclose; collections by set;
    dicts (e.g. node -> efficiency) compare key-set + per-key value (None-aware,
    numeric isclose)."""
    if _is_number(expected) and _is_number(actual):
        return floats_close(expected, actual, **float_kwargs)
    if isinstance(expected, dict) and isinstance(actual, dict):
        if frozenset(expected) != frozenset(actual):
            return False
        for key, exp in expected.items():
            act = actual[key]
            if exp is None or act is None:
                if exp is not act:
                    return False
            elif _is_number(exp) and _is_number(act):
                if not floats_close(exp, act, **float_kwargs):
                    return False
            elif str(exp) != str(act):
                return False
        return True
    if isinstance(expected, (set, frozenset, list, tuple)):
        return set_equal(expected, actual if isinstance(actual, (set, frozenset, list, tuple)) else [actual])
    return str(expected) == str(actual)


def clean_outliers(samples: Sequence[float], *, threshold: float = 3.5) -> list[float]:
    """Drop latency outliers via the robust modified z-score (median + MAD).

    Median/MAD is used instead of mean/std because a single extreme sample (a GC
    pause, a cold connection) inflates std enough to hide itself from a 3-sigma
    filter. No-op for <3 samples. Falls back to std-based bounds when MAD is 0
    (more than half the samples identical). Always returns at least the input.
    """
    vals = [float(s) for s in samples]
    if len(vals) < 3:
        return vals
    median = statistics.median(vals)
    abs_dev = [abs(v - median) for v in vals]
    mad = statistics.median(abs_dev)
    if mad == 0:
        std = statistics.pstdev(vals)
        if std == 0:
            return vals
        lo, hi = median - 3.0 * std, median + 3.0 * std
        kept = [v for v in vals if lo <= v <= hi]
        return kept or vals
    kept = [v for v, d in zip(vals, abs_dev) if 0.6745 * d / mad <= threshold]
    return kept or vals


def valid_efficiency_score(budget_ms: float, samples_ms: Sequence[float]) -> float:
    """VES in [0, 100]. Cleans outliers, then 100*sqrt(budget/mean_actual)."""
    cleaned = clean_outliers(samples_ms)
    if not cleaned:
        return 0.0
    actual = statistics.mean(cleaned)
    if actual <= 0:
        return 100.0
    return max(0.0, min(100.0, 100.0 * math.sqrt(budget_ms / actual)))


def percentiles(samples: Sequence[float]) -> dict[str, float]:
    vals = sorted(float(s) for s in samples)
    if not vals:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def _pct(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        idx = min(len(vals) - 1, max(0, round(p / 100.0 * (len(vals) - 1))))
        return vals[idx]

    return {"p50": _pct(50), "p95": _pct(95), "p99": _pct(99)}


def measure_latency(fn: Callable[[], Any], *, iters: int = 7, warmup: int = 1) -> tuple[Any, list[float]]:
    """Run ``fn`` ``warmup`` times (discarded), then ``iters`` timed runs.

    Returns the last result and per-iteration latencies in ms. Warm-up avoids
    cold-cache skew (BIRD repeats and averages for stable measurement).
    """
    for _ in range(max(0, warmup)):
        fn()
    samples: list[float] = []
    result: Any = None
    for _ in range(max(1, iters)):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return result, samples


def classify_difficulty(quality_tier: str, cross_module: bool = False, cardinality: str = "low") -> str:
    """Map our trust/shape signals to BIRD-style difficulty tiers.

    docs_only/candidate -> simple; approved single-hop -> moderate; cross-module
    or high-cardinality traversal -> challenging.
    """
    if cross_module or cardinality == "high":
        return "challenging"
    if quality_tier == "approved":
        return "moderate"
    return "simple"


def score_case(
    *,
    tool: str,
    expected: Any,
    actual: Any,
    samples_ms: Sequence[float],
    float_rel_tol: float = DEFAULT_FLOAT_REL_TOL,
    float_abs_tol: float = DEFAULT_FLOAT_ABS_TOL,
) -> dict[str, Any]:
    """Score one eval case: EX, then VES only if EX passed (BIRD: VES=0 on EX=0)."""
    correct = execution_accuracy(expected, actual, rel_tol=float_rel_tol, abs_tol=float_abs_tol)
    budget = VES_BUDGETS_MS.get(tool, 500.0)
    ves = valid_efficiency_score(budget, samples_ms) if correct else 0.0
    return {
        "tool": tool,
        "execution_accuracy": 1 if correct else 0,
        "ves": round(ves, 2),
        "budget_ms": budget,
        "latency": percentiles(samples_ms),
    }
