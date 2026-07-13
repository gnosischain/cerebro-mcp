"""Workload definitions for the SSE load suite. Pure stdlib at import time.

Only read-only, gate-free tools appear here (``list_databases``, ``get_help``,
``execute_query``): the server's governance session state is process-global,
so a gated tool in one worker (or a ``generate_report`` resetting the analysis
cycle) would change the behavior — and therefore the latency — of every other
concurrent worker mid-cell. Gate-free tools keep the cells independent.
"""

from __future__ import annotations

from typing import Any

# Bounded stats query that exercises the full execute_query path (validation,
# ClickHouse round-trip, payload shaping) WITHOUT depending on warehouse
# tables: numbers(90) synthesizes 90 rows server-side, so it runs on any
# ClickHouse and stays cheap under concurrency. A table-backed heavy query
# (e.g. a quantiles scan over a real fct_* model) is a future option once the
# suite can discover a stable table at run time.
HEAVY_SQL = (
    "SELECT toStartOfDay(day) AS d, quantile(0.5)(value) "
    "FROM (SELECT today() - number AS day, number * 1.0 AS value FROM numbers(90)) "
    "GROUP BY d ORDER BY d LIMIT 90"
)

CHEAP_TOOLS: tuple[str, ...] = ("list_databases", "get_help")

HEAVY_CALL: tuple[str, dict[str, Any]] = (
    "execute_query",
    {"sql": HEAVY_SQL, "database": "dbt", "max_rows": 90},
)

# Workloads whose concurrency is capped at ctx.max_heavy_concurrency.
CAPPED_WORKLOADS: frozenset[str] = frozenset({"heavy", "mixed"})

# In "mixed", calls with (i % 10) < MIXED_CHEAP_PER_10 are cheap: a fixed
# deterministic 70/30 split instead of random draws, so two runs of the same
# cell issue the identical call sequence.
MIXED_CHEAP_PER_10 = 7

WORKLOADS: dict[str, dict[str, Any]] = {
    "handshake": {
        "kind": "handshake",
        "tools": ["initialize", "tools/list"],
        "description": (
            "connect + initialize + tools/list then disconnect, in a loop; "
            "measures session TTFB and tools/list latency under N parallel "
            "connection churners"
        ),
    },
    "cheap": {
        "kind": "calls",
        "tools": list(CHEAP_TOOLS),
        "description": (
            "one long-lived session per worker, round-robin "
            "list_databases / get_help"
        ),
    },
    "heavy": {
        "kind": "calls",
        "tools": ["execute_query"],
        "description": (
            "one long-lived session per worker, repeated bounded stats query "
            "through execute_query (numbers()-backed, no warehouse tables)"
        ),
    },
    "mixed": {
        "kind": "calls",
        "tools": [*CHEAP_TOOLS, "execute_query"],
        "description": "70% cheap (list_databases/get_help) / 30% heavy (execute_query)",
    },
}


def call_for(workload: str, i: int) -> tuple[str, dict[str, Any]]:
    """Deterministic (tool, arguments) for the i-th call of a call workload."""
    if workload == "cheap":
        return CHEAP_TOOLS[i % len(CHEAP_TOOLS)], {}
    if workload == "heavy":
        return HEAVY_CALL[0], dict(HEAVY_CALL[1])
    if workload == "mixed":
        if i % 10 < MIXED_CHEAP_PER_10:
            return CHEAP_TOOLS[i % len(CHEAP_TOOLS)], {}
        return HEAVY_CALL[0], dict(HEAVY_CALL[1])
    raise ValueError(f"workload {workload!r} has no call plan (kind=handshake?)")
