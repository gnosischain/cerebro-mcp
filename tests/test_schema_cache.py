import time
import pytest
from unittest.mock import MagicMock, patch
from cerebro_mcp.clickhouse_client import ClickHouseManager


class TestSchemaCache:
    """Test the TTL cache in ClickHouseManager."""

    def setup_method(self):
        self.ch = ClickHouseManager()

    def test_cache_set_and_get(self):
        result = {"columns": ["a"], "rows": [[1]]}
        self.ch._cache_set("test_key", result)
        cached = self.ch._cache_get("test_key")
        assert cached == result

    def test_cache_miss_returns_none(self):
        assert self.ch._cache_get("nonexistent") is None

    def test_cache_ttl_expiry(self):
        result = {"columns": ["a"], "rows": [[1]]}
        self.ch._cache_set("test_key", result)

        # Simulate TTL expiry by backdating the timestamp
        ts, data = self.ch._schema_cache["test_key"]
        self.ch._schema_cache["test_key"] = (
            ts - self.ch.SCHEMA_CACHE_TTL - 1,
            data,
        )

        assert self.ch._cache_get("test_key") is None
        assert "test_key" not in self.ch._schema_cache

    def test_cache_key_isolation(self):
        result_a = {"columns": ["a"], "rows": [[1]]}
        result_b = {"columns": ["b"], "rows": [[2]]}
        self.ch._cache_set("key_a", result_a)
        self.ch._cache_set("key_b", result_b)

        assert self.ch._cache_get("key_a") == result_a
        assert self.ch._cache_get("key_b") == result_b

    def test_pseudo_lru_eviction(self):
        """When cache is full, the oldest entry should be evicted."""
        # Fill cache to max
        for i in range(self.ch.SCHEMA_CACHE_MAX_ENTRIES):
            self.ch._cache_set(f"key_{i}", {"data": i})

        assert len(self.ch._schema_cache) == self.ch.SCHEMA_CACHE_MAX_ENTRIES

        # Add one more — should evict key_0 (oldest)
        self.ch._cache_set("new_key", {"data": "new"})
        assert len(self.ch._schema_cache) == self.ch.SCHEMA_CACHE_MAX_ENTRIES
        assert self.ch._cache_get("key_0") is None
        assert self.ch._cache_get("new_key") == {"data": "new"}
        # key_1 should still exist
        assert self.ch._cache_get("key_1") == {"data": 1}

    def test_schema_cache_size_property(self):
        assert self.ch.schema_cache_size == 0
        self.ch._cache_set("a", {"data": 1})
        self.ch._cache_set("b", {"data": 2})
        assert self.ch.schema_cache_size == 2

    def test_execute_raw_cached_returns_cached(self):
        """execute_raw_cached should return cached result on hit."""
        result = {"columns": ["x"], "rows": [[42]]}
        self.ch._cache_set("my_key", result)

        # Should return cached without calling execute_raw
        with patch.object(self.ch, "execute_raw") as mock_raw:
            cached = self.ch.execute_raw_cached(
                "SELECT 1", "dbt", "my_key"
            )
            mock_raw.assert_not_called()
            assert cached == result

    def test_execute_raw_cached_queries_on_miss(self):
        """execute_raw_cached should query and cache on miss."""
        result = {"columns": ["x"], "rows": [[99]]}

        with patch.object(self.ch, "execute_raw", return_value=result) as mock_raw:
            got = self.ch.execute_raw_cached(
                "SELECT 1", "dbt", "miss_key", parameters={"p": "v"}
            )
            mock_raw.assert_called_once_with(
                "SELECT 1", "dbt", parameters={"p": "v"}
            )
            assert got == result
            # Should now be cached
            assert self.ch._cache_get("miss_key") == result
