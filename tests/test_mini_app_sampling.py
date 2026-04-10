"""Tests for the bounded-dataset sampler in cerebro_mcp.tools.mini_apps."""

from __future__ import annotations

import re

import pytest

from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.mini_apps import (
    HARD_TOTAL_LIMIT,
    MiniAppQueryError,
    PREVIEW_ROW_CAP,
    SAMPLE_TARGET,
    load_bounded_dataset,
)


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


class StubCH:
    """Configurable stub for ClickHouseManager.run_query.

    Each call records ``(sql, parameters)``. The behaviour is driven by
    callbacks keyed off whether the SQL is the wrapping ``count()`` query
    or a data fetch.
    """

    def __init__(self, total: int, fetch_handler=None, count_value=None):
        self.total = total
        self.calls: list[tuple[str, dict | None]] = []
        self.fetch_handler = fetch_handler or self._default_fetch
        self._count_value = count_value if count_value is not None else total

    def _default_fetch(self, sql, parameters, requested_max_rows):
        # Returns roughly `requested_max_rows` rows of fake data.
        n = min(requested_max_rows, self.total)
        rows = [[i, f"row_{i}", float(i)] for i in range(n)]
        return ExecutedQuery(
            sql=sql,
            executed_sql=sql,
            database="dbt",
            columns=["id", "label", "value"],
            rows=rows,
            row_count=n,
            elapsed_seconds=0.01,
            fetch_mode="rows",
            warnings=[],
        )

    def run_query(
        self,
        sql,
        database="dbt",
        requested_max_rows=100,
        audience="tool",
        fetch_mode="auto",
        parameters=None,
    ):
        self.calls.append((sql, parameters))
        if "count()" in sql:
            return ExecutedQuery(
                sql=sql,
                executed_sql=sql,
                database=database,
                columns=["c"],
                rows=[[self._count_value]],
                row_count=1,
                elapsed_seconds=0.0,
                fetch_mode="rows",
                warnings=[],
            )
        return self.fetch_handler(sql, parameters, requested_max_rows)


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------


def test_small_dataset_uses_exact_bounded_mode():
    ch = StubCH(total=500)
    dataset = load_bounded_dataset(ch, "SELECT * FROM tiny")
    assert dataset.stats.mode == "exact_bounded"
    assert dataset.stats.row_count == 500
    assert dataset.stats.sample_source_rows == 500
    assert dataset.stats.warnings == []


def test_large_dataset_uses_random_sample_mode():
    ch = StubCH(total=10_000)
    dataset = load_bounded_dataset(ch, "SELECT * FROM big")
    assert dataset.stats.mode == "random_sample"
    assert dataset.stats.sample_source_rows == 10_000
    assert dataset.stats.row_count >= SAMPLE_TARGET // 2
    assert any("approximate random sample" in w for w in dataset.stats.warnings)


def test_random_sample_retries_with_wider_cutoff():
    """First sample attempt under-samples; second attempt with a wider cutoff hits target."""
    attempts = {"n": 0}

    def handler(sql, parameters, requested_max_rows):
        if "count()" in sql:
            return ExecutedQuery(sql=sql, executed_sql=sql, database="dbt", columns=["c"], rows=[[10000]], row_count=1, elapsed_seconds=0.0, fetch_mode="rows", warnings=[])
        attempts["n"] += 1
        n = 100 if attempts["n"] == 1 else 2100
        rows = [[i, f"r_{i}"] for i in range(n)]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database="dbt", columns=["id", "label"], rows=rows,
            row_count=n, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )

    ch = StubCH(total=10_000, fetch_handler=handler)
    dataset = load_bounded_dataset(ch, "SELECT * FROM tricky")
    assert dataset.stats.mode == "random_sample"
    assert attempts["n"] == 2  # one undersample, one successful retry


def test_falls_back_to_preview_only_when_sampling_fails():
    """Both sampling attempts raise → preview_only with the standard warning."""

    def handler(sql, parameters, requested_max_rows):
        if "_sample" in sql:
            raise RuntimeError("hash function not supported in this clickhouse build")
        # Plain LIMIT wrap for the preview path
        n = min(requested_max_rows, 200)
        rows = [[i] for i in range(n)]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database="dbt", columns=["id"], rows=rows,
            row_count=n, elapsed_seconds=0.0, fetch_mode="rows", warnings=[],
        )

    ch = StubCH(total=100_000, fetch_handler=handler)
    dataset = load_bounded_dataset(ch, "SELECT * FROM hostile")
    assert dataset.stats.mode == "preview_only"
    assert dataset.stats.row_count <= PREVIEW_ROW_CAP
    assert any("Preview only" in w for w in dataset.stats.warnings)


def test_hard_cap_short_circuits_to_preview_only():
    """When count exceeds HARD_TOTAL_LIMIT we never run the bucket query."""
    ch = StubCH(total=HARD_TOTAL_LIMIT + 1, count_value=HARD_TOTAL_LIMIT + 1)
    dataset = load_bounded_dataset(ch, "SELECT * FROM enormous")
    assert dataset.stats.mode == "preview_only"
    # Confirm we never executed a hash-bucket sample
    sample_calls = [c for c in ch.calls if "_sample" in c[0]]
    assert sample_calls == []


# ---------------------------------------------------------------------------
# SQL hygiene
# ---------------------------------------------------------------------------


def test_sampler_never_uses_order_by_rand():
    """The deterministic hash bucket SQL must never contain ORDER BY rand()."""
    ch = StubCH(total=10_000)
    load_bounded_dataset(ch, "SELECT * FROM big")
    for sql, _params in ch.calls:
        assert not re.search(r"order\s+by\s+rand", sql, re.IGNORECASE), sql


def test_count_query_wraps_user_sql():
    ch = StubCH(total=500)
    load_bounded_dataset(ch, "SELECT 1 AS c")
    count_calls = [c for c in ch.calls if "count()" in c[0]]
    assert len(count_calls) == 1
    assert "_ml_count" in count_calls[0][0]


def test_zero_total_falls_back_to_preview_only():
    ch = StubCH(total=0, count_value=0)
    dataset = load_bounded_dataset(ch, "SELECT * FROM empty WHERE false")
    # Empty count → exact_bounded with zero rows
    assert dataset.stats.mode == "exact_bounded"
    assert dataset.stats.row_count == 0


# ---------------------------------------------------------------------------
# Error propagation (bug fix)
# ---------------------------------------------------------------------------


def test_broken_sql_raises_mini_app_query_error():
    """A ClickHouse error on the count query must propagate, not silently
    fall through to an empty preview_only dataset."""

    class BrokenCH:
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            raise RuntimeError(
                "Code: 47. DB::Exception: Unknown expression identifier `bad_col`"
            )

    with pytest.raises(MiniAppQueryError, match="Unknown expression identifier"):
        load_bounded_dataset(BrokenCH(), "SELECT * FROM t ORDER BY bad_col")


def test_exact_path_failure_raises_not_silently_preview():
    """If count() succeeds but the exact-path fetch fails, propagate
    (the query is valid syntactically, so a later failure is exceptional).
    """

    class PartialCH:
        def __init__(self):
            self.calls = 0

        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            self.calls += 1
            if "count()" in sql:
                return ExecutedQuery(
                    sql=sql, executed_sql=sql, database=database, columns=["c"],
                    rows=[[100]], row_count=1, elapsed_seconds=0.0,
                    fetch_mode="rows", warnings=[],
                )
            raise RuntimeError("Code: 241. DB::Exception: Memory limit exceeded")

    with pytest.raises(MiniAppQueryError, match="Memory limit"):
        load_bounded_dataset(PartialCH(), "SELECT * FROM expensive")


def test_preview_only_failure_raises_at_end_of_line():
    """When sampling fails AND the preview fallback also fails, raise —
    there is nothing to show, so we surface the ClickHouse error to the
    launcher instead of returning an empty placeholder dataset."""

    class TotallyBrokenCH:
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            if "count()" in sql:
                return ExecutedQuery(
                    sql=sql, executed_sql=sql, database=database, columns=["c"],
                    rows=[[50_000]], row_count=1, elapsed_seconds=0.0,
                    fetch_mode="rows", warnings=[],
                )
            raise RuntimeError("Code: 62. DB::Exception: Syntax error in subquery")

    with pytest.raises(MiniAppQueryError, match="Syntax error"):
        load_bounded_dataset(TotallyBrokenCH(), "SELECT * FROM mystery")
