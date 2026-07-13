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

The latency/VES math lives in ``benchmarks/core/stats.py`` (canonical home,
shared with the benchmark harness) and is re-exported here so existing
imports keep working. The EX/scoring functions stay in this module.
"""

from __future__ import annotations

import math
from typing import Any

from benchmarks.core.stats import (  # noqa: F401  (re-exports)
    VES_BUDGETS_MS,
    clean_outliers,
    measure_latency,
    measure_latency_async,
    percentiles,
    valid_efficiency_score,
)

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


def set_equal(expected, actual) -> bool:
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
    samples_ms,
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
