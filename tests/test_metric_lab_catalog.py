"""Tests for the Metric Lab all-models catalog + ClickHouse guardrails.

The catalog is a pure model explorer: every dbt model/source in the semantic
snapshot under its EXACT name — no registry metrics, no tier/origin concepts.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ClickHouseManager, ExecutedQuery
from cerebro_mcp.runtime.mcp_server import CerebroFastMCP
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
                "gas_used": {"data_type": "UInt64"},
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
    server = CerebroFastMCP("test-catalog")
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


# ---------------------------------------------------------------------------
# Type-aware time-column detection (Phase 0)
# ---------------------------------------------------------------------------


def test_unwrap_ch_type_strips_wrappers():
    assert metric_lab_module._unwrap_ch_type("Nullable(DateTime64(3))") == "DateTime64(3)"
    assert metric_lab_module._unwrap_ch_type("LowCardinality(Nullable(Date))") == "Date"
    assert metric_lab_module._unwrap_ch_type("String") == "String"
    assert metric_lab_module._unwrap_ch_type("") == ""


@pytest.mark.parametrize(
    ("ch_type", "expected"),
    [
        ("Date", True),
        ("Date32", True),
        ("DateTime", True),
        ("DateTime64(3, 'UTC')", True),
        ("Nullable(DateTime64(3))", True),
        ("LowCardinality(Nullable(Date))", True),
        ("String", False),
        ("UInt64", False),
        ("Decimal(38, 18)", False),
    ],
)
def test_is_time_type(ch_type, expected):
    assert metric_lab_module._is_time_type(ch_type) is expected


def test_time_column_prefers_typed_and_hinted():
    model_def = {
        "columns": {
            "block_timestamp": {"data_type": "DateTime64(3)"},
            "date": {"data_type": "Date"},
            "value": {"data_type": "Float64"},
        }
    }
    # "date" is typed AND hinted -> wins over the merely-typed block_timestamp.
    assert metric_lab_module._time_column(model_def) == "date"


def test_time_column_finds_typed_without_name_hint():
    # block_timestamp misses _TIME_COLUMN_HINTS but is DateTime64-typed.
    model_def = {
        "columns": {
            "block_timestamp": {"data_type": "DateTime64(3)"},
            "value": {"data_type": "Float64"},
        }
    }
    assert metric_lab_module._time_column(model_def) == "block_timestamp"


def test_time_column_falls_back_to_untyped_hint():
    # No data_type info at all -> name hints still work.
    model_def = {"columns": {"date": {}, "value": {}}}
    assert metric_lab_module._time_column(model_def) == "date"


def test_time_column_list_shape_and_created_at():
    model_def = {
        "columns": [
            {"name": "created_at", "data_type": "Nullable(DateTime)"},
            {"name": "amount", "data_type": "UInt64"},
        ]
    }
    assert metric_lab_module._time_column(model_def) == "created_at"


def test_time_column_empty_model():
    assert metric_lab_module._time_column({}) == ""
    assert metric_lab_module._time_column({"columns": {}}) == ""


def test_column_type_lookup():
    model_def = {"columns": {"a": {"data_type": "UInt8"}}}
    assert metric_lab_module._column_type(model_def, "a") == "UInt8"
    assert metric_lab_module._column_type(model_def, "missing") == ""


# ---------------------------------------------------------------------------
# A1: time grain, multi-Y, column projection
# ---------------------------------------------------------------------------


def test_aggregate_sql_multi_ys():
    sql, params = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="", agg="sum",
        ys=["balance", "balance_demurraged"],
    )
    assert "sum(`balance`) AS `sum_balance`" in sql
    assert "sum(`balance_demurraged`) AS `sum_balance_demurraged`" in sql
    assert sql.index("sum(`balance`)") < sql.index("sum(`balance_demurraged`)")
    assert "GROUP BY `date`" in sql
    assert params is None


def test_aggregate_sql_ys_rejections():
    with pytest.raises(ValueError, match="either y or ys"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="avatar", agg="sum",
            ys=["balance", "balance_demurraged"],
        )
    with pytest.raises(ValueError, match="Duplicate columns in ys"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="", agg="sum",
            ys=["balance", "balance"],
        )
    with pytest.raises(ValueError, match="count.*takes no measure list"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="", agg="count",
            ys=["balance", "balance_demurraged"],
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="", agg="sum",
            ys=["balance", "balance_demurraged"], series="token_address",
        )
    with pytest.raises(ValueError, match="y \\(or ys\\) is required"):
        _aggregate_load_sql(_PANEL_MODEL, "m", x="date", y="", agg="sum")
    with pytest.raises(ValueError, match="not a column"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="", agg="sum", ys=["nope"]
        )


def test_aggregate_sql_ys_matching_y_is_allowed():
    # y mirroring ys[0] (a lenient client sends both) must not be rejected.
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum",
        ys=["balance", "balance_demurraged"],
    )
    assert "sum(`balance_demurraged`)" in sql


def test_aggregate_sql_grain_buckets_and_aliases():
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum", grain="week"
    )
    assert "toStartOfWeek(`date`, 1) AS `date`" in sql
    # GROUP BY uses the expression, not the alias
    assert "GROUP BY toStartOfWeek(`date`, 1)" in sql
    assert "ORDER BY `date` ASC" in sql
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum", grain="month"
    )
    assert "toStartOfMonth(`date`) AS `date`" in sql
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum", grain="day"
    )
    assert "toDate(`date`) AS `date`" in sql


def test_aggregate_sql_grain_rejections():
    with pytest.raises(ValueError, match="grain must be one of"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="date", y="balance", agg="sum", grain="year"
        )
    with pytest.raises(ValueError, match="date/time x column"):
        _aggregate_load_sql(
            _PANEL_MODEL, "m", x="token_address", y="balance", agg="sum",
            grain="week",
        )


def test_aggregate_sql_grain_accepts_typed_non_hinted_column():
    model = {
        "relation_name": "`dbt`.`fct_events`",
        "columns": {
            "block_timestamp": {"data_type": "DateTime64(3)"},
            "value": {"data_type": "Float64"},
        },
    }
    sql, _ = _aggregate_load_sql(
        model, "m", x="block_timestamp", y="value", agg="sum", grain="day"
    )
    assert "toDate(`block_timestamp`) AS `block_timestamp`" in sql


def test_aggregate_sql_topn_never_orders_by_alias():
    # The top-N subselect must rank on the bare measure expression — an
    # `AS`-suffixed expression inside ORDER BY is invalid SQL.
    sql, _ = _aggregate_load_sql(
        _PANEL_MODEL, "m", x="date", y="balance", agg="sum",
        series="token_address",
    )
    import re as _re

    for order_by in _re.findall(r"ORDER BY ([^)]+?)(?: DESC| ASC)", sql):
        assert " AS " not in order_by, sql


def test_model_load_sql_projection_order_preserved():
    sql = _model_load_sql(
        _PANEL_MODEL, "m", limit=100, columns=["balance", "avatar", "date"]
    )
    assert sql.startswith("SELECT `balance`, `avatar`, `date` FROM")
    assert "ORDER BY `date` DESC" in sql
    assert sql.endswith("LIMIT 100")


def test_model_load_sql_projection_rejections():
    with pytest.raises(ValueError, match="Duplicate columns"):
        _model_load_sql(_PANEL_MODEL, "m", limit=10, columns=["date", "date"])
    with pytest.raises(ValueError, match="Not columns of m"):
        _model_load_sql(_PANEL_MODEL, "m", limit=10, columns=["nope"])


def test_model_load_sql_projection_without_date_col_skips_order():
    sql = _model_load_sql(_PANEL_MODEL, "m", limit=10, columns=["balance"])
    assert "ORDER BY" not in sql


def test_model_load_sql_empty_projection_selects_all():
    sql = _model_load_sql(_PANEL_MODEL, "m", limit=10, columns=[])
    assert sql.startswith("SELECT * FROM")


# ---------------------------------------------------------------------------
# A1 end-to-end: multi-Y defaults, grain, projection, stale-secondary removal
# ---------------------------------------------------------------------------


class ColsStubCH:
    """Stub that answers with a caller-defined column shape."""

    def __init__(self, columns, total=30):
        self.columns = columns
        self.total = total

    def run_query(self, sql, database="dbt", requested_max_rows=100,
                  audience="tool", fetch_mode="auto", parameters=None):
        if sql.startswith("SELECT count()"):
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=["c"],
                rows=[[self.total]], row_count=1, elapsed_seconds=0.0,
                fetch_mode="rows", warnings=[],
            )
        n = min(requested_max_rows, self.total)
        rows = [
            [f"2026-04-{(i % 28) + 1:02d}"] + [i] * (len(self.columns) - 1)
            for i in range(n)
        ]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database,
            columns=list(self.columns), rows=rows, row_count=n,
            elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )


def _build_server_with(ch):
    server = CerebroFastMCP("test-catalog")
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)
    return server


def test_multi_y_load_emits_yfields(snapshot_ready):
    server = _build_server_with(
        ColsStubCH(["date", "sum_tx_count", "sum_gas_used"])
    )
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        mode="aggregate",
        x="date",
        agg="sum",
        ys=["tx_count", "gas_used"],
    )
    sc = result.structuredContent
    assert not result.isError, result.content
    panel = sc["view_state"]["charts"][0]
    assert panel["yFields"] == ["sum_tx_count", "sum_gas_used"]
    assert panel["yField"] == "sum_tx_count"
    assert panel["y2Field"] == "sum_gas_used"
    assert panel["id"] == "c1"
    assert panel["datasetKey"] == "primary"
    # legacy `chart` is the SCALAR projection of charts[0]
    legacy = sc["view_state"]["chart"]
    assert set(legacy) == {"xField", "yField", "chartType", "aggregation", "groupBy"}
    assert legacy["yField"] == "sum_tx_count"
    assert sc["view_state"]["aggregate_config"]["ys"] == ["tx_count", "gas_used"]


def test_grain_load_flows_to_sql_and_config(snapshot_ready):
    server = _build_server_with(ColsStubCH(["date", "sum_tx_count"]))
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        mode="aggregate",
        x="date",
        y="tx_count",
        agg="sum",
        grain="week",
    )
    sc = result.structuredContent
    assert not result.isError, result.content
    assert "toStartOfWeek(`date`, 1)" in sc["datasets"]["primary"]["sql"]
    assert sc["view_state"]["aggregate_config"]["grain"] == "week"


def test_raw_projection_load_flows_to_sql_and_config(snapshot_ready):
    server = _build_server_with(ColsStubCH(["date", "tx_count"]))
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        columns=["date", "tx_count"],
    )
    sc = result.structuredContent
    assert not result.isError, result.content
    assert sc["datasets"]["primary"]["sql"].startswith(
        "SELECT `date`, `tx_count` FROM"
    )
    assert sc["view_state"]["raw_config"]["columns"] == ["date", "tx_count"]

    bad = load_fn(
        view_id=view_id,
        metric="api_execution_transactions_daily",
        columns=["nope"],
    )
    assert bad.isError
    assert "Not columns" in bad.content[0].text


def test_solo_load_after_dual_removes_stale_secondary(snapshot_ready):
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")

    dual = load_fn(
        view_id=view_id,
        metric=["api_execution_transactions_daily", "int_gbc_deposits_daily"],
    )
    assert set(dual.structuredContent["datasets"]) == {"primary", "secondary"}
    record = mini_apps.get_view(view_id)
    assert set(record.datasets) == {"primary", "secondary"}

    solo = load_fn(view_id=view_id, metric="api_execution_transactions_daily")
    assert set(solo.structuredContent["datasets"]) == {"primary"}
    record = mini_apps.get_view(view_id)
    assert set(record.datasets) == {"primary"}
    assert "secondary" not in record.dataset_revisions
    # Exact state replacement: no stale dual leftovers in view_state.
    assert record.view_state["selected_metrics"] == [
        "api_execution_transactions_daily"
    ]


# ---------------------------------------------------------------------------
# A2: N-model date-grain join
# ---------------------------------------------------------------------------


_JOIN_MODELS = {
    "api_a_daily": {
        "relation_name": "`dbt`.`api_a_daily`",
        "columns": {
            "date": {"data_type": "Date"},
            "volume": {"data_type": "Float64"},
            "label": {"data_type": "String"},
        },
    },
    "api_b_daily": {
        "relation_name": "`dbt`.`api_b_daily`",
        "columns": {
            "block_timestamp": {"data_type": "DateTime64(3)"},
            "users": {"data_type": "UInt64"},
        },
    },
    "api_no_date": {
        "relation_name": "`dbt`.`api_no_date`",
        "columns": {"bridge": {"data_type": "String"}, "tvl": {"data_type": "Float64"}},
    },
    "api_no_numeric": {
        "relation_name": "`dbt`.`api_no_numeric`",
        "columns": {"date": {"data_type": "Date"}, "label": {"data_type": "String"}},
    },
}


def test_multi_metric_join_sql_shape():
    specs = [
        {"model": "api_a_daily", "y": "volume", "agg": "sum"},
        {"model": "api_b_daily", "y": "users", "agg": "avg"},
    ]
    sql = metric_lab_module._multi_metric_join_sql(
        _JOIN_MODELS, specs, grain="week", window_days=90
    )
    # value columns aliased to MODEL NAMES; NULL-safe union collapsed by max()
    assert "max(m0) AS `api_a_daily`" in sql
    assert "max(m1) AS `api_b_daily`" in sql
    assert "sum(`volume`) AS m0, NULL AS m1" in sql
    assert "NULL AS m0, avg(`users`) AS m1" in sql
    # each branch buckets ITS OWN date column with the shared grain
    assert "toStartOfWeek(`date`, 1) AS date" in sql
    assert "toStartOfWeek(`block_timestamp`, 1) AS date" in sql
    assert sql.count("today() - 90") == 2
    assert " UNION ALL " in sql
    assert sql.startswith("SELECT date, max(m0)")
    assert "GROUP BY date ORDER BY date ASC" in sql


def test_multi_metric_join_sql_default_y_and_agg():
    specs = [
        {"model": "api_a_daily", "y": "", "agg": ""},
        {"model": "api_b_daily", "y": "", "agg": ""},
    ]
    sql = metric_lab_module._multi_metric_join_sql(_JOIN_MODELS, specs)
    # defaults: first numeric column, sum
    assert "sum(`volume`)" in sql
    assert "sum(`users`)" in sql


def test_multi_metric_join_sql_rejections():
    ok = {"model": "api_a_daily", "y": "volume", "agg": "sum"}
    with pytest.raises(ValueError, match="Duplicate models"):
        metric_lab_module._multi_metric_join_sql(_JOIN_MODELS, [ok, dict(ok)])
    with pytest.raises(ValueError, match="no date/time column"):
        metric_lab_module._multi_metric_join_sql(
            _JOIN_MODELS,
            [ok, {"model": "api_no_date", "y": "tvl", "agg": "sum"}],
        )
    with pytest.raises(ValueError, match="no numeric column"):
        metric_lab_module._multi_metric_join_sql(
            _JOIN_MODELS,
            [ok, {"model": "api_no_numeric", "y": "", "agg": "sum"}],
        )
    with pytest.raises(ValueError, match="not a column"):
        metric_lab_module._multi_metric_join_sql(
            _JOIN_MODELS,
            [ok, {"model": "api_b_daily", "y": "nope", "agg": "sum"}],
        )
    with pytest.raises(ValueError, match="not supported for joins"):
        metric_lab_module._multi_metric_join_sql(
            _JOIN_MODELS,
            [ok, {"model": "api_b_daily", "y": "users", "agg": "count"}],
        )
    with pytest.raises(ValueError, match="grain must be one of"):
        metric_lab_module._multi_metric_join_sql(_JOIN_MODELS, [ok], grain="year")


def test_resolve_join_specs_contract():
    specs = metric_lab_module._resolve_join_specs(
        ["api_a_daily", "api_b_daily"],
        {"api_b_daily": {"y": "users", "agg": "avg"}},
    )
    # metrics defines identity + order; join_specs is per-model config
    assert [s["model"] for s in specs] == ["api_a_daily", "api_b_daily"]
    assert specs[0] == {"model": "api_a_daily", "y": "", "agg": "sum"}
    assert specs[1] == {"model": "api_b_daily", "y": "users", "agg": "avg"}

    with pytest.raises(ValueError, match="unselected models"):
        metric_lab_module._resolve_join_specs(
            ["api_a_daily"], {"api_b_daily": {"y": "users"}}
        )
    with pytest.raises(ValueError, match="must be an object"):
        metric_lab_module._resolve_join_specs(
            ["api_a_daily"], {"api_a_daily": "sum"}
        )


def test_join_load_end_to_end(snapshot_ready):
    """3 models + mode=aggregate -> ONE wide primary dataset, yFields =
    model aliases, correlate suggestion, join routing."""
    cols = [
        "date",
        "api_execution_transactions_daily",
        "int_gbc_deposits_daily",
        "consensus.blocks",
    ]
    server = _build_server_with(ColsStubCH(cols))
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric=[
            "api_execution_transactions_daily",
            "int_gbc_deposits_daily",
            "consensus.blocks",
        ],
        mode="aggregate",
        grain="day",
        join_specs={"consensus.blocks": {"y": "slot", "agg": "max"}},
    )
    assert not result.isError, result.content
    sc = result.structuredContent
    assert set(sc["datasets"]) == {"primary"}
    sql = sc["datasets"]["primary"]["sql"]
    assert "max(m0) AS `api_execution_transactions_daily`" in sql
    assert "max(`slot`)" in sql
    panel = sc["view_state"]["charts"][0]
    assert panel["xField"] == "date"
    assert panel["yFields"] == cols[1:]
    assert panel["chartType"] == "line"
    legacy = sc["view_state"]["chart"]
    assert set(legacy) == {"xField", "yField", "chartType", "aggregation", "groupBy"}
    assert sc["view_state"]["load_mode"] == "join"
    assert sc["view_state"]["metric_fields"] == cols[1:]
    # 3 aligned value columns -> correlation scatter suggestion
    assert any(
        s["reason"] == "correlation" for s in sc["view_state"]["chart_suggestions"]
    )
    record = mini_apps.get_view(view_id)
    assert set(record.datasets) == {"primary"}


def test_join_routing_rejections(snapshot_ready):
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")

    dup = load_fn(
        view_id=view_id,
        metric=["api_execution_transactions_daily", "api_execution_transactions_daily"],
        mode="aggregate",
    )
    assert dup.isError and "Duplicate model" in dup.content[0].text

    over_cap = load_fn(
        view_id=view_id,
        metric=[f"api_fake_{i}" for i in range(9)],
        mode="aggregate",
    )
    assert over_cap.isError and "At most 8" in over_cap.content[0].text

    raw_three = load_fn(
        view_id=view_id,
        metric=[
            "api_execution_transactions_daily",
            "int_gbc_deposits_daily",
            "consensus.blocks",
        ],
        mode="raw",
    )
    assert raw_three.isError
    assert "aggregate" in raw_three.content[0].text

    mixed = load_fn(
        view_id=view_id,
        metric=["api_execution_transactions_daily", "avatar_count_value"],
        mode="aggregate",
    )
    assert mixed.isError and "Cannot mix" in mixed.content[0].text


# ---------------------------------------------------------------------------
# A3: chart-panel grid (charts[], update by chart_id, bulk persist)
# ---------------------------------------------------------------------------


def _open_and_load(server, metric="api_execution_transactions_daily"):
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(view_id=view_id, metric=metric)
    assert not result.isError, result.content
    return view_id, result.structuredContent


def test_initial_load_emits_panels_and_revisions(snapshot_ready):
    server = _build_server()
    view_id, sc = _open_and_load(server)
    panels = sc["view_state"]["charts"]
    assert len(panels) == 1
    assert panels[0]["id"] == "c1"
    assert panels[0]["datasetKey"] == "primary"
    assert sc["view_state"]["dataset_revisions"] == {"primary": 1}
    # reload bumps the revision
    load_fn = _get_tool(server, "load_metric_lab_metric")
    sc2 = load_fn(
        view_id=view_id, metric="api_execution_transactions_daily", limit=500
    ).structuredContent
    assert sc2["view_state"]["dataset_revisions"] == {"primary": 2}


def test_update_chart_by_panel_id(snapshot_ready):
    server = _build_server()
    view_id, sc = _open_and_load(server)
    update_fn = _get_tool(server, "update_metric_lab_chart")

    # legacy call (no chart_id) patches charts[0] AND the scalar chart
    result = update_fn(
        view_id=view_id, x_field="date", y_field="tx_count", chart_type="bar"
    )
    assert not result.isError, result.content
    patch = result.structuredContent["patch"]
    assert patch["chart"]["chartType"] == "bar"
    assert patch["charts"][0]["chartType"] == "bar"
    assert patch["charts"][0]["yFields"] == ["tx_count"]

    # unknown panel id -> error listing valid ids
    bad = update_fn(
        view_id=view_id, x_field="date", y_field="tx_count",
        chart_type="line", chart_id="nope",
    )
    assert bad.isError
    assert "c1" in bad.content[0].text

    # explicit id patches the matching panel
    ok = update_fn(
        view_id=view_id, x_field="date", y_field="tx_count",
        chart_type="line", chart_id="c1",
    )
    assert not ok.isError
    assert ok.structuredContent["patch"]["charts"][0]["chartType"] == "line"


def test_set_metric_lab_charts_validates_and_persists(snapshot_ready):
    server = _build_server()
    view_id, sc = _open_and_load(server)
    set_fn = _get_tool(server, "set_metric_lab_charts")

    panels = [
        {
            "id": "c1", "datasetKey": "primary", "xField": "date",
            "yField": "tx_count", "chartType": "line", "aggregation": "sum",
            "groupBy": "", "title": "Tx count",
        },
        {
            "id": "c2", "datasetKey": "primary", "xField": "date",
            "yFields": ["tx_count", "tx_count"],  # dupes -> canonicalized
            "yField": "", "chartType": "bar", "aggregation": "avg",
            "groupBy": "", "sortDir": "desc", "trendline": True,
        },
    ]
    result = set_fn(view_id=view_id, charts=panels)
    assert not result.isError, result.content
    saved = result.structuredContent["patch"]["charts"]
    assert [p["id"] for p in saved] == ["c1", "c2"]
    assert saved[1]["yFields"] == ["tx_count"]  # deduped
    assert saved[1]["yField"] == "tx_count"     # mirror re-derived
    assert result.structuredContent["patch"]["chart"]["yField"] == "tx_count"

    record = mini_apps.get_view(view_id)
    assert [p["id"] for p in record.view_state["charts"]] == ["c1", "c2"]


def test_set_metric_lab_charts_rejections(snapshot_ready):
    server = _build_server()
    view_id, _ = _open_and_load(server)
    set_fn = _get_tool(server, "set_metric_lab_charts")
    base = {
        "id": "c1", "datasetKey": "primary", "xField": "date",
        "yField": "tx_count", "chartType": "line", "aggregation": "sum",
        "groupBy": "",
    }

    empty = set_fn(view_id=view_id, charts=[])
    assert empty.isError and "non-empty" in empty.content[0].text

    over = set_fn(
        view_id=view_id,
        charts=[{**base, "id": f"c{i}"} for i in range(13)],
    )
    assert over.isError and "At most 12" in over.content[0].text

    dup = set_fn(view_id=view_id, charts=[base, dict(base)])
    assert dup.isError and "Duplicate panel id" in dup.content[0].text

    bad_id = set_fn(view_id=view_id, charts=[{**base, "id": "x" * 20}])
    assert bad_id.isError and "invalid" in bad_id.content[0].text

    bad_ds = set_fn(view_id=view_id, charts=[{**base, "datasetKey": "nope"}])
    assert bad_ds.isError and "does not reference" in bad_ds.content[0].text

    bad_field = set_fn(view_id=view_id, charts=[{**base, "xField": "typo"}])
    assert bad_field.isError and "not a column" in bad_field.content[0].text

    bad_title = set_fn(view_id=view_id, charts=[{**base, "title": "x" * 201}])
    assert bad_title.isError and "title" in bad_title.content[0].text

    wrong_view = set_fn(view_id="nope", charts=[base])
    assert wrong_view.isError


def test_set_metric_lab_charts_hidden_from_model(snapshot_ready):
    server = _build_server()
    assert "set_metric_lab_charts" in web_apps.MINI_APP_TOOL_REGISTRY
    assert "set_metric_lab_charts" in mini_apps.get_app_only_tool_names()
    names = [t.name for t in asyncio.run(server.list_tools())]
    assert "set_metric_lab_charts" not in names


def test_update_chart_rejects_wrong_app_view(snapshot_ready):
    server = _build_server()
    foreign_view = mini_apps.create_view("data_catalog", "other app")
    update_fn = _get_tool(server, "update_metric_lab_chart")
    result = update_fn(
        view_id=foreign_view, x_field="a", y_field="b", chart_type="line"
    )
    assert result.isError


def test_dual_load_emits_two_panels(snapshot_ready):
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    result = load_fn(
        view_id=view_id,
        metric=["api_execution_transactions_daily", "int_gbc_deposits_daily"],
        mode="raw",
    )
    assert not result.isError, result.content
    sc = result.structuredContent
    panels = sc["view_state"]["charts"]
    assert [p["datasetKey"] for p in panels] == ["primary", "secondary"]
    assert [p["id"] for p in panels] == ["c1", "c2"]
    assert sc["view_state"]["dataset_revisions"] == {"primary": 1, "secondary": 1}


# ---------------------------------------------------------------------------
# A4: SQL editor (run_metric_lab_sql)
# ---------------------------------------------------------------------------


class FailingCH(ColsStubCH):
    def run_query(self, sql, database="dbt", *args, **kwargs):
        raise RuntimeError("Syntax error: failed at position 1")


def test_run_sql_registered_and_hidden(snapshot_ready):
    server = _build_server()
    assert "run_metric_lab_sql" in web_apps.MINI_APP_TOOL_REGISTRY
    assert "run_metric_lab_sql" in mini_apps.get_app_only_tool_names()
    names = [t.name for t in asyncio.run(server.list_tools())]
    assert "run_metric_lab_sql" not in names


def test_run_sql_rejects_non_nestable_and_writes(snapshot_ready):
    server = _build_server()
    view_id, _ = _open_and_load(server)
    run_fn = _get_tool(server, "run_metric_lab_sql")

    for bad in ("SHOW TABLES", "DESCRIBE t", "INSERT INTO t VALUES (1)", "DROP TABLE t"):
        result = run_fn(view_id=view_id, sql=bad)
        assert result.isError, bad
        assert "SELECT or WITH" in result.content[0].text

    unknown_ds = run_fn(view_id=view_id, sql="SELECT 1", dataset_key="nope")
    assert unknown_ds.isError and "Unknown dataset" in unknown_ds.content[0].text

    wrong_view = run_fn(view_id="nope", sql="SELECT 1")
    assert wrong_view.isError

    foreign = mini_apps.create_view("data_catalog", "other")
    wrong_app = run_fn(view_id=foreign, sql="SELECT 1")
    assert wrong_app.isError


def test_run_sql_rejects_edited_sql_with_placeholders(snapshot_ready):
    server = _build_server()
    view_id, _ = _open_and_load(server)
    run_fn = _get_tool(server, "run_metric_lab_sql")
    result = run_fn(
        view_id=view_id,
        sql="SELECT * FROM t WHERE col = {flt:String}",
    )
    assert result.isError
    assert "placeholders" in result.content[0].text


def test_run_sql_broken_sql_surfaces_error(snapshot_ready):
    server = CerebroFastMCP("test-catalog")
    ch = StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)
    view_id, _ = _open_and_load(server)
    # swap in a CH that raises AFTER load succeeded
    ch.__class__ = FailingCH
    run_fn = _get_tool(server, "run_metric_lab_sql")
    result = run_fn(view_id=view_id, sql="SELECT broken FROM nowhere")
    assert result.isError
    assert "Syntax error" in result.content[0].text
    # nothing mutated: the prior dataset + state survive
    record = mini_apps.get_view(view_id)
    assert record.dataset_revisions == {"primary": 1}


def test_run_sql_replaces_only_target_dataset_and_repairs_panels(snapshot_ready):
    server = _build_server()
    open_fn = _get_tool(server, "open_metric_lab")
    view_id = open_fn().structuredContent["view_id"]
    load_fn = _get_tool(server, "load_metric_lab_metric")
    dual = load_fn(
        view_id=view_id,
        metric=["api_execution_transactions_daily", "int_gbc_deposits_daily"],
        mode="raw",
    )
    assert not dual.isError

    run_fn = _get_tool(server, "run_metric_lab_sql")
    result = run_fn(
        view_id=view_id,
        sql="SELECT date, amount FROM `dbt`.`int_gbc_deposits_daily` LIMIT 50",
        dataset_key="secondary",
    )
    assert not result.isError, result.content
    sc = result.structuredContent
    # both datasets still present; only secondary's revision bumped
    assert set(sc["datasets"]) == {"primary", "secondary"}
    assert sc["view_state"]["dataset_revisions"] == {"primary": 1, "secondary": 2}
    # per-dataset provenance recorded
    assert sc["view_state"]["provenance"]["secondary"]["source"] == "editor_sql"
    # panels preserved (2 from the dual load)
    assert [p["id"] for p in sc["view_state"]["charts"]] == ["c1", "c2"]
    record = mini_apps.get_view(view_id)
    assert record.dataset_revisions == {"primary": 1, "secondary": 2}


def test_run_sql_force_refresh_bypasses_cache(snapshot_ready):
    calls = {"n": 0}

    class CountingCH(StubCH):
        def run_query(self, sql, database="dbt", *args, **kwargs):
            calls["n"] += 1
            return super().run_query(sql, database, *args, **kwargs)

    server = CerebroFastMCP("test-catalog")
    ch = CountingCH()
    mini_apps.register_mini_app_infra(server, ch)
    register_metric_lab_tools(server, ch)
    view_id, sc = _open_and_load(server)
    run_fn = _get_tool(server, "run_metric_lab_sql")
    sql = sc["datasets"]["primary"]["sql"]

    before = calls["n"]
    first = run_fn(view_id=view_id, sql=sql)
    assert not first.isError
    after_first = calls["n"]
    assert after_first > before  # cache was NOT served

    second = run_fn(view_id=view_id, sql=sql)
    assert not second.isError
    assert calls["n"] > after_first  # rerun hits ClickHouse again


def test_load_bounded_dataset_force_refresh_flag():
    calls = {"n": 0}

    class CountingCH(StubCH):
        def run_query(self, sql, database="dbt", *args, **kwargs):
            calls["n"] += 1
            return super().run_query(sql, database, *args, **kwargs)

    ch = CountingCH()
    ds1 = mini_apps.load_bounded_dataset(ch, "SELECT 1", database="dbt")
    n1 = calls["n"]
    ds2 = mini_apps.load_bounded_dataset(ch, "SELECT 1", database="dbt")
    assert calls["n"] == n1  # cache hit
    ds3 = mini_apps.load_bounded_dataset(
        ch, "SELECT 1", database="dbt", force_refresh=True
    )
    assert calls["n"] > n1  # bypassed
    assert ds1.columns == ds2.columns == ds3.columns
