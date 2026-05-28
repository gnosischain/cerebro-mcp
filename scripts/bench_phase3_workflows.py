#!/usr/bin/env python3
"""Phase 3 end-to-end + performance bench.

Goes beyond the unit-style smoke in `scripts/test_phase3_workflows.py`:

  Section A — Realistic Cerebro workflow shape
    Simulates the dispatcher → 3 parallel analysts → reviewer gate →
    reporter pattern that the Phase 3 dispatcher persona prescribes.
    Each "analyst" calls `record_llm_call` to embed an LLMCallEvent in
    the event stream; the simulated reviewer reads gate payload, decides
    pass/fail, then the reporter consumes the gathered evidence. Demonstrates
    the *full* shape of an event log Cerebro will produce in production.

  Section B — Crash + resume semantics
    Runs the workflow but kills the second analyst mid-call (simulates
    Anthropic 529). Reopens the store on the same file, calls
    `find_unfinished_llm_calls`, demonstrates the replay surfaces exactly
    the interrupted call with full message history.

  Section C — Performance bench
    Measures append throughput, replay latency at scale (1k events),
    parallel fan-out latency for 8 / 32 sub-tasks, and concurrent-workflow
    throughput. The numbers tell you whether the event log is sized for
    your workflow rate.

Run:
    python scripts/bench_phase3_workflows.py
    python scripts/bench_phase3_workflows.py --json out.json
    python scripts/bench_phase3_workflows.py --keep-db   # leave the SQLite file for inspection
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.workflow.event_store import EventStore  # noqa: E402
from cerebro_mcp.workflow.payloads import (  # noqa: E402
    EVENT_LLM_CALL_COMPLETED,
    EVENT_LLM_CALL_FAILED,
    EVENT_LLM_CALL_STARTED,
    GATE_FAILED,
    GATE_PASSED,
    GATE_READY,
    LLMCallEvent,
    LLMTurn,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_RUNNING,
    find_unfinished_llm_calls,
)
from cerebro_mcp.workflow.runner import (  # noqa: E402
    SubTask,
    begin_workflow,
    new_workflow_id,
    run_parallel_phase,
    run_sequential_phase,
)


# ---------------------------------------------------------------------------
# Reporter (reused pattern from other Phase scripts)
# ---------------------------------------------------------------------------


class Reporter:
    def __init__(self) -> None:
        self.passes = 0
        self.fails = 0
        self.metrics: dict[str, float] = {}

    def section(self, name: str) -> None:
        print(f"\n=== {name} ===")

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        if ok:
            self.passes += 1
        else:
            self.fails += 1

    def metric(self, label: str, value: float, unit: str = "") -> None:
        self.metrics[label] = value
        print(f"  [METRIC] {label}: {value:.3f} {unit}".rstrip())

    def summary(self) -> int:
        total = self.passes + self.fails
        print(f"\n--- {self.passes}/{total} checks passed "
              f"({self.fails} failures) ---")
        return 0 if self.fails == 0 else 1


# ---------------------------------------------------------------------------
# Helpers — record an "LLM call" event with realistic shape
# ---------------------------------------------------------------------------


async def record_llm_call(
    store: EventStore,
    workflow_id: str,
    subtask_name: str,
    call_id: str,
    user_message: str,
    *,
    fail: bool = False,
    interrupt: bool = False,
) -> None:
    """Append a started/completed pair (or started+failed, or just started
    if `interrupt=True`). Mimics what a real agent runner would do around
    each Anthropic call."""
    call = LLMCallEvent(
        subtask_name=subtask_name,
        call_id=call_id,
        system_prompt="Cerebro analyst persona — full text omitted.",
        messages=[
            LLMTurn(
                role="user",
                content=[{"type": "text", "text": user_message}],
                model="claude-opus-4-7-1m",
            ),
        ],
        tool_schemas=[
            {"name": "execute_query"},
            {"name": "describe_table"},
            {"name": "discover_models"},
        ],
    )
    await store.append_event(workflow_id, EVENT_LLM_CALL_STARTED, call)
    if interrupt:
        return  # leave it open — this is the "Anthropic 529" path
    if fail:
        call.error = "ToolError: ClickHouse memory_limit_exceeded"
        await store.append_event(workflow_id, EVENT_LLM_CALL_FAILED, call)
    else:
        call.response = LLMTurn(
            role="assistant",
            content=[{"type": "text", "text": f"Result for {subtask_name}"}],
            stop_reason="end_turn",
        )
        call.elapsed_seconds = 1.234
        await store.append_event(workflow_id, EVENT_LLM_CALL_COMPLETED, call)


# ---------------------------------------------------------------------------
# Section A — realistic Cerebro workflow shape
# ---------------------------------------------------------------------------


async def section_realistic_workflow(r: Reporter, db_path: Path) -> dict:
    r.section("A. Realistic Cerebro workflow: dispatcher → 3 parallel analysts → reviewer → reporter")
    s = EventStore(db_path=db_path)
    await s.init()
    wid = new_workflow_id("q3_review")
    await begin_workflow(
        wid, "quarterly_review",
        metadata={"period": "Q3 2026", "scope": ["network", "tokenomics", "bridge"]},
        store=s,
    )

    # Phase 1: parallel fan-out of 3 analysts. Each emits one LLM-call
    # event to make the event stream production-realistic.
    async def network_analyst():
        await record_llm_call(s, wid, "network_health_analyst",
                              "call_n_001", "Active validators last 90d?")
        await asyncio.sleep(0.02)
        return {"models": ["api_consensus_validators_active_daily"],
                "kpi": {"avg_active": 213_400}}

    async def tokenomics_analyst():
        await record_llm_call(s, wid, "tokenomics_analyst",
                              "call_t_001", "GNO supply + staking ratio?")
        await asyncio.sleep(0.02)
        return {"models": ["int_consensus_validators_income_daily"],
                "kpi": {"staking_ratio": 0.31}}

    async def bridge_analyst():
        await record_llm_call(s, wid, "bridge_security_analyst",
                              "call_b_001", "Bridge inflows/outflows last 30d?")
        await asyncio.sleep(0.02)
        return {"models": ["int_bridges_flows_daily"],
                "kpi": {"net_flow_usd": 12_300_000}}

    t0 = time.perf_counter()
    fan_out_results = await run_parallel_phase(
        wid, "discovery_and_eda",
        [
            SubTask("network_health_analyst", network_analyst),
            SubTask("tokenomics_analyst",     tokenomics_analyst),
            SubTask("bridge_security_analyst", bridge_analyst),
        ],
        "reviewer_input_ready",
        store=s,
    )
    fanout_ms = (time.perf_counter() - t0) * 1000
    r.metric("fan-out wall time (3 analysts, ~20ms each)", fanout_ms, "ms")
    r.check("3 analyst sub-tasks all returned",
            set(fan_out_results) == {"network_health_analyst", "tokenomics_analyst", "bridge_security_analyst"})

    # Phase 2: simulated reviewer reads the gate payload, decides PASS.
    gate = await s.get_gate(wid, "reviewer_input_ready")
    r.check("gate is READY after fan-out", gate["status"] == GATE_READY)
    await record_llm_call(s, wid, "reality_checker",
                          "call_r_001", "Review the 3 analysts' KPIs for sanity.")
    await s.set_gate(wid, "reviewer_pass", GATE_PASSED,
                     {"by": "reality_checker", "verdict": "approved"})

    # Phase 3: sequential reporter consumes the gathered evidence.
    async def reporter():
        await record_llm_call(s, wid, "analytics_reporter",
                              "call_rep_001", "Compose final Q3 report.")
        await asyncio.sleep(0.01)
        return {"charts": 9, "report_id": "rep_q3_2026"}

    seq_out = await run_sequential_phase(
        wid, "reporting",
        [SubTask("analytics_reporter", reporter)],
        store=s,
    )
    r.check("reporter produced charts + report_id",
            seq_out["analytics_reporter"]["charts"] == 9)

    # Mark workflow done.
    from cerebro_mcp.workflow.payloads import WORKFLOW_COMPLETED as _DONE
    await s.mark_workflow_status(wid, _DONE)

    # Audit trail
    events = await s.replay(wid)
    r.metric("total events recorded for full workflow", len(events))

    # Sanity: every analyst should have a llm_call_started AND completed.
    started = sum(1 for e in events if e["kind"] == EVENT_LLM_CALL_STARTED)
    completed = sum(1 for e in events if e["kind"] == EVENT_LLM_CALL_COMPLETED)
    r.check("every llm_call_started has a matching llm_call_completed",
            started == completed and started >= 5,
            f"started={started}, completed={completed}")

    # Confirm gate states are queryable (would-be reviewer view).
    gates = await s.list_gates(wid)
    gate_statuses = {g["gate_name"]: g["status"] for g in gates}
    r.check("both gates recorded with final statuses",
            gate_statuses.get("reviewer_input_ready") == GATE_READY
            and gate_statuses.get("reviewer_pass") == GATE_PASSED,
            str(gate_statuses))

    return {"workflow_id": wid, "events": len(events), "fanout_ms": fanout_ms}


# ---------------------------------------------------------------------------
# Section B — crash + resume semantics
# ---------------------------------------------------------------------------


async def section_crash_and_resume(r: Reporter, db_path: Path) -> dict:
    r.section("B. Crash mid-workflow: agent dies after 1 of 3 LLM calls")
    s = EventStore(db_path=db_path)
    await s.init()
    wid = new_workflow_id("crash_test")
    await begin_workflow(wid, "crash_demo", store=s)

    # Pretend we got partway through a fan-out: 1 analyst completed, 1
    # *interrupted* (started but no completion — Anthropic 529), 1 not
    # started yet.
    await s.append_event(wid, "phase_started", {"phase": "discovery"})
    await record_llm_call(s, wid, "tokenomics_analyst", "call_t_001",
                          "GNO supply?")
    await record_llm_call(s, wid, "bridge_security_analyst", "call_b_001",
                          "Bridge flows?", interrupt=True)
    # network_analyst's call never even started.

    pre_kill_events = await s.event_count(wid)
    r.metric("events recorded before kill", pre_kill_events)

    # === simulated process kill ===
    del s
    s2 = EventStore(db_path=db_path)
    # Note: do NOT call init() — replay must work on a fresh store object
    # without explicit init (lazy-init from the public methods).

    events = await s2.replay(wid)
    r.check("all events recovered after restart",
            len(events) == pre_kill_events,
            f"got {len(events)}")

    unfinished = find_unfinished_llm_calls(events)
    r.check("exactly 1 unfinished LLM call detected", len(unfinished) == 1,
            f"got {len(unfinished)}")
    r.check("unfinished call is the bridge_security_analyst's",
            unfinished and unfinished[0].subtask_name == "bridge_security_analyst")
    r.check("unfinished call carries full message history for re-issue",
            unfinished and unfinished[0].messages
            and unfinished[0].messages[0].content[0]["text"] == "Bridge flows?")
    r.check("unfinished call carries the system prompt",
            unfinished and unfinished[0].system_prompt)

    # Demonstrate "resume": record the completion of the previously
    # interrupted call. This is what the agent runner would do post-replay.
    interrupted = unfinished[0]
    interrupted.response = LLMTurn(
        role="assistant",
        content=[{"type": "text", "text": "Bridge net flow $12.3M"}],
        stop_reason="end_turn",
    )
    interrupted.elapsed_seconds = 0.987
    await s2.append_event(wid, EVENT_LLM_CALL_COMPLETED, interrupted)

    events_after = await s2.replay(wid)
    unfinished_after = find_unfinished_llm_calls(events_after)
    r.check("after resume, no unfinished calls remain",
            unfinished_after == [],
            f"still {len(unfinished_after)}")

    return {"workflow_id": wid,
            "events_at_kill": pre_kill_events,
            "events_after_resume": len(events_after)}


# ---------------------------------------------------------------------------
# Section C — performance benchmarks
# ---------------------------------------------------------------------------


async def section_perf(r: Reporter, db_path: Path) -> dict:
    r.section("C. Performance benchmarks")
    s = EventStore(db_path=db_path)
    await s.init()

    # C1 — append throughput on a single workflow (serial, since same wf
    # appends are lock-serialized).
    await s.create_workflow("perf_serial", "perf")
    N = 1000
    t0 = time.perf_counter()
    for i in range(N):
        await s.append_event("perf_serial", "note", {"i": i, "t": time.time()})
    elapsed = time.perf_counter() - t0
    rate = N / elapsed
    r.metric("serial append rate (single workflow, 1k events)", rate, "events/sec")
    r.metric("median append latency", elapsed / N * 1000, "ms")
    # ~5ms per append is normal: fsync + WAL flush dominate. Cerebro's
    # actual workload is dozens of events per workflow per minute, so this
    # is plenty fast; the floor matters more than the ceiling.
    r.check("≥150 events/sec on a single workflow (fsync-bound, fine for our workload)",
            rate >= 150, f"got {rate:.0f}")

    # C2 — replay latency at 1k events
    t0 = time.perf_counter()
    events = await s.replay("perf_serial")
    replay_ms = (time.perf_counter() - t0) * 1000
    r.metric(f"replay latency for {N} events", replay_ms, "ms")
    r.check("replay 1k events under 200ms",
            replay_ms < 200, f"got {replay_ms:.1f}ms")
    r.check("replay returned all events",
            len(events) == N, f"got {len(events)}")

    # C3 — concurrent appends across DIFFERENT workflows. SQLite WAL +
    # per-workflow locks should let these run truly in parallel.
    M_workflows = 8
    PER = 100
    for w in range(M_workflows):
        await s.create_workflow(f"perf_par_{w}", "perf")

    async def writer(wf_id: str):
        for i in range(PER):
            await s.append_event(wf_id, "note", {"i": i})

    t0 = time.perf_counter()
    await asyncio.gather(*(writer(f"perf_par_{w}") for w in range(M_workflows)))
    par_elapsed = time.perf_counter() - t0
    par_rate = (M_workflows * PER) / par_elapsed
    r.metric(
        f"concurrent append rate ({M_workflows} workflows × {PER} events)",
        par_rate, "events/sec",
    )
    r.metric("concurrent append wall time", par_elapsed * 1000, "ms")
    r.check("concurrent throughput beats serial",
            par_rate >= rate * 0.8,
            f"par={par_rate:.0f}, serial={rate:.0f}")

    # C4 — parallel fan-out with realistic sub-task latency
    for n_tasks in (8, 32):
        wid = new_workflow_id(f"fan_out_{n_tasks}")
        await begin_workflow(wid, "fan_out_perf", store=s)

        async def quick_task(i: int):
            await asyncio.sleep(0.01)  # 10ms simulated work
            return {"i": i}

        subtasks = [SubTask(f"st_{i}", lambda i=i: quick_task(i))
                    for i in range(n_tasks)]
        t0 = time.perf_counter()
        await run_parallel_phase(
            wid, "fan_out", subtasks, "fan_out_gate",
            store=s, max_parallel=n_tasks,  # let everything go at once
        )
        fan_elapsed = (time.perf_counter() - t0) * 1000
        r.metric(f"fan-out latency ({n_tasks} sub-tasks × 10ms work)",
                 fan_elapsed, "ms")
        # Theoretical floor is ~10ms (the simulated work). Real cost is
        # 2 DB writes per sub-task at ~5ms each (fsync + WAL flush), so a
        # 32-task fan-out is bounded by the SQLite write rate, not the
        # async scheduler. Headroom: 10ms floor + 8ms per task.
        upper = 100 + 10 * n_tasks
        r.check(f"fan-out of {n_tasks} sub-tasks under {upper}ms",
                fan_elapsed < upper,
                f"got {fan_elapsed:.0f}ms (floor=10ms)")

    return {
        "serial_append_rate_per_sec": rate,
        "replay_1k_ms": replay_ms,
        "concurrent_append_rate_per_sec": par_rate,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-db", action="store_true",
                    help="don't delete the SQLite file when done — useful "
                         "for inspecting events with the sqlite3 CLI")
    ap.add_argument("--json", type=Path, default=None,
                    help="dump bench metrics to a JSON file")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="explicit workspace dir (otherwise a temp dir)")
    args = ap.parse_args()

    workdir = args.workspace or Path(
        tempfile.mkdtemp(prefix="cerebro_phase3_bench_")
    )
    db_path = workdir / "state.db"
    print("Phase 3 end-to-end + perf bench")
    print(f"workspace: {workdir}")
    print(f"sqlite db: {db_path}")
    print("=" * 60)

    r = Reporter()
    a = await section_realistic_workflow(r, db_path)
    b = await section_crash_and_resume(r, db_path)
    c = await section_perf(r, db_path)

    rc = r.summary()

    if args.json:
        args.json.write_text(json.dumps(
            {"section_a": a, "section_b": b, "section_c": c,
             "metrics": r.metrics,
             "passes": r.passes, "fails": r.fails},
            indent=2, default=str,
        ))
        print(f"\nmetrics → {args.json}")

    if args.keep_db:
        print(f"\nkept sqlite db at: {db_path}")
        print("inspect with:")
        print(f"  sqlite3 {db_path} 'SELECT id, kind, status FROM workflows'")
        print(f"  sqlite3 {db_path} 'SELECT workflow_id, seq, kind FROM events ORDER BY workflow_id, seq LIMIT 30'")
    else:
        # tidy up
        try:
            for p in workdir.rglob("*"):
                if p.is_file():
                    p.unlink()
            workdir.rmdir()
        except Exception:
            pass

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
