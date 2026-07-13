"""Canonical latency/scoring math for the benchmark harness.

Extracted from ``tests/eval/eval_harness.py`` (WS9, BIRD-style methodology) so
both the pytest mini-eval and the benchmark suites share one implementation.
``tests/eval/eval_harness.py`` re-exports everything here for backwards
compatibility.

- **Valid Efficiency Score (VES)** — latency vs a per-tool millisecond budget,
  ``100 * sqrt(budget / actual)`` clamped to ``[0, 100]``, computed over
  outlier-cleaned samples.
- ``measure_latency`` / ``measure_latency_async`` — warm-up + N timed runs via
  ``time.perf_counter`` so a cold cache or a GC pause doesn't skew the score.
- ``clean_outliers`` — robust modified z-score (median + MAD).
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

# Per-tool latency budgets (ms). A result at/under budget scores 100.
VES_BUDGETS_MS: dict[str, float] = {
    "search_graph_catalog": 100.0,
    "explore_neighborhood": 500.0,
    "calculate_flow_efficiency": 1000.0,
}


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


def sample_stats(samples_ms: Sequence[float]) -> dict[str, float]:
    """Full stats block for a benchmark case record (outlier-aware)."""
    vals = [float(s) for s in samples_ms]
    if not vals:
        return {
            "n": 0, "outliers_dropped": 0, "mean": 0.0,
            "p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0,
        }
    cleaned = clean_outliers(vals)
    pct = percentiles(cleaned)
    return {
        "n": len(vals),
        "outliers_dropped": len(vals) - len(cleaned),
        "mean": statistics.mean(cleaned),
        "p50": pct["p50"],
        "p95": pct["p95"],
        "p99": pct["p99"],
        "min": min(vals),
        "max": max(vals),
    }


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


async def measure_latency_async(
    coro_factory: Callable[[], Awaitable[Any]], *, iters: int = 7, warmup: int = 1
) -> tuple[Any, list[float]]:
    """Async twin of :func:`measure_latency` for awaitable tool calls."""
    for _ in range(max(0, warmup)):
        await coro_factory()
    samples: list[float] = []
    result: Any = None
    for _ in range(max(1, iters)):
        start = time.perf_counter()
        result = await coro_factory()
        samples.append((time.perf_counter() - start) * 1000.0)
    return result, samples
