"""Tests for the Data Catalog backend tools (catalog_search / get_catalog_entity).

These exercise the registry-native logic against a synthetic snapshot — no
ClickHouse, no manifest load. The catalog reads only ``current_snapshot()``, so
we monkeypatch it with a small duck-typed snapshot.
"""

from __future__ import annotations

import types

import pytest

import cerebro_mcp.tools.semantic.data_catalog as dc


@pytest.fixture
def snap(monkeypatch):
    snapshot = types.SimpleNamespace(
        registry_hash="test-hash-1",
        models={
            "fct_bridges_netflow_weekly": {
                "fqn": ["gnosis_dbt", "bridges", "marts", "fct_bridges_netflow_weekly"],
                "description": "Weekly net flow per bridge in USD.",
                "module": "bridges",
                "semantic_status": "approved",
                "quality_tier": "approved",
                "owner": "analytics_team",
                "tags": ["production", "bridges", "tier1"],
                "materialized": "table",
                "path": "bridges/marts/fct_bridges_netflow_weekly.sql",
                "relation_name": "dbt.fct_bridges_netflow_weekly",
                "resource_type": "model",
                "columns": {
                    "date": {"name": "date", "data_type": "Date", "description": "week start"},
                    "series": {"name": "series", "data_type": "String", "description": "bridge"},
                },
                "dimensions": [{"name": "date", "type": "time"}],
                "measures": [],
                "metric_names": ["bridges_netflow_weekly__value"],
                "lineage": {
                    "upstream": ["int_bridges_transfers"],
                    "downstream": ["api_bridges_cum_netflow_weekly"],
                },
            },
            "int_circles_trust_daily": {
                "fqn": ["gnosis_dbt", "circles", "int_circles_trust_daily"],
                "description": "Circles trust graph edges daily.",
                "module": "execution",
                "semantic_status": "candidate",
                "quality_tier": "candidate",
                "owner": "",
                "tags": ["circles"],
                "materialized": "incremental",
                "path": "execution/circles/int_circles_trust_daily.sql",
                "relation_name": "",
                "resource_type": "model",
                "columns": {},
                "dimensions": [],
                "measures": [],
                "metric_names": [],
                "lineage": {"upstream": [], "downstream": []},
            },
        },
        metrics={
            "bridges_netflow_weekly__value": {
                "label": "Bridges Net Flow Weekly",
                "description": "Sum of net flow USD weekly.",
                "module": "bridges",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "root_model": "fct_bridges_netflow_weekly",
                "type": "simple",
                "measure": {"agg": "sum"},
                "allowed_dimensions": ["date", "series"],
                "supported_time_grains": ["day", "week"],
                "question_synonyms": ["bridge net flow", "netflow"],
                "default_filters": [],
            },
        },
        graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snapshot)
    dc._INDEX_CACHE.clear()
    return snapshot


def test_search_ranks_models_and_metrics(snap):
    r = dc.catalog_search("bridge net flow")
    assert r["total"] >= 2
    types_found = {h["type"] for h in r["hits"]}
    assert "model" in types_found and "metric" in types_found
    # Facets are computed over the full query universe.
    assert r["facets"]["type"].get("model", 0) >= 1
    assert r["facets"]["type"].get("metric", 0) >= 1
    # Scores are populated and ordered descending.
    scores = [h["score"] for h in r["hits"] if h["score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_browse_empty_query_is_tier_then_name_ordered(snap):
    r = dc.catalog_search("")
    titles = [h["title"] for h in r["hits"]]
    # approved entities sort before candidate ones.
    assert titles[0] in {"Bridges Net Flow Weekly", "fct_bridges_netflow_weekly"}
    assert "int_circles_trust_daily" in titles


def test_module_filter_narrows_hits(snap):
    r = dc.catalog_search("", module="execution")
    assert r["total"] == 1
    assert r["hits"][0]["title"] == "int_circles_trust_daily"
    # Facet still shows the full module breakdown for the query universe.
    assert set(r["facets"]["module"]) == {"bridges", "execution"}


def test_entity_types_filter(snap):
    r = dc.catalog_search("netflow", entity_types=["metric"])
    assert r["total"] == 1
    assert {h["type"] for h in r["hits"]} == {"metric"}


def test_tier_filter(snap):
    r = dc.catalog_search("", tier="candidate")
    assert {h["tier"] for h in r["hits"]} == {"candidate"}


def test_zero_result_query_offers_suggestions(snap):
    # A query whose matches are all removed by an active facet filter returns
    # zero hits, but the query-relevant universe is surfaced as {name, title,
    # type} "did you mean" suggestions (drop-the-filter recovery, never a dead
    # end). "bridge" matches the two approved bridges entities; tier=candidate
    # filters them all out.
    r = dc.catalog_search("bridge", tier="candidate")
    assert r["total"] == 0 and r["hits"] == []
    assert isinstance(r["suggestions"], list) and len(r["suggestions"]) >= 1
    s = r["suggestions"][0]
    assert set(s) == {"name", "title", "type"}


def test_browse_no_query_has_no_suggestions(snap):
    # Suggestions only fire on a zero-result *query*; a normal browse omits them.
    r = dc.catalog_search("")
    assert r["suggestions"] == []


def test_tags_filter_requires_all(snap):
    r = dc.catalog_search("", tags=["bridges", "tier1"])
    assert r["total"] == 1
    assert r["hits"][0]["title"] == "fct_bridges_netflow_weekly"
    r2 = dc.catalog_search("", tags=["bridges", "nonexistent"])
    assert r2["total"] == 0


def test_get_model_entity_profile_shape(snap):
    e = dc.get_catalog_entity("fct_bridges_netflow_weekly")
    assert e["type"] == "model"
    assert e["fqn"] == "gnosis_dbt.bridges.marts.fct_bridges_netflow_weekly"
    assert e["tier"] == "approved"
    assert e["owner"] == "analytics_team"
    assert [c["name"] for c in e["columns"]] == ["date", "series"]
    assert e["column_count"] == 2
    assert e["upstream_count"] == 1 and e["downstream_count"] == 1
    assert e["metrics"][0]["name"] == "bridges_netflow_weekly__value"
    assert e["metrics"][0]["tier"] == "approved"


def test_get_metric_entity_profile(snap):
    e = dc.get_catalog_entity("bridges_netflow_weekly__value", "metric")
    assert e["type"] == "metric"
    assert e["root_model"] == "fct_bridges_netflow_weekly"
    assert e["supported_time_grains"] == ["day", "week"]


def test_unknown_model_returns_suggestions(snap):
    e = dc.get_catalog_entity("does_not_exist")
    assert "error" in e
    assert "suggestions" in e


def test_snapshot_unavailable_is_graceful(monkeypatch):
    monkeypatch.setattr(dc, "current_snapshot", lambda: None)
    r = dc.catalog_search("anything")
    assert r["total"] == 0 and r["hits"] == []
    e = dc.get_catalog_entity("x")
    assert "error" in e


# ---------------------------------------------------------------------------
# Privacy gate — regression tests for the CAT-1 PII leak (Step 0)
# ---------------------------------------------------------------------------


def test_filter_internal_models_removes_internal_and_refs():
    from cerebro_mcp.loaders.semantic import _filter_internal_models

    reg = {
        "models": {
            "pub": {"tags": ["production"]},
            "int_execution_gpay_user_identity_bridge": {"tags": ["internal_only"]},
            "secret_via_meta": {"tags": [], "meta": {"expose_to_mcp": False}},
        },
        "metrics": {
            "m_pub": {"root_model": "pub"},
            "m_int": {"root_model": "int_execution_gpay_user_identity_bridge"},
        },
        "relationships": [
            {"left_model": "pub", "right_model": "int_execution_gpay_user_identity_bridge"},
            {"left_model": "pub", "right_model": "pub"},
        ],
    }
    out = _filter_internal_models(reg)
    assert "pub" in out["models"]
    assert "int_execution_gpay_user_identity_bridge" not in out["models"]
    assert "secret_via_meta" not in out["models"]
    # Metrics + relationships referencing a hidden model are dropped too.
    assert "m_pub" in out["metrics"] and "m_int" not in out["metrics"]
    assert out["relationships"] == [{"left_model": "pub", "right_model": "pub"}]


def test_safety_backtick_internal_table_is_rejected():
    from cerebro_mcp import safety

    # backtick-quoted form previously bypassed the deny list entirely
    sql = "SELECT * FROM `dbt`.`int_execution_gpay_user_identity_bridge` LIMIT 5"
    names = safety.extract_table_names(sql)
    assert any(n.endswith("int_execution_gpay_user_identity_bridge") for n in names)
    ok, err = safety.validate_query(sql)
    assert ok is False and "internal-only" in err


def test_register_internal_only_tables_extends_denylist():
    from cerebro_mcp import safety

    safety.register_internal_only_tables(["int_mixpanel_ga_user_acquisition"])
    ok, err = safety.validate_query(
        "SELECT * FROM `dbt`.`int_mixpanel_ga_user_acquisition`"
    )
    assert ok is False and "internal-only" in err


class _FakeStructuredResult:
    def __init__(self):
        self.columns = ["addr", "value"]
        self.column_types = ["String", "UInt64"]
        self.rows = [["0xabc", 1], ["0xdef", 2]]
        self.row_count = 2


class _DateStructuredResult:
    def __init__(self):
        self.columns = ["date", "value"]
        self.column_types = ["int", "float"]
        self.rows = [[19723, 1.5]]
        self.row_count = 1


def test_catalog_sample_gates_restricted_model(monkeypatch):
    calls = []

    def _fake_rsq(ch, sql, database="dbt", parameters=None, requested_max_rows=5000):
        calls.append(sql)
        return _FakeStructuredResult()

    monkeypatch.setattr(dc, "run_structured_query", _fake_rsq)
    snap = types.SimpleNamespace(
        registry_hash="h-sample",
        models={
            "ok_model": {
                "relation_name": "`dbt`.`ok_model`",
                "tags": ["production"],
                "materialized": "table",
            },
            "int_mixpanel_ga_user_acquisition": {
                "relation_name": "`dbt`.`int_mixpanel_ga_user_acquisition`",
                "tags": ["privacy:mixpanel_ga", "mixpanel_ga"],
                "materialized": "incremental",
            },
        },
        metrics={},
        graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)

    # Restricted model: refused BEFORE any SQL is built/run.
    r = dc._catalog_sample_impl(object(), "int_mixpanel_ga_user_acquisition")
    assert r["available"] is False and r.get("restricted") is True
    assert calls == []

    # Sampleable model: queries the parsed bare db.table.
    r2 = dc._catalog_sample_impl(object(), "ok_model")
    assert r2["available"] is True and r2["row_count"] == 2
    assert calls and "dbt.ok_model" in calls[0] and "`" not in calls[0]


def test_format_sample_cell_dates():
    # Date column stores epoch-days; DateTime stores epoch-seconds.
    assert dc._format_sample_cell(19723, "Date") == "2024-01-01"
    assert dc._format_sample_cell(19723, "Nullable(Date)") == "2024-01-01"
    assert dc._format_sample_cell(1778584837, "DateTime").startswith("2026-")
    # Non-date / non-numeric / bool pass through unchanged.
    assert dc._format_sample_cell(42, "UInt64") == 42
    assert dc._format_sample_cell("x", "String") == "x"
    assert dc._format_sample_cell(True, "Bool") is True
    assert dc._format_sample_cell(None, "Date") is None


def test_catalog_sample_formats_date_columns(monkeypatch):
    def _fake_rsq(ch, sql, database="dbt", parameters=None, requested_max_rows=5000):
        return _DateStructuredResult()

    monkeypatch.setattr(dc, "run_structured_query", _fake_rsq)
    snap = types.SimpleNamespace(
        registry_hash="h-dates",
        models={
            "m": {
                "relation_name": "`dbt`.`m`",
                "tags": ["production"],
                "materialized": "table",
                "columns": {
                    "date": {"name": "date", "data_type": "Date"},
                    "value": {"name": "value", "data_type": "Float64"},
                },
            }
        },
        metrics={}, graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)
    r = dc._catalog_sample_impl(object(), "m")
    assert r["available"] is True
    assert r["column_types"] == ["Date", "Float64"]
    assert r["rows"][0][0] == "2024-01-01"  # epoch-day 19723 formatted
    assert r["rows"][0][1] == 1.5  # float untouched


def test_pii_subject_column_blocks_sample(monkeypatch):
    """A model carrying a per-subject identity column (wallet_address) must be
    refused as a sample even when it is NOT privacy-tagged (e.g. GPay per-wallet
    activity tagged production/gpay) — while public on-chain models with
    from_address/to_address stay samplable."""
    calls = []

    def _fake_rsq(ch, sql, database="dbt", parameters=None, requested_max_rows=5000):
        calls.append(sql)
        return _FakeStructuredResult()

    monkeypatch.setattr(dc, "run_structured_query", _fake_rsq)
    snap = types.SimpleNamespace(
        registry_hash="h-pii",
        models={
            "int_execution_gpay_activity_daily": {
                "relation_name": "`dbt`.`int_execution_gpay_activity_daily`",
                "tags": ["production", "execution", "gpay"],
                "materialized": "table",
                "columns": {"wallet_address": {"name": "wallet_address", "data_type": "String"},
                            "amount_usd": {"name": "amount_usd", "data_type": "Float64"}},
            },
            "int_execution_transfers": {
                "relation_name": "`dbt`.`int_execution_transfers`",
                "tags": ["production", "execution"],
                "materialized": "table",
                "columns": {"from_address": {"name": "from_address", "data_type": "String"},
                            "to_address": {"name": "to_address", "data_type": "String"}},
            },
        },
        metrics={}, graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)
    # PII subject column → refused before any SQL.
    r = dc._catalog_sample_impl(object(), "int_execution_gpay_activity_daily")
    assert r["available"] is False and r.get("restricted") is True
    assert calls == []
    # Public on-chain model (from/to address only) → still samplable.
    r2 = dc._catalog_sample_impl(object(), "int_execution_transfers")
    assert r2["available"] is True


def test_format_sample_cell_epoch_magnitude():
    # A column TYPED Date but storing epoch-SECONDS must not overflow to raw int.
    out = dc._format_sample_cell(1638921600, "Date", "date")
    assert out == "2021-12-08 00:00:00"
    # epoch-days still works (small magnitude).
    assert dc._format_sample_cell(19723, "Date", "date") == "2024-01-01"
    # epoch-millis.
    assert dc._format_sample_cell(1638921600000, "DateTime64(3)", "ts").startswith("2021-12-08")
    # A big non-temporal number (no Date type, non-temporal name) passes through.
    assert dc._format_sample_cell(1638921600, "UInt64", "block_number") == 1638921600
    # REGRESSION: a NUMERIC column named *_week / *_month / *_day must NOT be
    # reinterpreted as an epoch date (it's a count/value, not a timestamp).
    assert dc._format_sample_cell(34, "UInt64", "active_addresses_week") == 34
    assert dc._format_sample_cell(1234.5, "Float64", "volume_month") == 1234.5
    # Blank-type name inference still works (registry drift).
    assert dc._format_sample_cell(19723, "", "date") == "2024-01-01"


def test_seed_anchored_subgraph_keeps_seed_and_core():
    # 1 seed + 400 downstream — alphabetical slice would drop "seed"; BFS keeps it.
    nodes = [{"id": "seed", "name": "seed"}] + [{"id": f"n{i:03d}", "name": f"n{i:03d}"} for i in range(400)]
    edges = [{"id": f"e{i}", "source": "seed", "target": f"n{i:03d}"} for i in range(400)]
    kept_nodes, kept_edges = dc._seed_anchored_subgraph(nodes, edges, "seed", 50)
    ids = {n["id"] for n in kept_nodes}
    assert "seed" in ids and len(kept_nodes) == 50
    # every kept edge has both endpoints in the kept set (no orphan edges).
    assert all(e["source"] in ids and e["target"] in ids for e in kept_edges)


def test_slim_lineage_nodes_drops_heavy_fields():
    nodes = [{"id": "a", "name": "a", "kind": "model", "raw_sql": "x" * 1000, "columns": [1, 2], "column_count": 2}]
    slim = dc._slim_lineage_nodes(nodes)
    assert "raw_sql" not in slim[0] and "columns" not in slim[0]
    assert slim[0]["column_count"] == 2 and slim[0]["name"] == "a"


def test_catalog_sample_unknown_model_is_graceful(monkeypatch):
    monkeypatch.setattr(dc, "current_snapshot", lambda: types.SimpleNamespace(
        registry_hash="h", models={}, metrics={}, graph_profiles=()))
    r = dc._catalog_sample_impl(object(), "nope")
    assert r["available"] is False


# ---------------------------------------------------------------------------
# P1 backend tools — table stats / run config / overview
# ---------------------------------------------------------------------------


class _FakeCH:
    def __init__(self, rows):
        self._rows = rows

    def execute_raw_cached(self, sql, database, cache_key, parameters=None):
        return {"rows": self._rows, "columns": []}


def test_catalog_table_stats_table_and_view(monkeypatch):
    snap = types.SimpleNamespace(
        registry_hash="h-stats",
        models={
            "tbl": {"relation_name": "`dbt`.`tbl`", "materialized": "table"},
            "vw": {"relation_name": "`dbt`.`vw`", "materialized": "view"},
        },
        metrics={}, graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)
    ch = _FakeCH([["MergeTree", 1000, 50000]])
    r = dc._catalog_table_stats_impl(ch, "tbl")
    assert r["available"] and r["row_count"] == 1000 and r["size_bytes"] == 50000
    assert r["is_view"] is False
    rv = dc._catalog_table_stats_impl(ch, "vw")
    assert rv["available"] and rv["is_view"] is True and rv["row_count"] is None


def test_catalog_table_stats_gates_restricted_models(monkeypatch):
    """Row count / on-disk size must be refused for restricted models BEFORE any
    system.tables query — both the privacy-tagged path and the out-of-allowlist
    physical-DB path (an untagged table living in mixpanel_ga)."""
    queried = []

    class _RecCH:
        def execute_raw_cached(self, sql, database, cache_key, parameters=None):
            queried.append((database, parameters))
            return {"rows": [["MergeTree", 999, 123]], "columns": []}

    snap = types.SimpleNamespace(
        registry_hash="h-stats-priv",
        models={
            "tagged_restricted": {
                "relation_name": "`dbt`.`tagged_restricted`",
                "tags": ["mixpanel_ga"],
                "materialized": "table",
            },
            "untagged_out_of_scope": {
                "relation_name": "`mixpanel_ga`.`mixpanel_raw_events`",
                "tags": ["production"],
                "materialized": "table",
            },
        },
        metrics={}, graph_profiles=(),
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)
    ch = _RecCH()

    rt = dc._catalog_table_stats_impl(ch, "tagged_restricted")
    assert rt["available"] is False and rt.get("restricted") is True
    assert "row_count" not in rt or rt["row_count"] is None

    ru = dc._catalog_table_stats_impl(ch, "untagged_out_of_scope")
    assert ru["available"] is False and ru.get("restricted") is True

    # No system.tables read happened for either restricted model.
    assert queried == []

    # And the row sample for the out-of-scope DB is refused as restricted too
    # (clean classification, not a leaked allowlist error).
    monkeypatch.setattr(dc, "run_structured_query",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query")))
    rs = dc._catalog_sample_impl(object(), "untagged_out_of_scope")
    assert rs["available"] is False and rs.get("restricted") is True


def test_catalog_run_config_from_manifest(monkeypatch):
    node = {
        "config": {
            "materialized": "incremental",
            "incremental_strategy": "insert_overwrite",
            "unique_key": ["date"],
            "on_schema_change": "append_new_columns",
        },
        "tags": ["production"],
    }
    fake_manifest = types.SimpleNamespace(
        is_loaded=True, get_model=lambda n: node if n == "m" else None
    )
    monkeypatch.setattr(dc, "manifest", fake_manifest)
    r = dc.catalog_run_config("m")
    assert r["available"] and r["incremental_strategy"] == "insert_overwrite"
    assert dc.catalog_run_config("nope")["available"] is False


def test_catalog_overview_shape(snap):
    r = dc.catalog_overview()
    assert r["available"] and r["stats"]["models"] == 2
    assert "owned_pct" in r["stats"] and "doc_coverage_pct" in r["stats"]
    assert any(d["module"] == "bridges" for d in r["domains"])
    assert isinstance(r["entry_points"], list)
    assert isinstance(r["glossary_terms"], list)
    assert isinstance(r["top_metrics"], list)


def test_catalog_governance_shape(snap):
    r = dc.catalog_governance()
    assert r["available"] and r["model_count"] == 2
    assert any(o["owner"] == "analytics_team" for o in r["ownership"])
    assert r["tiers"].get("approved") == 1 and r["tiers"].get("candidate") == 1
    assert "restricted" in r["classification"] and "public" in r["classification"]
    assert any(d["module"] == "bridges" for d in r["doc_coverage_by_module"])
    # Unowned worklist rows are {name, module} objects (denser than bare names).
    assert isinstance(r["unowned_sample"], list)
    for row in r["unowned_sample"]:
        assert set(row) == {"name", "module"}


def test_elementary_tools_degrade_when_unavailable(monkeypatch):
    monkeypatch.setattr(dc, "_ELEM_AVAILABLE", None, raising=False)

    def _raise(*a, **k):
        raise RuntimeError("Code: 497 ACCESS_DENIED")

    monkeypatch.setattr(dc, "run_structured_query", _raise)
    snap = types.SimpleNamespace(
        registry_hash="h", models={"m": {}}, metrics={}, graph_profiles=()
    )
    monkeypatch.setattr(dc, "current_snapshot", lambda: snap)
    assert dc._catalog_run_state_impl(object(), "m")["available"] is False
    assert dc._catalog_test_results_impl(object(), "m")["available"] is False
    assert dc._catalog_health_impl(object())["available"] is False


def test_scaffolded_measure_wrappers_excluded_from_index(snap, monkeypatch):
    """Auto-scaffolded per-measure wrappers (measure == name, `*_value`) are
    catalog noise — the same data is already surfaced as the model + columns.
    Deliberately authored metrics (distinct measure) stay."""
    snap.metrics["execution_lending_aave_balance_cohorts_daily__holders_in_bucket_value"] = {
        "label": "Execution Lending Aave Balance Cohorts Daily - Holders In Bucket",
        "description": "",
        "module": "execution",
        "quality_tier": "approved",
        "semantic_status": "approved",
        "root_model": "execution_lending_aave_balance_cohorts_daily",
        # the scaffold giveaway: the measure IS the metric itself
        "measure": "execution_lending_aave_balance_cohorts_daily__holders_in_bucket_value",
        "question_synonyms": [],
    }
    dc._INDEX_CACHE.clear()
    r = dc.catalog_search("holders in bucket", entity_types=["metric"], limit=50)
    names = [h["name"] for h in r["hits"]]
    assert all("holders_in_bucket_value" not in n for n in names)
    # the curated metric from the fixture is still indexed
    r2 = dc.catalog_search("bridge net flow", entity_types=["metric"], limit=50)
    assert any(h["name"] == "bridges_netflow_weekly__value" for h in r2["hits"])
    dc._INDEX_CACHE.clear()
