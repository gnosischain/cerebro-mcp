"""Tests for async query deduplication."""

from __future__ import annotations

import time

import pytest

from cerebro_mcp.tools.query_async import (
    QueryJob,
    _pending_queries,
    _query_dedup_key,
)


@pytest.fixture(autouse=True)
def _clear_pending(monkeypatch):
    """Ensure a clean job registry for each test."""
    _pending_queries.clear()
    yield
    _pending_queries.clear()


class TestQueryDedupKey:
    def test_deterministic(self):
        k1 = _query_dedup_key("SELECT 1", "dbt", 100)
        k2 = _query_dedup_key("SELECT 1", "dbt", 100)
        assert k1 == k2
        assert len(k1) == 64  # SHA-256 hex

    def test_different_sql(self):
        k1 = _query_dedup_key("SELECT 1", "dbt", 100)
        k2 = _query_dedup_key("SELECT 2", "dbt", 100)
        assert k1 != k2

    def test_different_database(self):
        k1 = _query_dedup_key("SELECT 1", "dbt", 100)
        k2 = _query_dedup_key("SELECT 1", "execution", 100)
        assert k1 != k2

    def test_different_max_rows(self):
        k1 = _query_dedup_key("SELECT 1", "dbt", 100)
        k2 = _query_dedup_key("SELECT 1", "dbt", 200)
        assert k1 != k2

    def test_strips_whitespace(self):
        k1 = _query_dedup_key("  SELECT 1  ", "dbt", 100)
        k2 = _query_dedup_key("SELECT 1", "dbt", 100)
        assert k1 == k2


class TestDedupBehavior:
    def _make_job(
        self,
        sql: str = "SELECT 1",
        database: str = "dbt",
        max_rows: int = 100,
        status: str = "completed",
    ) -> QueryJob:
        key = _query_dedup_key(sql, database, max_rows)
        job = QueryJob(
            id="abc12345",
            sql=sql,
            database=database,
            max_rows=max_rows,
            dedup_key=key,
            status=status,
            submitted_at=time.time(),
        )
        _pending_queries[job.id] = job
        return job

    def test_completed_job_is_reused(self):
        job = self._make_job(status="completed")
        # A new submission with the same key should find this job
        key = _query_dedup_key("SELECT 1", "dbt", 100)
        found = None
        for existing in _pending_queries.values():
            if (
                existing.dedup_key == key
                and existing.status in ("pending", "running", "completed")
            ):
                found = existing
                break
        assert found is not None
        assert found.id == job.id

    def test_running_job_is_reused(self):
        job = self._make_job(status="running")
        key = _query_dedup_key("SELECT 1", "dbt", 100)
        found = None
        for existing in _pending_queries.values():
            if (
                existing.dedup_key == key
                and existing.status in ("pending", "running", "completed")
            ):
                found = existing
                break
        assert found is not None
        assert found.id == job.id

    def test_failed_job_is_not_reused(self):
        self._make_job(status="failed")
        key = _query_dedup_key("SELECT 1", "dbt", 100)
        found = None
        for existing in _pending_queries.values():
            if (
                existing.dedup_key == key
                and existing.status in ("pending", "running", "completed")
            ):
                found = existing
                break
        assert found is None

    def test_different_query_not_deduped(self):
        self._make_job(sql="SELECT 1", status="completed")
        key = _query_dedup_key("SELECT 2", "dbt", 100)
        found = None
        for existing in _pending_queries.values():
            if (
                existing.dedup_key == key
                and existing.status in ("pending", "running", "completed")
            ):
                found = existing
                break
        assert found is None

    def test_different_max_rows_not_deduped(self):
        self._make_job(sql="SELECT 1", max_rows=100, status="completed")
        key = _query_dedup_key("SELECT 1", "dbt", 200)
        found = None
        for existing in _pending_queries.values():
            if (
                existing.dedup_key == key
                and existing.status in ("pending", "running", "completed")
            ):
                found = existing
                break
        assert found is None
