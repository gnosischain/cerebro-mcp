#!/usr/bin/env python3
"""Phase 3 smoke test — exercise event store + parallel/sequential runner.

End-to-end demonstration of:

1. Open a fresh SQLite event store, schema is created (WAL mode active).
2. Begin a workflow, fan out 3 concurrent sub-tasks, gate at a reviewer.
3. Survive a "process kill" (close + reopen the store on the same file)
   and confirm every event replayed cleanly.
4. Detect an unfinished `llm_call_started` and surface it for resume.
5. Demonstrate orphan detection on a stale workflow.
6. Sequential phase short-circuits at the first failure.

Each section prints PASS / FAIL with a short detail. Exit code is non-zero
if any section fails. No external services required — runs anywhere.

Usage:
    python scripts/test_phase3_workflows.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.event_store import EventStore  # noqa: E402
from cerebro_mcp.workflow_payloads import (  # noqa: E402
    EVENT_LLM_CALL_COMPLETED,
    EVENT_LLM_CALL_STARTED,
    GATE_FAILED,
    GATE_READY,
    LLMCallEvent,
    LLMTurn,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_RUNNING,
    find_unfinished_llm_calls,
)
from cerebro_mcp.workflow_runner import (  # noqa: E402
    SubTask,
    begin_workflow,
    new_workflow_id,
    run_parallel_phase,
    run_sequential_phase,
)


# ---------------------------------------------------------------------------
# Tiny test runner
# ---------------------------------------------------------------------------


class Reporter:
    def __init__(self) -> None:
        self.passes = 0
        self.fails = 0

    def section(self, name: str) -> None:
        print(f"\n=== {name} ===")

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        if ok: self.passes += 1
        else:  self.fails += 1

    def summary(self) -> int:
        total = self.passes + self.fails
        print(f"\n--- {self.passes}/{total} checks passed "
              f"({self.fails} failures) ---")
        return 0 if self.fails == 0 else 1


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


async def section_basics(r: Reporter, db_path: Path) -> None:
    r.section("1. EventStore basics: create, append, replay")
    s = EventStore(db_path=db_path)
    await s.init()

    await s.create_workflow("smoke_basic", "demo")
    seq1 = await s.append_event("smoke_basic", "phase_started", {"phase": "discovery"})
    seq2 = await s.append_event("smoke_basic", "subtask_completed", {"name": "a"})
    seq3 = await s.append_event("smoke_basic", "phase_completed", {"phase": "discovery"})
    r.check("seq numbers monotonic from 1", (seq1, seq2, seq3) == (1, 2, 3),
            f"got {seq1},{seq2},{seq3}")

    events = await s.replay("smoke_basic")
    r.check("replay returns 3 events", len(events) == 3)
    r.check("replay payloads intact",
            events[0]["payload"] == {"phase": "discovery"})
    wf = await s.get_workflow("smoke_basic")
    r.check("workflow_started and updated_at touched", wf["status"] == WORKFLOW_RUNNING)


async def section_parallel(r: Reporter, db_path: Path) -> None:
    r.section("2. Parallel fan-out — 3 analysts gated at reviewer")
    s = EventStore(db_path=db_path)
    await s.init()
    wid = new_workflow_id("smoke_p")
    await begin_workflow(wid, "decomposable_demo", store=s)

    async def analyst_a():
        await asyncio.sleep(0.05)
        return {"models": ["A1"], "kpi": 100}

    async def analyst_b():
        await asyncio.sleep(0.05)
        return {"models": ["B1", "B2"], "kpi": 200}

    async def analyst_c():
        await asyncio.sleep(0.05)
        return {"models": ["C1"], "kpi": 50}

    t0 = time.perf_counter()
    out = await run_parallel_phase(
        wid, "discovery",
        [SubTask("a", analyst_a), SubTask("b", analyst_b), SubTask("c", analyst_c)],
        "reviewer_input_ready", store=s,
    )
    elapsed = time.perf_counter() - t0
    r.check("3 analyst sub-tasks all returned", set(out) == {"a", "b", "c"})
    r.check("ran concurrently (3×50ms <0.18s)",
            elapsed < 0.18, f"elapsed={elapsed*1000:.0f}ms")
    gate = await s.get_gate(wid, "reviewer_input_ready")
    r.check("reviewer gate reached READY", gate["status"] == GATE_READY)


async def section_failure_path(r: Reporter, db_path: Path) -> None:
    r.section("3. Fan-out with one failure → workflow marked failed, gate FAILED")
    s = EventStore(db_path=db_path)
    await s.init()
    wid = new_workflow_id("smoke_fail")
    await begin_workflow(wid, "fail_demo", store=s)

    async def good():
        return {"ok": True}

    async def bad():
        raise RuntimeError("clickhouse memory_limit_exceeded")

    raised = False
    try:
        await run_parallel_phase(
            wid, "discovery",
            [SubTask("good", good), SubTask("bad", bad), SubTask("good2", good)],
            "reviewer_input_ready", store=s,
        )
    except RuntimeError:
        raised = True
    r.check("RuntimeError propagated to caller", raised)
    gate = await s.get_gate(wid, "reviewer_input_ready")
    r.check("gate marked FAILED", gate["status"] == GATE_FAILED)
    r.check("succeeded subtasks recorded in gate payload",
            "good" in gate["payload"]["succeeded"]
            and "good2" in gate["payload"]["succeeded"])
    wf = await s.get_workflow(wid)
    r.check("workflow status FAILED", wf["status"] == WORKFLOW_FAILED)


async def section_crash_recovery(r: Reporter, db_path: Path) -> None:
    r.section("4. Crash recovery: close + reopen, events still there")
    s1 = EventStore(db_path=db_path)
    await s1.init()
    await s1.create_workflow("smoke_crash", "demo")
    for i in range(5):
        await s1.append_event("smoke_crash", "phase_started", {"i": i})

    # Drop reference, open fresh store on same file (mimics process restart).
    del s1
    s2 = EventStore(db_path=db_path)
    events = await s2.replay("smoke_crash")
    r.check("5 events recovered after reopen",
            [e["payload"]["i"] for e in events] == [0, 1, 2, 3, 4])
    wf = await s2.get_workflow("smoke_crash")
    r.check("workflow row recovered", wf is not None)


async def section_resume_unfinished_llm(r: Reporter, db_path: Path) -> None:
    r.section("5. Resume hint: detect interrupted LLM calls")
    s = EventStore(db_path=db_path)
    await s.init()
    await s.create_workflow("smoke_llm", "demo")

    completed = LLMCallEvent(
        subtask_name="defi_analyst", call_id="call_a",
        system_prompt="…", messages=[
            LLMTurn(role="user", content=[{"type": "text", "text": "TVL?"}]),
        ], tool_schemas=[{"name": "execute_query"}],
    )
    interrupted = LLMCallEvent(
        subtask_name="growth_analyst", call_id="call_b",
        system_prompt="…", messages=[
            LLMTurn(role="user", content=[{"type": "text", "text": "growth?"}]),
        ], tool_schemas=[{"name": "execute_query"}],
    )

    await s.append_event("smoke_llm", EVENT_LLM_CALL_STARTED, completed)
    await s.append_event("smoke_llm", EVENT_LLM_CALL_COMPLETED, completed)
    await s.append_event("smoke_llm", EVENT_LLM_CALL_STARTED, interrupted)
    # NB: no completed/failed for `interrupted` — simulates a 529 timeout.

    events = await s.replay("smoke_llm")
    unfinished = find_unfinished_llm_calls(events)
    r.check("exactly one unfinished call surfaced", len(unfinished) == 1,
            f"got {len(unfinished)}")
    r.check("the unfinished one is growth_analyst/call_b",
            unfinished and unfinished[0].call_id == "call_b")
    r.check("messages history available for re-issue",
            unfinished[0].messages[0].content[0]["text"] == "growth?")


async def section_orphan_sweep(r: Reporter, db_path: Path) -> None:
    r.section("6. Orphan detection: stale workflows identified by bootstrap")
    s = EventStore(db_path=db_path)
    await s.init()
    await s.create_workflow("orphan_old", "demo")
    await s.create_workflow("orphan_new", "demo")
    # Force `orphan_old` into the past.
    async with s._connect() as db:
        await db.execute(
            "UPDATE workflows SET updated_at = updated_at - 100000 "
            "WHERE id = 'orphan_old'"
        )
        await db.commit()
    candidates = await s.list_workflows(
        statuses=[WORKFLOW_RUNNING], older_than_seconds=3600,
    )
    ids = {c["id"] for c in candidates}
    r.check("only the stale workflow is orphan-eligible",
            ids == {"orphan_old"}, f"got {ids}")


async def section_sequential(r: Reporter, db_path: Path) -> None:
    r.section("7. Sequential phase short-circuits at first failure")
    s = EventStore(db_path=db_path)
    await s.init()
    wid = new_workflow_id("smoke_seq")
    await begin_workflow(wid, "seq_demo", store=s)

    seen: list[str] = []

    async def good(name: str):
        seen.append(name)
        return {"name": name}

    async def bad():
        raise RuntimeError("statistical_reviewer rejected")

    raised = False
    try:
        await run_sequential_phase(
            wid, "math_chain",
            [SubTask("s1", lambda: good("s1")),
             SubTask("s2", bad),
             SubTask("s3", lambda: good("s3"))],
            store=s,
        )
    except RuntimeError:
        raised = True
    r.check("sequential phase raised on s2", raised)
    r.check("s3 NOT executed (short-circuit)", seen == ["s1"])


async def main() -> int:
    print("Phase 3 workflow smoke test")
    workdir = Path(tempfile.mkdtemp(prefix="cerebro_phase3_smoke_"))
    db_path = workdir / "state.db"
    print(f"workspace: {workdir}")
    print("=" * 60)

    r = Reporter()
    await section_basics(r, db_path)
    await section_parallel(r, db_path)
    await section_failure_path(r, db_path)
    await section_crash_recovery(r, db_path)
    await section_resume_unfinished_llm(r, db_path)
    await section_orphan_sweep(r, db_path)
    await section_sequential(r, db_path)
    return r.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
