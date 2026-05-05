"""Phase 3 / Sprint 2 — quarterly_review migration tests.

Covers the resume handler in `quarterly_review_resume.py` and the event
helpers in `event_store_sync.py`. The MCP-tool integration is exercised
via the smoke script; here we keep tests narrow and unit-style.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import config as cerebro_config
from cerebro_mcp import event_store_sync as ev
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.quarterly_review_resume import (
    install_quarterly_review_resume_handler,
    resume_quarterly_review,
)
from cerebro_mcp.workflow_payloads import (
    WORKFLOW_COMPLETED,
    WORKFLOW_RUNNING,
)
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_READY_TO_RESUME,
    default_workflow_registry,
    reset_default_workflow_registry,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "qbr.db"
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH", str(db_path),
        raising=True,
    )
    ev._reset_bootstrap_cache()
    s = EventStore(db_path=db_path)
    await s.init()
    return s


def _read_workflow_status(db_path: Path, workflow_id: str) -> str:
    conn = sqlite3.connect(str(db_path))
    return conn.execute(
        "SELECT status FROM workflows WHERE id = ?", (workflow_id,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Helper-level: events get appended in the right shape
# ---------------------------------------------------------------------------


class TestQuarterlyHelpers:
    async def test_started_creates_workflow_and_event(self, store):
        ev.record_quarterly_review_started(
            "qbr_001", quarter="2026Q1",
            hypothesis="Q1 review", scope="quarterly_review",
        )
        wid = ev.workflow_id_for_quarterly("qbr_001")
        wf = await store.get_workflow(wid)
        assert wf is not None
        assert wf["kind"] == "quarterly_review"
        assert wf["status"] == WORKFLOW_RUNNING

        events = await store.replay(wid)
        kinds = [e["kind"] for e in events]
        assert kinds == ["workflow_started"]
        assert events[0]["payload"]["quarter"] == "2026Q1"

    async def test_evidence_attached_event_recorded(self, store):
        ev.record_quarterly_review_started("qbr_002", "2026Q1", "h", "s")
        ev.record_quarterly_evidence_attached(
            "qbr_002", kind="chart", ref_id="cht_abc", quarter="2026Q1",
        )
        wid = ev.workflow_id_for_quarterly("qbr_002")
        events = await store.replay(wid)
        kinds = [e["kind"] for e in events]
        assert kinds == ["workflow_started", "evidence_attached"]
        assert events[1]["payload"]["ref_id"] == "cht_abc"
        assert events[1]["payload"]["quarter"] == "2026Q1"

    async def test_published_marks_completed(self, store, tmp_path):
        ev.record_quarterly_review_started("qbr_003", "2026Q1", "h", "s")
        ev.record_quarterly_review_published(
            "qbr_003", report_id="rep_abc", title="Q1 Health Report",
        )
        wid = ev.workflow_id_for_quarterly("qbr_003")
        events = await store.replay(wid)
        assert events[-1]["kind"] == "report_published"
        wf = await store.get_workflow(wid)
        assert wf["status"] == WORKFLOW_COMPLETED


# ---------------------------------------------------------------------------
# Resume handler — state machine over the event stream
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def quarterly_workflow(store):
    """Minimal QBR workflow row + workflow_started event."""
    workflow_id = ev.workflow_id_for_quarterly("qbr_p")
    await store.create_workflow(
        workflow_id, "quarterly_review",
        metadata={"project_id": "qbr_p", "quarter": "2026Q1"},
    )
    await store.append_event(
        workflow_id, "workflow_started",
        {"project_id": "qbr_p", "quarter": "2026Q1"},
    )
    return workflow_id


class TestResumeHandler:
    async def test_published_returns_complete(
        self, store, quarterly_workflow,
    ):
        await store.append_event(
            quarterly_workflow, "report_published",
            {"report_id": "rep_x", "title": "T"},
        )
        wf = await store.get_workflow(quarterly_workflow)
        events = await store.replay(quarterly_workflow)
        out = await resume_quarterly_review(quarterly_workflow, wf, events)
        assert out.action == ACTION_COMPLETE

    async def test_no_evidence_yet_resumes_to_save_analysis(
        self, store, quarterly_workflow,
    ):
        wf = await store.get_workflow(quarterly_workflow)
        events = await store.replay(quarterly_workflow)
        out = await resume_quarterly_review(quarterly_workflow, wf, events)
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["next_action"] == "save_quarterly_analysis"
        assert out.resume_hint["evidence_count"] == 0

    async def test_few_evidence_still_attaches(
        self, store, quarterly_workflow,
    ):
        for i in range(2):
            await store.append_event(
                quarterly_workflow, "evidence_attached",
                {"kind": "chart", "ref_id": f"c_{i}", "quarter": "2026Q1"},
            )
        wf = await store.get_workflow(quarterly_workflow)
        events = await store.replay(quarterly_workflow)
        out = await resume_quarterly_review(quarterly_workflow, wf, events)
        assert out.resume_hint["next_action"] == "save_quarterly_analysis"
        assert out.resume_hint["evidence_count"] == 2

    async def test_many_evidence_nudges_publish(
        self, store, quarterly_workflow,
    ):
        for i in range(4):
            await store.append_event(
                quarterly_workflow, "evidence_attached",
                {"kind": "chart", "ref_id": f"c_{i}", "quarter": "2026Q1"},
            )
        wf = await store.get_workflow(quarterly_workflow)
        events = await store.replay(quarterly_workflow)
        out = await resume_quarterly_review(quarterly_workflow, wf, events)
        assert out.resume_hint["next_action"] == "publish_quarterly_review"
        assert out.resume_hint["evidence_count"] == 4

    async def test_quarter_extracted_from_events(
        self, store, quarterly_workflow,
    ):
        await store.append_event(
            quarterly_workflow, "evidence_attached",
            {"kind": "chart", "ref_id": "c_x", "quarter": "2026Q2"},
        )
        wf = await store.get_workflow(quarterly_workflow)
        events = await store.replay(quarterly_workflow)
        out = await resume_quarterly_review(quarterly_workflow, wf, events)
        # Most-recent quarter referenced in events wins over metadata.
        assert out.resume_hint["quarter"] == "2026Q2"


# ---------------------------------------------------------------------------
# install_quarterly_review_resume_handler — registration is idempotent and
# the registry dispatches to it.
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_install_registers_kind(self):
        reset_default_workflow_registry()
        install_quarterly_review_resume_handler()
        install_quarterly_review_resume_handler()  # idempotent
        registry = default_workflow_registry()
        assert registry.has_handler("quarterly_review")
        reset_default_workflow_registry()

    async def test_registry_dispatches_to_quarterly_handler(
        self, store, quarterly_workflow, monkeypatch,
    ):
        # Wire the singletons so resume() uses our isolated store.
        from cerebro_mcp import event_store as event_store_mod
        from cerebro_mcp import workflow_registry as workflow_registry_mod
        monkeypatch.setattr(
            event_store_mod, "_default_store", store, raising=False,
        )
        workflow_registry_mod._default_registry = None  # type: ignore[attr-defined]
        install_quarterly_review_resume_handler()
        registry = default_workflow_registry()
        outcome = await registry.resume(quarterly_workflow)
        assert outcome.kind == "quarterly_review"
        assert outcome.action == ACTION_READY_TO_RESUME
        reset_default_workflow_registry()
