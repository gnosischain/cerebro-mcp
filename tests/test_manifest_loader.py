import json
import os
import pytest
from cerebro_mcp.manifest_loader import ManifestLoader


# Try to load real manifest for integration tests
MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "dbt-cerebro",
    "target",
    "manifest.json",
)
HAS_MANIFEST = os.path.exists(MANIFEST_PATH)


@pytest.fixture
def loader_with_sample():
    """Create a loader with a minimal sample manifest."""
    loader = ManifestLoader()
    sample = {
        "nodes": {
            "model.gnosis_dbt.stg_execution__blocks": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.stg_execution__blocks",
                "name": "stg_execution__blocks",
                "description": "Staging model for execution layer blocks",
                "schema": "dbt",
                "alias": "stg_execution__blocks",
                "path": "execution/blocks/staging/stg_execution__blocks.sql",
                "tags": ["execution", "production"],
                "config": {"materialized": "view"},
                "columns": {
                    "block_number": {
                        "data_type": "UInt32",
                        "description": "The block number",
                    },
                    "block_timestamp": {
                        "data_type": "DateTime",
                        "description": "Block timestamp in UTC",
                    },
                },
                "raw_code": "SELECT * FROM {{ source('execution', 'blocks') }}",
                "compiled_code": "SELECT * FROM execution.blocks",
                "depends_on": {"nodes": ["source.gnosis_dbt.execution.blocks"]},
            },
            "model.gnosis_dbt.int_execution_blocks_daily": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.int_execution_blocks_daily",
                "name": "int_execution_blocks_daily",
                "description": "Daily block aggregation metrics",
                "schema": "dbt",
                "alias": "int_execution_blocks_daily",
                "path": "execution/blocks/intermediate/int_execution_blocks_daily.sql",
                "tags": ["execution"],
                "config": {"materialized": "table"},
                "columns": {},
                "raw_code": "SELECT toDate(block_timestamp) AS day...",
                "compiled_code": "",
                "depends_on": {
                    "nodes": ["model.gnosis_dbt.stg_execution__blocks"]
                },
            },
            "model.gnosis_dbt.api_consensus_validators_active_daily": {
                "resource_type": "model",
                "unique_id": "model.gnosis_dbt.api_consensus_validators_active_daily",
                "name": "api_consensus_validators_active_daily",
                "description": "Daily active validator count",
                "schema": "dbt",
                "alias": "api_consensus_validators_active_daily",
                "path": "consensus/marts/api_consensus_validators_active_daily.sql",
                "tags": ["consensus", "production", "validators"],
                "config": {"materialized": "view"},
                "columns": {},
                "raw_code": "",
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
                "description": "Raw execution layer blocks",
                "columns": {
                    "block_number": {
                        "data_type": "UInt32",
                        "description": "Block number",
                    }
                },
            }
        },
        "parent_map": {
            "model.gnosis_dbt.stg_execution__blocks": [
                "source.gnosis_dbt.execution.blocks"
            ],
            "model.gnosis_dbt.int_execution_blocks_daily": [
                "model.gnosis_dbt.stg_execution__blocks"
            ],
        },
        "child_map": {
            "source.gnosis_dbt.execution.blocks": [
                "model.gnosis_dbt.stg_execution__blocks"
            ],
            "model.gnosis_dbt.stg_execution__blocks": [
                "model.gnosis_dbt.int_execution_blocks_daily"
            ],
        },
    }
    loader._build_indexes(sample)
    loader._loaded = True
    return loader


class TestManifestLoader:
    def test_model_count(self, loader_with_sample):
        assert len(loader_with_sample.get_all_model_names()) == 3

    def test_get_model(self, loader_with_sample):
        model = loader_with_sample.get_model("stg_execution__blocks")
        assert model is not None
        assert model["name"] == "stg_execution__blocks"

    def test_get_model_not_found(self, loader_with_sample):
        model = loader_with_sample.get_model("nonexistent")
        assert model is None

    def test_search_by_name(self, loader_with_sample):
        results = loader_with_sample.search_models(query="blocks")
        assert len(results) == 2
        names = [r["name"] for r in results]
        assert "stg_execution__blocks" in names
        assert "int_execution_blocks_daily" in names

    def test_search_by_description(self, loader_with_sample):
        results = loader_with_sample.search_models(query="validator")
        assert len(results) == 1
        assert results[0]["name"] == "api_consensus_validators_active_daily"

    def test_search_by_tag(self, loader_with_sample):
        results = loader_with_sample.search_models(tags=["consensus"])
        assert len(results) == 1
        assert results[0]["name"] == "api_consensus_validators_active_daily"

    def test_search_by_module(self, loader_with_sample):
        results = loader_with_sample.search_models(module="execution")
        assert len(results) == 2

    def test_search_combined_filters(self, loader_with_sample):
        results = loader_with_sample.search_models(
            query="blocks", module="execution", tags=["production"]
        )
        assert len(results) == 1
        assert results[0]["name"] == "stg_execution__blocks"

    def test_get_model_details(self, loader_with_sample):
        details = loader_with_sample.get_model_details("stg_execution__blocks")
        assert details is not None
        assert details["name"] == "stg_execution__blocks"
        assert details["table_name"] == "dbt.stg_execution__blocks"
        assert "block_number" in details["columns"]
        assert details["columns"]["block_number"]["data_type"] == "UInt32"
        assert len(details["upstream"]) > 0

    def test_get_model_details_not_found(self, loader_with_sample):
        details = loader_with_sample.get_model_details("nonexistent")
        assert details is None

    def test_get_lineage_upstream(self, loader_with_sample):
        lineage = loader_with_sample.get_lineage(
            "int_execution_blocks_daily", direction="upstream", depth=2
        )
        assert "upstream" in lineage
        names = [n["name"] for n in lineage["upstream"]]
        assert "stg_execution__blocks" in names

    def test_get_lineage_downstream(self, loader_with_sample):
        lineage = loader_with_sample.get_lineage(
            "stg_execution__blocks", direction="downstream", depth=1
        )
        assert "downstream" in lineage
        names = [n["name"] for n in lineage["downstream"]]
        assert "int_execution_blocks_daily" in names

    def test_get_modules(self, loader_with_sample):
        modules = loader_with_sample.get_modules()
        assert "execution" in modules
        assert modules["execution"] == 2
        assert "consensus" in modules
        assert modules["consensus"] == 1

    def test_get_sources_for_database(self, loader_with_sample):
        sources = loader_with_sample.get_sources_for_database("execution")
        assert len(sources) == 1
        assert sources[0]["name"] == "blocks"

    def test_get_sources_empty(self, loader_with_sample):
        sources = loader_with_sample.get_sources_for_database("nonexistent")
        assert len(sources) == 0


@pytest.mark.skipif(not HAS_MANIFEST, reason="Real manifest not available")
class TestManifestLoaderReal:
    """Integration tests using the real dbt-cerebro manifest."""

    @pytest.fixture
    def real_loader(self):
        loader = ManifestLoader()
        with open(MANIFEST_PATH, "r") as f:
            data = json.load(f)
        loader._build_indexes(data)
        loader._loaded = True
        return loader

    def test_loads_many_models(self, real_loader):
        count = len(real_loader.get_all_model_names())
        assert count > 300  # Should have ~400 models

    def test_search_execution_models(self, real_loader):
        results = real_loader.search_models(module="execution")
        assert len(results) > 100

    def test_search_consensus_models(self, real_loader):
        results = real_loader.search_models(module="consensus")
        assert len(results) > 30

    def test_modules_exist(self, real_loader):
        modules = real_loader.get_modules()
        assert "execution" in modules
        assert "consensus" in modules
