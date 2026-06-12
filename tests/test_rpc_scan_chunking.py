"""Adaptive range scanning and bounded pool behavior."""
import threading
import time

import pytest

from cerebro_mcp.clients.raw_rpc import RpcError
from cerebro_mcp.rpc_scan.chunking import RangeResult, chunked, run_pool, scan_adaptive


def test_chunked_splits_evenly():
    assert list(chunked(list(range(7)), 3)) == [[0, 1, 2], [3, 4, 5], [6]]


def test_scan_adaptive_happy_path_yields_every_range():
    calls = []

    def fetch(lo, hi):
        calls.append((lo, hi))
        return []

    ranges = list(scan_adaptive(0, 99, fetch, init_chunk=50))
    assert [(r.lo, r.hi) for r in ranges] == [(0, 49), (50, 99)]
    # Empty ranges ARE yielded — zero-row units must checkpoint.
    assert all(r.items == [] for r in ranges)


def test_scan_adaptive_halves_on_error_and_retries_same_cursor():
    calls = []

    def fetch(lo, hi):
        calls.append((lo, hi))
        if hi - lo + 1 > 25:
            raise RpcError("eth_getLogs", -32005, "too many results")
        return [f"{lo}-{hi}"]

    ranges = list(scan_adaptive(0, 99, fetch, init_chunk=100))
    # 0-99 fails, 0-49 fails, 0-24 succeeds; cursor always retried in place.
    assert calls[0] == (0, 99)
    assert calls[1] == (0, 49)
    assert calls[2] == (0, 24)
    assert ranges[0].lo == 0 and ranges[0].hi == 24
    # Full coverage despite the halving.
    covered = sorted((r.lo, r.hi) for r in ranges)
    assert covered[0][0] == 0 and covered[-1][1] == 99


def test_scan_adaptive_grows_back_after_success():
    sizes = []

    def fetch(lo, hi):
        sizes.append(hi - lo + 1)
        if len(sizes) == 1:
            raise RpcError("eth_getLogs", -32005, "too many results")
        return []

    list(scan_adaptive(0, 399, fetch, init_chunk=100))
    # First call 100 fails -> 50 succeeds -> grows back toward 100.
    assert sizes[0] == 100 and sizes[1] == 50
    assert max(sizes[2:]) == 100


def test_scan_adaptive_skips_stuck_single_block():
    skipped = []

    def fetch(lo, hi):
        if lo <= 5 <= hi:
            raise RpcError("eth_getLogs", -32005, "boom")
        return []

    ranges = list(
        scan_adaptive(0, 9, fetch, init_chunk=4, on_skip=lambda b, e: skipped.append(b))
    )
    assert skipped == [5]
    covered = [(r.lo, r.hi) for r in ranges]
    assert all(not (lo <= 5 <= hi) for lo, hi in covered)
    assert max(hi for _, hi in covered) == 9


def test_scan_adaptive_propagates_non_retryable():
    def fetch(lo, hi):
        raise RpcError("trace_filter", -32601, "method not found")

    with pytest.raises(RpcError, match="method not found"):
        list(scan_adaptive(0, 99, fetch, init_chunk=10))


def test_scan_adaptive_stops_on_should_stop():
    stop = threading.Event()

    def fetch(lo, hi):
        stop.set()
        return []

    ranges = list(scan_adaptive(0, 999, fetch, init_chunk=10, should_stop=stop.is_set))
    assert len(ranges) == 1  # first range yields, then the loop observes stop


def test_run_pool_bounds_in_flight_submissions():
    submitted = []
    release = threading.Event()

    def fn(x):
        submitted.append(x)
        release.wait(timeout=5)
        return x

    results = []

    def consume():
        for r in run_pool(fn, range(100), workers=2):
            results.append(r)

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.2)
    # Bounded: at most workers*2 tasks have started, not all 100.
    assert len(submitted) <= 4
    release.set()
    t.join(timeout=10)
    assert sorted(results) == list(range(100))


def test_run_pool_stops_scheduling_on_should_stop():
    started = []
    stop = threading.Event()

    def fn(x):
        started.append(x)
        return x

    consumed = []
    for r in run_pool(fn, range(1000), workers=2, should_stop=stop.is_set):
        consumed.append(r)
        stop.set()
    # Far fewer than 1000 tasks ever started.
    assert len(started) < 20
