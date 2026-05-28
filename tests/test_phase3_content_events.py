"""Phase 3 Step 1 expansion to QBR + storyteller — content events.

Mirrors the research work-event expansion. Adds:
  - Storyteller content events: context_brief / big_idea / storyboard /
    visual_spec / final_story
  - QBR note event: observation / priority / action

Plus enriched resume-hint payloads for both kinds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import config as cerebro_config
from cerebro_mcp.workflow import event_store_sync as ev
from cerebro_mcp.workflow.event_store import EventStore
from cerebro_mcp.storyteller.resume import (
    _scan_content,
    resume_storyteller_session,
)
from cerebro_mcp.workflow.registry import (
    ACTION_READY_TO_RESUME,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "content_events.db"
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH", str(db_path),
        raising=True,
    )
    ev._reset_bootstrap_cache()
    s = EventStore(db_path=db_path)
    await s.init()
    return s


# ---------------------------------------------------------------------------
# Storyteller helpers + content events
# ---------------------------------------------------------------------------


class TestStorytellerContentHelpers:
    async def test_context_brief_event(self, store):
        ev.record_storyteller_session_started("st_ctx")
        ev.record_storyteller_context_brief_recorded(
            "st_ctx", audience="VPs and engineering leadership",
            mechanism="memo", required_action="approve Q1 budget",
        )
        wid = ev.workflow_id_for_storyteller("st_ctx")
        events = await store.replay(wid)
        last = events[-1]
        assert last["kind"] == "context_brief_recorded"
        assert "VPs and engineering" in last["payload"]["audience"]
        assert last["payload"]["mechanism"] == "memo"

    async def test_big_idea_event(self, store):
        ev.record_storyteller_session_started("st_bi")
        ev.record_storyteller_big_idea_recorded(
            "st_bi",
            sentence="Q3 retention is up 8% MoM but driven entirely by a single onboarding cohort.",
            stakes="Without diversifying the cohort, growth stalls in Q4.",
        )
        wid = ev.workflow_id_for_storyteller("st_bi")
        events = await store.replay(wid)
        p = events[-1]["payload"]
        assert "Q3 retention is up 8%" in p["sentence"]
        assert "diversifying" in p["stakes"]

    async def test_storyboard_event(self, store):
        ev.record_storyteller_session_started("st_sb")
        ev.record_storyteller_storyboard_recorded(
            "st_sb", scene_count=5, narrative_order="chronological",
            rationale="Build trust, then reveal the cohort dependency.",
        )
        events = await store.replay(ev.workflow_id_for_storyteller("st_sb"))
        p = events[-1]["payload"]
        assert p["scene_count"] == 5
        assert p["narrative_order"] == "chronological"

    async def test_visual_spec_per_scene(self, store):
        ev.record_storyteller_session_started("st_vs")
        for i in range(3):
            ev.record_storyteller_visual_spec_recorded(
                "st_vs", scene_index=i, chart_family="line",
                relationship="trend",
                action_title=f"Scene {i}: retention by month",
            )
        events = await store.replay(ev.workflow_id_for_storyteller("st_vs"))
        spec_events = [e for e in events if e["kind"] == "visual_spec_recorded"]
        assert len(spec_events) == 3
        assert {e["payload"]["scene_index"] for e in spec_events} == {0, 1, 2}

    async def test_final_story_event(self, store):
        ev.record_storyteller_session_started("st_fs")
        ev.record_storyteller_final_story_recorded(
            "st_fs", title="Q3 Retention Brief", content_length=4823,
        )
        events = await store.replay(ev.workflow_id_for_storyteller("st_fs"))
        p = events[-1]["payload"]
        assert p["title"] == "Q3 Retention Brief"
        assert p["content_length"] == 4823


# ---------------------------------------------------------------------------
# _scan_content folds events into the resume hint
# ---------------------------------------------------------------------------


class TestStorytellerScanContent:
    async def test_empty_returns_default_shape(self):
        out = _scan_content([])
        assert out["audience"] is None
        assert out["big_idea_sentence"] is None
        assert out["storyboard_scene_count"] == 0
        assert out["visual_specs_recorded"] == []
        assert out["final_story_title"] is None

    async def test_picks_up_full_pipeline(self):
        events = [
            {"kind": "context_brief_recorded",
             "payload": {"audience": "exec team", "mechanism": "memo",
                         "required_action": "approve"}},
            {"kind": "big_idea_recorded",
             "payload": {"sentence": "Cashback drives retention.",
                         "stakes": "Cutting it kills growth."}},
            {"kind": "storyboard_recorded",
             "payload": {"scene_count": 4, "narrative_order": "lead_with_ending"}},
            {"kind": "visual_spec_recorded",
             "payload": {"scene_index": 0, "chart_family": "line",
                         "relationship": "trend", "action_title": "x"}},
            {"kind": "visual_spec_recorded",
             "payload": {"scene_index": 1, "chart_family": "bar_vertical",
                         "relationship": "category_comparison",
                         "action_title": "y"}},
            {"kind": "final_story_recorded",
             "payload": {"title": "Cashback Brief", "content_length": 2100}},
        ]
        out = _scan_content(events)
        assert out["audience"] == "exec team"
        assert "Cashback drives retention" in out["big_idea_sentence"]
        assert out["storyboard_scene_count"] == 4
        assert out["visual_specs_recorded"] == [0, 1]
        assert out["visual_spec_chart_families"] == {
            "line": 1, "bar_vertical": 1,
        }
        assert out["final_story_title"] == "Cashback Brief"
        assert out["final_story_length"] == 2100

    async def test_dedupes_repeated_visual_spec_index(self):
        # Agent re-records spec for scene 1 (revision). Should appear once.
        events = [
            {"kind": "visual_spec_recorded",
             "payload": {"scene_index": 1, "chart_family": "line",
                         "relationship": "trend", "action_title": "v1"}},
            {"kind": "visual_spec_recorded",
             "payload": {"scene_index": 1, "chart_family": "bar_vertical",
                         "relationship": "category_comparison",
                         "action_title": "v2 (revised)"}},
        ]
        out = _scan_content(events)
        assert out["visual_specs_recorded"] == [1]


# ---------------------------------------------------------------------------
# Storyteller resume hint includes the content block
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storyteller_workflow_with_content(store):
    workflow_id = ev.workflow_id_for_storyteller("sess_full")
    await store.create_workflow(
        workflow_id, "storyteller_session",
        metadata={"session_id": "sess_full"},
    )
    await store.append_event(
        workflow_id, "workflow_started", {"session_id": "sess_full"},
    )
    await store.append_event(
        workflow_id, "context_brief_recorded",
        {"audience": "Board members", "mechanism": "decision_brief",
         "required_action": "Approve Q4 reorg"},
    )
    await store.append_event(
        workflow_id, "phase_advanced", {"from": "context", "to": "narrative"},
    )
    await store.append_event(
        workflow_id, "big_idea_recorded",
        {"sentence": "Q4 reorg unlocks 15% velocity at the cost of 2 senior departures.",
         "stakes": "Without it, ship dates slip 6 weeks."},
    )
    await store.append_event(
        workflow_id, "phase_advanced", {"from": "narrative", "to": "storyboard"},
    )
    await store.append_event(
        workflow_id, "storyboard_recorded",
        {"scene_count": 3, "narrative_order": "chronological"},
    )
    await store.append_event(
        workflow_id, "phase_advanced", {"from": "storyboard", "to": "visual_design"},
    )
    return workflow_id


class TestStorytellerResumeHintIncludesContent:
    async def test_resume_hint_carries_content_block(
        self, store, storyteller_workflow_with_content,
    ):
        wf = await store.get_workflow(storyteller_workflow_with_content)
        events = await store.replay(storyteller_workflow_with_content)
        out = await resume_storyteller_session(
            storyteller_workflow_with_content, wf, events,
        )
        assert out.action == ACTION_READY_TO_RESUME
        assert "content" in out.resume_hint
        c = out.resume_hint["content"]
        assert c["audience"] == "Board members"
        assert "Q4 reorg unlocks 15%" in (c["big_idea_sentence"] or "")
        assert c["storyboard_scene_count"] == 3

    async def test_resume_summary_mentions_content(
        self, store, storyteller_workflow_with_content,
    ):
        wf = await store.get_workflow(storyteller_workflow_with_content)
        events = await store.replay(storyteller_workflow_with_content)
        out = await resume_storyteller_session(
            storyteller_workflow_with_content, wf, events,
        )
        assert "big_idea recorded" in out.summary
        assert "storyboard has 3 scenes" in out.summary


