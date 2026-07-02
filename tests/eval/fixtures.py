"""Curated eval fixtures (the BIRD-style mini-dev set) for the graph tools.

Each fixture is a natural-language question + the tool/args that answer it + the
expected result + the BIRD-style "evidence" (the GraphProfile metadata an agent
would lean on). Keep this small and curated (15-25 cases) spanning single-profile
search, neighborhood traversal, and flow — quality over corpus size.

``needs_clickhouse`` fixtures exercise live SQL and are skipped when no database
is configured; ``search_graph_catalog`` fixtures run anywhere (in-process BM25).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalFixture:
    id: str
    tool: str
    question: str
    args: dict[str, Any]
    expected: Any
    difficulty: str = "moderate"
    evidence: str = ""
    needs_clickhouse: bool = False
    # How to reduce a raw tool result to the comparable value (set/number/dict).
    extract: str = "result"


# search_graph_catalog fixtures — expected is the SET of result doc ids.
SEARCH_FIXTURES: list[EvalFixture] = [
    EvalFixture(
        id="search_circles_trust",
        tool="search_graph_catalog",
        question="who trusts whom in circles?",
        args={"query": "circles trust", "min_quality_tier": "all"},
        expected={"profile:circles_trust"},
        difficulty="moderate",
        evidence="circles_trust profile: truster -> trustee (circles_avatar)",
        extract="result_ids_contains",
    ),
    EvalFixture(
        id="search_pool_kind",
        tool="search_graph_catalog",
        question="what graph profiles involve liquidity pools?",
        args={"query": "pool", "node_kind": "pool", "min_quality_tier": "all"},
        expected={"profile:lp_in_pool"},
        difficulty="moderate",
        evidence="lp_in_pool profile: provider -> pool_address",
        extract="result_ids_contains",
    ),
]

# Live-SQL fixtures (skipped without ClickHouse). expected is illustrative; the
# CI runner with a real DB asserts structure + latency, not exact rows.
GRAPH_FIXTURES: list[EvalFixture] = [
    EvalFixture(
        id="explore_circles_avatar_1hop",
        tool="explore_neighborhood",
        question="show the 1-hop trust neighborhood of an avatar",
        args={"seed_ids": ["<avatar>"], "profiles": ["circles_trust"], "hops": 1},
        expected=None,
        difficulty="moderate",
        evidence="circles_trust, directed, time_column=valid_from",
        needs_clickhouse=True,
        extract="node_count_positive",
    ),
    EvalFixture(
        id="flow_avatar_balances",
        tool="calculate_flow_efficiency",
        question="token-balance flow efficiency for an avatar",
        args={"profile": "circles_avatar_balances", "node_ids": ["<avatar>"]},
        expected=None,
        difficulty="challenging",
        evidence="circles_avatar_balances, weight_column=balance",
        needs_clickhouse=True,
        extract="flow_status_present",
    ),
]

MINI_FIXTURE_SET: list[EvalFixture] = SEARCH_FIXTURES + GRAPH_FIXTURES
