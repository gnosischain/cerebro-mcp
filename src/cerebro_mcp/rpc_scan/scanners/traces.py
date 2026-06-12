"""trace_filter scans (native value flows) and single-tx call-tree summaries.

Native xDAI transfers emit no log — they are the blind spot of every
Transfer-event method. ``trace_filter`` fills it, but nodes cap it at
~100 blocks per call, so windows are chunked and fanned over a bounded
pool. Windows complete out of order; the durable cursor only advances
along the contiguous frontier of flushed windows.
"""
from __future__ import annotations

import heapq
from typing import Any

from cerebro_mcp.clients.raw_rpc import (
    RpcError,
    RpcRouter,
    TEACHING_DEBUG_UNSUPPORTED,
    TEACHING_TRACE_UNSUPPORTED,
)
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.chunking import run_pool
from cerebro_mcp.rpc_scan.jobs import ScanJob, commit_unit
from cerebro_mcp.rpc_scan.schemas import traces_table_ddl
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore
from cerebro_mcp.rpc_scan.scanners.calls import _fit_u256


def run_trace_scan(job: ScanJob, spec: dict[str, Any], *,
                   rpc: RpcRouter, store: ScratchStore) -> None:
    if not rpc.supports("trace_filter"):
        raise RpcError("trace_filter", -32601, TEACHING_TRACE_UNSUPPORTED)
    u256 = store.uint256_type()
    ddl, order_by, columns = traces_table_ddl(u256=u256)
    store.create_scan_table(job.table_name, ddl, order_by)
    client = rpc.archive  # traces hard-require archive

    from_block = int(spec["from_block"])
    to_block = int(spec["to_block"])
    start = max(job.cursor.next_block or from_block, from_block)
    cap = settings.RPC_SCAN_TRACE_BLOCKS_PER_CALL
    # Re-queue previously skipped windows first (resume heals them). They stay
    # in cursor.skipped until their re-scan succeeds and flushes, so a crash
    # mid-resume never loses the disclosure.
    requeued = [(int(lo), int(hi), True) for lo, hi in job.cursor.skipped]
    windows = requeued + [
        (lo, min(lo + cap - 1, to_block), False)
        for lo in range(start, to_block + 1, cap)
    ]
    job.progress.blocks_total = to_block - from_block + 1
    job.progress.blocks_done = start - from_block

    base_filter: dict[str, Any] = {}
    if spec.get("push_from"):
        base_filter["fromAddress"] = spec["push_from"]
    if spec.get("push_to"):
        base_filter["toAddress"] = spec["push_to"]
    engine_from = set(spec.get("engine_filter_from") or [])
    engine_to = set(spec.get("engine_filter_to") or [])
    min_value = int(spec.get("min_value_wei", 0))
    include_failed = bool(spec.get("include_failed"))

    inserter = BatchInserter(
        store, job.table_name, columns,
        on_flush=lambda n: setattr(
            job.progress, "rows_written", job.progress.rows_written + n
        ),
    )

    def fetch(window: tuple[int, int, bool]):
        lo, hi, is_requeued = window
        params = dict(base_filter, fromBlock=hex(lo), toBlock=hex(hi))
        job.progress.rpc_calls += 1
        try:
            traces = rpc.retry(
                lambda: client.request("trace_filter", [params]),
                base_sleep=0.5,
            )
            return lo, hi, is_requeued, traces, None
        except RpcError as exc:
            if not exc.retryable:
                raise
            return lo, hi, is_requeued, [], exc
        except Exception as exc:  # noqa: BLE001
            return lo, hi, is_requeued, [], exc

    # Fresh windows complete out of order; advance the durable cursor only
    # along the contiguous frontier of flushed windows. Re-queued skip windows
    # sit below the frontier and are settled via cursor.skipped instead.
    frontier = start
    completed_heap: list[tuple[int, int]] = []

    for lo, hi, is_requeued, traces, error in run_pool(
        fetch, windows,
        workers=spec.get("workers") or settings.RPC_SCAN_TRACE_WORKERS,
        should_stop=job.cancel_event.is_set,
    ):
        if error is not None:
            job.progress.last_error = f"blocks {lo}-{hi}: {error}"
            if not is_requeued:  # re-queued failures are already recorded
                job.progress.skipped_ranges += 1
                job.cursor.skipped.append([lo, hi])
            continue
        job.progress.items_found += len(traces)
        for t in traces:
            row = _trace_row(
                t, u256,
                engine_from=engine_from, engine_to=engine_to,
                min_value=min_value, include_failed=include_failed,
            )
            if row is not None:
                inserter.add(row)
        job.progress.blocks_done += hi - lo + 1
        if is_requeued:
            # Healed: flush the window's rows FIRST, then drop it from the
            # skip list and persist — a crash in between keeps the disclosure.
            commit_unit(job, inserter, store, force_persist=True)
            job.cursor.skipped = [
                s for s in job.cursor.skipped
                if [int(s[0]), int(s[1])] != [lo, hi]
            ]
            commit_unit(job, inserter, store, force_persist=True)
            continue
        heapq.heappush(completed_heap, (lo, hi))
        new_frontier = frontier
        while completed_heap and completed_heap[0][0] == new_frontier:
            _, top_hi = heapq.heappop(completed_heap)
            new_frontier = top_hi + 1
        if new_frontier != frontier:
            frontier = new_frontier
            commit_unit(job, inserter, store, next_block=frontier)
    inserter.close()
    commit_unit(job, inserter, store, force_persist=True)


def _trace_row(t: dict[str, Any], u256: str, *, engine_from: set[str],
               engine_to: set[str], min_value: int,
               include_failed: bool) -> list[Any] | None:
    if t.get("type") != "call":
        return None
    action = t.get("action") or {}
    if action.get("callType") not in ("call", "callcode"):
        return None
    error = t.get("error") or ""
    if error and not include_failed:
        return None
    value = int(action.get("value") or "0x0", 16)
    if value < min_value:
        return None
    from_addr = (action.get("from") or "").lower()
    to_addr = (action.get("to") or "").lower()
    if engine_from and from_addr not in engine_from:
        return None
    if engine_to and to_addr not in engine_to:
        return None
    result = t.get("result") or {}
    input_data = action.get("input") or "0x"
    return [
        int(t.get("blockNumber") or 0),
        (t.get("transactionHash") or "").lower(),
        ".".join(str(i) for i in (t.get("traceAddress") or [])),
        action.get("callType") or "",
        from_addr,
        to_addr,
        _fit_u256(value, u256),
        int(result.get("gasUsed") or "0x0", 16) if result else 0,
        input_data[:10] if len(input_data) >= 10 else "",
        0 if error else 1,
        error[:200],
    ]


# ---------------------------------------------------------------------------
# Single-transaction call tree (sync; debug_traceTransaction callTracer)
# ---------------------------------------------------------------------------

def summarize_transaction_trace(
    rpc: RpcRouter, tx_hash: str, *, max_depth: int = 8,
    max_children: int = 50,
) -> dict[str, Any]:
    if not rpc.supports("debug_traceTransaction"):
        raise RpcError("debug_traceTransaction", -32601, TEACHING_DEBUG_UNSUPPORTED)
    client = rpc.archive if rpc.has_archive() else rpc.standard
    tree = rpc.retry(lambda: client.request(
        "debug_traceTransaction", [tx_hash, {"tracer": "callTracer"}]
    ))
    if not tree:
        raise ValueError(f"No trace returned for {tx_hash}")
    return _compact(tree, depth=0, max_depth=max_depth, max_children=max_children)


def _compact(frame: dict[str, Any], *, depth: int, max_depth: int,
             max_children: int) -> dict[str, Any]:
    input_data = frame.get("input") or "0x"
    node: dict[str, Any] = {
        "call_type": frame.get("type") or "CALL",
        "from": (frame.get("from") or "").lower(),
        "to": (frame.get("to") or "").lower(),
        "value_wei": int(frame.get("value") or "0x0", 16),
        "gas_used": int(frame.get("gasUsed") or "0x0", 16),
        "selector": input_data[:10] if len(input_data) >= 10 else "",
        "error": frame.get("error") or "",
        "children": [],
        "truncated": False,
    }
    children = frame.get("calls") or []
    if depth >= max_depth and children:
        node["truncated"] = True
        node["hidden_children"] = _count_frames(children)
        return node
    for child in children[:max_children]:
        node["children"].append(
            _compact(child, depth=depth + 1, max_depth=max_depth,
                     max_children=max_children)
        )
    if len(children) > max_children:
        node["truncated"] = True
        node["hidden_children"] = _count_frames(children[max_children:])
    return node


def _count_frames(frames: list[dict[str, Any]]) -> int:
    total = 0
    for f in frames:
        total += 1 + _count_frames(f.get("calls") or [])
    return total


def net_native_flows(node: dict[str, Any]) -> dict[str, int]:
    """Aggregate native value movement per address across successful frames."""
    flows: dict[str, int] = {}

    def walk(n: dict[str, Any]) -> None:
        if not n["error"] and n["value_wei"] and n["call_type"].upper() in (
            "CALL", "CALLCODE",
        ):
            flows[n["from"]] = flows.get(n["from"], 0) - n["value_wei"]
            flows[n["to"]] = flows.get(n["to"], 0) + n["value_wei"]
        for child in n["children"]:
            walk(child)

    walk(node)
    return {a: v for a, v in flows.items() if v != 0}
