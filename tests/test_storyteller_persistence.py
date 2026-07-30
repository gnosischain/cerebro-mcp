"""A completed storyteller pipeline must survive a restart.

The incident: a pipeline with every gate passed — 12 charts, context brief, big
idea, 8-scene storyboard, 8 visual specs, final story, clarity review 8/8 — was
stranded because one boolean could not be recorded. Restarting the server to
clear the stuck tool would have destroyed the work, because `storyteller_state`
is an in-process singleton with no persistence.

The existing `record_storyteller_*_recorded` events did not help: they are
resume HINTS, deliberately truncated. `storyboard_recorded` carries a scene
COUNT, `visual_spec_recorded` a chart family, and `final_story_recorded` a
content LENGTH — the story markdown was never written anywhere. They tell a
resuming agent where it was, never what it made.
"""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from cerebro_mcp.config import settings


@pytest.fixture
def pipeline(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from cerebro_mcp.storyteller.state import storyteller_state
    from cerebro_mcp.tools.storyteller import storyteller
    from cerebro_mcp.workflow import event_store_sync as es

    monkeypatch.setattr(
        settings,
        "EVENT_STORE_PATH",
        tempfile.mkdtemp() + "/state.db",
        raising=False,
    )
    es._reset_write_state()
    es._reset_bootstrap_cache()

    mcp = FastMCP("storyteller-persistence-test")
    storyteller.register_storyteller_tools(mcp)
    mgr = mcp._tool_manager

    def call(name, args):
        result = asyncio.run(mgr.call_tool(name, args))
        # Storyteller tools return isError results rather than raising, so a
        # silent failure here would make the whole test vacuous.
        assert not getattr(result, "isError", False), f"{name} failed: {result}"
        return result

    yield call, storyteller_state, es

    es._reset_write_state()
    es._reset_bootstrap_cache()


STORY_MARKDOWN = "# The crossing\n\n" + ("Substantial narrative body. " * 400)


def _build(call):
    call("storyteller_start_session", {})
    call(
        "storyteller_record_context_brief",
        {
            "audience": "GnosisDAO delegates",
            "required_action": "approve the counter campaign budget",
            "mechanism": "memo",
            "tone": "recommendation",
            "weakens_case": "quarterly volume is lumpy",
        },
    )
    call(
        "storyteller_record_big_idea",
        {
            "sentence": "Stablecoin volume compounds faster than the narrative.",
            "stakes": "We keep under-funding the channel that works.",
        },
    )
    call(
        "storyteller_record_storyboard",
        {
            "scenes": [
                {"index": 0, "intent": "where we started", "role": "setup"},
                {"index": 1, "intent": "the lumpiness", "role": "tension"},
                {"index": 2, "intent": "the crossing", "role": "resolution"},
            ],
            "narrative_order": "chronological",
            "rationale": "classic arc",
        },
    )
    for i in range(3):
        call(
            "storyteller_record_visual_spec",
            {
                "scene_index": i,
                "relationship": "trend",
                "chart_family": "line",
                "focal_element": "the crossing point",
                "action_title": f"Scene {i} carries the beat",
            },
        )
    call(
        "storyteller_record_final_story",
        {
            "title": "Eleven years of Gnosis",
            "content_markdown": STORY_MARKDOWN,
        },
    )


def test_completed_pipeline_survives_a_restart(pipeline):
    call, state, es = pipeline
    _build(call)

    assert state.final_story_markdown == STORY_MARKDOWN
    assert len(state.visual_specs) == 3

    sessions = es.list_storyteller_sessions()
    assert sessions, "no recoverable session was persisted"
    session_id = sessions[0]["session_id"]

    # A fresh process starts with an empty singleton.
    state.start_session()
    assert state.final_story_markdown == ""
    assert state.visual_specs == []

    payload = es.load_latest_storyteller_snapshot(session_id)
    assert payload is not None, "no snapshot to recover from"
    state.restore_from_payload(payload)

    # The artifacts themselves, not a count and a length.
    assert state.final_story_markdown == STORY_MARKDOWN
    assert state.final_story_title == "Eleven years of Gnosis"
    assert len(state.visual_specs) == 3
    assert len(state.storyboard.scenes) == 3
    assert state.big_idea.sentence.startswith("Stablecoin volume compounds")
    assert state.context_brief.audience == "GnosisDAO delegates"
    assert state.phase == "critique"


def test_snapshot_is_written_before_the_phase_advances(pipeline):
    """Visual spec 2 of 3 does not advance the phase, yet it is exactly the
    work that must not be lost. Persistence cannot be tied to phase changes."""
    call, state, es = pipeline
    call("storyteller_start_session", {})
    call(
        "storyteller_record_context_brief",
        {
            "audience": "GnosisDAO delegates",
            "required_action": "approve the counter campaign budget",
            "mechanism": "memo",
        },
    )
    call(
        "storyteller_record_big_idea",
        {
            "sentence": "Stablecoin volume compounds faster than the narrative.",
            "stakes": "We keep under-funding the channel that works.",
        },
    )
    call(
        "storyteller_record_storyboard",
        {
            "scenes": [
                {"index": 0, "intent": "a", "role": "setup"},
                {"index": 1, "intent": "b", "role": "tension"},
                {"index": 2, "intent": "c", "role": "resolution"},
            ],
            "narrative_order": "chronological",
        },
    )
    call(
        "storyteller_record_visual_spec",
        {
            "scene_index": 0,
            "relationship": "trend",
            "chart_family": "line",
            "focal_element": "x",
            "action_title": "only the first scene",
        },
    )

    session_id = es.list_storyteller_sessions()[0]["session_id"]
    payload = es.load_latest_storyteller_snapshot(session_id)
    assert payload is not None
    assert len(payload["visual_specs"]) == 1, (
        "a partial visual-spec pass was not persisted; persistence is tied to "
        "phase advancement rather than to mutation"
    )


def test_round_trip_is_lossless(pipeline):
    call, state, es = pipeline
    _build(call)
    before = state.to_payload()
    state.restore_from_payload(before)
    assert state.to_payload() == before


def test_charts_survive_a_restart(pipeline):
    """The other half of the stranded pipeline.

    Recovering the narrative is useless if the charts it references are gone:
    `{{chart:ID}}` resolves against a process-global dict with a 2h TTL, so a
    restart destroyed all 12 charts the session had built.
    """
    from datetime import datetime

    import cerebro_mcp.tools.visualization.charts as charts

    _call, _state, _es = pipeline

    for i in range(1, 13):
        cid = f"chart_{i}"
        entry = {
            "option": {"series": [{"data": list(range(200))}]},
            "title": f"Chart {i}",
            "chart_type": "line",
            "data_points": 200,
            "created_at": datetime.now(),
            "sql": f"SELECT {i}",
            "database": "dbt",
            "series_field": None,
            "change_field": None,
            "input_shape": "long",
            "source": "raw",
            "source_model": "m",
            "rationale": "",
        }
        charts._chart_registry[cid] = entry
        charts._persist_chart(cid, entry)
    charts._chart_counter = 12

    charts._chart_registry.clear()
    charts._chart_counter = 0

    restored = charts.restore_chart_registry()
    assert restored == 12
    assert charts._chart_registry["chart_7"]["sql"] == "SELECT 7"
    # The option embeds the data — a restore that dropped it would leave an
    # unrenderable chart.
    assert len(charts._chart_registry["chart_7"]["option"]["series"][0]["data"]) == 200

    # A stale counter would reuse chart_1 and silently overwrite a recovery.
    assert charts._next_chart_id() == "chart_13"

    charts._chart_registry.clear()
    charts._chart_counter = 0


def test_restore_can_select_specific_charts(pipeline):
    from datetime import datetime

    import cerebro_mcp.tools.visualization.charts as charts

    for i in (1, 2, 3):
        cid = f"chart_{i}"
        entry = {
            "option": {},
            "title": f"Chart {i}",
            "chart_type": "line",
            "data_points": 0,
            "created_at": datetime.now(),
            "sql": f"SELECT {i}",
        }
        charts._persist_chart(cid, entry)

    charts._chart_registry.clear()
    charts._chart_counter = 0
    assert charts.restore_chart_registry(["chart_2"]) == 1
    assert set(charts._chart_registry) == {"chart_2"}

    charts._chart_registry.clear()
    charts._chart_counter = 0


def test_a_wedged_store_does_not_block_the_pipeline(pipeline, monkeypatch):
    """Persistence is best-effort: it rides the event store's write deadline,
    so a stalled filesystem loses the snapshot rather than the session."""
    import time

    call, state, es = pipeline
    monkeypatch.setattr(
        settings, "EVENT_STORE_WRITE_TIMEOUT_SECONDS", 0.2, raising=False
    )
    monkeypatch.setattr(
        settings, "EVENT_STORE_DEGRADED_COOLDOWN_SECONDS", 30.0, raising=False
    )
    call("storyteller_start_session", {})
    monkeypatch.setattr(es, "_connect", lambda: time.sleep(30))

    started = time.monotonic()
    call(
        "storyteller_record_context_brief",
        {
            "audience": "GnosisDAO delegates",
            "required_action": "approve the counter campaign budget",
            "mechanism": "memo",
        },
    )
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"a wedged event store blocked the tool for {elapsed:.1f}s"
    assert state.context_brief is not None, "the mutation itself must still apply"
