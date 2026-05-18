import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.semantic_models import SemanticSnapshot
from cerebro_mcp.tools.semantic import register_semantic_tools


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
    from cerebro_mcp.tools import semantic as semantic_tools

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


def test_discover_metrics_hides_candidate_only_metrics(semantic_runtime_ready):
    _semantic_tools, _snapshot = semantic_runtime_ready
    mcp = FastMCP("semantic-discovery-filter-test")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    fn = mcp._tool_manager._tools["discover_metrics"].fn
    result = fn(query="wallet candidate")

    assert result.results == []


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
    from cerebro_mcp.tools import semantic as semantic_tools

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
    from cerebro_mcp.tools import semantic as semantic_tools

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
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot({"good": _pr3_metric("good", quality_tier="approved")})
    names, defs, err = _resolve_executable_metrics(snapshot, ["good"])

    assert err == ""
    assert names == ["good"]
    assert defs[0]["name"] == "good"


def test_resolve_executable_metrics_candidate_default_rejects_with_opt_in_hint():
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

    snapshot = _pr3_snapshot({"cand": _pr3_metric("cand", quality_tier="candidate")})
    _, _, err = _resolve_executable_metrics(snapshot, ["cand"])

    assert "Semantic coverage gap" in err
    # The new hint that didn't exist before this PR.
    assert "allow_candidate=true" in err
    assert "execute_query" in err


def test_resolve_executable_metrics_candidate_with_allow_candidate_passes():
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

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
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

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
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

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
    from cerebro_mcp.tools.semantic import _resolve_executable_metrics

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
    from cerebro_mcp.tools import semantic as semantic_tools

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
