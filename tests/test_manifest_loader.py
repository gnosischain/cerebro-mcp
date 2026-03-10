import json
import os
from unittest.mock import patch, MagicMock
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
    indexes = loader._build_indexes_internal(sample)
    loader._apply_indexes(indexes)
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
        indexes = loader._build_indexes_internal(data)
        loader._apply_indexes(indexes)
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


class TestMultiTokenSearch:
    """Test that multi-word queries are tokenized and matched independently."""

    def test_search_multi_word_query(self, loader_with_sample):
        """Multi-word queries should match on individual tokens."""
        results = loader_with_sample.search_models(
            query="validator daily count"
        )
        assert len(results) >= 1
        assert any(
            r["name"] == "api_consensus_validators_active_daily"
            for r in results
        )

    def test_search_relevance_ordering(self, loader_with_sample):
        """Models matching more tokens should rank higher."""
        results = loader_with_sample.search_models(
            query="execution blocks daily"
        )
        names = [r["name"] for r in results]
        # int_execution_blocks_daily matches all 3 tokens, should be first
        assert names[0] == "int_execution_blocks_daily"

    def test_search_includes_tags(self, loader_with_sample):
        """Tags should be included in search matching."""
        results = loader_with_sample.search_models(query="production")
        assert len(results) >= 1
        # stg_execution__blocks and api_consensus_validators_active_daily
        # both have tag "production"
        names = [r["name"] for r in results]
        assert "stg_execution__blocks" in names

    def test_search_short_words_ignored(self, loader_with_sample):
        """Words shorter than 3 chars should be ignored."""
        results = loader_with_sample.search_models(query="a of in blocks")
        assert len(results) == 2  # Same as searching "blocks"

    def test_search_single_word_still_works(self, loader_with_sample):
        """Single-word queries should behave the same as before."""
        results = loader_with_sample.search_models(query="blocks")
        assert len(results) == 2
        names = [r["name"] for r in results]
        assert "stg_execution__blocks" in names
        assert "int_execution_blocks_daily" in names

    def test_search_underscore_splitting(self, loader_with_sample):
        """Underscores in query should be treated as word separators."""
        results = loader_with_sample.search_models(
            query="execution_blocks"
        )
        assert len(results) == 2

    def test_search_empty_query_returns_all(self, loader_with_sample):
        """Empty query should return all models."""
        results = loader_with_sample.search_models(query="")
        assert len(results) == 3


class TestLowercaseModules:
    """Test that module index is case-insensitive."""

    def test_module_search_case_insensitive(self, loader_with_sample):
        """ESG, esg, Esg should all match the same module."""
        # The sample has 'execution' and 'consensus' modules (lowercase in path)
        results_lower = loader_with_sample.search_models(module="execution")
        results_upper = loader_with_sample.search_models(module="EXECUTION")
        results_mixed = loader_with_sample.search_models(module="Execution")
        assert len(results_lower) == len(results_upper) == len(results_mixed)
        assert len(results_lower) == 2

    def test_module_index_keys_are_lowercase(self, loader_with_sample):
        modules = loader_with_sample.get_modules()
        for key in modules:
            assert key == key.lower(), f"Module key '{key}' is not lowercase"

    def test_get_module_models_case_insensitive(self, loader_with_sample):
        lower = loader_with_sample.get_module_models("execution")
        upper = loader_with_sample.get_module_models("EXECUTION")
        assert len(lower) == len(upper) == 2

    def test_esg_case_normalization(self):
        """Specifically test ESG → esg normalization."""
        loader = ManifestLoader()
        sample = {
            "nodes": {
                "model.gnosis_dbt.esg_model": {
                    "resource_type": "model",
                    "unique_id": "model.gnosis_dbt.esg_model",
                    "name": "esg_model",
                    "description": "ESG metrics",
                    "schema": "dbt",
                    "alias": "esg_model",
                    "path": "ESG/esg_model.sql",  # uppercase in path
                    "tags": [],
                    "config": {"materialized": "view"},
                    "columns": {},
                    "raw_code": "",
                    "compiled_code": "",
                },
            },
            "sources": {},
            "parent_map": {},
            "child_map": {},
        }
        indexes = loader._build_indexes_internal(sample)
        loader._apply_indexes(indexes)
        loader._loaded = True

        # Should be indexed as 'esg' not 'ESG'
        assert "esg" in loader.get_modules()
        assert "ESG" not in loader.get_modules()

        # Both casings should find the model
        assert len(loader.search_models(module="ESG")) == 1
        assert len(loader.search_models(module="esg")) == 1


class TestConditionalGET:
    """Test manifest conditional GET refresh logic."""

    def test_reload_no_url_returns_false(self):
        """With no URL configured, reload should do nothing."""
        loader = ManifestLoader()
        with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
            mock_settings.DBT_MANIFEST_URL = None
            changed, error = loader.reload_if_changed()
            assert changed is False
            assert error is None

    def test_reload_304_not_modified(self):
        """304 response should not update indexes."""
        loader = ManifestLoader()
        loader._etag = '"abc123"'
        loader._loaded = True
        loader._content_hash = "oldhash"

        mock_resp = MagicMock()
        mock_resp.status_code = 304

        with patch("cerebro_mcp.manifest_loader.requests.get", return_value=mock_resp):
            with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
                mock_settings.DBT_MANIFEST_URL = "http://test.com/manifest.json"
                changed, error = loader.reload_if_changed()

        assert changed is False
        assert loader._content_hash == "oldhash"

    def test_reload_200_new_content_updates_indexes(self):
        """200 response with new content should update indexes."""
        loader = ManifestLoader()
        loader._loaded = True
        loader._content_hash = "oldhash"

        new_data = {
            "nodes": {
                "model.test.new_model": {
                    "resource_type": "model",
                    "unique_id": "model.test.new_model",
                    "name": "new_model",
                    "description": "A new model",
                    "schema": "dbt",
                    "alias": "new_model",
                    "path": "test/new_model.sql",
                    "tags": [],
                    "config": {"materialized": "view"},
                    "columns": {},
                    "raw_code": "",
                    "compiled_code": "",
                },
            },
            "sources": {},
            "parent_map": {},
            "child_map": {},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = new_data
        mock_resp.headers = {"ETag": '"new_etag"', "Last-Modified": "Thu, 01 Jan 2026"}

        with patch("cerebro_mcp.manifest_loader.requests.get", return_value=mock_resp):
            with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
                mock_settings.DBT_MANIFEST_URL = "http://test.com/manifest.json"
                changed, error = loader.reload_if_changed()

        assert changed is True
        assert error is None
        assert loader.get_model("new_model") is not None
        assert loader._etag == '"new_etag"'
        assert loader._content_hash != "oldhash"

    def test_reload_error_keeps_stale_data(self):
        """Network error should keep existing data and set error."""
        loader = ManifestLoader()
        loader._loaded = True
        loader._content_hash = "existing"

        # Set up some existing data
        sample = {
            "nodes": {
                "model.test.existing": {
                    "resource_type": "model",
                    "unique_id": "model.test.existing",
                    "name": "existing_model",
                    "description": "",
                    "schema": "dbt",
                    "alias": "existing_model",
                    "path": "test/existing.sql",
                    "tags": [],
                    "config": {"materialized": "view"},
                    "columns": {},
                    "raw_code": "",
                    "compiled_code": "",
                },
            },
            "sources": {},
            "parent_map": {},
            "child_map": {},
        }
        indexes = loader._build_indexes_internal(sample)
        loader._apply_indexes(indexes)

        with patch(
            "cerebro_mcp.manifest_loader.requests.get",
            side_effect=Exception("Connection timeout"),
        ):
            with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
                mock_settings.DBT_MANIFEST_URL = "http://test.com/manifest.json"
                changed, error = loader.reload_if_changed()

        assert changed is False
        assert "Connection timeout" in loader.last_refresh_error
        # Existing data should still be there
        assert loader.get_model("existing_model") is not None

    def test_reload_uses_short_timeout(self):
        """Conditional GET should use 1s timeout, not 30s."""
        loader = ManifestLoader()
        loader._loaded = True

        mock_resp = MagicMock()
        mock_resp.status_code = 304

        with patch(
            "cerebro_mcp.manifest_loader.requests.get", return_value=mock_resp
        ) as mock_get:
            with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
                mock_settings.DBT_MANIFEST_URL = "http://test.com/manifest.json"
                loader.reload_if_changed()

        # Verify the timeout was 1 second
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["timeout"] == 1

    def test_content_hash_dedup(self):
        """Same content should not trigger index rebuild."""
        loader = ManifestLoader()
        loader._loaded = True

        data = {
            "nodes": {},
            "sources": {},
            "parent_map": {},
            "child_map": {},
        }
        content_hash = loader._hash_bytes(
            json.dumps(data, sort_keys=True).encode()
        )
        loader._content_hash = content_hash

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = data
        mock_resp.headers = {}

        with patch("cerebro_mcp.manifest_loader.requests.get", return_value=mock_resp):
            with patch("cerebro_mcp.manifest_loader.settings") as mock_settings:
                mock_settings.DBT_MANIFEST_URL = "http://test.com/manifest.json"
                changed, error = loader.reload_if_changed()

        assert changed is False
        assert error is None

    def test_status_properties(self, loader_with_sample):
        """Test read-only status properties."""
        assert loader_with_sample.model_count == 3
        assert loader_with_sample.content_hash is None  # Not set via _build_indexes
        assert loader_with_sample.last_refresh_error is None
