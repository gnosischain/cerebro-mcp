"""Tests for the Storyteller mode: models, state gates, and tool surface.

The storyteller is opt-in and must not affect the standard-mode session state
or the existing report pipeline. These tests focus on:

- Pydantic model validation (audience vagueness, action articulability,
  big_idea sentence structure, visual_spec chart bans, storyboard tension)
- Session-state gates (context before big_idea, big_idea before storyboard,
  storyboard before visual spec, final_story requires all scenes, review
  required before handoff)
- Tool registration (all storyteller tools are registered and callable)
- Persona files exist on disk
"""

from __future__ import annotations

import importlib.resources

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from cerebro_mcp.storyteller_models import (
    BigIdea,
    ClarityCheck,
    ContextBrief,
    ReviewReport,
    Storyboard,
    StoryboardScene,
    VisualSpec,
)
from cerebro_mcp.storyteller_state import StorytellerState, storyteller_state
from cerebro_mcp.tools.agents import _VALID_ROLES, register_agent_tools
from cerebro_mcp.tools.storyteller import register_storyteller_tools


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_global_storyteller_state():
    """The module-level storyteller_state is a singleton; reset between tests."""
    storyteller_state.end_session()
    yield
    storyteller_state.end_session()


@pytest.fixture
def valid_brief() -> ContextBrief:
    return ContextBrief(
        audience="Q2 budget committee",
        required_action="Approve $X funding to continue the summer pilot",
        mechanism="memo",
        tone="recommendation",
        background="Pilot ran last summer; survey data collected",
        success_definition="Committee approves full-year budget",
    )


@pytest.fixture
def valid_big_idea() -> BigIdea:
    return BigIdea(
        sentence=(
            "The summer learning pilot improved student science perception "
            "by 28 points, so the committee should fund a full-year rollout."
        ),
        stakes="Without funding, the program ends and perception reverts",
    )


@pytest.fixture
def valid_storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(index=0, intent="Set up the pilot's goal", role="setup"),
            StoryboardScene(index=1, intent="Show pre-pilot baseline gap", role="tension"),
            StoryboardScene(index=2, intent="Show the 28-point lift", role="evidence"),
            StoryboardScene(index=3, intent="Ask for full-year funding", role="resolution"),
        ],
        narrative_order="lead_with_ending",
        rationale="Committee is time-constrained; lead with the ask",
    )


def _valid_visual_spec(scene_index: int) -> VisualSpec:
    return VisualSpec(
        scene_index=scene_index,
        relationship="start_vs_end",
        chart_family="slopegraph",
        focal_element="the 28-point lift in positive perception",
        action_title="Perception of science jumped 28 points after the pilot",
        annotations=["Pre-pilot baseline marked in grey"],
    )


# ── ContextBrief validation ──────────────────────────────────────


def test_context_brief_rejects_vague_audience():
    with pytest.raises(ValidationError) as exc:
        ContextBrief(
            audience="stakeholders",
            required_action="Approve the budget next quarter",
            mechanism="memo",
        )
    assert "too vague" in str(exc.value)


def test_context_brief_rejects_leadership_alias():
    with pytest.raises(ValidationError):
        ContextBrief(
            audience="leadership",
            required_action="Approve the budget next quarter",
            mechanism="memo",
        )


def test_context_brief_rejects_empty_audience():
    with pytest.raises(ValidationError):
        ContextBrief(
            audience="   ",
            required_action="Approve the budget next quarter",
            mechanism="memo",
        )


def test_context_brief_rejects_short_action():
    with pytest.raises(ValidationError) as exc:
        ContextBrief(
            audience="Q2 budget committee",
            required_action="act",
            mechanism="memo",
        )
    assert "articulable" in str(exc.value)


def test_context_brief_accepts_specific_audience(valid_brief):
    assert valid_brief.audience == "Q2 budget committee"
    assert valid_brief.tone == "recommendation"


# ── BigIdea validation ───────────────────────────────────────────


def test_big_idea_rejects_label():
    with pytest.raises(ValidationError):
        BigIdea(sentence="Q3 revenue")


def test_big_idea_rejects_trailing_colon():
    with pytest.raises(ValidationError):
        BigIdea(sentence="Q3 revenue summary for the executive committee:")


def test_big_idea_accepts_complete_sentence(valid_big_idea):
    assert "28 points" in valid_big_idea.sentence


# ── Storyboard validation ────────────────────────────────────────


def test_storyboard_requires_tension():
    with pytest.raises(ValidationError) as exc:
        Storyboard(
            scenes=[
                StoryboardScene(index=0, intent="Show the data", role="evidence"),
                StoryboardScene(index=1, intent="Close it out", role="resolution"),
            ],
            narrative_order="chronological",
        )
    assert "tension" in str(exc.value)


def test_storyboard_requires_resolution():
    with pytest.raises(ValidationError):
        Storyboard(
            scenes=[
                StoryboardScene(index=0, intent="Setup", role="setup"),
                StoryboardScene(index=1, intent="Problem", role="tension"),
            ],
            narrative_order="chronological",
        )


def test_storyboard_requires_at_least_two_scenes():
    with pytest.raises(ValidationError):
        Storyboard(
            scenes=[StoryboardScene(index=0, intent="Solo", role="resolution")],
            narrative_order="chronological",
        )


# ── VisualSpec validation ────────────────────────────────────────


def test_visual_spec_rejects_pie():
    with pytest.raises(ValidationError):
        VisualSpec(
            scene_index=0,
            relationship="composition",
            chart_family="pie",  # type: ignore[arg-type]
            focal_element="market share of top suppliers",
            action_title="Supplier A holds 34% of the market, more than any competitor",
        )


def test_visual_spec_rejects_label_as_title():
    with pytest.raises(ValidationError):
        VisualSpec(
            scene_index=0,
            relationship="trend",
            chart_family="line",
            focal_element="Q3 trend",
            action_title="Q3 revenue",
        )


def test_visual_spec_accepts_valid_spec():
    spec = _valid_visual_spec(scene_index=2)
    assert spec.chart_family == "slopegraph"
    assert "28 points" in spec.action_title


# ── StorytellerState gates ───────────────────────────────────────


def test_state_rejects_big_idea_without_context(valid_big_idea):
    st = StorytellerState()
    st.start_session()
    with pytest.raises(RuntimeError, match="context_brief"):
        st.record_big_idea(valid_big_idea)


def test_state_rejects_storyboard_without_big_idea(valid_brief, valid_storyboard):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    with pytest.raises(RuntimeError, match="big_idea"):
        st.record_storyboard(valid_storyboard)


def test_state_rejects_visual_spec_without_storyboard(valid_brief, valid_big_idea):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    st.record_big_idea(valid_big_idea)
    with pytest.raises(RuntimeError, match="storyboard"):
        st.record_visual_spec(_valid_visual_spec(scene_index=0))


def test_state_rejects_visual_spec_for_unknown_scene(
    valid_brief, valid_big_idea, valid_storyboard
):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    st.record_big_idea(valid_big_idea)
    st.record_storyboard(valid_storyboard)
    with pytest.raises(ValueError, match="scene_index"):
        st.record_visual_spec(_valid_visual_spec(scene_index=99))


def test_state_rejects_final_story_with_missing_visuals(
    valid_brief, valid_big_idea, valid_storyboard
):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    st.record_big_idea(valid_big_idea)
    st.record_storyboard(valid_storyboard)
    # Record only two of four scenes
    st.record_visual_spec(_valid_visual_spec(scene_index=0))
    st.record_visual_spec(_valid_visual_spec(scene_index=1))
    with pytest.raises(RuntimeError, match="scenes need a visual_spec"):
        st.record_final_story("A story", "content")


def test_state_advances_phase_through_full_flow(
    valid_brief, valid_big_idea, valid_storyboard
):
    st = StorytellerState()
    st.start_session()
    assert st.phase == "context"

    st.record_context_brief(valid_brief)
    assert st.phase == "narrative"

    st.record_big_idea(valid_big_idea)
    assert st.phase == "storyboard"

    st.record_storyboard(valid_storyboard)
    assert st.phase == "visual_design"

    for idx in range(len(valid_storyboard.scenes)):
        st.record_visual_spec(_valid_visual_spec(scene_index=idx))
    assert st.phase == "write"

    st.record_final_story("The pilot worked", "## Open\n{{chart:chart_1}}\n## Close")
    assert st.phase == "critique"

    passed_report = ReviewReport(
        checks=[
            ClarityCheck(test="title_only_readthrough", passed=True),
            ClarityCheck(test="per_scene_reinforcement", passed=True),
            ClarityCheck(test="reverse_storyboard", passed=True),
            ClarityCheck(test="fresh_eye_simulation", passed=True),
            ClarityCheck(test="emphasis_alignment", passed=True),
            ClarityCheck(test="chart_type_audit", passed=True),
            ClarityCheck(test="action_title_audit", passed=True),
            ClarityCheck(test="assumption_surfacing", passed=True),
        ],
        ready_for_handoff=True,
    )
    st.record_review(passed_report)
    assert st.phase == "accessibility"

    st.record_accessibility_pass(True)
    assert st.phase == "handoff"

    st.require_ready_for_handoff()


def test_state_loops_back_on_critic_failure(
    valid_brief, valid_big_idea, valid_storyboard
):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    st.record_big_idea(valid_big_idea)
    st.record_storyboard(valid_storyboard)
    for idx in range(len(valid_storyboard.scenes)):
        st.record_visual_spec(_valid_visual_spec(scene_index=idx))
    st.record_final_story("The pilot worked", "content")

    failing = ReviewReport(
        checks=[
            ClarityCheck(
                test="title_only_readthrough",
                passed=False,
                finding="Scene 2 title is descriptive, not active",
                fix="Rewrite as an action title",
            ),
        ],
        ready_for_handoff=False,
        blocking_issues=["visual: scene 2 title is descriptive"],
    )
    st.record_review(failing)
    # Blocking mentions 'visual', so we should loop back to visual_design
    assert st.phase == "visual_design"


def test_state_require_ready_for_handoff_raises_without_review(
    valid_brief, valid_big_idea, valid_storyboard
):
    st = StorytellerState()
    st.start_session()
    st.record_context_brief(valid_brief)
    st.record_big_idea(valid_big_idea)
    st.record_storyboard(valid_storyboard)
    for idx in range(len(valid_storyboard.scenes)):
        st.record_visual_spec(_valid_visual_spec(scene_index=idx))
    st.record_final_story("The pilot worked", "content")
    with pytest.raises(RuntimeError, match="clarity review"):
        st.require_ready_for_handoff()


def test_state_rejects_recording_when_inactive():
    st = StorytellerState()
    # Never called start_session
    with pytest.raises(RuntimeError, match="not active"):
        st.record_context_brief(
            ContextBrief(
                audience="Q2 budget committee",
                required_action="Approve the proposed budget for the program",
                mechanism="memo",
            )
        )


# ── Persona files exist on disk ──────────────────────────────────


STORYTELLER_ROLES = [
    "storyteller_orchestrator",
    "storyteller_context",
    "storyteller_narrative",
    "storyteller_visual_designer",
    "storyteller_writer",
    "storyteller_critic",
    "storyteller_accessibility",
]


@pytest.mark.parametrize("role", STORYTELLER_ROLES)
def test_persona_file_exists(role):
    content = (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(f"{role}.md")
        .read_text("utf-8")
    )
    assert len(content) > 200, f"persona {role} looks empty"


def test_all_storyteller_roles_registered_in_agents_valid_roles():
    for role in STORYTELLER_ROLES:
        assert role in _VALID_ROLES


def test_get_agent_persona_returns_storyteller_orchestrator():
    mcp = FastMCP("test-storyteller-personas")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    content = fn(role="storyteller_orchestrator")
    assert "Storyteller Orchestrator" in content


# ── Tool surface ─────────────────────────────────────────────────


def test_all_storyteller_tools_registered():
    mcp = FastMCP("test-storyteller-tools")
    register_storyteller_tools(mcp)
    expected = {
        "storyteller_start_session",
        "storyteller_end_session",
        "storyteller_status",
        "storyteller_record_context_brief",
        "storyteller_record_big_idea",
        "storyteller_record_storyboard",
        "storyteller_record_visual_spec",
        "storyteller_record_final_story",
        "storyteller_run_clarity_checks",
        "storyteller_record_accessibility_pass",
        "storyteller_generate_story_report",
    }
    registered = set(mcp._tool_manager._tools.keys())
    missing = expected - registered
    assert not missing, f"missing storyteller tools: {missing}"


def test_tool_start_session_and_record_context_brief():
    mcp = FastMCP("test-storyteller-flow")
    register_storyteller_tools(mcp)

    start = mcp._tool_manager._tools["storyteller_start_session"].fn
    record = mcp._tool_manager._tools["storyteller_record_context_brief"].fn

    start_result = start()
    assert not start_result.isError
    structured = start_result.structuredContent
    assert structured["active"] is True
    assert structured["phase"] == "context"

    record_result = record(
        audience="Q2 budget committee",
        required_action="Approve $250k to continue the summer pilot next year",
        mechanism="memo",
        tone="recommendation",
    )
    assert not record_result.isError
    assert record_result.structuredContent["has_context_brief"] is True
    assert record_result.structuredContent["phase"] == "narrative"


def test_tool_rejects_vague_audience_via_validation():
    mcp = FastMCP("test-storyteller-vague")
    register_storyteller_tools(mcp)

    start = mcp._tool_manager._tools["storyteller_start_session"].fn
    record = mcp._tool_manager._tools["storyteller_record_context_brief"].fn

    start()
    result = record(
        audience="stakeholders",
        required_action="Approve the next-year proposal",
        mechanism="memo",
    )
    assert result.isError is True
    assert "vague" in result.content[0].text.lower()


def test_tool_generate_story_report_blocks_without_gates():
    mcp = FastMCP("test-storyteller-gate")
    register_storyteller_tools(mcp)

    start = mcp._tool_manager._tools["storyteller_start_session"].fn
    generate = mcp._tool_manager._tools["storyteller_generate_story_report"].fn

    start()
    result = generate()
    assert result.isError is True
    assert "Gate" in result.content[0].text


def _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard):
    """Run the storyteller pipeline to ready_for_handoff and return the
    fully-baked singleton state."""
    storyteller_state.start_session()
    storyteller_state.record_context_brief(valid_brief)
    storyteller_state.record_big_idea(valid_big_idea)
    storyteller_state.record_storyboard(valid_storyboard)
    for idx in range(len(valid_storyboard.scenes)):
        storyteller_state.record_visual_spec(_valid_visual_spec(scene_index=idx))
    storyteller_state.record_final_story(
        title="Fund the pilot",
        content_markdown=(
            "## Setup\n\nIntro paragraph.\n\n"
            "## Tension\n\nThe gap is real.\n\n"
            "## Resolution\n\nFund it.\n"
        ),
    )
    storyteller_state.record_review(
        ReviewReport(
            checks=[
                ClarityCheck(test="title_only_readthrough", passed=True),
                ClarityCheck(test="per_scene_reinforcement", passed=True),
                ClarityCheck(test="reverse_storyboard", passed=True),
                ClarityCheck(test="fresh_eye_simulation", passed=True),
                ClarityCheck(test="emphasis_alignment", passed=True),
                ClarityCheck(test="chart_type_audit", passed=True),
                ClarityCheck(test="action_title_audit", passed=True),
                ClarityCheck(test="assumption_surfacing", passed=True),
            ],
            ready_for_handoff=True,
        )
    )
    storyteller_state.record_accessibility_pass(True)


def test_research_metadata_from_snapshot_maps_fields(
    valid_brief, valid_big_idea, valid_storyboard
):
    from cerebro_mcp.tools.storyteller import _research_metadata_from_snapshot

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    snap = storyteller_state.snapshot()
    meta = _research_metadata_from_snapshot(snap)

    # Deck = big idea sentence, capped at 240 chars.
    assert meta["deck"].startswith("The summer learning pilot improved")
    assert len(meta["deck"]) <= 240
    # Takeaways = storyboard scene intents (4 scenes here).
    assert meta["key_takeaways"][0] == "Set up the pilot's goal"
    assert 3 <= len(meta["key_takeaways"]) <= 6
    # Category derived from mechanism (memo).
    assert meta["category"] == "Storyteller · Memo"


def test_research_metadata_takeaways_capped_at_six(
    valid_brief, valid_big_idea
):
    """A storyboard with >6 scene intents is capped at 6 takeaways."""
    from cerebro_mcp.tools.storyteller import _research_metadata_from_snapshot

    big_storyboard = Storyboard(
        scenes=[
            StoryboardScene(index=0, intent=f"Scene {i}", role="setup")
            if i == 0
            else StoryboardScene(index=i, intent=f"Scene {i}", role="tension")
            if i < 6
            else StoryboardScene(index=i, intent=f"Scene {i}", role="resolution")
            for i in range(8)
        ],
        narrative_order="chronological",
    )
    storyteller_state.start_session()
    storyteller_state.record_context_brief(valid_brief)
    storyteller_state.record_big_idea(valid_big_idea)
    storyteller_state.record_storyboard(big_storyboard)
    snap = storyteller_state.snapshot()

    meta = _research_metadata_from_snapshot(snap)
    assert len(meta["key_takeaways"]) == 6
    # Order preserved.
    assert meta["key_takeaways"][0] == "Scene 0"
    assert meta["key_takeaways"][5] == "Scene 5"


def test_storyteller_generate_story_report_research_default(
    valid_brief, valid_big_idea, valid_storyboard, tmp_path, monkeypatch
):
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
    mcp = FastMCP("test-storyteller-research")
    register_storyteller_tools(mcp)

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    generate = mcp._tool_manager._tools["storyteller_generate_story_report"].fn

    result = generate()  # default style = "research"
    assert not result.isError, result.content[0].text
    structured = result.structuredContent
    assert structured["presentation_mode"] == "research"
    meta = structured["research_metadata"]
    assert meta["deck"].startswith("The summer learning pilot")
    assert len(meta["key_takeaways"]) >= 3

    # File written under research filename prefix.
    files = list(tmp_path.glob("cerebro_research_*.html"))
    assert len(files) == 1


def test_storyteller_generate_story_report_scrollytelling_style(
    valid_brief, valid_big_idea, valid_storyboard, tmp_path, monkeypatch
):
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
    mcp = FastMCP("test-storyteller-scrolly")
    register_storyteller_tools(mcp)

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    generate = mcp._tool_manager._tools["storyteller_generate_story_report"].fn

    result = generate(style="scrollytelling")
    assert not result.isError, result.content[0].text
    structured = result.structuredContent
    assert structured["presentation_mode"] == "scrollytelling"
    meta = structured["case_study_metadata"]
    assert meta["deck"].startswith("The summer learning pilot")
    assert len(meta["key_points"]) >= 3

    files = list(tmp_path.glob("cerebro_case_study_*.html"))
    assert len(files) == 1


def test_case_study_metadata_from_snapshot_maps_fields(
    valid_brief, valid_big_idea, valid_storyboard
):
    from cerebro_mcp.tools.storyteller import _case_study_metadata_from_snapshot

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    snap = storyteller_state.snapshot()
    meta = _case_study_metadata_from_snapshot(snap)

    assert meta["deck"].startswith("The summer learning pilot")
    assert len(meta["key_points"]) >= 3
    assert meta["category"]  # derived from brief.mechanism


def test_storyteller_generate_story_report_rejects_unknown_style(
    valid_brief, valid_big_idea, valid_storyboard, tmp_path, monkeypatch
):
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
    mcp = FastMCP("test-storyteller-bad-style")
    register_storyteller_tools(mcp)

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    generate = mcp._tool_manager._tools["storyteller_generate_story_report"].fn

    result = generate(style="bogus")
    assert result.isError
    assert "Unknown style" in result.content[0].text


def test_storyteller_generate_story_report_dashboard_opt_out(
    valid_brief, valid_big_idea, valid_storyboard, tmp_path, monkeypatch
):
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
    mcp = FastMCP("test-storyteller-dashboard")
    register_storyteller_tools(mcp)

    _drive_to_handoff(valid_brief, valid_big_idea, valid_storyboard)
    generate = mcp._tool_manager._tools["storyteller_generate_story_report"].fn

    result = generate(style="dashboard")
    assert not result.isError, result.content[0].text
    # Dashboard mode falls back to default (no research metadata).
    structured = result.structuredContent
    assert structured.get("presentation_mode") in (None, "report", "visual_answer")
    assert "research_metadata" not in structured
    files = list(tmp_path.glob("cerebro_report_*.html"))
    assert len(files) == 1


def test_standard_mode_untouched_by_storyteller():
    """Starting a storyteller session must not touch the standard SessionState."""
    from cerebro_mcp.tools.session_state import state

    state.reset()
    state.record_search_models("test", 5)
    before_count = state.search_models_count

    storyteller_state.start_session()
    storyteller_state.record_context_brief(
        ContextBrief(
            audience="Platform engineering leads on the bridges workstream",
            required_action="Decide whether to deprecate the legacy bridge",
            mechanism="brief",
        )
    )
    storyteller_state.end_session()

    # Standard state should be unchanged.
    assert state.search_models_count == before_count
