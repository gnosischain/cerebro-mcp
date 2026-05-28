"""Phase 3 Step 1 expansion — event capture for research work tools.

The original migration only captured PHASE-LEVEL transitions (start/plan/
execute/verify/peer_review/publish). The actual analytical work — queries,
memories, findings, evidence — bypassed the event log, so a crashed
session left the resume hint stuck at "ready_to_resume at phase mapping"
with no visibility into what the agent had actually done.

This expansion adds 4 event kinds and updates the research resume handler
to surface them. These tests verify each new helper writes the expected
event AND that the resume hint folds them into a useful summary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import config as cerebro_config
from cerebro_mcp.workflow import event_store_sync as ev
from cerebro_mcp.workflow.event_store import EventStore
from cerebro_mcp.research.resume import (
    _scan_work,
    resume_research_project,
)
from cerebro_mcp.workflow.registry import (
    ACTION_READY_TO_RESUME,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "work_events.db"
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH", str(db_path),
        raising=True,
    )
    ev._reset_bootstrap_cache()
    s = EventStore(db_path=db_path)
    await s.init()
    return s


# ---------------------------------------------------------------------------
# Helper-level: each new event kind lands the expected payload
# ---------------------------------------------------------------------------


class TestNewEventHelpers:
    async def test_query_executed_event(self, store):
        ev.record_research_started("p_q", "h", "s")
        ev.record_research_query_executed(
            "p_q", sql="SELECT count(*) FROM dbt.t",
            database="dbt", row_count=1,
            elapsed_seconds=0.123, evidence_title="row count smoke",
        )
        wid = ev.workflow_id_for_research("p_q")
        events = await store.replay(wid)
        last = events[-1]
        assert last["kind"] == "query_executed"
        p = last["payload"]
        assert p["sql_preview"].startswith("SELECT count(*)")
        assert p["row_count"] == 1
        assert p["evidence_title"] == "row count smoke"
        assert p["error_class"] is None

    async def test_query_executed_truncates_long_sql(self, store):
        ev.record_research_started("p_qlong", "h", "s")
        long_sql = "SELECT " + "x," * 1000 + "1 FROM t"
        ev.record_research_query_executed(
            "p_qlong", sql=long_sql, database="dbt",
            row_count=0, elapsed_seconds=0.0,
        )
        wid = ev.workflow_id_for_research("p_qlong")
        events = await store.replay(wid)
        p = events[-1]["payload"]
        assert len(p["sql_preview"]) == 1500
        assert p["sql_full_len"] == len(long_sql)

    async def test_query_executed_with_error(self, store):
        ev.record_research_started("p_qerr", "h", "s")
        ev.record_research_query_executed(
            "p_qerr", sql="SELECT bad_col FROM t",
            database="dbt", row_count=0, elapsed_seconds=0.0,
            error_class="clickhouse_code_47",
        )
        wid = ev.workflow_id_for_research("p_qerr")
        events = await store.replay(wid)
        p = events[-1]["payload"]
        assert p["error_class"] == "clickhouse_code_47"

    async def test_memory_recorded_event(self, store):
        ev.record_research_started("p_m", "h", "s")
        ev.record_research_memory_recorded(
            "p_m", memory_id="mem_123", kind="observation",
            statement="Two distinct activity waves observed.",
            confidence=0.9,
        )
        wid = ev.workflow_id_for_research("p_m")
        events = await store.replay(wid)
        last = events[-1]
        assert last["kind"] == "memory_recorded"
        assert last["payload"]["memory_id"] == "mem_123"
        assert last["payload"]["kind"] == "observation"
        assert last["payload"]["confidence"] == 0.9

    async def test_finding_recorded_event(self, store):
        ev.record_research_started("p_f", "h", "s")
        ev.record_research_finding_recorded(
            "p_f", finding_id="find_xyz",
            title="Marketplace dropoff at modal step",
            confidence=0.85, evidence_count=3,
        )
        wid = ev.workflow_id_for_research("p_f")
        events = await store.replay(wid)
        p = events[-1]["payload"]
        assert p["finding_id"] == "find_xyz"
        assert p["evidence_count"] == 3

    async def test_evidence_attached_event(self, store):
        ev.record_research_started("p_e", "h", "s")
        ev.record_research_evidence_attached(
            "p_e", kind="query_result", ref_id="qry_abc",
            phase="execution", title="EDA stats",
        )
        wid = ev.workflow_id_for_research("p_e")
        events = await store.replay(wid)
        p = events[-1]["payload"]
        assert p["kind"] == "query_result"
        assert p["phase"] == "execution"


# ---------------------------------------------------------------------------
# _scan_work folds events into the resume hint payload
# ---------------------------------------------------------------------------


class TestScanWork:
    async def test_empty_events_returns_zero_counters(self):
        out = _scan_work([])
        assert out["queries_run"] == 0
        assert out["memory_count"] == 0
        assert out["finding_count"] == 0
        assert out["evidence_count"] == 0

    async def test_counts_queries_and_failures(self):
        events = [
            {"kind": "query_executed",
             "payload": {"sql_preview": "x", "error_class": None}},
            {"kind": "query_executed",
             "payload": {"sql_preview": "y", "error_class": "clickhouse_code_47"}},
            {"kind": "query_executed",
             "payload": {"sql_preview": "z", "error_class": "clickhouse_code_184"}},
            {"kind": "query_executed",
             "payload": {"sql_preview": "w", "error_class": "clickhouse_code_47"}},
        ]
        out = _scan_work(events)
        assert out["queries_run"] == 4
        assert out["queries_failed"] == 3
        assert out["query_error_classes"] == {
            "clickhouse_code_47": 2, "clickhouse_code_184": 1,
        }

    async def test_recent_memories_capped_at_three(self):
        events = [
            {"kind": "memory_recorded",
             "payload": {"memory_id": f"m_{i}", "kind": "observation",
                         "statement_preview": f"obs {i}", "confidence": 0.5}}
            for i in range(7)
        ]
        out = _scan_work(events)
        assert out["memory_count"] == 7
        # Only the latest 3.
        assert len(out["recent_memories"]) == 3
        ids = [m["memory_id"] for m in out["recent_memories"]]
        assert ids == ["m_4", "m_5", "m_6"]

    async def test_evidence_grouped_by_phase(self):
        events = [
            {"kind": "evidence_attached",
             "payload": {"kind": "query_result", "ref_id": "q1",
                         "phase": "execution", "title": "EDA-1"}},
            {"kind": "evidence_attached",
             "payload": {"kind": "query_result", "ref_id": "q2",
                         "phase": "execution", "title": "EDA-2"}},
            {"kind": "evidence_attached",
             "payload": {"kind": "schema_snapshot", "ref_id": "s1",
                         "phase": "mapping", "title": "schema"}},
        ]
        out = _scan_work(events)
        assert out["evidence_count"] == 3
        assert out["evidence_by_phase"] == {"execution": 2, "mapping": 1}
        assert "EDA-2" in out["recent_evidence_titles"]


# ---------------------------------------------------------------------------
# Enriched resume hint — end to end
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def project_with_work(store):
    """A research workflow that has progressed through some work events
    but not phase transitions. Mirrors the real failure mode where the
    Claude session crashes while the agent is exploring inside a
    phase."""
    workflow_id = ev.workflow_id_for_research("p_real")
    await store.create_workflow(
        workflow_id, "research_project",
        metadata={"project_id": "p_real",
                  "hypothesis": "h", "scope": "s"},
    )
    await store.append_event(
        workflow_id, "workflow_started", {"project_id": "p_real"},
    )
    # Six queries (one failure)
    for i in range(6):
        err = "clickhouse_code_47" if i == 2 else None
        await store.append_event(
            workflow_id, "query_executed",
            {"sql_preview": f"SELECT col_{i} FROM t", "row_count": 100,
             "error_class": err, "evidence_title": f"q_{i}"},
        )
    # One memory
    await store.append_event(
        workflow_id, "memory_recorded",
        {"memory_id": "mem_1", "kind": "observation",
         "statement_preview": "Marketplace TWO distinct waves...",
         "confidence": 0.9},
    )
    # Two evidence items
    for i in range(2):
        await store.append_event(
            workflow_id, "evidence_attached",
            {"kind": "query_result", "ref_id": f"q_{i}",
             "phase": "execution", "title": f"EDA stat {i}"},
        )
    return workflow_id


class TestEnrichedResumeHint:
    async def test_resume_includes_work_section(
        self, store, project_with_work,
    ):
        wf = await store.get_workflow(project_with_work)
        events = await store.replay(project_with_work)
        out = await resume_research_project(project_with_work, wf, events)

        assert out.action == ACTION_READY_TO_RESUME
        assert "work" in out.resume_hint
        work = out.resume_hint["work"]
        assert work["queries_run"] == 6
        assert work["queries_failed"] == 1
        assert work["query_error_classes"] == {"clickhouse_code_47": 1}
        assert work["memory_count"] == 1
        assert work["evidence_count"] == 2
        # Resume's recent_memories should preserve the actual statement
        # so the agent on a fresh Claude session can read its own past
        # observation rather than guess.
        assert "TWO distinct waves" in (
            work["recent_memories"][0]["statement_preview"] or ""
        )

    async def test_resume_summary_line_counts_work(
        self, store, project_with_work,
    ):
        wf = await store.get_workflow(project_with_work)
        events = await store.replay(project_with_work)
        out = await resume_research_project(project_with_work, wf, events)
        # Summary line should mention queries, memories, evidence so a
        # human glancing at `list_resumable_workflows` sees real progress.
        assert "6 queries run" in out.summary
        assert "1 failed" in out.summary
        assert "1 memory entries" in out.summary
        assert "2 evidence items" in out.summary

    async def test_no_work_events_means_minimal_hint(self, store):
        """When the agent only called start_research_project, the work
        section should still exist but be all zeros — no implicit
        promotion of stale-but-empty state."""
        wid = ev.workflow_id_for_research("p_empty")
        await store.create_workflow(
            wid, "research_project",
            metadata={"project_id": "p_empty"},
        )
        await store.append_event(
            wid, "workflow_started", {"project_id": "p_empty"},
        )
        wf = await store.get_workflow(wid)
        events = await store.replay(wid)
        out = await resume_research_project(wid, wf, events)
        work = out.resume_hint["work"]
        assert work["queries_run"] == 0
        assert work["memory_count"] == 0
        assert work["finding_count"] == 0
        assert work["evidence_count"] == 0
