"""Phase 3 tests: event store + parallel workflow runner.

Self-contained — no live ClickHouse / Anthropic. The event log is a fresh
SQLite file per test (via `tmp_path`); workflow sub-tasks are bare async
callables that return dicts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp.event_store import EventStore
from cerebro_mcp.workflow_payloads import (
    EVENT_LLM_CALL_COMPLETED,
    EVENT_LLM_CALL_FAILED,
    EVENT_LLM_CALL_STARTED,
    EVENT_PHASE_COMPLETED,
    EVENT_PHASE_FAILED,
    EVENT_PHASE_STARTED,
    EVENT_SUBTASK_COMPLETED,
    EVENT_SUBTASK_FAILED,
    EVENT_SUBTASK_STARTED,
    GATE_FAILED,
    GATE_READY,
    LLMCallEvent,
    LLMTurn,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
    find_unfinished_llm_calls,
)
from cerebro_mcp.workflow_runner import (
    SubTask,
    begin_workflow,
    new_workflow_id,
    run_parallel_phase,
    run_sequential_phase,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> EventStore:
    s = EventStore(db_path=tmp_path / "test_state.db",
                   compression_threshold=2_000_000)  # disable compression
    await s.init()
    return s


# ---------------------------------------------------------------------------
# EventStore basics
# ---------------------------------------------------------------------------


class TestEventStoreBasics:
    async def test_create_and_get_workflow(self, store: EventStore):
        await store.create_workflow("wf_a", "test_kind", {"foo": "bar"})
        wf = await store.get_workflow("wf_a")
        assert wf is not None
        assert wf["status"] == WORKFLOW_RUNNING
        assert wf["kind"] == "test_kind"
        assert wf["metadata"] == {"foo": "bar"}

    async def test_get_unknown_returns_none(self, store: EventStore):
        assert await store.get_workflow("nope") is None

    async def test_append_and_replay_events(self, store: EventStore):
        await store.create_workflow("wf_b", "k")
        s1 = await store.append_event("wf_b", "phase_started", {"phase": "X"})
        s2 = await store.append_event("wf_b", "subtask_completed", {"name": "a"})
        s3 = await store.append_event("wf_b", "phase_completed", {"phase": "X"})
        assert (s1, s2, s3) == (1, 2, 3)

        events = await store.replay("wf_b")
        assert [e["seq"] for e in events] == [1, 2, 3]
        assert events[0]["payload"] == {"phase": "X"}
        assert events[1]["payload"] == {"name": "a"}

    async def test_event_count(self, store: EventStore):
        await store.create_workflow("wf_count", "k")
        for i in range(5):
            await store.append_event("wf_count", "note", {"i": i})
        assert await store.event_count("wf_count") == 5

    async def test_mark_workflow_status(self, store: EventStore):
        await store.create_workflow("wf_st", "k")
        await store.mark_workflow_status("wf_st", WORKFLOW_COMPLETED)
        wf = await store.get_workflow("wf_st")
        assert wf["status"] == WORKFLOW_COMPLETED

    async def test_invalid_status_rejected(self, store: EventStore):
        await store.create_workflow("wf_bad", "k")
        with pytest.raises(ValueError, match="Invalid workflow status"):
            await store.mark_workflow_status("wf_bad", "nonsense")

    async def test_set_and_get_gate(self, store: EventStore):
        await store.create_workflow("wf_g", "k")
        await store.set_gate("wf_g", "review", GATE_READY, {"by": "human"})
        gate = await store.get_gate("wf_g", "review")
        assert gate is not None
        assert gate["status"] == GATE_READY
        assert gate["payload"] == {"by": "human"}

    async def test_gate_upsert(self, store: EventStore):
        await store.create_workflow("wf_g2", "k")
        await store.set_gate("wf_g2", "review", GATE_READY)
        await store.set_gate("wf_g2", "review", GATE_FAILED, {"reason": "low"})
        gate = await store.get_gate("wf_g2", "review")
        assert gate["status"] == GATE_FAILED
        assert gate["payload"] == {"reason": "low"}

    async def test_list_workflows_by_status(self, store: EventStore):
        await store.create_workflow("a", "k")
        await store.create_workflow("b", "k")
        await store.mark_workflow_status("b", WORKFLOW_COMPLETED)
        running = await store.list_workflows(statuses=[WORKFLOW_RUNNING])
        ids = {wf["id"] for wf in running}
        assert ids == {"a"}


# ---------------------------------------------------------------------------
# Compression + replay round-trip on large payloads
# ---------------------------------------------------------------------------


class TestEventStoreCompression:
    async def test_large_payload_compressed_and_replayable(self, tmp_path: Path):
        # Threshold 100 bytes, payload several KB → compression triggers.
        s = EventStore(db_path=tmp_path / "comp.db", compression_threshold=100)
        await s.init()
        await s.create_workflow("wf_big", "k")
        big = {"text": "x" * 10_000}
        await s.append_event("wf_big", "note", big)
        events = await s.replay("wf_big")
        assert len(events) == 1
        assert events[0]["payload"] == big


# ---------------------------------------------------------------------------
# Crash safety: open + close + reopen, events still present
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    async def test_reopen_preserves_events(self, tmp_path: Path):
        path = tmp_path / "crash.db"
        s1 = EventStore(db_path=path)
        await s1.init()
        await s1.create_workflow("wf_crash", "k")
        for i in range(3):
            await s1.append_event("wf_crash", "phase_started", {"i": i})

        # Drop the reference; open a fresh store on the same file (mimics
        # process kill + restart).
        s2 = EventStore(db_path=path)
        events = await s2.replay("wf_crash")
        assert [e["payload"]["i"] for e in events] == [0, 1, 2]
        wf = await s2.get_workflow("wf_crash")
        assert wf["status"] == WORKFLOW_RUNNING


# ---------------------------------------------------------------------------
# LLMCallEvent payload contract + resume helper
# ---------------------------------------------------------------------------


class TestLLMPayloads:
    async def test_llm_call_event_roundtrip(self, store: EventStore):
        await store.create_workflow("wf_llm", "k")
        call = LLMCallEvent(
            subtask_name="defi_analyst",
            call_id="call_001",
            system_prompt="You are an analyst.",
            messages=[
                LLMTurn(role="user", content=[{"type": "text", "text": "TVL?"}]),
            ],
            tool_schemas=[{"name": "execute_query"}],
        )
        await store.append_event("wf_llm", EVENT_LLM_CALL_STARTED, call)
        events = await store.replay("wf_llm")
        replayed = LLMCallEvent.from_dict(events[0]["payload"])
        assert replayed.subtask_name == "defi_analyst"
        assert replayed.call_id == "call_001"
        assert replayed.messages[0].role == "user"

    async def test_find_unfinished_llm_calls(self, store: EventStore):
        await store.create_workflow("wf_unf", "k")

        def _ev(call_id: str, kind: str):
            return LLMCallEvent(
                subtask_name="x", call_id=call_id, system_prompt="",
                messages=[], tool_schemas=[],
            ), kind

        # Two calls started; only one completed. The other should surface
        # as unfinished.
        a, k1 = _ev("a", EVENT_LLM_CALL_STARTED)
        await store.append_event("wf_unf", k1, a)
        b, k2 = _ev("b", EVENT_LLM_CALL_STARTED)
        await store.append_event("wf_unf", k2, b)
        a_done, k3 = _ev("a", EVENT_LLM_CALL_COMPLETED)
        await store.append_event("wf_unf", k3, a_done)

        events = await store.replay("wf_unf")
        unfinished = find_unfinished_llm_calls(events)
        assert {c.call_id for c in unfinished} == {"b"}

    async def test_failed_llm_call_clears_unfinished(self, store: EventStore):
        await store.create_workflow("wf_fail", "k")
        for kind in (EVENT_LLM_CALL_STARTED, EVENT_LLM_CALL_FAILED):
            await store.append_event("wf_fail", kind, LLMCallEvent(
                subtask_name="x", call_id="c1", system_prompt="",
                messages=[], tool_schemas=[],
            ))
        events = await store.replay("wf_fail")
        assert find_unfinished_llm_calls(events) == []


# ---------------------------------------------------------------------------
# Workflow runner — parallel + sequential
# ---------------------------------------------------------------------------


class TestParallelRunner:
    async def test_all_succeed(self, store: EventStore):
        wid = new_workflow_id("test")
        await begin_workflow(wid, "decomposable_test", store=store)

        async def make_result(name: str):
            await asyncio.sleep(0.01)  # force concurrency
            return {"name": name, "value": 42}

        subtasks = [
            SubTask("a", lambda: make_result("a")),
            SubTask("b", lambda: make_result("b")),
            SubTask("c", lambda: make_result("c")),
        ]
        out = await run_parallel_phase(
            wid, "discovery", subtasks, "discovery_ready", store=store,
        )
        assert set(out) == {"a", "b", "c"}
        gate = await store.get_gate(wid, "discovery_ready")
        assert gate["status"] == GATE_READY

    async def test_one_failure_marks_workflow_failed(self, store: EventStore):
        wid = new_workflow_id("test")
        await begin_workflow(wid, "decomposable_test", store=store)

        async def good():
            return {"ok": True}

        async def bad():
            raise RuntimeError("explode")

        subtasks = [SubTask("good", good), SubTask("bad", bad)]
        with pytest.raises(RuntimeError, match="failed"):
            await run_parallel_phase(
                wid, "discovery", subtasks, "discovery_ready", store=store,
            )
        gate = await store.get_gate(wid, "discovery_ready")
        assert gate["status"] == GATE_FAILED
        assert "bad" in gate["payload"]["failed"]
        wf = await store.get_workflow(wid)
        assert wf["status"] == WORKFLOW_FAILED

    async def test_event_log_records_full_phase(self, store: EventStore):
        wid = new_workflow_id("test")
        await begin_workflow(wid, "log_check", store=store)

        async def quick(name: str):
            return {"name": name}

        await run_parallel_phase(
            wid, "p1",
            [SubTask("a", lambda: quick("a")), SubTask("b", lambda: quick("b"))],
            "p1_ready", store=store,
        )
        events = await store.replay(wid)
        kinds = [e["kind"] for e in events]
        # Must contain a phase_started + phase_completed and 2 subtask pairs.
        assert kinds.count(EVENT_PHASE_STARTED) >= 1
        assert kinds.count(EVENT_PHASE_COMPLETED) >= 1
        assert kinds.count(EVENT_SUBTASK_STARTED) == 2
        assert kinds.count(EVENT_SUBTASK_COMPLETED) == 2

    async def test_max_parallel_bounded(self, store: EventStore):
        # If 5 sub-tasks each sleep 50ms and max_parallel=1, total wall time
        # must be ≥ 250ms (serial). With max_parallel=5 it'd be ~50ms.
        wid = new_workflow_id("test")
        await begin_workflow(wid, "bounded_test", store=store)

        async def slow(name: str):
            await asyncio.sleep(0.05)
            return {"name": name}

        subtasks = [SubTask(f"t{i}", lambda i=i: slow(f"t{i}")) for i in range(5)]

        import time
        t0 = time.perf_counter()
        await run_parallel_phase(
            wid, "bounded", subtasks, "bounded_gate", store=store, max_parallel=1,
        )
        elapsed = time.perf_counter() - t0
        # Allow some slack for scheduler overhead but it must be clearly
        # serial (≥ 0.20s for 5×50ms with bound=1).
        assert elapsed >= 0.20, f"expected ≥0.20s serial run, got {elapsed:.3f}"


class TestSequentialRunner:
    async def test_sequential_basic(self, store: EventStore):
        wid = new_workflow_id("test")
        await begin_workflow(wid, "seq_test", store=store)

        async def step(name: str):
            return {"name": name, "ts": name}

        out = await run_sequential_phase(
            wid, "chain",
            [SubTask("s1", lambda: step("s1")),
             SubTask("s2", lambda: step("s2")),
             SubTask("s3", lambda: step("s3"))],
            store=store,
        )
        assert list(out) == ["s1", "s2", "s3"]

    async def test_sequential_short_circuits_on_failure(self, store: EventStore):
        wid = new_workflow_id("test")
        await begin_workflow(wid, "seq_fail", store=store)

        seen = []

        async def good(name: str):
            seen.append(name)
            return {"name": name}

        async def bad():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await run_sequential_phase(
                wid, "chain",
                [SubTask("s1", lambda: good("s1")),
                 SubTask("s2", bad),
                 SubTask("s3", lambda: good("s3"))],
                store=store,
            )
        # `s3` must NOT have run — sequential phases short-circuit.
        assert seen == ["s1"]


# ---------------------------------------------------------------------------
# Orphan detection (the bootstrap path)
# ---------------------------------------------------------------------------


class TestOrphanDetection:
    async def test_old_running_workflow_is_orphan_candidate(self, store: EventStore):
        await store.create_workflow("old", "k")
        await store.create_workflow("new", "k")
        # Force `old` to look stale.
        async with store._connect() as db:
            await db.execute(
                "UPDATE workflows SET updated_at = updated_at - 100000 "
                "WHERE id = 'old'"
            )
            await db.commit()
        candidates = await store.list_workflows(
            statuses=[WORKFLOW_RUNNING], older_than_seconds=3600,
        )
        ids = {c["id"] for c in candidates}
        assert ids == {"old"}

    async def test_completed_workflows_skipped_in_orphan_sweep(
        self, store: EventStore
    ):
        await store.create_workflow("done_old", "k")
        await store.mark_workflow_status("done_old", WORKFLOW_COMPLETED)
        async with store._connect() as db:
            await db.execute(
                "UPDATE workflows SET updated_at = updated_at - 100000 "
                "WHERE id = 'done_old'"
            )
            await db.commit()
        candidates = await store.list_workflows(
            statuses=[WORKFLOW_RUNNING], older_than_seconds=3600,
        )
        assert candidates == []
