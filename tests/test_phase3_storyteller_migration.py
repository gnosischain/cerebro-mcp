"""Phase 3 / Sprint 3 — storyteller migration tests.

Covers the resume handler in `storyteller_resume.py` and the event
helpers in `event_store_sync.py`. The storyteller state machine itself
is NOT migrated to durable state; we only test the observability layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import config as cerebro_config
from cerebro_mcp import event_store_sync as ev
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.storyteller_resume import (
    install_storyteller_resume_handler,
    resume_storyteller_session,
)
from cerebro_mcp.workflow_payloads import (
    WORKFLOW_COMPLETED,
    WORKFLOW_RUNNING,
)
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_FAILED,
    ACTION_READY_TO_RESUME,
    default_workflow_registry,
    reset_default_workflow_registry,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "story.db"
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH", str(db_path),
        raising=True,
    )
    ev._reset_bootstrap_cache()
    s = EventStore(db_path=db_path)
    await s.init()
    return s


# ---------------------------------------------------------------------------
# Helper-level: events get appended in the right shape
# ---------------------------------------------------------------------------


class TestStorytellerHelpers:
    async def test_session_started_creates_workflow(self, store):
        ev.record_storyteller_session_started("st_abc123")
        wid = ev.workflow_id_for_storyteller("st_abc123")
        wf = await store.get_workflow(wid)
        assert wf is not None
        assert wf["kind"] == "storyteller_session"
        assert wf["status"] == WORKFLOW_RUNNING

    async def test_phase_advanced_recorded(self, store):
        ev.record_storyteller_session_started("st_p1")
        ev.record_storyteller_phase_advanced("st_p1", "context", "narrative")
        wid = ev.workflow_id_for_storyteller("st_p1")
        events = await store.replay(wid)
        kinds = [e["kind"] for e in events]
        assert kinds == ["workflow_started", "phase_advanced"]
        assert events[1]["payload"]["from"] == "context"
        assert events[1]["payload"]["to"] == "narrative"

    async def test_gate_failed_recorded(self, store):
        ev.record_storyteller_session_started("st_g1")
        ev.record_storyteller_gate_failed(
            "st_g1", gate="clarity_review", blocking_phase="write",
            reason="Title-only readthrough failed",
        )
        wid = ev.workflow_id_for_storyteller("st_g1")
        events = await store.replay(wid)
        last = events[-1]
        assert last["kind"] == "gate_failed"
        assert last["payload"]["gate"] == "clarity_review"
        assert last["payload"]["blocking_phase"] == "write"

    async def test_handoff_marks_completed(self, store):
        ev.record_storyteller_session_started("st_h1")
        ev.record_storyteller_handoff_completed(
            "st_h1", report_id="rep_xyz", style="research",
        )
        wid = ev.workflow_id_for_storyteller("st_h1")
        wf = await store.get_workflow(wid)
        assert wf["status"] == WORKFLOW_COMPLETED


# ---------------------------------------------------------------------------
# Resume handler — state machine over the event stream
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storyteller_workflow(store):
    workflow_id = ev.workflow_id_for_storyteller("sess_p")
    await store.create_workflow(
        workflow_id, "storyteller_session",
        metadata={"session_id": "sess_p"},
    )
    await store.append_event(
        workflow_id, "workflow_started", {"session_id": "sess_p"},
    )
    return workflow_id


class TestResumeHandler:
    async def test_handoff_completed_returns_complete(
        self, store, storyteller_workflow,
    ):
        await store.append_event(
            storyteller_workflow, "handoff_completed",
            {"report_id": "rep_z", "style": "research"},
        )
        wf = await store.get_workflow(storyteller_workflow)
        events = await store.replay(storyteller_workflow)
        out = await resume_storyteller_session(
            storyteller_workflow, wf, events,
        )
        assert out.action == ACTION_COMPLETE

    async def test_fresh_session_resumes_at_context(
        self, store, storyteller_workflow,
    ):
        wf = await store.get_workflow(storyteller_workflow)
        events = await store.replay(storyteller_workflow)
        out = await resume_storyteller_session(
            storyteller_workflow, wf, events,
        )
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["current_phase"] == "context"
        assert out.resume_hint["next_action"] == \
               "storyteller_record_context_brief"

    async def test_phase_advanced_updates_current(
        self, store, storyteller_workflow,
    ):
        for ev_pair in [("context", "narrative"),
                        ("narrative", "storyboard"),
                        ("storyboard", "visual_design")]:
            await store.append_event(
                storyteller_workflow, "phase_advanced",
                {"from": ev_pair[0], "to": ev_pair[1]},
            )
        wf = await store.get_workflow(storyteller_workflow)
        events = await store.replay(storyteller_workflow)
        out = await resume_storyteller_session(
            storyteller_workflow, wf, events,
        )
        assert out.resume_hint["current_phase"] == "visual_design"
        assert out.resume_hint["next_action"] == \
               "storyteller_record_visual_spec"

    async def test_unresolved_gate_failed_returns_failed(
        self, store, storyteller_workflow,
    ):
        # Walk to critique, fail the clarity gate, no recovery.
        for f, t in [("context", "narrative"),
                     ("narrative", "storyboard"),
                     ("storyboard", "visual_design"),
                     ("visual_design", "write"),
                     ("write", "critique")]:
            await store.append_event(
                storyteller_workflow, "phase_advanced",
                {"from": f, "to": t},
            )
        await store.append_event(
            storyteller_workflow, "gate_failed",
            {"gate": "clarity_review", "blocking_phase": "write",
             "reason": "title-only readthrough failed"},
        )
        wf = await store.get_workflow(storyteller_workflow)
        events = await store.replay(storyteller_workflow)
        out = await resume_storyteller_session(
            storyteller_workflow, wf, events,
        )
        assert out.action == ACTION_FAILED
        assert out.resume_hint["failed_gate"] == "clarity_review"
        assert out.resume_hint["blocking_phase"] == "write"
        assert out.resume_hint["next_action"] == \
               "storyteller_record_final_story"  # back at write

    async def test_resolved_gate_failed_returns_ready_to_resume(
        self, store, storyteller_workflow,
    ):
        # Fail then recover past the blocking phase.
        await store.append_event(
            storyteller_workflow, "phase_advanced",
            {"from": "context", "to": "narrative"},
        )
        await store.append_event(
            storyteller_workflow, "gate_failed",
            {"gate": "x", "blocking_phase": "context"},
        )
        # Move forward past the blocking phase — gate is "resolved".
        await store.append_event(
            storyteller_workflow, "phase_advanced",
            {"from": "context", "to": "narrative"},
        )
        wf = await store.get_workflow(storyteller_workflow)
        events = await store.replay(storyteller_workflow)
        out = await resume_storyteller_session(
            storyteller_workflow, wf, events,
        )
        # No longer ACTION_FAILED — agent moved past the failure.
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["current_phase"] == "narrative"


# ---------------------------------------------------------------------------
# Registration via bootstrap
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_install_registers_kind(self):
        reset_default_workflow_registry()
        install_storyteller_resume_handler()
        install_storyteller_resume_handler()  # idempotent
        registry = default_workflow_registry()
        assert registry.has_handler("storyteller_session")
        reset_default_workflow_registry()
