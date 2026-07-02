"""In-process usage telemetry for the graph-native tools (WS12 analytics).

A lightweight, thread-safe recorder so the ``graph_usage_analytics`` tool can
answer "what's being explored, and where are the coverage gaps?" without standing
up a metrics store. Bounded memory (latency samples are capped). This complements
— it does not replace — the Prometheus counters in runtime.observability.
"""

from __future__ import annotations

import statistics
import threading
from collections import Counter, defaultdict
from typing import Any

_MAX_LATENCY_SAMPLES = 500

_lock = threading.Lock()
_tool_calls: Counter = Counter()
_profile_hits: Counter = Counter()
_node_kind_hits: Counter = Counter()
_search_queries: Counter = Counter()
_latency_ms: dict[str, list[float]] = defaultdict(list)


def record(
    tool: str,
    *,
    profiles: tuple[str, ...] | list[str] = (),
    node_kind: str = "",
    query: str = "",
    latency_ms: float | None = None,
) -> None:
    with _lock:
        _tool_calls[tool] += 1
        for profile in profiles:
            if profile:
                _profile_hits[profile] += 1
        if node_kind:
            _node_kind_hits[node_kind] += 1
        if query:
            _search_queries[query.strip().lower()] += 1
        if latency_ms is not None:
            samples = _latency_ms[tool]
            samples.append(float(latency_ms))
            if len(samples) > _MAX_LATENCY_SAMPLES:
                del samples[: len(samples) - _MAX_LATENCY_SAMPLES]


def _latency_summary() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for tool, samples in _latency_ms.items():
        if not samples:
            continue
        ordered = sorted(samples)
        out[tool] = {
            "count": len(ordered),
            "p50": round(ordered[len(ordered) // 2], 2),
            "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            "mean": round(statistics.mean(ordered), 2),
            "max": round(ordered[-1], 2),
        }
    return out


def snapshot(*, limit: int = 20, coverage_kinds: set[str] | None = None) -> dict[str, Any]:
    """Aggregated usage. ``coverage_kinds`` (all registered node kinds) lets the
    report flag kinds that exist in the graph but have never been explored."""
    with _lock:
        explored = set(_node_kind_hits)
        gaps = sorted((coverage_kinds or set()) - explored)
        return {
            "tool_calls": dict(_tool_calls),
            "top_profiles": _profile_hits.most_common(limit),
            "top_node_kinds": _node_kind_hits.most_common(limit),
            "top_search_queries": _search_queries.most_common(limit),
            "latency_ms": _latency_summary(),
            "coverage_gaps": gaps,
            "total_calls": sum(_tool_calls.values()),
        }


def reset() -> None:
    """Clear all counters (tests)."""
    with _lock:
        _tool_calls.clear()
        _profile_hits.clear()
        _node_kind_hits.clear()
        _search_queries.clear()
        _latency_ms.clear()
