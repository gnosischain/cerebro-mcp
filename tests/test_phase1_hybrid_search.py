"""Phase 1 tests: BM25 + RRF + networkx lineage + column-scoped schema.

These tests are deliberately self-contained — they build a small synthetic
manifest dict and feed it through `ManifestLoader._build_indexes_internal`,
the same code path production uses. No fixtures from the staging manifest
are required.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.loaders.manifest import ManifestLoader
from cerebro_mcp.runtime.schema_context import build_scoped_schema_block
from cerebro_mcp.semantic.bm25 import BM25Index, BM25Doc, ColumnBM25Index, ColumnDoc
from cerebro_mcp.semantic.index import rrf_fuse


# ---------------------------------------------------------------------------
# Fixture: a manifest that previously hallucinated under pure token-overlap.
#
# The query "trades by token" should rank `fct_execution_trades_by_token_daily`
# first. Under the old scorer it ties with `fct_execution_pools_daily` because
# both have "by token" in their description. BM25 + RRF promotes the model
# whose name itself matches the query terms.
# ---------------------------------------------------------------------------


@pytest.fixture
def trade_manifest_loader() -> ManifestLoader:
    loader = ManifestLoader()
    sample = {
        "nodes": {
            "model.gnosis_dbt.fct_execution_pools_daily": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.fct_execution_pools_daily",
                "name": "fct_execution_pools_daily",
                "description": "Daily DEX pool aggregates including trade volume by token pair",
                "schema": "dbt",
                "alias": "fct_execution_pools_daily",
                "path": "execution/dex/marts/fct_execution_pools_daily.sql",
                "tags": ["execution", "dex"],
                "config": {"materialized": "table"},
                "columns": {
                    "day": {"data_type": "Date", "description": "trading day"},
                    "pool_address": {"data_type": "String", "description": "pool contract"},
                    "volume_usd": {"data_type": "Float64", "description": "USD volume"},
                },
                "depends_on": {"nodes": []},
            },
            "model.gnosis_dbt.fct_execution_trades_by_token_daily": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.fct_execution_trades_by_token_daily",
                "name": "fct_execution_trades_by_token_daily",
                "description": "Daily trade counts and volume aggregated per token",
                "schema": "dbt",
                "alias": "fct_execution_trades_by_token_daily",
                "path": "execution/dex/marts/fct_execution_trades_by_token_daily.sql",
                "tags": ["execution", "dex", "tokens"],
                "config": {"materialized": "table"},
                "columns": {
                    "day": {"data_type": "Date", "description": "trading day"},
                    "token_address": {"data_type": "String", "description": "token contract"},
                    "token_symbol": {"data_type": "String", "description": "ticker"},
                    "trade_count": {"data_type": "UInt64", "description": "number of trades"},
                    "volume_usd": {"data_type": "Float64", "description": "USD volume"},
                },
                "depends_on": {
                    "nodes": ["model.gnosis_dbt.fct_execution_pools_daily"]
                },
            },
            "model.gnosis_dbt.api_dex_dashboard": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.api_dex_dashboard",
                "name": "api_dex_dashboard",
                "description": "DEX dashboard fact rollup",
                "schema": "dbt",
                "alias": "api_dex_dashboard",
                "path": "execution/dex/api/api_dex_dashboard.sql",
                "tags": ["execution", "dex"],
                "config": {"materialized": "view"},
                "columns": {},
                "depends_on": {
                    "nodes": [
                        "model.gnosis_dbt.fct_execution_trades_by_token_daily"
                    ]
                },
            },
        },
        "sources": {},
        "parent_map": {
            "model.gnosis_dbt.fct_execution_pools_daily": [],
            "model.gnosis_dbt.fct_execution_trades_by_token_daily": [
                "model.gnosis_dbt.fct_execution_pools_daily"
            ],
            "model.gnosis_dbt.api_dex_dashboard": [
                "model.gnosis_dbt.fct_execution_trades_by_token_daily"
            ],
        },
        "child_map": {
            "model.gnosis_dbt.fct_execution_pools_daily": [
                "model.gnosis_dbt.fct_execution_trades_by_token_daily"
            ],
            "model.gnosis_dbt.fct_execution_trades_by_token_daily": [
                "model.gnosis_dbt.api_dex_dashboard"
            ],
        },
    }
    indexes = loader._build_indexes_internal(sample)
    loader._apply_indexes(indexes)
    loader._loaded = True
    return loader


# ---------------------------------------------------------------------------
# BM25 + RRF
# ---------------------------------------------------------------------------


class TestBM25Index:
    def test_empty_corpus_safe(self):
        idx = BM25Index([])
        assert idx.search("anything") == []
        assert idx.ranking("anything") == []

    def test_ranks_by_token_match(self):
        idx = BM25Index(
            [
                BM25Doc("a", "trades by token daily"),
                BM25Doc("b", "validator counts"),
                BM25Doc("c", "blocks per day"),
            ]
        )
        ranking = idx.ranking("trades by token")
        assert ranking[0] == "a"
        # 'b' has no overlap, should be filtered out (zero score).
        assert "b" not in ranking

    def test_empty_query_returns_empty(self):
        idx = BM25Index([BM25Doc("a", "anything")])
        assert idx.ranking("") == []

    def test_top_k_respected(self):
        idx = BM25Index(
            [BM25Doc(f"m{i}", f"trades token model {i}") for i in range(20)]
        )
        assert len(idx.ranking("trades", top_k=5)) == 5


class TestColumnBM25Index:
    def test_top_columns_for_model(self):
        idx = ColumnBM25Index(
            [
                ColumnDoc("m", "volume_usd", "volume_usd Float64 USD trade volume"),
                ColumnDoc("m", "fee_usd", "fee_usd Float64 USD fee revenue"),
                ColumnDoc("m", "block_number", "block_number UInt32"),
            ]
        )
        top = idx.top_columns_for_model("m", "fee revenue", top_k=2)
        assert top[0] == "fee_usd"

    def test_unknown_model_returns_empty(self):
        idx = ColumnBM25Index([ColumnDoc("a", "x", "x text")])
        assert idx.top_columns_for_model("missing", "x") == []


class TestRRFFusion:
    def test_basic_fusion(self):
        # Both rankings agree that "a" is best — it should dominate.
        fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]])
        names = [name for name, _ in fused]
        assert names[0] == "a"

    def test_missing_items_no_penalty(self):
        # "x" appears only in the second list, but at rank 1. RRF should
        # still surface it because absence is not a penalty.
        fused = rrf_fuse([["a", "b"], ["x", "a"]])
        names = [name for name, _ in fused]
        assert "x" in names

    def test_top_k_cap(self):
        fused = rrf_fuse([list("abcdef")], top_k=3)
        assert len(fused) == 3


# ---------------------------------------------------------------------------
# search_models: hybrid ranking via the public ManifestLoader API
# ---------------------------------------------------------------------------


class TestSearchModelsHybrid:
    def test_query_promotes_name_match(self, trade_manifest_loader):
        # Under pure token-overlap both 'fct_execution_pools_daily' and
        # 'fct_execution_trades_by_token_daily' had similar overlap. With
        # BM25 + RRF the model whose *name* contains the query terms should
        # be top-1.
        results = trade_manifest_loader.search_models(query="trades by token")
        assert results, "expected at least one result"
        assert results[0]["name"] == "fct_execution_trades_by_token_daily"

    def test_module_filter_still_applies(self, trade_manifest_loader):
        # Tag/module filters must restrict the candidate set BEFORE BM25
        # ranking — confirm a tag mismatch zeroes results.
        results = trade_manifest_loader.search_models(
            query="trades", tags=["nonexistent_tag"]
        )
        assert results == []


# ---------------------------------------------------------------------------
# networkx lineage
# ---------------------------------------------------------------------------


class TestLineageNetworkX:
    def test_upstream_transitive(self, trade_manifest_loader):
        # api_dex_dashboard -> fct_execution_trades_by_token_daily
        #                  -> fct_execution_pools_daily
        # Both ancestors should appear in upstream(), regardless of depth.
        ancestors = trade_manifest_loader.upstream_named("api_dex_dashboard")
        assert "fct_execution_trades_by_token_daily" in ancestors
        assert "fct_execution_pools_daily" in ancestors

    def test_downstream_transitive(self, trade_manifest_loader):
        descendants = trade_manifest_loader.downstream_named(
            "fct_execution_pools_daily"
        )
        assert "fct_execution_trades_by_token_daily" in descendants
        assert "api_dex_dashboard" in descendants

    def test_unknown_model_empty(self, trade_manifest_loader):
        assert trade_manifest_loader.upstream("does_not_exist") == []
        assert trade_manifest_loader.downstream("does_not_exist") == []

    def test_leaf_has_no_descendants(self, trade_manifest_loader):
        assert trade_manifest_loader.downstream_named("api_dex_dashboard") == []


# ---------------------------------------------------------------------------
# Column-scoped schema injection
# ---------------------------------------------------------------------------


class TestColumnScopedSchema:
    def test_narrow_table_full_schema(self):
        # Exactly two columns, threshold defaults to 30 -> full schema, no scoping.
        cols = {
            "id": {"data_type": "UInt64", "description": "primary key"},
            "name": {"data_type": "String", "description": "label"},
        }
        scoped = build_scoped_schema_block(
            "tiny_model",
            cols,
            "anything",
            top_columns_for_model=lambda *a, **k: [],
        )
        assert scoped.was_scoped is False
        assert scoped.total_columns == 2
        assert "id" in scoped.kept_columns and "name" in scoped.kept_columns
        assert "omitted" not in scoped.block

    def test_wide_table_scoped_with_always_keep(self):
        # 50 columns: 1 BM25 hit + always-keep ('day', 'token_address').
        cols = {f"metric_{i}": {"data_type": "Float64", "description": ""} for i in range(48)}
        cols["day"] = {"data_type": "Date", "description": "trading day"}
        cols["token_address"] = {"data_type": "String", "description": "token contract"}

        def fake_top(model, query, top_k):
            return ["metric_3"]

        scoped = build_scoped_schema_block(
            "wide_model",
            cols,
            "metric 3 over time",
            top_columns_for_model=fake_top,
            full_schema_threshold=30,
            top_k=5,
        )
        assert scoped.was_scoped is True
        assert "metric_3" in scoped.kept_columns
        # Always-keep columns made it in even though they weren't in the
        # BM25 result.
        assert "day" in scoped.kept_columns
        assert "token_address" in scoped.kept_columns
        # Dropped the rest.
        assert "metric_42" not in scoped.kept_columns
        assert "omitted" in scoped.block
        assert "get_relevant_columns" in scoped.block

    def test_bm25_empty_falls_back_to_first_k(self):
        # BM25 returns nothing (e.g. query is all stopwords). The helper
        # should still produce a useful block by falling back to the first
        # K columns plus always-keep, so the LLM isn't left with nothing.
        cols = {f"col_{i}": {"data_type": "Float64", "description": ""} for i in range(40)}
        cols["date"] = {"data_type": "Date", "description": ""}
        scoped = build_scoped_schema_block(
            "wide_model",
            cols,
            "the the the",
            top_columns_for_model=lambda *a, **k: [],
            full_schema_threshold=30,
            top_k=5,
        )
        assert scoped.was_scoped is True
        # 'date' is always-keep.
        assert "date" in scoped.kept_columns
        # Some col_* survived via the fallback.
        assert any(c.startswith("col_") for c in scoped.kept_columns)

    def test_anaemic_keep_set_is_padded(self):
        # BM25 returns 1 weak hit, only 1 always-keep matches: without the
        # min-floor pad, we'd return 2 of 50 columns. With the pad we should
        # produce at least `top_k` kept columns.
        cols = {f"col_{i}": {"data_type": "Float64", "description": ""} for i in range(48)}
        cols["day"] = {"data_type": "Date", "description": ""}  # always-keep
        cols["weak_signal"] = {"data_type": "String", "description": ""}

        scoped = build_scoped_schema_block(
            "wide_model",
            cols,
            "weak signal",
            top_columns_for_model=lambda *a, **k: ["weak_signal"],
            full_schema_threshold=30,
            top_k=10,
        )
        assert scoped.was_scoped is True
        assert "day" in scoped.kept_columns
        assert "weak_signal" in scoped.kept_columns
        # Pad fired — we should have at least top_k columns now.
        assert len(scoped.kept_columns) >= 10
