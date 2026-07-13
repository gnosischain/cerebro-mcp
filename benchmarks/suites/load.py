"""Load / concurrency suite: a REAL ``cerebro-mcp --sse`` subprocess under
N parallel MCP clients.

Requires ``CEREBRO_EVAL_CLICKHOUSE=1`` — the spawned server's ``/health``
gate returns 503 without a reachable ClickHouse, and a load test against a
half-up server measures nothing. Without it the suite emits one skipped
case and never spawns anything.

Grid: each workload in ``benchmarks.cases.load_workloads`` crossed with each
worker count in ``ctx.concurrency`` (heavy/mixed capped at
``ctx.max_heavy_concurrency``). Every worker owns ONE session and loops calls
for ``ctx.duration_s`` seconds (the handshake workload loops full
connect/initialize/tools-list/disconnect cycles instead). One CaseResult per
cell: ``samples_ms`` carries every per-call latency; ``meta`` carries TTFB
percentiles, per-tool percentiles, throughput, error/timeout counts, and a
best-effort ``cerebro_mcp_tool_calls_total`` delta scraped from ``/metrics``
(auth-exempt) around the cell.

Only read-only, gate-free tools are exercised — see the
``load_workloads`` module docstring for why (process-global session state).
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from benchmarks.cases.load_workloads import CAPPED_WORKLOADS, WORKLOADS, call_for
from benchmarks.core.mcp_client import open_sse_session, tool_is_error
from benchmarks.core.results import CaseResult
from benchmarks.core.runner import BenchContext
from benchmarks.core.server_process import BenchServer
from benchmarks.core.stats import percentiles

SUPPORTED_MODES = frozenset({"sse"})

# Per-call read timeout. Generous: a timed-out call counts as an error but
# does NOT abandon the worker's session (unlike a broken connection).
_CALL_TIMEOUT_S = 30.0

_METRIC_LINE = re.compile(
    r"^cerebro_mcp_tool_calls_total\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.eE+-]+)\s*$"
)
_LABEL = re.compile(r'(\w+)="([^"]*)"')


@dataclass
class _WorkerStats:
    calls: list[tuple[str, float, bool]] = field(default_factory=list)  # (tool, ms, ok)
    ttfb_ms: list[float] = field(default_factory=list)
    timeouts: int = 0
    conn_errors: int = 0


def run(ctx: BenchContext) -> list[CaseResult]:
    if not ctx.real_clickhouse:
        case_id = "load/all"
        if not ctx.should_run(case_id):
            return []
        return [
            CaseResult.skipped_case(
                case_id,
                "load suite needs CEREBRO_EVAL_CLICKHOUSE=1 (spawned server /health must pass)",
            )
        ]

    cases: list[CaseResult] = []
    with BenchServer(port=ctx.port, scratch_dir=ctx.scratch_dir) as server:
        try:
            health = server.wait_healthy(timeout=90)
        except Exception as exc:  # noqa: BLE001 — spawn/health failure is the result
            return [CaseResult.error_case("load/server", str(exc))]
        print(
            f"[load] server healthy on {server.base_url} "
            f"(clickhouse {health.get('clickhouse_version', '?')})"
        )

        for workload in WORKLOADS:
            for n, requested_n in _cell_concurrency(workload, ctx):
                case_id = f"load/{workload}/c{n}"
                if not ctx.should_run(case_id):
                    continue
                cases.append(
                    _run_cell(server, workload, n, requested_n, ctx.duration_s)
                )
    return cases


def _cell_concurrency(workload: str, ctx: BenchContext) -> list[tuple[int, int]]:
    """(effective_n, requested_n) per cell; capped workloads clamp and dedupe
    (concurrency [1,4,8,16] with cap 8 yields c1, c4, c8 — not c8 twice)."""
    cap = ctx.max_heavy_concurrency if workload in CAPPED_WORKLOADS else None
    cells: list[tuple[int, int]] = []
    seen: set[int] = set()
    for requested in ctx.concurrency:
        effective = min(requested, cap) if cap is not None else requested
        if effective not in seen:
            seen.add(effective)
            cells.append((effective, requested))
    return cells


def _run_cell(
    server: BenchServer, workload: str, n: int, requested_n: int, duration_s: int
) -> CaseResult:
    spec = WORKLOADS[workload]
    tool_label = "+".join(spec["tools"])
    before = _scrape_tool_calls(server.metrics_url)

    started = time.monotonic()
    try:
        workers = asyncio.run(_run_workers(server, workload, n, duration_s))
    except Exception as exc:  # noqa: BLE001 — cell-level failure becomes the case
        return CaseResult.error_case(
            f"load/{workload}/c{n}", str(exc), tool=tool_label
        )
    elapsed_s = time.monotonic() - started

    after = _scrape_tool_calls(server.metrics_url)

    calls = [c for w in workers for c in w.calls]
    ttfbs = [t for w in workers for t in w.ttfb_ms]
    ok_calls = [ms for _, ms, ok in calls if ok]
    errors = sum(1 for _, _, ok in calls if not ok) + sum(w.conn_errors for w in workers)
    timeouts = sum(w.timeouts for w in workers)

    by_tool: dict[str, dict[str, float]] = {}
    for tool in sorted({t for t, _, _ in calls}):
        tool_ms = [ms for t, ms, _ in calls if t == tool]
        pct = percentiles(tool_ms)
        by_tool[tool] = {
            "p50": round(pct["p50"], 2),
            "p95": round(pct["p95"], 2),
            "n": len(tool_ms),
        }
    ttfb_pct = percentiles(ttfbs)

    meta: dict[str, Any] = {
        "workload": workload,
        "concurrency": n,
        "duration_s": duration_s,
        "elapsed_s": round(elapsed_s, 2),
        "calls": len(calls),
        "errors": errors,
        "timeouts": timeouts,
        "error_rate": round(errors / len(calls), 4) if calls else 1.0,
        "throughput_cps": round(len(ok_calls) / elapsed_s, 2) if elapsed_s > 0 else 0.0,
        "ttfb_ms": {
            "p50": round(ttfb_pct["p50"], 2),
            "p95": round(ttfb_pct["p95"], 2),
            "n": len(ttfbs),
        },
        "by_tool": by_tool,
        "server_metrics": _tool_call_deltas(before, after),
    }
    if requested_n != n:
        meta["requested_concurrency"] = requested_n

    case_id = f"load/{workload}/c{n}"
    if not ok_calls:
        return CaseResult.error_case(
            case_id,
            f"no successful calls in {elapsed_s:.1f}s "
            f"({errors} errors, {timeouts} timeouts across {n} workers)",
            tool=tool_label,
            meta=meta,
        )
    return CaseResult(
        id=case_id,
        tool=tool_label,
        samples_ms=[ms for _, ms, _ in calls],
        meta=meta,
    ).finalize()


async def _run_workers(
    server: BenchServer, workload: str, n: int, duration_s: int
) -> list[_WorkerStats]:
    deadline = time.monotonic() + duration_s
    if WORKLOADS[workload]["kind"] == "handshake":
        coros = [
            _handshake_worker(server.sse_url, server.token, deadline)
            for _ in range(n)
        ]
    else:
        coros = [
            _call_worker(server.sse_url, server.token, workload, deadline)
            for _ in range(n)
        ]
    return list(await asyncio.gather(*coros))


async def _handshake_worker(url: str, token: str, deadline: float) -> _WorkerStats:
    """Loop full connect/initialize/tools-list/disconnect cycles."""
    stats = _WorkerStats()
    while time.monotonic() < deadline:
        t0 = time.perf_counter()
        try:
            async with open_sse_session(url, token) as session:
                ttfb = (time.perf_counter() - t0) * 1000.0
                stats.ttfb_ms.append(ttfb)
                stats.calls.append(("initialize", ttfb, True))
                t1 = time.perf_counter()
                await session.list_tools()
                stats.calls.append(
                    ("tools/list", (time.perf_counter() - t1) * 1000.0, True)
                )
        except Exception:  # noqa: BLE001 — a failed cycle is a data point
            stats.conn_errors += 1
            stats.calls.append(
                ("initialize", (time.perf_counter() - t0) * 1000.0, False)
            )
            await asyncio.sleep(0.2)  # don't hot-loop against a dying server
    return stats


async def _call_worker(
    url: str, token: str, workload: str, deadline: float
) -> _WorkerStats:
    """One session for the worker's lifetime; loop the workload's call plan."""
    stats = _WorkerStats()
    read_timeout = timedelta(seconds=_CALL_TIMEOUT_S)
    t0 = time.perf_counter()
    try:
        async with open_sse_session(url, token) as session:
            stats.ttfb_ms.append((time.perf_counter() - t0) * 1000.0)
            i = 0
            while time.monotonic() < deadline:
                tool, args = call_for(workload, i)
                i += 1
                t1 = time.perf_counter()
                try:
                    result = await session.call_tool(
                        tool, args, read_timeout_seconds=read_timeout
                    )
                    stats.calls.append(
                        (tool, (time.perf_counter() - t1) * 1000.0, not tool_is_error(result))
                    )
                except Exception as exc:  # noqa: BLE001 — classify, don't crash the cell
                    ms = (time.perf_counter() - t1) * 1000.0
                    stats.calls.append((tool, ms, False))
                    if _looks_like_timeout(exc):
                        stats.timeouts += 1  # session survives a request timeout
                    else:
                        stats.conn_errors += 1
                        break  # transport is broken — abandon the worker
    except Exception:  # noqa: BLE001 — connect/handshake failure
        stats.conn_errors += 1
    return stats


def _looks_like_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    return "timeout" in f"{type(exc).__name__}: {exc}".lower()


def _scrape_tool_calls(metrics_url: str) -> dict[tuple[str, str], float]:
    """Best-effort ``cerebro_mcp_tool_calls_total`` snapshot from ``/metrics``
    (auth-exempt route). Returns {} on any failure — the load numbers matter
    more than the server-side cross-check."""
    try:
        with urllib.request.urlopen(metrics_url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[tuple[str, str], float] = {}
    try:
        for line in body.splitlines():
            m = _METRIC_LINE.match(line)
            if not m:
                continue
            labels = dict(_LABEL.findall(m.group("labels")))
            key = (labels.get("tool_name", "?"), labels.get("status", "?"))
            out[key] = out.get(key, 0.0) + float(m.group("value"))
    except Exception:  # noqa: BLE001
        return {}
    return out


def _tool_call_deltas(
    before: dict[tuple[str, str], float], after: dict[tuple[str, str], float]
) -> dict[str, Any]:
    if not after:
        return {"scrape_failed": True}
    deltas = {}
    for key, value in sorted(after.items()):
        diff = value - before.get(key, 0.0)
        if diff:
            deltas[f"{key[0]}:{key[1]}"] = round(diff, 1)
    return {"tool_calls_delta": deltas}
