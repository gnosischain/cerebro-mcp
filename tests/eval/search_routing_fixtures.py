"""Pinned routing + discovery-precision expectations for the search benchmark suite.

PURE STDLIB at import time (dataclasses + literals only) — ``benchmarks/run.py``
imports the suite lazily after env redirection, but this module must stay safe
to import from anywhere (pytest collection, case listings) without touching
``cerebro_mcp``.

Every case below was authored against the RECORDED fixtures
(``tests/fixtures/routing_registry.json.gz`` for routing,
``tests/fixtures/search_corpus.json.gz`` for discovery precision) by running
the actual ``find`` / ``preflight_analytics_request`` / ``search_models``
surfaces and pinning what they produce. If a deliberate routing or ranking
change breaks a case, re-derive and update the case in the SAME change set —
do not weaken the assertion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingCase:
    """One pinned expectation for the ``find`` / preflight front doors.

    ``expected_metrics_contains`` must be a subset of the ``top_metrics``
    names ``find`` returns (top-5); ``expect_low_confidence`` pins the
    below-acceptance-bar softener (route stays ``semantic_coverage_gap`` but
    the closest metrics are surfaced flagged ``low_confidence``).
    """

    id: str
    query: str
    mode: str  # "answer" | "chart" | "report" | "auto"
    expected_route: str
    expected_action_tool: str
    expected_metrics_contains: tuple[str, ...] = ()
    expect_low_confidence: bool = False


@dataclass(frozen=True)
class DiscoveryPrecisionCase:
    """Precision pin for ``manifest.search_models``: ``must_include`` names
    belong in the top-``k``; ``must_exclude`` names (off-domain models) must
    not leak into the top-``k``."""

    id: str
    query: str
    k: int
    must_include: tuple[str, ...]
    must_exclude: tuple[str, ...]


# Category tags for the GOLDEN queries in tests/test_search_quality.py, keyed
# by the exact query string. Queries not listed here default to
# "plain_language" (handled suite-side).
CATEGORY_BY_QUERY: dict[str, str] = {
    "gas_used": "column_name",
    "api_execution_circles_v2_avatar_balances_daily": "exact_name",
    "int_briges_flows_daily": "typo",
}


# ---------------------------------------------------------------------------
# Routing cases (fixture registry: 1645 metrics, 1258 models)
# ---------------------------------------------------------------------------

ROUTING_CASES: tuple[RoutingCase, ...] = (
    # Covered metric, one query across all three explicit modes: answer goes
    # straight to query_metrics; chart/report must route through preflight.
    RoutingCase(
        id="search/routing/covered-answer-bridge-netflow",
        query="bridge netflow last week",
        mode="answer",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=("bridge_netflow_7d",),
    ),
    RoutingCase(
        id="search/routing/covered-chart-bridge-netflow",
        query="bridge netflow last week",
        mode="chart",
        expected_route="semantic_ready",
        expected_action_tool="preflight_analytics_request",
        expected_metrics_contains=("bridge_netflow_7d",),
    ),
    RoutingCase(
        id="search/routing/covered-report-bridge-netflow",
        query="bridge netflow last week",
        mode="report",
        expected_route="semantic_ready",
        expected_action_tool="preflight_analytics_request",
        expected_metrics_contains=("bridge_netflow_7d",),
    ),
    # Covered metrics across other registry domains (answer mode).
    RoutingCase(
        id="search/routing/covered-answer-netflow-by-bridge",
        query="weekly bridge netflow by bridge",
        mode="answer",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=("bridge_netflow_weekly_by_bridge",),
    ),
    RoutingCase(
        id="search/routing/covered-answer-gnosis-app-wau",
        query="gnosis app weekly active users",
        mode="answer",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=(
            "execution_gnosis_app_weekly_active_users__active_users_value",
        ),
    ),
    RoutingCase(
        id="search/routing/covered-answer-validator-apy",
        query="validator apy distribution",
        mode="answer",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=("consensus_validators_apy_dist_daily__q50_value",),
    ),
    RoutingCase(
        id="search/routing/covered-answer-pools-tvl",
        query="pools tvl",
        mode="answer",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=("kpi_pools_tvl_usd_value",),
    ),
    # Clearly-uncovered topics: coverage gap -> raw discovery.
    RoutingCase(
        id="search/routing/gap-answer-solana-memes",
        query="solana meme coin launches",
        mode="answer",
        expected_route="semantic_coverage_gap",
        expected_action_tool="discover_models",
    ),
    RoutingCase(
        id="search/routing/gap-answer-offtopic",
        query="favorite pizza toppings",
        mode="answer",
        expected_route="semantic_coverage_gap",
        expected_action_tool="discover_models",
    ),
    # mode="auto" intent inference: "plot ..." -> chart (the "plot" token also
    # lands in uncovered topics, so the route is hybrid_ready), "report on ..."
    # -> report, plain question -> answer.
    RoutingCase(
        id="search/routing/auto-chart-plot-netflow",
        query="plot bridge netflow last week",
        mode="auto",
        expected_route="hybrid_ready",
        expected_action_tool="preflight_analytics_request",
        expected_metrics_contains=("bridge_netflow_7d",),
    ),
    RoutingCase(
        id="search/routing/auto-report-netflow-by-bridge",
        query="report on bridge netflow by bridge",
        mode="auto",
        expected_route="semantic_ready",
        expected_action_tool="preflight_analytics_request",
        expected_metrics_contains=("bridge_netflow_weekly_by_bridge",),
    ),
    RoutingCase(
        id="search/routing/auto-answer-plain-question",
        query="what was the bridge netflow last week",
        mode="auto",
        expected_route="semantic_ready",
        expected_action_tool="query_metrics",
        expected_metrics_contains=("bridge_netflow_7d",),
    ),
    # Low-confidence near-miss: the specificity tokens ("wallet", "overlap")
    # appear in no scored metric's blob, so nothing clears the acceptance bar
    # but the closest bridge metrics surface flagged low_confidence.
    RoutingCase(
        id="search/routing/low-confidence-near-miss",
        query="wallet overlap between bridges",
        mode="answer",
        expected_route="semantic_coverage_gap",
        expected_action_tool="discover_models",
        expected_metrics_contains=("bridge_distinct_chains",),
        expect_low_confidence=True,
    ),
)


# ---------------------------------------------------------------------------
# Discovery-precision cases (manifest.search_models over the search corpus)
# ---------------------------------------------------------------------------

DISCOVERY_PRECISION_CASES: tuple[DiscoveryPrecisionCase, ...] = (
    DiscoveryPrecisionCase(
        id="search/discovery/bridge-flows",
        query="bridge flows",
        k=5,
        must_include=("int_bridges_flows_daily",),
        must_exclude=("api_p2p_clients_latest", "api_p2p_discv5_clients_daily"),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/bridge-netflow-weekly",
        query="bridge netflow weekly",
        k=5,
        must_include=("fct_bridges_netflow_weekly_by_bridge",),
        must_exclude=("api_execution_circles_v2_avatar_balances_daily",),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/token-netflow-by-bridge",
        query="token netflow by bridge",
        k=5,
        must_include=("api_bridges_token_netflow_daily_by_bridge",),
        must_exclude=("api_consensus_validators_performance_daily",),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/avatar-balances",
        query="avatar balances",
        k=5,
        must_include=("api_execution_circles_v2_avatar_balances_daily",),
        must_exclude=("int_bridges_flows_daily",),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/validator-performance",
        query="validator performance",
        k=5,
        must_include=("api_consensus_validators_performance_daily",),
        must_exclude=("api_execution_pools_volume_daily", "int_bridges_flows_daily"),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/p2p-clients",
        query="p2p clients",
        k=5,
        must_include=("api_p2p_clients_latest",),
        must_exclude=("int_bridges_flows_daily",),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/pool-volume-daily",
        query="pool volume daily",
        k=5,
        must_include=("api_execution_pools_volume_daily",),
        must_exclude=("api_p2p_clients_latest",),
    ),
    DiscoveryPrecisionCase(
        id="search/discovery/gas-used-daily",
        query="gas used daily",
        k=5,
        must_include=("api_execution_transactions_gas_used_daily",),
        must_exclude=(
            "api_bridges_token_netflow_daily_by_bridge",
            "api_p2p_clients_latest",
        ),
    ),
)
