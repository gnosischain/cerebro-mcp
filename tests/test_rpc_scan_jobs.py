"""Job lifecycle, unit checkpointing, and status classification."""
import threading
import time

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.jobs import (
    ScanCursor,
    ScanJobManager,
    commit_unit,
    new_job,
)
from cerebro_mcp.rpc_scan.scratch import BatchInserter
from tests.rpc_scan_fakes import InMemoryRegistryStore, make_store, wait_for_terminal


def _job():
    return new_job("logs", "test", {"from_block": 0, "to_block": 9})


def test_new_job_table_name_matches_regex():
    job = _job()
    assert job.table_name == f"rpc_logs_{job.id}"
    assert len(job.id) == 8


def test_commit_unit_zero_rows_still_advances_cursor():
    store = InMemoryRegistryStore()
    job = _job()
    inserter = BatchInserter(store, job.table_name, ["a"])
    commit_unit(job, inserter, store, next_block=101, force_persist=True)
    assert job.cursor.next_block == 101
    assert store.registry[job.id]["cursor_json"] == job.cursor.to_json()


def test_commit_unit_flush_failure_leaves_cursor_unmoved(monkeypatch):
    monkeypatch.setattr("cerebro_mcp.rpc_scan.scratch.time.sleep", lambda s: None)
    monkeypatch.setattr(settings, "RPC_SCAN_INSERT_MAX_RETRIES", 1)
    store = InMemoryRegistryStore()
    store._fake_client.fail_inserts = 1
    job = _job()
    job.cursor.next_block = 50
    inserter = BatchInserter(store, job.table_name, ["a"])
    inserter.add(["row"])
    with pytest.raises(RuntimeError):
        commit_unit(job, inserter, store, next_block=101)
    assert job.cursor.next_block == 50  # unmoved
    # Retry: same rows re-insert, then the cursor advances.
    commit_unit(job, inserter, store, next_block=101)
    assert job.cursor.next_block == 101
    assert store._fake_client.rows_for(job.table_name) == [["row"]]


def test_manager_classifies_completed():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)
    job = mgr.submit(_job(), lambda j: None)
    wait_for_terminal(job)
    assert job.status == "completed"
    assert store.registry[job.id]["status"] == "completed"


def test_manager_classifies_failed_when_no_rows():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)

    def boom(j):
        raise RuntimeError("kaput")

    job = mgr.submit(_job(), boom)
    wait_for_terminal(job)
    assert job.status == "failed"
    assert "kaput" in (job.error or "")


def test_manager_classifies_partial_when_rows_written():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)

    def partial(j):
        j.progress.rows_written = 10
        raise RuntimeError("died mid-flight")

    job = mgr.submit(_job(), partial)
    wait_for_terminal(job)
    assert job.status == "partial"
    assert job.resumable


def test_manager_classifies_partial_on_skipped_ranges():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)

    def skipper(j):
        j.progress.skipped_ranges = 1
        j.cursor.skipped.append([5, 5])

    job = mgr.submit(_job(), skipper)
    wait_for_terminal(job)
    assert job.status == "partial"


def test_cancel_mid_flight_persists_cursor():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)
    started = threading.Event()

    def long_runner(j):
        started.set()
        while not j.cancel_event.is_set():
            time.sleep(0.01)
        j.cursor.next_block = 42

    job = mgr.submit(_job(), long_runner)
    assert started.wait(timeout=5)
    assert mgr.cancel(job.id)
    wait_for_terminal(job)
    assert job.status == "cancelled"
    assert job.resumable
    assert ScanCursor.from_json(store.registry[job.id]["cursor_json"]).next_block == 42


def test_cancel_unknown_or_terminal_job_returns_false():
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1)
    assert mgr.cancel("deadbeef") is False
    job = mgr.submit(_job(), lambda j: None)
    wait_for_terminal(job)
    assert mgr.cancel(job.id) is False


def test_cleanup_expired_evicts_old_terminal_jobs(monkeypatch):
    store = InMemoryRegistryStore()
    mgr = ScanJobManager(store, max_concurrent=1, job_ttl_seconds=10)
    job = mgr.submit(_job(), lambda j: None)
    wait_for_terminal(job)
    assert mgr.get(job.id) is not None
    monkeypatch.setattr(
        "cerebro_mcp.rpc_scan.jobs.time.time",
        lambda: job.completed_at + 11,
    )
    assert mgr.cleanup_expired() == 1
    assert mgr.get(job.id) is None


def test_cursor_json_roundtrip():
    cursor = ScanCursor(next_block=7, address_index=3, chunk_index=2, skipped=[[5, 5]])
    restored = ScanCursor.from_json(cursor.to_json())
    assert restored == cursor
