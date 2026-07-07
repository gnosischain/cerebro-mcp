"""Tests for the `find` router (Phase 1) + preflight slim mode (Phase 2).

Mirrors the `test_semantic_tools.py` snapshot/fixture style. Covers:
- `find` ranks tools + metrics + models and returns a mode-appropriate action;
- the tool corpus excludes APP_ONLY tools;
- `find` sets `semantic_find_ran` but NOT `semantic_preflight_ran`;
- `_semantic_discovery_gate` passes after `find` without paying preflight;
- `preflight_analytics_request(detail="slim")` is compact;
- `_route` is the shared routing core.
"""

from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.models.semantic import SemanticSnapshot
from cerebro_mcp.tools.semantic.find import register_find_tool, _reset_tool_corpus
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
            "default_filters": [],
            "question_synonyms": ["tx count"],
            "all_synonyms": ["transaction_count", "transaction count", "tx count"],
            "search_blob": "transaction_count transaction count execution tx count",
        },
    }
    return SemanticSnapshot(
        registry_hash="registry-hash",
        manifest_hash="manifest-hash",
        catalog_hash="catalog-hash",
        docs_hash="docs-hash",
        graph={"adjacency": {}},
        vertex_ids={"api_execution_transactions_by_sector_daily": 0},
        synonym_index={
            "transaction_count": "transaction_count",
            "transaction count": "transaction_count",
            "tx count": "transaction_count",
        },
        dimension_index={
            "day": [
                {
                    "provider_model": "api_execution_transactions_by_sector_daily",
                    "module": "execution",
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
        docs_index={},
        loaded_at=0.0,
    )


@pytest.fixture()
def semantic_ready(monkeypatch):
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    snapshot = _make_snapshot()
    semantic_tools.state.reset()
    _reset_tool_corpus()
    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_ENABLED", True)
    monkeypatch.setattr(semantic_tools.settings, "SEMANTIC_REFRESH_INTERVAL_SECONDS", 10_000)
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_snapshot", snapshot)
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_execution_available", True)
    monkeypatch.setattr(semantic_tools.semantic_runtime, "_stale_reason", None)
    monkeypatch.setattr(semantic_tools.manifest, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(semantic_tools.catalog, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(
        semantic_tools.semantic_runtime, "refresh_if_changed", lambda: (False, None)
    )
    try:
        yield semantic_tools, snapshot
    finally:
        semantic_tools.state.reset()
        _reset_tool_corpus()


def _mcp_with_find(name: str) -> FastMCP:
    """A FastMCP with a couple of ordinary tools + an APP_ONLY tool, then the
    semantic tools and `find` registered LAST (as in server.py)."""
    from cerebro_mcp.tools.visualization.mini_apps import mark_app_only

    mcp = FastMCP(name)

    @mcp.tool()
    def execute_query(sql: str) -> str:
        """Run a raw ClickHouse SQL query for exploratory analysis."""
        return ""

    @mcp.tool()
    def describe_table(name: str) -> str:
        """Describe a table's columns, data types, and schema."""
        return ""

    @mcp.tool()
    def get_mini_app_rows(app_id: str) -> str:
        """Hydration tool for the mini-app frontend (app-only)."""
        return ""

    @mcp.tool()
    def open_metric_lab(query: str = "", title: str = "") -> str:
        """Open the interactive Metric Lab app to explore metric datasets."""
        return ""

    mark_app_only("get_mini_app_rows")

    register_semantic_tools(mcp, SimpleNamespace(), SimpleNamespace())
    register_find_tool(mcp)
    return mcp


def test_find_ranks_tools_metrics_models_with_answer_action(semantic_ready):
    mcp = _mcp_with_find("find-answer-test")
    find = mcp._tool_manager._tools["find"].fn

    result = find(query="transaction count by sector", mode="answer")

    assert result["route"] == "semantic_ready"
    # metrics from the shared routing core
    metric_names = [m["name"] for m in result["top_metrics"]]
    assert "transaction_count" in metric_names
    assert result["top_metrics"][0]["executable"] is True
    # tools ranked (query_metrics/execute_query should surface for this query)
    tool_names = [t["name"] for t in result["top_tools"]]
    assert tool_names, "expected ranked tools"
    assert all("call" in t and "summary" in t for t in result["top_tools"])
    # models via catalog_search(entity_types=["model"])
    model_names = [m["name"] for m in result["top_models"]]
    assert "api_execution_transactions_by_sector_daily" in model_names
    # answer mode → recommend query_metrics DIRECTLY (no preflight)
    assert result["recommended_action"]["tool"] == "query_metrics"
    assert "transaction_count" in result["recommended_action"]["args"]["metrics"]


def test_find_chart_mode_routes_through_preflight(semantic_ready):
    mcp = _mcp_with_find("find-chart-test")
    find = mcp._tool_manager._tools["find"].fn

    result = find(query="transaction count by sector", mode="chart")

    assert result["recommended_action"]["tool"] == "preflight_analytics_request"
    assert result["recommended_action"]["args"]["mode"] == "chart"


def test_find_auto_mode_defaults_to_answer(semantic_ready):
    mcp = _mcp_with_find("find-auto-test")
    find = mcp._tool_manager._tools["find"].fn

    result = find(query="transaction count by sector", mode="auto")

    # No chart/report words → auto infers answer → query_metrics directly.
    assert result["recommended_action"]["tool"] == "query_metrics"


def test_find_corpus_excludes_app_only(semantic_ready):
    mcp = _mcp_with_find("find-apponly-test")
    from cerebro_mcp.tools.semantic.find import _tool_corpus

    _idx, docs = _tool_corpus(mcp)
    assert "get_mini_app_rows" not in docs
    # ordinary tools ARE in the corpus
    assert "execute_query" in docs
    assert "describe_table" in docs


def test_find_corpus_excludes_metric_lab_tools(semantic_ready):
    """Metric Lab tools open a UI, they never answer a question — they must be
    kept out of the corpus so `find` never surfaces them and nudges the model
    to open the app unprompted."""
    mcp = _mcp_with_find("find-metriclab-test")
    from cerebro_mcp.tools.semantic.find import _tool_corpus

    _idx, docs = _tool_corpus(mcp)
    assert "open_metric_lab" not in docs
    # a directly metric-flavored query must not surface the lab in top_tools
    find = mcp._tool_manager._tools["find"].fn
    result = find(query="open metric lab to explore metrics", mode="answer")
    tool_names = [t["name"] for t in result["top_tools"]]
    assert "open_metric_lab" not in tool_names


def test_find_sets_find_ran_not_preflight_ran(semantic_ready):
    semantic_tools, _snap = semantic_ready
    mcp = _mcp_with_find("find-state-test")
    find = mcp._tool_manager._tools["find"].fn

    find(query="transaction count by sector", mode="answer")

    assert semantic_tools.state.semantic_find_ran is True
    assert semantic_tools.state.semantic_preflight_ran is False
    assert semantic_tools.state.semantic_find_route == "semantic_ready"


def test_discovery_gate_passes_after_find_without_preflight(semantic_ready):
    """After an answer-mode `find`, `_semantic_discovery_gate` returns "" and
    never pays the O(N) preflight — enforced by patching get_semantic_preflight
    to blow up if it's called."""
    semantic_tools, _snap = semantic_ready
    from cerebro_mcp.tools.analytics import dbt as dbt_tools

    mcp = _mcp_with_find("find-gate-test")
    find = mcp._tool_manager._tools["find"].fn
    find(query="transaction count by sector", mode="answer")

    import cerebro_mcp.tools.semantic.semantic as sem

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("get_semantic_preflight must not be called after find")

    original = sem.get_semantic_preflight
    sem.get_semantic_preflight = _boom
    try:
        gate = dbt_tools._semantic_discovery_gate("transaction count by sector")
    finally:
        sem.get_semantic_preflight = original

    assert gate == ""


def test_discovery_gate_auto_routes_when_nothing_routed(semantic_ready):
    """First-touch discovery is no longer bounced: the gate runs the routing
    itself, records it as an answer-mode `find`, and returns "" so discovery
    proceeds immediately (no extra round-trip)."""
    semantic_tools, _snap = semantic_ready
    from cerebro_mcp.tools.analytics import dbt as dbt_tools

    st = semantic_tools.state
    assert st.semantic_find_ran is False

    gate = dbt_tools._semantic_discovery_gate("transaction count by sector")

    assert gate == ""
    assert st.semantic_find_ran is True
    assert st.semantic_mode_last == "answer"
    assert st.semantic_route_last != ""


def test_find_satisfies_chart_gate_but_not_report_gate(semantic_ready):
    """`find` (any mode) satisfies the chart gate's routing leg — it records
    the same route/mode data as a preflight. The REPORT gate stays strict:
    it still demands an explicit `preflight_analytics_request(mode="report")`,
    so a chart ask can never silently escalate into a report."""
    semantic_tools, _snap = semantic_ready
    mcp = _mcp_with_find("find-hardgate-test")
    find = mcp._tool_manager._tools["find"].fn
    find(query="transaction count by sector", mode="answer")

    import cerebro_mcp.config as cfg

    # The gates only enforce when these are on.
    object.__setattr__(cfg.settings, "SEMANTIC_ENABLED", True)
    object.__setattr__(cfg.settings, "ENFORCE_CHART_PRECONDITIONS", True)

    st = semantic_tools.state
    _passed, reason = st.check_chart_preconditions(raw_path=True)
    # The routing leg is satisfied by find — any remaining failure must be a
    # route redirect or depth gap, never the preflight requirement.
    assert "Semantic preflight required" not in reason

    r_passed, r_reason, _w = st.check_report_preconditions(
        {"chart_1": {"chart_type": "bar"}}
    )
    assert r_passed is False
    assert (
        "preflight" in r_reason.lower()
        or "not routed as a report" in r_reason
    )


def test_preflight_slim_is_compact(semantic_ready):
    mcp = _mcp_with_find("preflight-slim-test")
    preflight = mcp._tool_manager._tools["preflight_analytics_request"].fn

    full = preflight(query="transaction count and bridge volume weekly report", mode="report")
    slim = preflight(
        query="transaction count and bridge volume weekly report",
        mode="report",
        detail="slim",
    )

    # Same route + metrics from the shared _route core.
    assert slim.route == full.route
    assert slim.recommended_metrics == full.recommended_metrics
    # Slim skips the covered/uncovered-topic + provisional dump.
    assert slim.covered_topics == []
    assert slim.uncovered_topics == []
    assert len(slim.summary_markdown) < len(full.summary_markdown)


def test_route_is_shared_by_find_and_preflight(semantic_ready):
    """`_route` returns the same route/metrics that both front doors surface."""
    import cerebro_mcp.tools.semantic.semantic as sem

    routing = sem._route("transaction count by sector", "answer")
    assert routing["status"] == "ok"
    assert routing["route"] == "semantic_ready"
    assert routing["recommended_metrics"][0] == "transaction_count"
