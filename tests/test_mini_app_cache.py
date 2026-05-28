"""Tests for the mini-app dataset cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import (
    CachedDataset,
    MiniAppCache,
    get_cache,
    make_cache_key,
    reset_cache_for_tests,
)
from cerebro_mcp.models.mini_app import DatasetStats
from cerebro_mcp.tools.visualization import mini_apps
from cerebro_mcp.tools.visualization.mini_apps import load_bounded_dataset


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_same_inputs_yield_same_key():
    a = make_cache_key("SELECT 1", "dbt", {"x": 1}, "exact_bounded")
    b = make_cache_key("SELECT 1", "dbt", {"x": 1}, "exact_bounded")
    assert a == b


def test_mode_is_part_of_key():
    a = make_cache_key("SELECT 1", "dbt", None, "exact_bounded")
    b = make_cache_key("SELECT 1", "dbt", None, "random_sample")
    assert a != b


def test_parameters_order_does_not_matter():
    a = make_cache_key("SELECT 1", "dbt", {"x": 1, "y": 2}, "exact_bounded")
    b = make_cache_key("SELECT 1", "dbt", {"y": 2, "x": 1}, "exact_bounded")
    assert a == b


def test_database_changes_key():
    a = make_cache_key("SELECT 1", "dbt", None, "exact_bounded")
    b = make_cache_key("SELECT 1", "consensus", None, "exact_bounded")
    assert a != b


# ---------------------------------------------------------------------------
# In-memory cache behaviour
# ---------------------------------------------------------------------------


def _make_dataset() -> CachedDataset:
    stats = DatasetStats(
        row_count=3, rows_returned=3, mode="exact_bounded",
        sample_source_rows=3, elapsed_seconds=0.0, warnings=[],
    )
    return CachedDataset(
        columns=["a"], column_types=["int"], rows=[[1], [2], [3]],
        stats=stats, sql="SELECT a", database="dbt", parameters=None,
    )


def test_put_then_get_returns_dataset():
    cache = MiniAppCache()
    cache.put("k", _make_dataset())
    assert cache.get("k") is not None


def test_expired_entry_is_dropped():
    cache = MiniAppCache(ttl=timedelta(seconds=1))
    ds = _make_dataset()
    cache.put("k", ds)
    # Force expiry
    ds.expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert cache.get("k") is None


def test_max_entries_evicts_oldest():
    cache = MiniAppCache(max_entries=2)
    for i in range(3):
        cache.put(f"k{i}", _make_dataset())
    assert cache.size() == 2


# ---------------------------------------------------------------------------
# Sampler ↔ cache integration
# ---------------------------------------------------------------------------


class CountingCH:
    def __init__(self, total: int):
        self.total = total
        self.fetch_calls = 0

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None):
        if "count()" in sql:
            return ExecutedQuery(
                sql=sql, executed_sql=sql, database=database, columns=["c"],
                rows=[[self.total]], row_count=1, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
            )
        self.fetch_calls += 1
        n = min(requested_max_rows, self.total)
        rows = [[i] for i in range(n)]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database, columns=["id"],
            rows=rows, row_count=n, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )


def test_repeated_load_hits_cache():
    """Identical (sql, database, params) loads should fetch ClickHouse only once."""
    ch = CountingCH(total=500)
    sql = "SELECT * FROM tiny"
    load_bounded_dataset(ch, sql, database="dbt")
    first_fetches = ch.fetch_calls
    load_bounded_dataset(ch, sql, database="dbt")
    assert ch.fetch_calls == first_fetches  # cache hit


def test_different_databases_get_separate_cache_entries():
    ch = CountingCH(total=500)
    sql = "SELECT * FROM tiny"
    load_bounded_dataset(ch, sql, database="dbt")
    load_bounded_dataset(ch, sql, database="consensus")
    assert ch.fetch_calls == 2
