"""Tests for the model-lineage backend: get_subgraph + column lineage.

Backs Phase A (`ManifestLoader.get_subgraph`) and Phase B
(`loaders.column_lineage.get_column_lineage`) of the Model Lineage Explorer.
Uses a self-contained sample manifest (same pattern as
``test_manifest_loader.py``) so the tests do not depend on a real dbt build.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.loaders import column_lineage as col_mod
from cerebro_mcp.loaders.manifest import ManifestLoader


def _sample_manifest() -> dict:
    return {
        "nodes": {
            "model.gnosis_dbt.stg_execution__blocks": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.stg_execution__blocks",
                "name": "stg_execution__blocks",
                "description": "Staging blocks",
                "schema": "dbt",
                "alias": "stg_execution__blocks",
                "path": "execution/blocks/staging/stg_execution__blocks.sql",
                "tags": ["execution", "staging"],
                "config": {"materialized": "view"},
                "columns": {
                    "block_number": {"data_type": "UInt32", "description": "n"},
                    "block_timestamp": {"data_type": "DateTime", "description": "t"},
                },
                "compiled_code": "SELECT block_number, block_timestamp FROM execution.blocks",
                "depends_on": {"nodes": ["source.gnosis_dbt.execution.blocks"]},
            },
            "model.gnosis_dbt.int_execution_blocks_daily": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.int_execution_blocks_daily",
                "name": "int_execution_blocks_daily",
                "description": "Daily blocks",
                "schema": "dbt",
                "alias": "int_execution_blocks_daily",
                "path": "execution/blocks/intermediate/int_execution_blocks_daily.sql",
                "tags": ["execution"],
                "config": {"materialized": "table"},
                "columns": {
                    "bn": {"data_type": "UInt32", "description": "block num"},
                },
                "compiled_code": (
                    "SELECT block_number AS bn, block_timestamp "
                    "FROM dbt.stg_execution__blocks"
                ),
                "depends_on": {"nodes": ["model.gnosis_dbt.stg_execution__blocks"]},
            },
            "model.gnosis_dbt.fct_execution_blocks_summary": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.fct_execution_blocks_summary",
                "name": "fct_execution_blocks_summary",
                "description": "Summary",
                "schema": "dbt",
                "alias": "fct_execution_blocks_summary",
                "path": "execution/blocks/marts/fct_execution_blocks_summary.sql",
                "tags": ["execution", "marts"],
                "config": {"materialized": "table"},
                "columns": {"bn": {"data_type": "UInt32"}},
                # Intentionally unparseable (macro residue) → forces fallback.
                "compiled_code": "SELECT {{ broken( FROM ",
                "depends_on": {
                    "nodes": ["model.gnosis_dbt.int_execution_blocks_daily"]
                },
            },
            "model.gnosis_dbt.api_consensus_validators": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.api_consensus_validators",
                "name": "api_consensus_validators",
                "description": "Validators",
                "schema": "dbt",
                "alias": "api_consensus_validators",
                "path": "consensus/marts/api_consensus_validators.sql",
                "tags": ["consensus"],
                "config": {"materialized": "view"},
                "columns": {},
                "compiled_code": "",
                "depends_on": {"nodes": []},
            },
        },
        "sources": {
            "source.gnosis_dbt.execution.blocks": {
                "resource_type": "source",
                "schema": "execution",
                "name": "blocks",
                "identifier": "blocks",
                "description": "Raw blocks",
                "columns": {"block_number": {"data_type": "UInt32"}},
            }
        },
        "parent_map": {
            "model.gnosis_dbt.stg_execution__blocks": [
                "source.gnosis_dbt.execution.blocks"
            ],
            "model.gnosis_dbt.int_execution_blocks_daily": [
                "model.gnosis_dbt.stg_execution__blocks"
            ],
            "model.gnosis_dbt.fct_execution_blocks_summary": [
                "model.gnosis_dbt.int_execution_blocks_daily"
            ],
        },
        "child_map": {
            "source.gnosis_dbt.execution.blocks": [
                "model.gnosis_dbt.stg_execution__blocks"
            ],
            "model.gnosis_dbt.stg_execution__blocks": [
                "model.gnosis_dbt.int_execution_blocks_daily"
            ],
            "model.gnosis_dbt.int_execution_blocks_daily": [
                "model.gnosis_dbt.fct_execution_blocks_summary"
            ],
        },
    }


@pytest.fixture
def loader() -> ManifestLoader:
    ld = ManifestLoader()
    indexes = ld._build_indexes_internal(_sample_manifest())
    ld._apply_indexes(indexes)
    ld._loaded = True
    return ld


@pytest.fixture
def patched_manifest(loader, monkeypatch):
    """Point the module-level `manifest` singleton at the sample loader."""
    monkeypatch.setattr(col_mod, "manifest", loader)
    return loader


# --------------------------------------------------------------------------
# Phase A — get_subgraph
# --------------------------------------------------------------------------


class TestGetSubgraph:
    def test_unknown_seed_returns_error(self, loader):
        res = loader.get_subgraph(seed="does_not_exist")
        assert res["error"]
        assert res["nodes"] == []
        assert res["edges"] == []

    def test_depth_zero_is_seed_only(self, loader):
        res = loader.get_subgraph(
            seed="int_execution_blocks_daily", direction="both", depth=0
        )
        assert len(res["nodes"]) == 1
        assert res["nodes"][0]["name"] == "int_execution_blocks_daily"
        assert res["edges"] == []

    def test_upstream_one_hop(self, loader):
        res = loader.get_subgraph(
            seed="int_execution_blocks_daily", direction="upstream", depth=1
        )
        names = {n["name"] for n in res["nodes"]}
        assert "int_execution_blocks_daily" in names
        assert "stg_execution__blocks" in names
        # downstream neighbour must NOT appear for an upstream-only traversal
        assert "fct_execution_blocks_summary" not in names

    def test_downstream_one_hop(self, loader):
        res = loader.get_subgraph(
            seed="int_execution_blocks_daily", direction="downstream", depth=1
        )
        names = {n["name"] for n in res["nodes"]}
        assert "fct_execution_blocks_summary" in names
        assert "stg_execution__blocks" not in names

    def test_depth_bounds_frontier(self, loader):
        shallow = loader.get_subgraph(
            seed="fct_execution_blocks_summary", direction="upstream", depth=1
        )
        deep = loader.get_subgraph(
            seed="fct_execution_blocks_summary", direction="upstream", depth=3
        )
        assert len(deep["nodes"]) > len(shallow["nodes"])
        deep_names = {n["name"] for n in deep["nodes"]}
        # depth 3 upstream reaches the raw source
        assert "stg_execution__blocks" in deep_names

    def test_tag_filter_drops_non_matching_models(self, loader):
        res = loader.get_subgraph(
            seed="int_execution_blocks_daily",
            direction="both",
            depth=2,
            tags=["marts"],
        )
        names = {n["name"] for n in res["nodes"]}
        # seed always retained even though it lacks the "marts" tag
        assert "int_execution_blocks_daily" in names
        # carries "marts"
        assert "fct_execution_blocks_summary" in names
        # staging model lacks "marts" → dropped
        assert "stg_execution__blocks" not in names

    def test_include_kinds_filters_sources(self, loader):
        res = loader.get_subgraph(
            seed="stg_execution__blocks",
            direction="upstream",
            depth=1,
            include_kinds=["model"],
        )
        kinds = {n["kind"] for n in res["nodes"]}
        assert "source" not in kinds

    def test_edges_only_between_kept_nodes(self, loader):
        res = loader.get_subgraph(
            seed="int_execution_blocks_daily", direction="both", depth=1
        )
        kept_ids = {n["id"] for n in res["nodes"]}
        for edge in res["edges"]:
            assert edge["source"] in kept_ids
            assert edge["target"] in kept_ids


# --------------------------------------------------------------------------
# Phase B — column lineage
# --------------------------------------------------------------------------


class TestColumnLineage:
    def test_manifest_not_loaded_warns(self, monkeypatch):
        ld = ManifestLoader()  # not loaded
        monkeypatch.setattr(col_mod, "manifest", ld)
        res = col_mod.get_column_lineage("int_execution_blocks_daily", "bn")
        assert res["level"] == "model"
        assert res["edges"] == []
        assert res["warnings"]

    def test_unknown_model_warns(self, patched_manifest):
        res = col_mod.get_column_lineage("nope", "x")
        assert res["edges"] == []
        assert any("not found" in w for w in res["warnings"])

    @pytest.mark.skipif(
        not col_mod._SQLGLOT_AVAILABLE, reason="sqlglot not installed"
    )
    def test_column_level_edge_resolved(self, patched_manifest):
        res = col_mod.get_column_lineage(
            "int_execution_blocks_daily", "bn", direction="upstream", depth=1
        )
        assert res["level"] == "column"
        assert res["edges"], "expected at least one column edge"
        edge = res["edges"][0]
        assert edge["target_model"] == "int_execution_blocks_daily"
        assert edge["target_column"] == "bn"
        assert edge["source_model"] == "stg_execution__blocks"
        assert edge["source_column"] == "block_number"

    def test_unparseable_sql_falls_back_to_model_level(self, patched_manifest):
        res = col_mod.get_column_lineage(
            "fct_execution_blocks_summary", "bn", direction="upstream", depth=1
        )
        assert res["level"] == "model"
        assert res["warnings"]
        # model-level fallback yields the manifest upstream edge
        assert any(
            e["source_model"] == "int_execution_blocks_daily"
            for e in res["edges"]
        )

    def test_downstream_uses_model_level(self, patched_manifest):
        res = col_mod.get_column_lineage(
            "stg_execution__blocks", "block_number", direction="downstream"
        )
        assert res["level"] == "model"
        assert any(
            e["target_model"] == "int_execution_blocks_daily"
            for e in res["edges"]
        )


class TestRawSqlRendering:
    """Parse-only manifests carry Jinja raw_code and empty compiled_code.

    Mirrors the real gnosis_dbt manifest: column lineage must still resolve by
    rendering ``{{ ref() }}`` / ``{{ source() }}`` to physical relations.
    """

    @pytest.fixture
    def raw_only_loader(self) -> ManifestLoader:
        m = _sample_manifest()
        # Wipe compiled_code; replace bare relations with Jinja refs.
        m["nodes"]["model.gnosis_dbt.int_execution_blocks_daily"][
            "compiled_code"
        ] = ""
        m["nodes"]["model.gnosis_dbt.int_execution_blocks_daily"][
            "raw_code"
        ] = (
            "{{ config(materialized='table') }}\n"
            "SELECT block_number AS bn, block_timestamp "
            "FROM {{ ref('stg_execution__blocks') }}"
        )
        ld = ManifestLoader()
        indexes = ld._build_indexes_internal(m)
        ld._apply_indexes(indexes)
        ld._loaded = True
        return ld

    @pytest.mark.skipif(
        not col_mod._SQLGLOT_AVAILABLE, reason="sqlglot not installed"
    )
    def test_ref_rendered_when_compiled_empty(
        self, raw_only_loader, monkeypatch
    ):
        monkeypatch.setattr(col_mod, "manifest", raw_only_loader)
        res = col_mod.get_column_lineage(
            "int_execution_blocks_daily", "bn", direction="upstream", depth=1
        )
        assert res["level"] == "column"
        edge = res["edges"][0]
        assert edge["source_model"] == "stg_execution__blocks"
        assert edge["source_column"] == "block_number"

    def test_render_strips_config_and_resolves_ref(self, patched_manifest):
        sql = col_mod._render_raw_sql(
            "int_execution_blocks_daily",
            "{{ config(materialized='view') }}\n"
            "SELECT a FROM {{ ref('stg_execution__blocks') }}",
        )
        assert "config" not in sql
        # ref rewritten to schema.alias
        assert "dbt.stg_execution__blocks" in sql
