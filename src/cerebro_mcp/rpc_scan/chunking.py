"""Adaptive block-range scanning and bounded worker pools.

The adaptive control flow is a port of the pattern proven in the
gp_rpc_forensics incident scripts (lib/rpc.py): halve the window on a
retryable provider error and retry the same cursor; when a single block
still fails, skip it via callback and keep going; grow the window back
toward the initial size after success.
"""
from __future__ import annotations

import itertools
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def chunked(seq: Sequence[T], n: int) -> Iterator[list[T]]:
    for i in range(0, len(seq), max(1, n)):
        yield list(seq[i:i + n])


def run_pool(
    fn: Callable[[T], R],
    items: Iterable[T],
    workers: int,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[R]:
    """Bounded in-flight thread pool, yielding results as they complete.

    At most ``workers * 2`` futures are outstanding at any moment, so a
    cancellation (``should_stop`` flipping True) actually stops scheduling
    instead of leaving thousands of pre-submitted futures to drain.
    """
    it = iter(items)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        in_flight = {ex.submit(fn, x) for x in itertools.islice(it, max(1, workers) * 2)}
        while in_flight:
            done, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in done:
                yield fut.result()
            if should_stop and should_stop():
                for fut in in_flight:
                    fut.cancel()
                return
            for x in itertools.islice(it, len(done)):
                in_flight.add(ex.submit(fn, x))


@dataclass
class RangeResult:
    lo: int
    hi: int
    items: list[Any]


def scan_adaptive(
    from_block: int,
    to_block: int,
    fetch: Callable[[int, int], list[Any]],
    *,
    init_chunk: int,
    min_chunk: int = 1,
    should_stop: Callable[[], bool] | None = None,
    on_skip: Callable[[int, Exception], None] | None = None,
) -> Iterator[RangeResult]:
    """Sweep ``[from_block, to_block]`` with an adaptively-sized window.

    ``fetch(lo, hi)`` returns the items for that window or raises. Errors
    with a falsy ``retryable`` attribute propagate immediately (e.g. an
    unsupported method); everything else triggers the halve/skip dance.

    Every completed range is yielded — including empty ones — so callers
    can checkpoint zero-row units.
    """
    cursor, chunk = from_block, max(min_chunk, init_chunk)
    while cursor <= to_block:
        if should_stop and should_stop():
            return
        hi = min(cursor + chunk - 1, to_block)
        try:
            items = fetch(cursor, hi)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "retryable", True) is False:
                raise
            if hi > cursor:
                chunk = max(min_chunk, chunk // 2)
                continue
            if on_skip:
                on_skip(cursor, exc)
            cursor += 1
            chunk = init_chunk
            continue
        yield RangeResult(cursor, hi, items)
        cursor = hi + 1
        if chunk < init_chunk:
            chunk = min(init_chunk, chunk * 2)
