"""Phase 3 migration: research_workflow → event log.

Verifies that `tools/research.py` MCP tools emit the right events into
the SQLite event store as a research project moves through its phases.

Each test starts with a fresh `EVENT_STORE_PATH` (tmp_path) and exercises
the relevant slice of the research workflow, then reads the event store
back to confirm the expected events + gates landed.

Tests use the sync event-store API (`event_store_sync`) directly — no
need to spin up FastMCP. The integration with the MCP tools is covered
by the existing `tests/test_research_*` suites.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cerebro_mcp import config as cerebro_config
from cerebro_mcp import event_store_sync as ev
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.workflow_payloads import (
    GATE_FAILED,
    GATE_PASSED,
    WORKFLOW_COMPLETED,
    WORKFLOW_RUNNING,
)


@pytest.fixture
def event_db(tmp_path: Path, monkeypatch):
    """Point `EVENT_STORE_PATH` at a fresh tmp file and bootstrap the
    schema (the production path does this in `bootstrap.init_event_store_*`).
    """
    db_path = tmp_path / "research_state.db"
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH", str(db_path),
        raising=True,
    )
    # Bootstrap the schema once via the async store so the sync helpers
    # can assume the tables exist.
    import asyncio
    store = EventStore(db_path=db_path)
    asyncio.run(store.init())
    return db_path


def _all_events(db_path: Path, workflow_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT seq, kind FROM events WHERE workflow_id = ? ORDER BY seq",
        (workflow_id,),
    )
    return [{"seq": r[0], "kind": r[1]} for r in cur.fetchall()]


def _workflow_row(db_path: Path, workflow_id: str) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT id, kind, status FROM workflows WHERE id = ?",
        (workflow_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "kind": row[1], "status": row[2]}


def _gate(db_path: Path, workflow_id: str, gate: str) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT status, payload_json FROM gates "
        "WHERE workflow_id = ? AND gate_name = ?",
        (workflow_id, gate),
    )
    row = cur.fetchone()
    if not row:
        return None
    import json as _json
    return {"status": row[0], "payload": _json.loads(row[1] or "{}")}


# ---------------------------------------------------------------------------
# Domain-specific helpers
# ---------------------------------------------------------------------------


class TestResearchEventLogIntegration:
    def test_started_creates_workflow_and_event(self, event_db):
        ev.record_research_started("proj_001", "Hypothesis: X.", "Scope: Y.")
        wid = ev.workflow_id_for_research("proj_001")

        wf = _workflow_row(event_db, wid)
        assert wf is not None
        assert wf["kind"] == "research_project"
        assert wf["status"] == WORKFLOW_RUNNING

        events = _all_events(event_db, wid)
        assert len(events) == 1
        assert events[0]["kind"] == "workflow_started"

    def test_phase_lifecycle_records_planned_and_completed(self, event_db):
        ev.record_research_started("proj_002", "h", "s")
        ev.record_research_phase_planned("proj_002", "mapping", "## Plan\nQ1...")
        ev.record_research_phase_completed("proj_002", "mapping",
                                           advanced_to="hypothesis")

        wid = ev.workflow_id_for_research("proj_002")
        events = _all_events(event_db, wid)
        kinds = [e["kind"] for e in events]
        assert kinds == ["workflow_started", "phase_planned", "phase_completed"]

    def test_verification_pass_sets_gate_passed(self, event_db):
        ev.record_research_started("proj_v", "h", "s")
        ev.record_research_verification(
            "proj_v", "verification", passed=True, summary="all checks ok",
        )
        wid = ev.workflow_id_for_research("proj_v")
        gate = _gate(event_db, wid, "verification:verification")
        assert gate is not None
        assert gate["status"] == GATE_PASSED

    def test_verification_fail_sets_gate_failed(self, event_db):
        ev.record_research_started("proj_v_fail", "h", "s")
        ev.record_research_verification(
            "proj_v_fail", "verification", passed=False,
            summary="schema check failed",
        )
        wid = ev.workflow_id_for_research("proj_v_fail")
        gate = _gate(event_db, wid, "verification:verification")
        assert gate is not None
        assert gate["status"] == GATE_FAILED

    def test_peer_review_approved_passes_gate(self, event_db):
        ev.record_research_started("proj_pr", "h", "s")
        ev.record_research_peer_review("proj_pr", "approved", "looks good")
        wid = ev.workflow_id_for_research("proj_pr")
        gate = _gate(event_db, wid, "peer_review")
        assert gate["status"] == GATE_PASSED
        assert gate["payload"]["verdict"] == "approved"

    def test_peer_review_rejected_fails_gate(self, event_db):
        ev.record_research_started("proj_pr_r", "h", "s")
        ev.record_research_peer_review("proj_pr_r", "rejected",
                                       "stats too thin")
        wid = ev.workflow_id_for_research("proj_pr_r")
        gate = _gate(event_db, wid, "peer_review")
        assert gate["status"] == GATE_FAILED

    def test_publication_marks_workflow_completed(self, event_db):
        ev.record_research_started("proj_pub", "h", "s")
        ev.record_research_published("proj_pub", "rep_abc",
                                     "Q3 Health Report")
        wid = ev.workflow_id_for_research("proj_pub")
        wf = _workflow_row(event_db, wid)
        assert wf["status"] == WORKFLOW_COMPLETED
        events = _all_events(event_db, wid)
        # workflow_started + report_published
        assert any(e["kind"] == "report_published" for e in events)

    def test_full_lifecycle_event_sequence(self, event_db):
        """End-to-end: started → planned → completed → verified → peer
        review approved → published. Every transition lands in the log
        and gates flip correctly."""
        pid = "proj_full"
        ev.record_research_started(pid, "Hypothesis Z", "Scope W")
        for phase, advance_to in [
            ("mapping", "hypothesis"),
            ("hypothesis", "execution"),
            ("execution", "verification"),
        ]:
            ev.record_research_phase_planned(pid, phase, f"plan for {phase}")
            ev.record_research_phase_completed(pid, phase, advance_to)
        ev.record_research_verification(pid, "verification", passed=True,
                                        summary="ok")
        ev.record_research_peer_review(pid, "approved", "lgtm")
        ev.record_research_published(pid, "rep_full", "Final Report")

        wid = ev.workflow_id_for_research(pid)
        events = _all_events(event_db, wid)
        kinds = [e["kind"] for e in events]
        # Expected order, allowing for the trailing report_published
        # to come after verification + peer_review events.
        assert kinds[0] == "workflow_started"
        assert kinds.count("phase_planned") == 3
        assert kinds.count("phase_completed") == 3
        assert "verification_completed" in kinds
        assert "peer_review_recorded" in kinds
        assert kinds[-1] == "report_published"

        # Gates: both verification and peer_review should be passed.
        assert _gate(event_db, wid, "verification:verification")["status"] == GATE_PASSED
        assert _gate(event_db, wid, "peer_review")["status"] == GATE_PASSED

        wf = _workflow_row(event_db, wid)
        assert wf["status"] == WORKFLOW_COMPLETED


# ---------------------------------------------------------------------------
# Failure tolerance — the helpers must NEVER raise. Production research
# tools must keep working even if the event store is broken.
# ---------------------------------------------------------------------------


class TestFailureTolerance:
    def test_record_started_does_not_raise_on_db_error(self, monkeypatch):
        # Point the event store at an unwritable path; the helper should
        # log + return without raising.
        monkeypatch.setattr(
            cerebro_config.settings, "EVENT_STORE_PATH",
            "/nonexistent_dir/cant_write/state.db",
            raising=True,
        )
        # Should NOT raise.
        ev.record_research_started("proj_x", "h", "s")
        ev.record_research_phase_completed("proj_x", "mapping", "hypothesis")
        ev.record_research_peer_review("proj_x", "approved", "")
        ev.record_research_published("proj_x", "r", "t")

    def test_invalid_status_returns_false_not_raises(self, event_db):
        # Direct call with a bad status — must return False, not raise.
        ok = ev.set_gate_safe("wid_x", "g", "totally_invalid_status", {})
        assert ok is False

    def test_duplicate_workflow_returns_false_not_raises(self, event_db):
        ev.record_research_started("proj_dup", "h", "s")
        # Second create on same project_id — workflow already exists.
        # create_workflow_safe must catch IntegrityError and return False.
        ok = ev.create_workflow_safe(
            ev.workflow_id_for_research("proj_dup"), "research_project",
        )
        assert ok is False
