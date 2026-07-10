"""Tests for the Metric Lab all-models catalog + ClickHouse guardrails.

The catalog is a pure model explorer: every dbt model/source in the semantic
snapshot under its EXACT name — no registry metrics, no tier/origin concepts.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ClickHouseManager, ExecutedQuery
from cerebro_mcp.models.semantic import SemanticSnapshot
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools.visualization import metric_lab as metric_lab_module
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.metric_lab import (
    METRIC_CATALOG_MAX_LIMIT,
    _aggregate_load_sql,
    _entry_is_timeseries,
    _model_layer,
    _model_load_sql,
    build_metric_catalog,
    get_catalog_entry_detail,
    get_metric_catalog,
    register_metric_lab_tools,
)


# ---------------------------------------------------------------------------
# Fake snapshot: models across all layers, incl. a dotted raw source, plus
# one legacy registry metric (for the suggestion path only).
# ---------------------------------------------------------------------------


def _make_snapshot() -> SemanticSnapshot:
    models = {
        "api_execution_transactions_daily": {
            "name": "api_execution_transactions_daily",
            "module": "execution",
            "relation_name": "`dbt`.`api_execution_transactions_daily`",
            "materialized": "view",
            "semantic_status": "approved",
            "description": "Daily transactions. " + "x" * 300,
            "tags": ["production", "tier1", "granularity:daily"],
            "columns": {
                "date": {"data_type": "Date"},
                "tx_count": {"data_type": "UInt64"},
            },
        },
        "fct_bridges_kpis_snapshot": {
            "name": "fct_bridges_kpis_snapshot",
            "module": "bridges",
            "relation_name": "`dbt`.`fct_bridges_kpis_snapshot`",
            "materialized": "table",
            "semantic_status": "approved",
            "description": "Bridge KPI snapshot — no date column.",
            "tags": ["production", "mart"],
            "columns": {
                "bridge": {"data_type": "String"},
                "tvl_usd": {"data_type": "Float64"},
            },
        },
        "int_gbc_deposits_daily": {
            "name": "int_gbc_deposits_daily",
            "module": "execution",
            "relation_name": "`dbt`.`int_gbc_deposits_daily`",
            "materialized": "incremental",
            "semantic_status": "approved",
            "description": "Intermediate deposits.",
            "tags": ["execution"],
            "columns": {
                "date": {"data_type": "Date"},
                "amount": {"data_type": "Float64"},
            },
        },
        "stg_consensus__attestations": {
            "name": "stg_consensus__attestations",
            "module": "consensus",
            "relation_name": "`dbt`.`stg_consensus__attestations`",
            "materialized": "view",
            "semantic_status": "approved",
            "description": "Staging attestations.",
            "tags": ["consensus"],
            "columns": {"slot": {"data_type": "UInt64"}},
        },
        "consensus.blocks": {
            "name": "consensus.blocks",
            "module": "consensus",
            "relation_name": "`consensus`.`blocks`",
            "materialized": "source",
            "semantic_status": "approved",
            "description": "Raw consensus blocks source.",
            "tags": [],
            "columns": {
                "slot": {"data_type": "UInt64"},
                "timestamp": {"data_type": "DateTime"},
            },
        },
    }
    metrics = {
        # Legacy registry metric — must NOT appear in the catalog; the detail
        # tool redirects it to its root model.
        "avatar_count_value": {
            "name": "avatar_count_value",
            "label": "Circles Avatars by Trust Bucket",
            "module": "execution",
            "root_model": "api_execution_transactions_daily",
            "measure": "avatar_count_value",
            "quality_tier": "approved",
            "semantic_status": "approved",
            "allowed_dimensions": ["date"],
            "supported_time_grains": ["day"],
            "all_synonyms": ["avatar_count_value"],
            "search_blob": "avatar_count_value",
        },
    }
    return SemanticSnapshot(
        registry_hash="registry-hash",
        manifest_hash="manifest-hash",
        catalog_hash="catalog-hash",
        docs_hash="docs-hash",
        graph={"adjacency": {}},
        vertex_ids={"api_execution_transactions_daily": 0},
        synonym_index={},
        dimension_index={},
        metrics=metrics,
        models=models,
        relationships=[],
        docs_index={},
        loaded_at=0.0,
    )


@pytest.fixture()
def snapshot_ready(monkeypatch):
    from cerebro_mcp.loaders.semantic import semantic_runtime
    from cerebro_mcp.tools.semantic import semantic as semantic_tools

    snapshot = _make_snapshot()
    monkeypatch.setattr(semantic_runtime, "_snapshot", snapshot)
    monkeypatch.setattr(semantic_runtime, "_execution_available", True)
    monkeypatch.setattr(semantic_runtime, "_stale_reason", None)
    monkeypatch.setattr(semantic_tools.manifest, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(semantic_tools.catalog, "reload_if_changed", lambda: (False, None))
    monkeypatch.setattr(semantic_runtime, "refresh_if_changed", lambda: (False, None))
    yield snapshot


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


# ---------------------------------------------------------------------------
# Catalog: models only, exact names, layers, tags, timeseries, paging
# ---------------------------------------------------------------------------


def test_catalog_is_models_only_with_exact_names(snapshot_ready):
    result = build_metric_catalog()
    names = {e["name"] for e in result["entries"]}
    # every model, all layers, incl. the dotted raw source
    assert names == {
        "api_execution_transactions_daily",
        "fct_bridges_kpis_snapshot",
        "int_gbc_deposits_daily",
        "stg_consensus__attestations",
        "consensus.blocks",
    }
    # registry metrics NEVER appear
    assert "avatar_count_value" not in names
    for e in result["entries"]:
        assert e["kind"] == "model"
        assert e["label"] == e["name"]  # exact name, no inventions
        assert e["relation_name"].startswith("`")


def test_layer_classification_and_facet(snapshot_ready):
    assert _model_layer("api_x_y") == "api"
    assert _model_layer("fct_x") == "fct"
    assert _model_layer("int_x") == "int"
    assert _model_layer("stg_x") == "stg"
    assert _model_layer("consensus.blocks") == "source"
    assert _model_layer("intergalactic") == "source"  # prefix needs the underscore

    result = build_metric_catalog()
    assert result["facets"]["layer"] == {
        "api": 1, "fct": 1, "int": 1, "stg": 1, "source": 1,
    }

    api_only = build_metric_catalog(layer="api")
    assert {e["name"] for e in api_only["entries"]} == {"api_execution_transactions_daily"}
    # facets stay pre-filter
    assert api_only["facets"]["layer"]["source"] == 1


def test_tag_and_sector_and_timeseries_filters(snapshot_ready):
    tagged = build_metric_catalog(tag="tier1")
    assert {e["name"] for e in tagged["entries"]} == {"api_execution_transactions_daily"}

    consensus = build_metric_catalog(sector="consensus")
    assert {e["name"] for e in consensus["entries"]} == {
        "stg_consensus__attestations",
        "consensus.blocks",
    }

    ts = build_metric_catalog(timeseries=True)
    ts_names = {e["name"] for e in ts["entries"]}
    # date column → in; timestamp column → in; snapshot/slot-only → out
    assert "api_execution_transactions_daily" in ts_names
    assert "consensus.blocks" in ts_names  # has `timestamp`
    assert "fct_bridges_kpis_snapshot" not in ts_names
    assert "stg_consensus__attestations" not in ts_names


def test_paging_and_wrapper(snapshot_ready):
    clamped = build_metric_catalog(limit=10_000)
    assert len(clamped["entries"]) <= METRIC_CATALOG_MAX_LIMIT
    page1 = build_metric_catalog(limit=2, offset=0)["entries"]
    page2 = build_metric_catalog(limit=2, offset=2)["entries"]
    assert {e["name"] for e in page1}.isdisjoint({e["name"] for e in page2})
    # no-query sort: api first
    assert build_metric_catalog()["entries"][0]["layer"] == "api"
    assert isinstance(get_metric_catalog(), list)


def test_entry_is_timeseries_predicate():
    assert _entry_is_timeseries({"columns": [{"name": "date", "type": "Date"}]}) is True
    assert _entry_is_timeseries({"columns": [{"name": "total", "type": "UInt64"}]}) is False


# ---------------------------------------------------------------------------
# Detail: models any layer; metric names redirect to their root model
# ---------------------------------------------------------------------------


def test_detail_for_all_layers(snapshot_ready):
    d = get_catalog_entry_detail("consensus.blocks")
    assert d["kind"] == "model"
    assert d["layer"] == "source"
    assert d["relation_name"] == "`consensus`.`blocks`"
    assert {c["name"] for c in d["columns"]} == {"slot", "timestamp"}

    api = get_catalog_entry_detail("api_execution_transactions_daily")
    assert len(api["description"]) > 280  # untruncated
    assert api["materialized"] == "view"


def test_detail_metric_name_redirects_to_root_model(snapshot_ready):
    d = get_catalog_entry_detail("avatar_count_value")
    assert "error" in d
    assert "registry metric" in d["error"]
    assert d["suggestions"] == ["api_execution_transactions_daily"]


def test_detail_unknown_suggests_models(snapshot_ready):
    d = get_catalog_entry_detail("api_execution_transactions")
    assert "error" in d
    assert "api_execution_transactions_daily" in d["suggestions"]


# ---------------------------------------------------------------------------
# Load SQL: relation_name FROM + window pushdown
# ---------------------------------------------------------------------------


def test_model_load_sql_uses_relation_and_window(snapshot_ready):
    snap = snapshot_ready
    source = snap.models["consensus.blocks"]
    sql = _model_load_sql(source, "consensus.blocks", 500, window_days=0)
    assert "FROM `consensus`.`blocks`" in sql  # NOT dbt.consensus.blocks
    assert "ORDER BY `timestamp` DESC" in sql
    assert "WHERE" not in sql

    windowed = _model_load_sql(source, "consensus.blocks", 500, window_days=90)
    assert "WHERE `timestamp` >= today() - 90" in windowed

    # no date column → no window, no order
    snapshot_sql = _model_load_sql(
        snap.models["fct_bridges_kpis_snapshot"], "fct_bridges_kpis_snapshot", 500, 90
    )
    assert "WHERE" not in snapshot_sql and "ORDER BY" not in snapshot_sql


# ---------------------------------------------------------------------------
# Aggregate mode: server-side GROUP BY SQL
# ---------------------------------------------------------------------------


_PANEL_MODEL = {
    "relation_name": "`dbt`.`api_execution_circles_v2_avatar_balances_daily`",
    "columns": {
        "avatar": {"data_type": "String"},
        "balance": {"data_type": "Float64"},
        "balance_demurraged": {"data_type": "Float64"},
        "date": {"data_type": "Date"},
        "token_address": {"data_type": "String"},
    },
}


def test_aggregate_sql_happy_path():
    sql, params = _aggregate_load_sql(
        _PANEL_MODEL, "avatar_balances", x="date", y="balance", agg="sum"
    )
    assert "SELECT `date`, sum(`balance`) AS `sum_balance`" in sql
    assert "FROM `dbt`.`api_execution_circles_v2_avatar_balances_daily`" in sql
    assert "GROUP BY `date`" in sql
    assert "ORDER BY `date` ASC" in sql
    assert params is None


def test_aggregate_sql_series_top_n_and_window():
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum",
        series="token_address", series_top_n=5, window_days=365,
    )
    assert "`date` >= today() - 365" in sql
    assert "`token_address` IN (SELECT `token_address` FROM" in sql
    assert "ORDER BY sum(`balance`) DESC LIMIT 5" in sql
    assert "GROUP BY `date`, `token_address`" in sql


def test_aggregate_sql_uniq_median_count():
    sql, _ = _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="avatar", agg="uniq")
    assert "uniqExact(`avatar`) AS `uniq_avatar`" in sql
    sql, _ = _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="balance", agg="median")
    assert "quantile(0.5)(`balance`)" in sql
    sql, _ = _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="", agg="count")
    assert "count() AS `row_count`" in sql


def test_aggregate_sql_filter_parameterized():
    sql, params = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum",
        filter_col="token_address", filter_op="=", filter_value="0xCRC'; DROP",
    )
    # value is BOUND, never interpolated
    assert "{flt:String}" in sql
    assert "DROP" not in sql
    assert params == {"flt": "0xCRC'; DROP"}


def test_aggregate_sql_rejections():
    with pytest.raises(ValueError, match="not a column"):
        _aggregate_load_sql(_PANEL_MODEL, "m", x="nope", y="balance", agg="sum")
    with pytest.raises(ValueError, match="not supported"):
        _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="balance", agg="explode")
    with pytest.raises(ValueError, match="differ from x"):
        _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="balance", agg="sum", series="date")
    with pytest.raises(ValueError, match="filter_op"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="balance", agg="sum",
            filter_col="avatar", filter_op="LIKE", filter_value="x",
        )


# ---------------------------------------------------------------------------
# App tools: registration + visibility + search params
# ---------------------------------------------------------------------------


class StubCH:
    def __init__(self, total=60):
        self.total = total

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None):
        if "count()" in sql:
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=["c"],
                rows=[[self.total]], row_count=1, elapsed_seconds=0.0,
                fetch_mode="rows", warnings=[],
            )
        n = min(requested_max_rows, self.total)
        rows = [[f"2026-04-{(i % 28) + 1:02d}", i] for i in range(n)]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database,
            columns=["date", "tx_count"], rows=rows, row_count=n,
            elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )


def _build_server(total=60):
    server = FastMCP("test-catalog")
    ch = StubCH(total=total)
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)
    return server


def _get_tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def test_app_tools_registered_and_hidden(snapshot_ready):
    server = _build_server()
    assert "search_metric_catalog" in web_apps.MINI_APP_TOOL_REGISTRY
    assert "get_metric_catalog_entry" in web_apps.MINI_APP_TOOL_REGISTRY
    app_only = mini_apps.get_app_only_tool_names()
    assert "search_metric_catalog" in app_only
    names = [t.name for t in asyncio.run(server.list_tools())]
    assert "search_metric_catalog" not in names
    assert "open_metric_lab" in names


def test_search_tool_layer_param(snapshot_ready):
    server = _build_server()
    fn = _get_tool(server, "search_metric_catalog")
    result = fn(layer="source", limit=10)
    sc = result.structuredContent
    assert sc["total_matching"] == 1
    assert sc["entries"][0]["name"] == "consensus.blocks"
    assert sc["layer"] == "source"


def test_model_load_via_membership_and_window(snapshot_ready):
    """A non-api model (dotted source) loads via relation_name; window flows."""
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(view_id=view_id, metric="consensus.blocks", window_days=90)
    sc = result.structuredContent
    assert sc["view_state"]["mode"] == "loaded"
    assert "primary" in sc["datasets"]
    assert "`consensus`.`blocks`" in sc["datasets"]["primary"]["sql"]
    assert "today() - 90" in sc["datasets"]["primary"]["sql"]


def test_aggregate_mode_load_end_to_end(snapshot_ready):
    """mode=aggregate loads the grouped dataset with the right defaults."""
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    opened = open_fn()
    view_id = opened.structuredContent["view_id"]

    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        mode="aggregate",
        x="date",
        y="tx_count",
        agg="sum",
    )
    sc = result.structuredContent
    assert sc["view_state"]["load_mode"] == "aggregate"
    assert sc["view_state"]["aggregate_config"]["x"] == "date"
    assert "GROUP BY `date`" in sc["datasets"]["primary"]["sql"]
    # default chart: X=date, Y=agg column, line
    chart = sc["view_state"]["chart"]
    assert chart["xField"] == "date"
    assert chart["chartType"] == "line"

    # aggregate errors surface with column list
    bad = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        mode="aggregate",
        x="typo",
        y="tx_count",
        agg="sum",
    )
    assert bad.isError is True
    assert "tx_count" in bad.content[0].text

    # raw default unchanged (agent regression)
    raw = load_fn(view_id=view_id, metric="api_execution_transactions_daily")
    assert raw.structuredContent["view_state"]["load_mode"] == "raw"
    assert "GROUP BY" not in raw.structuredContent["datasets"]["primary"]["sql"]


# ---------------------------------------------------------------------------
# Guardrails: memory-cap settings + 241 error rewrite
# ---------------------------------------------------------------------------


def test_session_settings_include_memory_cap(monkeypatch):
    import cerebro_mcp.config as cfg

    object.__setattr__(cfg.settings, "CLICKHOUSE_MAX_QUERY_MEMORY_GB", 4.0)
    s = ClickHouseManager._session_settings()
    assert s["max_memory_usage"] == int(4.0 * 2**30)
    assert s["readonly"] == 1

    object.__setattr__(cfg.settings, "CLICKHOUSE_MAX_QUERY_MEMORY_GB", 0)
    s = ClickHouseManager._session_settings()
    assert "max_memory_usage" not in s
    object.__setattr__(cfg.settings, "CLICKHOUSE_MAX_QUERY_MEMORY_GB", 4.0)


def test_memory_error_rewritten_friendly():
    exc = RuntimeError(
        "Received ClickHouse exception, code: 241, server response: Code: 241. "
        "DB::Exception: (total) memory limit exceeded: would use 10.80 GiB "
        "(MEMORY_LIMIT_EXCEEDED)"
    )
    msg = mini_apps._friendly_query_error(exc)
    assert "too heavy to load whole" in msg
    assert "time window" in msg

    plain = mini_apps._friendly_query_error(ValueError("syntax error"))
    assert plain == "syntax error"
