"""Golden-query search-quality suite (CS-2, plan Phase 1 item 5).

~30 (query -> expected model) pairs pinned against a RECORDED registry
fixture (tests/fixtures/search_corpus.json.gz — real model names, descriptions,
tags, and column schemas; regenerate with
``uv run python tests/fixtures/record_search_corpus.py``). Every unified
search surface must place the expected model in its top-5 for every pair:

* the canonical ``ModelSearchIndex`` (semantic/search.py)
* ``catalog_search`` (data_catalog.py, models leg)
* the Metric Lab catalog (``build_metric_catalog``)
* ``manifest.search_models`` (token-overlap + field-weighted legs, RRF-fused)

Failing this test = a search regression. If a deliberate ranking change
breaks a pair, update the pair in the SAME change set with a note.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.semantic.search import (
    ModelSearchIndex,
    reset_search_cache_for_tests,
)
from tests.eval.corpus_fixtures import (
    build_manifest_loader,
    build_snapshot,
    load_search_corpus,
)

# (query, expected model) — expected must be a REAL model name from the
# recorded corpus. Mix of: plain-language asks, column-name asks, exact-name
# paste, and one typo (fuzzy path).
GOLDEN: list[tuple[str, str]] = [
    ("avatar balances", "api_execution_circles_v2_avatar_balances_daily"),
    ("avatar balance daily", "api_execution_circles_v2_avatar_balances_daily"),
    ("bridge flows daily", "int_bridges_flows_daily"),
    ("bridge netflow weekly", "fct_bridges_netflow_weekly_by_bridge"),
    ("token netflow by bridge", "api_bridges_token_netflow_daily_by_bridge"),
    ("gas used daily", "api_execution_transactions_gas_used_daily"),
    ("gas_used", "api_execution_transactions_gas_used_daily"),
    ("dex trades by token", "fct_execution_trades_by_token_daily"),
    ("pool volume daily", "api_execution_pools_volume_daily"),
    ("validator proposer rewards", "int_consensus_validators_proposer_rewards_daily"),
    ("validator performance", "api_consensus_validators_performance_daily"),
    ("active trusts", "fct_execution_circles_v2_active_trusts_daily"),
    ("trusts distribution", "api_execution_circles_v2_trusts_distribution"),
    ("transactions count daily", "api_execution_transactions_cnt_daily"),
    ("weekly active users gnosis app", "api_execution_gnosis_app_weekly_active_users"),
    ("token transfers daily", "int_execution_tokens_transfers_daily"),
    ("stablecoin supply", "api_quarterly_data_stablecoin_supply"),
    ("pools tvl by pool", "api_execution_pools_tvl_by_pool_latest"),
    ("token prices daily", "int_execution_token_prices_daily"),
    ("consensus blocks daily", "api_consensus_blocks_daily"),
    ("validator deposits", "int_consensus_validators_deposits_daily"),
    ("deposits withdrawals volume", "api_consensus_deposits_withdrawls_volume_daily"),
    ("p2p clients", "api_p2p_clients_latest"),
    ("discv5 clients daily", "api_p2p_discv5_clients_daily"),
    # "gpay volume" alone is ambiguous with the api_celo_gpay_volume_* family
    ("quarterly gpay volume", "api_quarterly_data_gpay_volume"),
    ("swap fees gnosis app", "api_execution_gnosis_app_swap_fees_daily"),
    ("unified fee revenue", "int_revenue_fees_unified_daily"),
    ("circles transfers daily", "api_execution_circles_v2_transfers_daily"),
    ("p2p velocity", "api_execution_circles_v2_p2p_velocity_daily"),
    ("crc20 prices", "api_execution_circles_v2_crc20_prices_daily"),
    # typo — fuzzy fallback path
    ("int_briges_flows_daily", "int_bridges_flows_daily"),
    # exact-name paste
    (
        "api_execution_circles_v2_avatar_balances_daily",
        "api_execution_circles_v2_avatar_balances_daily",
    ),
]

TOP_K = 5


@pytest.fixture(scope="module")
def corpus() -> dict[str, dict]:
    return load_search_corpus()


@pytest.fixture(scope="module")
def snapshot(corpus):
    return build_snapshot(corpus)


@pytest.fixture(autouse=True)
def _clean_search_cache():
    reset_search_cache_for_tests()
    yield
    reset_search_cache_for_tests()


def _report(misses: list[tuple[str, str, list[str]]]) -> str:
    lines = [f"{len(misses)} golden queries missed top-{TOP_K}:"]
    for q, expect, hits in misses:
        lines.append(f"  {q!r} expected {expect}, got {hits}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Surface 1: the canonical index itself
# ---------------------------------------------------------------------------


def test_core_index_hits_at_5(snapshot):
    idx = ModelSearchIndex.for_snapshot(snapshot)
    misses = []
    for q, expect in GOLDEN:
        hits = [h.name for h in idx.search(q, limit=TOP_K)]
        if expect not in hits:
            misses.append((q, expect, hits))
    assert not misses, _report(misses)


# ---------------------------------------------------------------------------
# Surface 2: catalog_search (models leg)
# ---------------------------------------------------------------------------


def test_catalog_search_hits_at_5(snapshot, monkeypatch):
    import cerebro_mcp.tools.semantic.data_catalog as dc

    monkeypatch.setattr(dc, "current_snapshot", lambda: snapshot)
    dc._INDEX_CACHE.clear()

    misses = []
    for q, expect in GOLDEN:
        r = dc.catalog_search(q, entity_types=["model"], limit=TOP_K)
        hits = [h["name"] for h in r["hits"][:TOP_K]]
        if expect not in hits:
            misses.append((q, expect, hits))
    dc._INDEX_CACHE.clear()
    assert not misses, _report(misses)


# ---------------------------------------------------------------------------
# Surface 3: Metric Lab catalog
# ---------------------------------------------------------------------------


def test_metric_lab_catalog_hits_at_5(snapshot, monkeypatch):
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic import semantic as semantic_tools
    from cerebro_mcp.tools.visualization import metric_lab as ml

    monkeypatch.setattr(semantic_runtime, "_snapshot", snapshot)
    monkeypatch.setattr(semantic_runtime, "_execution_available", True)
    monkeypatch.setattr(semantic_runtime, "_stale_reason", None)
    monkeypatch.setattr(
        semantic_tools.manifest, "reload_if_changed", lambda: (False, None)
    )
    monkeypatch.setattr(
        semantic_tools.catalog, "reload_if_changed", lambda: (False, None)
    )
    monkeypatch.setattr(semantic_runtime, "refresh_if_changed", lambda: (False, None))
    ml._BASE_CATALOG_CACHE.clear()

    misses = []
    for q, expect in GOLDEN:
        r = ml.build_metric_catalog(query=q, limit=TOP_K)
        hits = [e["name"] for e in r["entries"][:TOP_K]]
        if expect not in hits:
            misses.append((q, expect, hits))
    ml._BASE_CATALOG_CACHE.clear()
    assert not misses, _report(misses)


# ---------------------------------------------------------------------------
# Surface 4: manifest.search_models (RRF of token-overlap + canonical index)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest_loader(corpus):
    return build_manifest_loader(corpus)


def test_manifest_search_models_hits_at_5(manifest_loader):
    misses = []
    for q, expect in GOLDEN:
        results = manifest_loader.search_models(query=q, limit=TOP_K)
        hits = [r["name"] for r in results[:TOP_K]]
        if expect not in hits:
            misses.append((q, expect, hits))
    assert not misses, _report(misses)
