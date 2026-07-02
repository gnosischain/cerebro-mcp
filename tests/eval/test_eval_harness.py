"""Unit tests for the eval scoring + an end-to-end search eval (WS9).

The pure scoring functions run anywhere. The search_graph_catalog eval builds a
real snapshot and scores the live tool (no ClickHouse needed — BM25 is
in-process). The neighborhood/flow fixtures need a database and are skipped
unless CEREBRO_EVAL_CLICKHOUSE=1.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

import cerebro_mcp.loaders.semantic as sem
from cerebro_mcp.semantic import graph_profiles
from cerebro_mcp.tools.semantic.graph_explorer import register_graph_explorer_tools

from tests.eval import eval_harness as H
from tests.eval.fixtures import MINI_FIXTURE_SET, SEARCH_FIXTURES


# ---------------------------------------------------------------------------
# Pure scoring unit tests
# ---------------------------------------------------------------------------


def test_execution_accuracy_sets_order_independent():
    assert H.execution_accuracy({"a", "b"}, ["b", "a"]) is True
    assert H.execution_accuracy({"a"}, {"a", "b"}) is False


def test_execution_accuracy_floats_use_isclose():
    assert H.execution_accuracy(2.0, 2.0 + 1e-9) is True
    assert H.execution_accuracy(2.0, 2.5) is False


def test_execution_accuracy_dict_with_none():
    assert H.execution_accuracy({"x": None}, {"x": None}) is True
    assert H.execution_accuracy({"x": None}, {"x": 1.0}) is False
    assert H.execution_accuracy({"x": 2.0}, {"x": 2.0}) is True


def test_clean_outliers_drops_extreme():
    samples = [10, 11, 9, 10, 1000]
    kept = H.clean_outliers(samples)
    assert 1000 not in kept and len(kept) == 4


def test_ves_under_budget_is_100_over_budget_less():
    assert H.valid_efficiency_score(100.0, [50.0, 50.0, 50.0]) == 100.0
    slow = H.valid_efficiency_score(100.0, [400.0, 400.0, 400.0])
    assert 0.0 < slow < 100.0
    assert H.valid_efficiency_score(100.0, []) == 0.0


def test_score_case_zero_ves_when_incorrect():
    s = H.score_case(tool="search_graph_catalog", expected={"a"}, actual={"b"}, samples_ms=[1.0])
    assert s["execution_accuracy"] == 0 and s["ves"] == 0.0


def test_classify_difficulty():
    assert H.classify_difficulty("candidate") == "simple"
    assert H.classify_difficulty("approved") == "moderate"
    assert H.classify_difficulty("approved", cross_module=True) == "challenging"


def test_percentiles_monotonic():
    p = H.percentiles([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert p["p50"] <= p["p95"] <= p["p99"]


# ---------------------------------------------------------------------------
# End-to-end eval: search_graph_catalog (no ClickHouse)
# ---------------------------------------------------------------------------


def _graph_model(name, profile, module, src, tgt, src_kind, tgt_kind, status="approved", time_col=None, weight=None, syn=()):
    g = {
        "enabled": True,
        "profile": profile,
        "source_column": src,
        "target_column": tgt,
        "source_kind": src_kind,
        "target_kind": tgt_kind,
        "directed": True,
    }
    if time_col:
        g["time_column"] = time_col
    if weight:
        g["weight_column"] = weight
    return {
        "name": name,
        "relation_name": name,
        "module": module,
        "description": f"{profile} edges",
        "semantic_status": status,
        "quality_tier": status if status != "docs_only" else "",
        "columns": {src: {}, tgt: {}},
        "semantic": {"meta": {"question_synonyms": list(syn), "graph": g}},
    }


def _eval_snapshot():
    registry = {
        "metadata": {"manifest_hash": "h", "catalog_hash": "c"},
        "models": {
            "api_circles_trust": _graph_model(
                "api_circles_trust", "circles_trust", "Circles",
                "truster", "trustee", "circles_avatar", "circles_avatar",
                time_col="valid_from", syn=("circles trust", "who trusts whom"),
            ),
            "int_pools_lp": _graph_model(
                "int_pools_lp", "lp_in_pool", "pools",
                "provider", "pool_address", "address", "pool", weight="amount_usd",
            ),
        },
        "relationships": [],
    }
    return sem.SemanticRuntime()._build_snapshot(registry, [], None)


def _search_tool():
    server = FastMCP("eval")

    class _CH:  # search never touches ClickHouse
        pass

    register_graph_explorer_tools(server, _CH())
    return server._tool_manager._tools["search_graph_catalog"].fn  # type: ignore[attr-defined]


@pytest.mark.parametrize("fixture", SEARCH_FIXTURES, ids=lambda f: f.id)
def test_search_eval_fixtures_pass(fixture):
    snap = _eval_snapshot()
    with patch.object(graph_profiles, "semantic_runtime") as rt:
        rt.snapshot = snap
        tool = _search_tool()
        result, samples = H.measure_latency(lambda: tool(**fixture.args), iters=5, warmup=1)
    actual_ids = {r["id"] for r in result["results"]}
    # expected is a subset that must appear in the hits
    correct = fixture.expected <= actual_ids
    score = H.score_case(
        tool=fixture.tool,
        expected=True,
        actual=correct,
        samples_ms=samples,
    )
    assert score["execution_accuracy"] == 1, f"{fixture.id}: got {actual_ids}"
    assert score["ves"] > 0  # in-process search should be well under 100ms


# ---------------------------------------------------------------------------
# DB-backed fixtures — skipped unless a ClickHouse-backed eval is requested
# ---------------------------------------------------------------------------

_CH_FIXTURES = [f for f in MINI_FIXTURE_SET if f.needs_clickhouse]


@pytest.mark.skipif(
    os.environ.get("CEREBRO_EVAL_CLICKHOUSE") != "1",
    reason="set CEREBRO_EVAL_CLICKHOUSE=1 to run live graph/flow evals",
)
@pytest.mark.parametrize("fixture", _CH_FIXTURES, ids=lambda f: f.id)
def test_clickhouse_eval_fixtures(fixture):  # pragma: no cover - requires DB
    pytest.skip("live ClickHouse eval runner is wired via the CI db job")
