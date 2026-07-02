import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.models.semantic import SemanticSnapshot
from cerebro_mcp.tools.semantic.semantic import register_semantic_tools


def _make_snapshot() -> SemanticSnapshot:
    models = {
        "api_execution_transactions_by_sector_daily": {
            "name": "api_execution_transactions_by_sector_daily",
            "module": "execution",
            "relation_name": "dbt.api_execution_transactions_by_sector_daily",
            "semantic_status": "approved",
            "dimensions": [
                {"name": "day", "type": "time", "expr": "day"},
                {"name": "sector", "type": "categorical", "expr": "sector"},
            ],
            "measures": [
                {"name": "transaction_count_value", "agg": "sum", "expr": "txs"},
            ],
        },
        "api_consensus_validators_active_daily": {
            "name": "api_consensus_validators_active_daily",
            "module": "consensus",
            "relation_name": "dbt.api_consensus_validators_active_daily",
            "semantic_status": "approved",
            "dimensions": [
                {"name": "day", "type": "time", "expr": "day"},
            ],
            "measures": [
                {"name": "validators_active_value", "agg": "sum", "expr": "cnt"},
            ],
        },
    }
    metrics = {
        "transaction_count": {
            "name": "transaction_count",
            "label": "Transaction Count",
            "module": "execution",
            "root_model": "api_execution_transactions_by_sector_daily",
            "measure": "transaction_count_value",
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["day", "sector"],
            "supported_time_grains": ["day", "week", "month"],
            "default_filters": [{"field": "sector", "op": "!=", "value": "spam"}],
            "question_synonyms": ["tx count"],
            "all_synonyms": ["transaction_count", "transaction count", "tx count"],
            "search_blob": "transaction_count transaction count execution tx count",
        },
        "candidate_wallet_metric": {
            "name": "candidate_wallet_metric",
            "label": "Candidate Wallet Metric",
            "module": "execution",
            "root_model": "api_execution_transactions_by_sector_daily",
            "measure": "transaction_count_value",
            "quality_tier": "candidate",
            "semantic_status": "candidate",
            "allowed_dimensions": ["sector"],
            "supported_time_grains": ["day"],
            "default_filters": [],
            "question_synonyms": ["wallet candidate"],
            "all_synonyms": ["candidate_wallet_metric", "wallet candidate"],
            "search_blob": "candidate_wallet_metric wallet candidate execution",
        },
        "validators_active": {
            "name": "validators_active",
            "label": "Validators Active",
            "module": "consensus",
            "root_model": "api_consensus_validators_active_daily",
            "measure": "validators_active_value",
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["day"],
            "supported_time_grains": ["day", "week", "month"],
            "default_filters": [],
            "question_synonyms": ["active validators", "validator count"],
            "all_synonyms": ["validators_active", "active validators", "validator count"],
            "search_blob": "validators_active active validators validator count consensus",
        },
    }
    return SemanticSnapshot(
        registry_hash="registry-hash",
        manifest_hash="manifest-hash",
        catalog_hash="catalog-hash",
        docs_hash="docs-hash",
        graph={"adjacency": {}},
        vertex_ids={
            "api_execution_transactions_by_sector_daily": 0,
            "api_consensus_validators_active_daily": 1,
        },
        synonym_index={
            "transaction_count": "transaction_count",
            "transaction count": "transaction_count",
            "tx count": "transaction_count",
            "validators_active": "validators_active",
            "active validators": "validators_active",
            "validator count": "validators_active",
        },
        dimension_index={
            "day": [
                {
                    "provider_model": "api_execution_transactions_by_sector_daily",
                    "module": "execution",
                    "dimension": {"name": "day", "type": "time", "expr": "day"},
                    "semantic_status": "approved",
                },
                {
                    "provider_model": "api_consensus_validators_active_daily",
                    "module": "consensus",
                    "dimension": {"name": "day", "type": "time", "expr": "day"},
                    "semantic_status": "approved",
                }
            ],
            "sector": [
                {
                    "provider_model": "api_execution_transactions_by_sector_daily",
                    "module": "execution",
                    "dimension": {"name": "sector", "type": "categorical", "expr": "sector"},
                    "semantic_status": "approved",
                }
            ],
        },
        metrics=metrics,
        models=models,
        relationships=[],
        docs_index={
            "gnosis://semantic-model/api_execution_transactions_by_sector_daily": {
                "uri": "gnosis://semantic-model/api_execution_transactions_by_sector_daily",
                "type": "model",
                "path": "semantic_docs/models/api_execution_transactions_by_sector_daily.html",
                "title": "api_execution_transactions_by_sector_daily",
            }
        },
        loaded_at=0.0,
    )


@pytest.fixture()
def semantic_runtime_ready(monkeypatch):
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    snapshot = _make_snapshot()
    semantic_tools.state.reset()
    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_ENABLED", True)
    monkeypatch.setattr(
        semantic_tools.settings,
        "SEMANTIC_REFRESH_INTERVAL_SECONDS",
        10_000,
    )
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_snapshot",
        snapshot,
    )
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_execution_available",
        True,
    )
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_stale_reason",
        None,
    )
    monkeypatch.setattr(semantic_tools.manifest, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(semantic_tools.catalog, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "refresh_if_changed",
        lambda: (False, None),
    )
    monkeypatch.setattr(
        semantic_tools.state,
        "record_search_models",
        lambda query, count, source="raw": None,
    )
    monkeypatch.setattr(
        semantic_tools.state,
        "record_get_model_details",
        lambda model_name, source="raw": None,
    )
    monkeypatch.setattr(
        semantic_tools.state,
        "record_describe_table",
        lambda model_name, source="raw": None,
    )
    monkeypatch.setattr(
        semantic_tools.state,
        "record_execute_query",
        lambda sql, source="raw": None,
    )
    try:
        yield semantic_tools, snapshot
    finally:
        semantic_tools.state.reset()


def test_discover_metrics_returns_ranked_hits(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-test")
    ch = SimpleNamespace()
    research_store = SimpleNamespace()

    register_semantic_tools(mcp, ch, research_store)
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    result = fn(query="tx count")

    assert result.query == "tx count"
    assert result.results[0].name == "transaction_count"


def test_discover_metrics_surfaces_candidate_metrics_as_provisional(semantic_runtime_ready):
    """Candidate-tier metrics are no longer hidden from discovery: they
    appear flagged executable=False / provisional=True, and the summary
    carries the not-analyst-vetted warning with the allow_candidate hint."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-discovery-filter-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    result = fn(query="wallet candidate")

    names = [hit.name for hit in result.results]
    assert "candidate_wallet_metric" in names
    hit = next(h for h in result.results if h.name == "candidate_wallet_metric")
    assert hit.executable is False
    assert hit.provisional is True
    assert hit.quality_tier == "candidate"
    assert "provisional (candidate)" in result.summary_markdown
    assert "not analyst-vetted" in result.summary_markdown
    assert "allow_candidate=true" in result.summary_markdown


def test_discover_metrics_ranks_approved_before_higher_scoring_candidates(semantic_runtime_ready):
    """Approved metrics fill the result limit FIRST; provisional candidates
    only take remaining slots even when they outscore the approved hits."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-discovery-ranking-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    # "wallet candidate execution" is a substring of the candidate's search
    # blob (base 25 + three token bonuses) while transaction_count only gets
    # the "execution" token bonus + approved bonus — the candidate outscores
    # the approved metric on raw score.
    result = fn(query="wallet candidate execution")

    names = [hit.name for hit in result.results]
    assert names.index("transaction_count") < names.index("candidate_wallet_metric")
    approved_hit = next(h for h in result.results if h.name == "transaction_count")
    candidate_hit = next(h for h in result.results if h.name == "candidate_wallet_metric")
    assert candidate_hit.score > approved_hit.score  # ranking ignores raw score across tiers
    assert approved_hit.executable is True and approved_hit.provisional is False

    # With limit=1 the single slot goes to the approved hit.
    limited = fn(query="wallet candidate execution", limit=1)
    assert [hit.name for hit in limited.results] == ["transaction_count"]
    # The provisional warning still fires so the candidate isn't silently lost.
    assert "provisional (candidate)" in limited.summary_markdown


def test_preflight_routes_approved_metric_to_semantic_ready(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="transaction count by sector", mode="chart")

    assert result.route == "semantic_ready"
    assert result.recommended_metrics[0] == "transaction_count"
    assert result.recommended_dimensions == ["sector"]
    assert result.recommended_next_tool == "quick_metric_chart"


def test_preflight_routes_unknown_request_to_coverage_gap(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-gap-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="validators owning gnosis pay wallets", mode="report")

    assert result.route == "semantic_coverage_gap"
    assert result.fallback_reason == "semantic_coverage_gap"
    assert "validators_active" not in result.recommended_metrics


def test_preflight_routes_partial_coverage_to_hybrid_ready(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-hybrid-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    # "transaction count" is covered, "bridge volume" is not
    result = fn(query="transaction count and bridge volume weekly report", mode="report")

    assert result.route == "hybrid_ready"
    assert result.hybrid_ready is True
    assert len(result.covered_topics) >= 1
    assert len(result.uncovered_topics) >= 1
    assert result.recommended_metrics[0] == "transaction_count"


def test_preflight_routes_active_validators_question_to_semantic_ready(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-validators-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="How many active validators are there over time?", mode="answer")

    assert result.route == "semantic_ready"
    assert result.recommended_metrics[0] == "validators_active"
    assert result.recommended_dimensions == ["day"]
    assert result.recommended_next_tool == "query_metrics"


def test_preflight_semantic_ready_emits_explicit_query_metrics_directive(semantic_runtime_ready):
    """`semantic_ready` route must bake an explicit "call query_metrics
    FIRST" directive into the summary_markdown so agents can't ignore
    `recommended_metrics` and drop into raw discovery anyway."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-directive-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="How many active validators are there over time?", mode="answer")

    md = result.summary_markdown
    assert "Next action" in md
    assert "query_metrics" in md
    assert "validators_active" in md
    # Discourage parallel raw-discovery path on the semantic_ready route.
    assert "Do not run" in md or "do not run" in md.lower()
    # answer-mode directive carries the no-chart/no-report constraint.
    assert "no chart" in md.lower() or "no report" in md.lower()


def test_preflight_hybrid_ready_emits_split_directive(semantic_runtime_ready):
    """`hybrid_ready` must instruct: call `query_metrics` for the covered
    side FIRST, then raw discovery only for the uncovered side."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-hybrid-directive-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="transaction count and bridge volume weekly report", mode="report")

    assert result.route == "hybrid_ready"
    md = result.summary_markdown
    assert "Next action" in md
    assert "query_metrics" in md
    # The directive must mention the covered metric and tell the agent
    # to NOT skip the semantic call just because raw SQL also works.
    assert "transaction_count" in md
    assert "covered" in md.lower()
    assert "uncovered" in md.lower()


def test_preflight_coverage_gap_emits_raw_discovery_directive(semantic_runtime_ready):
    """`semantic_coverage_gap` should explicitly route to raw discovery
    without leaving the agent guessing."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-gap-directive-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="validators owning gnosis pay wallets", mode="report")

    assert result.route == "semantic_coverage_gap"
    md = result.summary_markdown
    assert "Next action" in md
    assert "discover_models" in md
    assert "execute_query" in md


def test_preflight_reuses_cached_result_without_growing_cache(semantic_runtime_ready):
    semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-cache-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn

    first = fn(query="How many active validators are there over time?", mode="answer")
    cache_size = semantic_tools.state.semantic_summary()["semantic_preflight_cache_size"]
    second = fn(query="How many active validators are there over time?", mode="answer")

    assert first.route == "semantic_ready"
    assert second.route == "semantic_ready"
    assert semantic_tools.state.semantic_summary()["semantic_preflight_cache_size"] == cache_size == 1


def test_semantic_tools_not_registered_when_disabled(monkeypatch):
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_ENABLED", False)
    mcp = FastMCP("semantic-disabled-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())

    assert "discover_metrics" not in mcp._tool_manager._tools


def test_query_metrics_repairs_unknown_identifier_once(semantic_runtime_ready):
    semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-query-test")

    executed = SimpleNamespace(
        sql="SELECT sector, transaction_count FROM branch_1",
        database="dbt",
        columns=["sector", "transaction_count"],
        rows=[["defi", 10]],
        row_count=1,
        elapsed_seconds=0.02,
        fetch_mode="rows",
        warnings=[],
        truncated=False,
        rows_returned=1,
    )

    class FakeClickHouse:
        def __init__(self):
            self.calls = 0

        def run_query(self, sql, database, requested_max_rows, audience, fetch_mode):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("UNKNOWN_IDENTIFIER: sector")
            return executed

        def build_query_result(self, executed_query, max_rows):
            return executed_query

    register_semantic_tools(mcp, FakeClickHouse(), SimpleNamespace())
    fn = mcp._tool_manager._tools["query_metrics"].fn
    result = fn(metrics=["transaction_count"], dimensions=["sector"], limit=10)

    assert result.rows == [["defi", 10]]
    assert len(result.repair_traces) == 2
    assert result.repair_traces[0].repair_action == "qualify_identifiers"
    assert result.repair_traces[1].success is True


def test_explain_metric_query_inlines_single_branch_select(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-inline-compiler-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["explain_metric_query"].fn
    result = fn(metrics=["transaction_count"], dimensions=["sector"], limit=25)

    assert result.compiled_sql.startswith("SELECT\n")
    assert "WITH\nbranch_1" not in result.compiled_sql
    assert "FROM dbt.api_execution_transactions_by_sector_daily AS b1_root" in result.compiled_sql
    assert "GROUP BY sector" in result.compiled_sql
    assert "LIMIT 25" in result.compiled_sql


def test_query_metrics_normalizes_date_dimension_alias(semantic_runtime_ready):
    semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-date-alias-test")

    executed = SimpleNamespace(
        sql="SELECT day, validators_active FROM dbt.api_consensus_validators_active_daily",
        database="dbt",
        columns=["day", "validators_active"],
        rows=[["2026-03-01", 144621]],
        row_count=1,
        elapsed_seconds=0.02,
        fetch_mode="rows",
        warnings=[],
        truncated=False,
        rows_returned=1,
    )

    class FakeClickHouse:
        def run_query(self, sql, database, requested_max_rows, audience, fetch_mode):
            return executed

        def build_query_result(self, executed_query, max_rows):
            return executed_query

    register_semantic_tools(mcp, FakeClickHouse(), SimpleNamespace())
    fn = mcp._tool_manager._tools["query_metrics"].fn
    result = fn(metrics=["validators_active"], dimensions=["date"], limit=10)

    assert result.resolved_dimensions == ["day"]
    assert result.rows == [["2026-03-01", 144621]]


def test_semantic_execution_unavailable_returns_graceful_error(monkeypatch):
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_ENABLED", True)
    monkeypatch.setattr(
        semantic_tools.settings,
        "SEMANTIC_REFRESH_INTERVAL_SECONDS",
        10_000,
    )
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_snapshot", _make_snapshot())
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_execution_available", False)
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_stale_reason",
        "manifest_hash_mismatch",
    )
    monkeypatch.setattr(semantic_tools.manifest, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(semantic_tools.catalog, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "refresh_if_changed",
        lambda: (False, None),
    )

    mcp = FastMCP("semantic-unavailable-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["explain_metric_query"].fn
    result = fn(metrics=["transaction_count"])

    assert "Semantic execution unavailable" in result


def test_query_metrics_returns_semantic_coverage_gap_for_candidate_metric(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-gap-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["query_metrics"].fn
    result = fn(metrics=["candidate_wallet_metric"])

    assert "Semantic coverage gap" in result
    # The improved error message also nudges the caller toward the
    # `allow_candidate` opt-in (PR 3) so authoring loops can iterate
    # without promoting to approved.
    assert "allow_candidate" in result


# ─── PROVISIONAL CANDIDATES: allow_candidate end-to-end ──────────────


def _candidate_query_executed():
    return SimpleNamespace(
        sql="SELECT sector, candidate_wallet_metric FROM branch_1",
        database="dbt",
        columns=["sector", "candidate_wallet_metric"],
        rows=[["defi", 7]],
        row_count=1,
        elapsed_seconds=0.01,
        fetch_mode="rows",
        warnings=[],
        truncated=False,
        rows_returned=1,
    )


def test_query_metrics_allow_candidate_plans_and_executes_candidate_metric(semantic_runtime_ready):
    """End-to-end: `allow_candidate=True` on a candidate metric with an
    approved root must survive the tool gate AND the planner (which used to
    re-reject it with `not approved for semantic execution`)."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-allow-candidate-exec-test")

    executed = _candidate_query_executed()

    class FakeClickHouse:
        def run_query(self, sql, database, requested_max_rows, audience, fetch_mode):
            return executed

        def build_query_result(self, executed_query, max_rows):
            return executed_query

    register_semantic_tools(mcp, FakeClickHouse(), SimpleNamespace())
    fn = mcp._tool_manager._tools["query_metrics"].fn
    result = fn(
        metrics=["candidate_wallet_metric"],
        dimensions=["sector"],
        limit=10,
        allow_candidate=True,
    )

    assert not isinstance(result, str), result
    assert result.resolved_metrics == ["candidate_wallet_metric"]
    assert result.planner_mode == "single_model"
    assert result.rows == [["defi", 7]]


def test_explain_metric_query_allow_candidate_plans_candidate_metric(semantic_runtime_ready):
    """explain_metric_query with allow_candidate=True compiles SQL for a
    candidate metric (approved root); without the flag it still refuses."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-allow-candidate-explain-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["explain_metric_query"].fn

    result = fn(
        metrics=["candidate_wallet_metric"],
        dimensions=["sector"],
        allow_candidate=True,
    )
    assert not isinstance(result, str), result
    assert result.resolved_metrics == ["candidate_wallet_metric"]
    assert result.planner_mode == "single_model"
    assert "FROM dbt.api_execution_transactions_by_sector_daily" in result.compiled_sql

    denied = fn(metrics=["candidate_wallet_metric"], dimensions=["sector"])
    assert isinstance(denied, str)
    assert "allow_candidate" in denied


def test_get_metric_details_returns_provisional_banner_for_candidate(semantic_runtime_ready):
    """Candidate metrics return full details (dims/root — needed to decide
    whether allow_candidate is worth pulling) behind a provisional banner,
    instead of the old refusal."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-candidate-details-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["get_metric_details"].fn

    result = fn(metric_name="candidate_wallet_metric")
    assert not isinstance(result, str), result
    assert result.name == "candidate_wallet_metric"
    assert result.root_model == "api_execution_transactions_by_sector_daily"
    assert result.allowed_dimensions == ["sector"]
    assert result.semantic_status == "candidate"
    assert "PROVISIONAL" in result.summary_markdown
    assert "allow_candidate" in result.summary_markdown

    # Approved metrics carry no banner.
    approved = fn(metric_name="transaction_count")
    assert not isinstance(approved, str)
    assert "PROVISIONAL" not in approved.summary_markdown


def test_preflight_provisional_topics_surface_candidate_coverage(semantic_runtime_ready):
    """When approved coverage leaves topics uncovered but a candidate metric
    matches them, preflight surfaces provisional_topics + an allow_candidate
    hint WITHOUT changing the route."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-provisional-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="wallet candidate activity", mode="answer")

    # Routing is decided on approved coverage only — still a gap.
    assert result.route == "semantic_coverage_gap"
    assert result.fallback_reason == "semantic_coverage_gap"
    assert result.recommended_metrics == []
    # But the candidate coverage is surfaced as informational metadata.
    assert "wallet" in result.provisional_topics
    md = result.summary_markdown
    assert "candidate_wallet_metric" in md
    assert "allow_candidate" in md
    assert "not analyst-vetted" in md


def test_preflight_provisional_topics_empty_when_no_candidate_matches(semantic_runtime_ready):
    """Uncovered topics with no candidate coverage leave provisional_topics
    empty and the summary free of the provisional line."""
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-preflight-no-provisional-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    result = fn(query="transaction count and bridge volume weekly report", mode="report")

    assert result.route == "hybrid_ready"  # routing unchanged
    assert result.provisional_topics == []
    assert "Provisional coverage" not in result.summary_markdown


# ─── PR 3: allow_candidate + scalar-KPI dedicated error ──────────────


class _PR3Snapshot(SimpleNamespace):
    """Minimal snapshot for _resolve_executable_metrics unit tests."""


def _pr3_snapshot(metrics: dict, models: dict | None = None) -> _PR3Snapshot:
    """Build a tiny snapshot exposing the three things
    _resolve_executable_metrics looks up: metrics, models,
    synonym_index."""
    default_models = {
        "approved_model": {"name": "approved_model", "semantic_status": "approved"},
        "candidate_model": {"name": "candidate_model", "semantic_status": "candidate"},
    }
    return _PR3Snapshot(
        metrics=metrics,
        models=models or default_models,
        synonym_index={name: name for name in metrics},
    )


def _pr3_metric(
    name: str,
    *,
    quality_tier: str,
    root_model: str = "approved_model",
    allowed_dimensions: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "quality_tier": quality_tier,
        "semantic_status": "approved" if quality_tier == "approved" else "candidate",
        "root_model": root_model,
        "allowed_dimensions": allowed_dimensions if allowed_dimensions is not None else ["day"],
        "measure": f"{name}_value",
    }


def test_resolve_executable_metrics_approved_passes():
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot({"good": _pr3_metric("good", quality_tier="approved")})
    names, defs, err = _resolve_executable_metrics(snapshot, ["good"])

    assert err == ""
    assert names == ["good"]
    assert defs[0]["name"] == "good"


def test_resolve_executable_metrics_candidate_default_rejects_with_opt_in_hint():
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot({"cand": _pr3_metric("cand", quality_tier="candidate")})
    _, _, err = _resolve_executable_metrics(snapshot, ["cand"])

    assert "Semantic coverage gap" in err
    # The new hint that didn't exist before this PR.
    assert "allow_candidate=true" in err
    assert "execute_query" in err


def test_resolve_executable_metrics_candidate_with_allow_candidate_passes():
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot({"cand": _pr3_metric("cand", quality_tier="candidate")})
    names, _, err = _resolve_executable_metrics(
        snapshot, ["cand"], allow_candidate=True
    )

    assert err == ""
    assert names == ["cand"]


def test_resolve_executable_metrics_allow_candidate_does_not_bypass_unapproved_root():
    """The opt-in is for QUALITY review escape, not authorization escape.
    A candidate metric whose root model is itself not approved must still
    be rejected even with allow_candidate=True."""
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot(
        {"cand": _pr3_metric("cand", quality_tier="candidate", root_model="candidate_model")}
    )
    _, _, err = _resolve_executable_metrics(
        snapshot, ["cand"], allow_candidate=True
    )

    # Falls through to the generic "not approved" error because the
    # candidate path requires an approved root model.
    assert "Semantic coverage gap" in err


def test_resolve_executable_metrics_scalar_kpi_gets_dedicated_error():
    """A metric with no dimensions can't be semantically planned —
    show a specific message pointing at execute_query rather than
    the generic 'not approved' fallback."""
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot(
        {"kpi": _pr3_metric("kpi", quality_tier="candidate", allowed_dimensions=[])}
    )
    _, _, err = _resolve_executable_metrics(snapshot, ["kpi"])

    assert "scalar / single-row KPI" in err
    assert "execute_query" in err
    assert "approved_model" in err  # surfaces the root for the caller
    # The generic 'allow_candidate' hint should NOT appear here — the
    # problem isn't quality tier, it's structural.
    assert "allow_candidate" not in err


def test_resolve_executable_metrics_scalar_kpi_takes_precedence_over_candidate():
    """A metric that's BOTH candidate AND scalar should get the scalar
    error (more actionable). allow_candidate=True doesn't help — the
    metric is unrunnable regardless of quality tier."""
    from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot(
        {"kpi": _pr3_metric("kpi", quality_tier="candidate", allowed_dimensions=[])}
    )
    _, _, err = _resolve_executable_metrics(
        snapshot, ["kpi"], allow_candidate=True
    )

    assert "scalar / single-row KPI" in err


# ─── PR 4: reload_semantic_registry admin tool ──────────────────────


def test_reload_semantic_registry_returns_hash_and_counts(
    semantic_runtime_ready, monkeypatch
):
    """The admin tool surfaces enough metadata for the caller to
    verify a forced refresh picked up new content."""
    semantic_tools, snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-reload-test")

    # Pretend a fresh registry came in: stub force_reload() to return
    # (changed=True, error=None) so the tool follows the success path.
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "force_reload",
        lambda: (True, None),
    )

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["reload_semantic_registry"].fn
    result = fn()

    # The shape the tool returns for downstream verification.
    assert result["changed"] is True
    assert "before_hash" in result
    assert "after_hash" in result
    assert result["metric_count"] == len(snapshot.metrics)
    assert result["model_count"] == len(snapshot.models)
    assert result["approved_metric_count"] == sum(
        1 for m in snapshot.metrics.values() if m.get("quality_tier") == "approved"
    )
    assert result.get("error", "") == ""


def test_reload_semantic_registry_reports_error_from_force_reload(
    semantic_runtime_ready, monkeypatch
):
    """When the registry source is unavailable, error is surfaced verbatim."""
    semantic_tools, _ = semantic_runtime_ready
    mcp = FastMCP("semantic-reload-err-test")

    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "force_reload",
        lambda: (False, "semantic registry unavailable"),
    )

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["reload_semantic_registry"].fn
    result = fn()

    assert result["changed"] is False
    assert "unavailable" in result["error"]


def test_semantic_model_resource_prefers_generated_docs_page(
    semantic_runtime_ready,
    monkeypatch,
    tmp_path,
):
    semantic_tools, snapshot = semantic_runtime_ready

    docs_root = tmp_path / "target"
    page_path = docs_root / "semantic_docs" / "models" / "api_execution_transactions_by_sector_daily.html"
    page_path.parent.mkdir(parents=True)
    page_path.write_text("<html><body>semantic model page</body></html>", encoding="utf-8")
    docs_index_path = docs_root / "semantic_docs_index.json"
    docs_index_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_DOCS_INDEX_URL", "")
    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_DOCS_INDEX_PATH", str(docs_index_path))
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_snapshot", snapshot)

    mcp = FastMCP("semantic-resource-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._resource_manager._templates["gnosis://semantic-model/{name}"].fn
    result = fn("api_execution_transactions_by_sector_daily")

    assert "semantic model page" in result


def test_clickhouse_query_rules_registers_only_with_valid_bundle(
    semantic_runtime_ready,
    monkeypatch,
    tmp_path,
):
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    bundle_dir = tmp_path / "clickhouse_agent_skills"
    skill_dir = bundle_dir / "skills" / "clickhouse-best-practices"
    skill_dir.mkdir(parents=True)
    (bundle_dir / "LICENSE").write_text("license", encoding="utf-8")
    (bundle_dir / "NOTICE").write_text("notice", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("skill", encoding="utf-8")
    (skill_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (skill_dir / "AGENTS.md").write_text("compiled rules", encoding="utf-8")
    (bundle_dir / "bundle_manifest.json").write_text(
        json.dumps(
            {
                "source_repo_url": "https://github.com/ClickHouse/agent-skills",
                "source_ref": "fab8c0077a42aad93069dbc2b65170191edbf52a",
                "compiled_rules_path": "skills/clickhouse-best-practices/AGENTS.md",
                "required_files": [
                    "skills/clickhouse-best-practices/AGENTS.md",
                    "skills/clickhouse-best-practices/SKILL.md",
                    "skills/clickhouse-best-practices/metadata.json",
                    "LICENSE",
                    "NOTICE",
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        semantic_tools.settings,
        "CLICKHOUSE_AGENT_SKILLS_PATH",
        str(bundle_dir),
    )

    mcp = FastMCP("semantic-bundle-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())

    assert "get_clickhouse_query_rules" in mcp._tool_manager._tools
    fn = mcp._tool_manager._tools["get_clickhouse_query_rules"].fn
    result = fn()
    assert "Pinned ref: fab8c0077a42aad93069dbc2b65170191edbf52a" in result
    assert "compiled rules" in result


def test_resolve_dimension_name_handles_mixed_date_day_naming():
    """The registry mixes daily time-dimension names (`date` on scaffolded
    marts, `day` on consensus/transactions/spine). The resolver must map a
    request to whatever the metric actually exposes, regardless of which
    convention the user types — the `date -> day` alias alone broke
    `date`-named metrics (the day-grain query_metrics bug)."""
    from cerebro_mcp.tools.semantic.semantic import _resolve_dimension_name

    date_named = {"date", "week", "month"}
    day_named = {"day", "week", "month"}

    # date-named metric: both 'date' and 'day' inputs must resolve to 'date'
    assert _resolve_dimension_name("date", date_named) == "date"
    assert _resolve_dimension_name("day", date_named) == "date"
    # day-named metric: forward alias and literal both resolve to 'day'
    assert _resolve_dimension_name("date", day_named) == "day"
    assert _resolve_dimension_name("day", day_named) == "day"
    # coarser grains and non-time dimensions pass through untouched
    assert _resolve_dimension_name("week", date_named) == "week"
    assert _resolve_dimension_name("country_code", {"date", "country_code"}) == "country_code"
    # genuinely unsupported names fall through (caller flags them)
    assert _resolve_dimension_name("nope", date_named) == "nope"


def test_semantic_runtime_stats_record_calls_latencies_and_cache(semantic_runtime_ready):
    """The rolling stats registry counts calls, samples latencies, and
    tracks the token-idf cache hit/miss pattern for discover_metrics."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    semantic_tools._TOKEN_IDF_CACHE.clear()
    mcp = FastMCP("semantic-runtime-stats-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    first = fn(query="tx count")
    second = fn(query="tx count")

    # Sanity: both calls succeeded (error paths return str).
    assert first.results and second.results

    stats = semantic_tools.get_semantic_runtime_stats()
    # Every tracked tool appears in the summary even when unused.
    for tool_name in (
        "discover_metrics",
        "query_metrics",
        "preflight_analytics_request",
        "explain_metric_query",
    ):
        assert tool_name in stats

    discover = stats["discover_metrics"]
    assert discover["count"] == 2
    assert discover["errors"] == 0
    assert discover["latency_samples"] == 2
    assert discover["p50_ms"] is not None and discover["p50_ms"] >= 0.0
    assert discover["p95_ms"] is not None and discover["p95_ms"] >= discover["p50_ms"]
    # First call computes the idf table (miss); second reuses it (hit).
    assert discover["cache_misses"] == 1
    assert discover["cache_hits"] == 1
    assert discover["cache_hit_rate"] == 0.5

    # Untouched tools carry zeroed counters and null percentiles.
    assert stats["query_metrics"]["count"] == 0
    assert stats["query_metrics"]["p50_ms"] is None
    assert stats["query_metrics"]["cache_hit_rate"] is None


def test_semantic_runtime_stats_count_error_returns(semantic_runtime_ready, monkeypatch):
    """Error-path returns (plain strings) increment the errors counter and
    still record a latency sample."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    mcp = FastMCP("semantic-runtime-stats-error-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())

    monkeypatch.setattr(semantic_tools.semantic_runtime, "_snapshot", None)
    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_stale_reason",
        "registry_unavailable",
    )

    fn = mcp._tool_manager._tools["discover_metrics"].fn
    result = fn(query="tx count")

    assert isinstance(result, str)
    discover = semantic_tools.get_semantic_runtime_stats()["discover_metrics"]
    assert discover["count"] == 1
    assert discover["errors"] == 1
    assert discover["latency_samples"] == 1


def test_semantic_runtime_stats_track_preflight_cache_hits(semantic_runtime_ready):
    """The preflight result cache feeds the per-tool cache hit/miss counters."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    mcp = FastMCP("semantic-runtime-preflight-cache-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["preflight_analytics_request"].fn
    fn(query="How many active validators are there over time?", mode="answer")
    fn(query="How many active validators are there over time?", mode="answer")

    preflight = semantic_tools.get_semantic_runtime_stats()["preflight_analytics_request"]
    assert preflight["count"] == 2
    assert preflight["cache_misses"] == 1
    assert preflight["cache_hits"] == 1
    assert preflight["cache_hit_rate"] == 0.5


def test_discover_uses_prebaked_token_idf_without_recompute(
    semantic_runtime_ready, monkeypatch
):
    """A snapshot warmed at build time (token_idf pre-baked) must be used
    as-is: discover never calls the compute fn and counts the warm table
    as a cache hit on the very first call."""
    import dataclasses

    from cerebro_mcp.semantic.index import build_token_idf

    semantic_tools, snapshot = semantic_runtime_ready
    warmed = dataclasses.replace(
        snapshot,
        registry_hash="registry-hash-warmed",
        token_idf=build_token_idf(snapshot.metrics.values()),
    )
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_snapshot", warmed)

    calls = {"n": 0}
    real_build_token_idf = semantic_tools.build_token_idf

    def counting_build_token_idf(metrics):
        calls["n"] += 1
        return real_build_token_idf(metrics)

    monkeypatch.setattr(semantic_tools, "build_token_idf", counting_build_token_idf)
    semantic_tools.reset_semantic_runtime_stats()
    semantic_tools._TOKEN_IDF_CACHE.clear()

    mcp = FastMCP("semantic-warm-idf-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    first = fn(query="tx count")
    second = fn(query="tx count")

    assert first.results and second.results
    assert first.results[0].name == "transaction_count"
    assert calls["n"] == 0  # pre-baked table used; lazy compute never ran
    assert semantic_tools._TOKEN_IDF_CACHE == {}  # fallback cache untouched

    stats = semantic_tools.get_semantic_runtime_stats()["discover_metrics"]
    # Call 1: warm idf table counts as a hit. Call 2: result-LRU hit.
    assert stats["cache_hits"] == 2
    assert stats["cache_misses"] == 0
    assert stats["cache_hit_rate"] == 1.0


def test_discover_result_cache_hit_recorded_and_skips_rescoring(
    semantic_runtime_ready, monkeypatch
):
    """Second identical discover call is served from the result LRU: no
    re-scoring, hit recorded in the rolling stats, key is normalization-
    insensitive, and the returned copy echoes the caller's raw query."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    semantic_tools._TOKEN_IDF_CACHE.clear()

    calls = {"n": 0}
    real_score_metric = semantic_tools.score_metric

    def counting_score_metric(query, metric, token_idf=None):
        calls["n"] += 1
        return real_score_metric(query, metric, token_idf=token_idf)

    monkeypatch.setattr(semantic_tools, "score_metric", counting_score_metric)

    mcp = FastMCP("semantic-discover-lru-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn

    first = fn(query="tx count")
    scored_once = calls["n"]
    assert scored_once > 0
    second = fn(query="TX  Count")  # normalizes to the same cache key

    assert calls["n"] == scored_once  # LRU hit -> scoring skipped entirely
    assert second.results == first.results
    assert second.query == "TX  Count"  # copy echoes the raw query

    stats = semantic_tools.get_semantic_runtime_stats()["discover_metrics"]
    assert stats["cache_hits"] == 1  # the LRU hit
    assert stats["cache_misses"] == 1  # call 1's lazy token-idf miss


def test_discover_result_cache_invalidates_on_registry_hash_change(
    semantic_runtime_ready, monkeypatch
):
    """A new registry generation (fresh registry_hash) must drop the result
    LRU: the same query re-runs scoring instead of serving stale rankings."""
    import dataclasses

    semantic_tools, snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    semantic_tools._TOKEN_IDF_CACHE.clear()

    calls = {"n": 0}
    real_score_metric = semantic_tools.score_metric

    def counting_score_metric(query, metric, token_idf=None):
        calls["n"] += 1
        return real_score_metric(query, metric, token_idf=token_idf)

    monkeypatch.setattr(semantic_tools, "score_metric", counting_score_metric)

    mcp = FastMCP("semantic-discover-lru-invalidate-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn

    fn(query="tx count")
    scored_once = calls["n"]
    fn(query="tx count")
    assert calls["n"] == scored_once  # same generation -> cached

    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "_snapshot",
        dataclasses.replace(snapshot, registry_hash="registry-hash-v2"),
    )
    result = fn(query="tx count")

    assert calls["n"] == 2 * scored_once  # rescored under the new generation
    assert result.results[0].name == "transaction_count"

    stats = semantic_tools.get_semantic_runtime_stats()["discover_metrics"]
    # Call 2 is the only hit; calls 1 and 3 each record a token-idf miss.
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 2


def test_discover_result_cache_copies_are_mutation_safe(semantic_runtime_ready):
    """Cached discover results are stored/served as deep copies — a caller
    mutating a returned payload cannot poison later hits."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()

    mcp = FastMCP("semantic-discover-lru-copy-test")
    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn

    first = fn(query="tx count")
    first.results[0].name = "poisoned"
    second = fn(query="tx count")

    assert second.results[0].name == "transaction_count"
    assert second is not first


def test_performance_stats_include_semantic_runtime_section(semantic_runtime_ready):
    """get_performance_stats surfaces the semantic_runtime section (rendered
    by the shared `_semantic_runtime_stats_lines` helper)."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    mcp = FastMCP("semantic-runtime-perf-stats-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    fn(query="tx count")
    fn(query="active validators")

    from cerebro_mcp.tools.governance.reasoning import _semantic_runtime_stats_lines

    text = "\n".join(_semantic_runtime_stats_lines())
    assert "semantic_runtime" in text
    assert "| `discover_metrics` | 2 | 0 |" in text
    assert "| `query_metrics` | 0 | 0 |" in text

    stats = semantic_tools.get_semantic_runtime_stats()
    assert stats["discover_metrics"]["count"] >= 2


def test_reload_semantic_registry_includes_runtime_stats(
    semantic_runtime_ready, monkeypatch
):
    """reload_semantic_registry exposes the rolling runtime summary so
    authoring loops can read before/after numbers in one call."""
    semantic_tools, _snapshot = semantic_runtime_ready
    semantic_tools.reset_semantic_runtime_stats()
    mcp = FastMCP("semantic-reload-runtime-stats-test")

    monkeypatch.setattr(
        semantic_tools.semantic_runtime,
        "force_reload",
        lambda: (True, None),
    )

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    discover = mcp._tool_manager._tools["discover_metrics"].fn
    discover(query="tx count")
    result = mcp._tool_manager._tools["reload_semantic_registry"].fn()

    assert set(result["runtime_stats"]) == {
        "discover_metrics",
        "query_metrics",
        "preflight_analytics_request",
        "explain_metric_query",
    }
    assert result["runtime_stats"]["discover_metrics"]["count"] == 1
    assert result["runtime_stats"]["discover_metrics"]["p50_ms"] is not None


# ─── Ratio / derived metric executable gating (tool layer) ────────────


def _derived_gate_snapshot(input_tier: str = "approved") -> SimpleNamespace:
    """Minimal snapshot for _resolve_executable_metrics: a ratio metric
    whose denominator input's quality tier is parameterised."""
    return SimpleNamespace(
        models={"root_a": {"name": "root_a", "semantic_status": "approved"}},
        synonym_index={},
        metrics={
            "num_metric": {
                "name": "num_metric",
                "type": "simple",
                "root_model": "root_a",
                "measure": "num_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "den_metric": {
                "name": "den_metric",
                "type": "simple",
                "root_model": "root_a",
                "measure": "den_value",
                "quality_tier": input_tier,
                "semantic_status": input_tier,
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "rate_metric": {
                "name": "rate_metric",
                "type": "ratio",
                "type_params": {"numerator": "num_metric", "denominator": "den_metric"},
                "root_model": "root_a",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
        },
    )


class TestDerivedMetricExecutableGate:
    def test_ratio_executable_when_all_inputs_executable(self):
        from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

        snapshot = _derived_gate_snapshot()
        names, metrics, error = _resolve_executable_metrics(snapshot, ["rate_metric"])

        assert error == ""
        assert names == ["rate_metric"]
        assert metrics[0]["type"] == "ratio"

    def test_ratio_blocked_when_input_is_candidate(self):
        from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

        snapshot = _derived_gate_snapshot(input_tier="candidate")
        names, _metrics, error = _resolve_executable_metrics(snapshot, ["rate_metric"])

        assert names == []
        assert "input metric 'den_metric' is not approved" in error
        assert "allow_candidate" in error

    def test_ratio_candidate_input_passes_with_allow_candidate(self):
        from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

        snapshot = _derived_gate_snapshot(input_tier="candidate")
        names, _metrics, error = _resolve_executable_metrics(
            snapshot, ["rate_metric"], allow_candidate=True
        )

        assert error == ""
        assert names == ["rate_metric"]

    def test_ratio_blocked_on_unknown_input(self):
        from cerebro_mcp.tools.semantic.semantic import _resolve_executable_metrics

        snapshot = _derived_gate_snapshot()
        snapshot.metrics["rate_metric"]["type_params"]["denominator"] = "ghost"
        names, _metrics, error = _resolve_executable_metrics(snapshot, ["rate_metric"])

        assert names == []
        assert "unknown input metric 'ghost'" in error
