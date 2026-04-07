"""Pydantic data contracts for the Storyteller mode.

The storyteller is an opt-in, multi-agent pipeline that sits alongside the
standard `generate_charts` / `generate_report` workflow. Standard mode is
unchanged; the storyteller only runs when the user explicitly requests a
narrative/decision artifact.

These models are the artifacts passed between storyteller agents:
- ContextBrief   — audience, required action, mechanism, tone, constraints
- Insight        — one candidate finding from exploration
- InsightSlate   — ranked list of candidate findings
- BigIdea        — one declarative sentence with stakes
- StoryboardScene, Storyboard — low-fidelity outline before chart generation
- VisualSpec     — per-chart design rationale (relationship, focal point, action title)
- ClarityCheck, ReviewReport — adversarial review results

Grounding: Nussbaumer Knaflic, *Storytelling with Data* (Wiley, 2015), chs. 1-10.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Context (Chapter 1)
# ---------------------------------------------------------------------------


Mechanism = Literal[
    "live_presentation",
    "slide_deck_leave_behind",
    "emailed_deck",
    "memo",
    "brief",
    "dashboard_excerpt",
    "script",
]


Tone = Literal[
    "neutral",
    "celebratory",
    "urgent",
    "cautionary",
    "exploratory",
    "recommendation",
]


class ContextBrief(BaseModel):
    """Audience, action, and delivery context captured before any analysis runs.

    The system refuses to proceed without a specific audience and an
    articulable required action. Vague audiences like "stakeholders" are
    rejected; the agent must name a decision-maker or a clearly scoped group.
    """

    audience: str = Field(
        ...,
        description=(
            "Specific audience. Name a decision-maker or a concrete scoped "
            "group. Rejects: 'stakeholders', 'leadership', 'anyone interested'."
        ),
    )
    required_action: str = Field(
        ...,
        description=(
            "What the audience needs to know or do. If none, the "
            "communication should not exist."
        ),
    )
    mechanism: Mechanism = Field(
        ...,
        description="Delivery medium. Drives density of titles, annotations, and prose.",
    )
    tone: Tone = "neutral"
    background: str = Field(
        default="",
        description="Relevant context the audience may or may not already hold.",
    )
    biases: str = Field(
        default="",
        description="Known audience biases that support or resist the message.",
    )
    weakens_case: str = Field(
        default="",
        description=(
            "Evidence or context that weakens the case. Kept visible, never "
            "hidden. A one-sided story is both misleading and fragile."
        ),
    )
    constraints: str = Field(
        default="",
        description="Time, brand, accessibility, or other constraints.",
    )
    success_definition: str = Field(
        default="",
        description="What a successful outcome looks like for this communication.",
    )

    @field_validator("audience")
    @classmethod
    def _audience_specific(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("audience cannot be empty")
        banned = {
            "stakeholders",
            "internal and external stakeholders",
            "leadership",
            "management",
            "anyone interested",
            "everyone",
            "the team",
            "the org",
            "the organization",
        }
        if normalized in banned:
            raise ValueError(
                f"audience '{value}' is too vague — name a decision-maker "
                f"or a concrete scoped group (e.g., 'Q2 budget committee')"
            )
        return value.strip()

    @field_validator("required_action")
    @classmethod
    def _action_articulable(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError(
                "required_action must be articulable. If you cannot name a "
                "concrete thing the audience should know or do, the "
                "communication should not exist."
            )
        return normalized


# ---------------------------------------------------------------------------
# Exploration (feeds narrative; never ships as final)
# ---------------------------------------------------------------------------


RelationshipType = Literal[
    "single_value",
    "category_comparison",
    "composition",
    "trend",
    "distribution",
    "correlation",
    "geographic",
    "start_vs_end",
    "running_total",
]


class Insight(BaseModel):
    """One candidate finding from the exploratory phase."""

    label: str
    summary: str
    relationship: RelationshipType
    supporting_evidence: str = Field(
        default="",
        description="Query reference, chart id, or statistical check backing the insight.",
    )
    opposing_evidence: str = Field(
        default="",
        description="Evidence that weakens or qualifies the finding. Required for honesty.",
    )
    strength: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in the finding, 0-1.",
    )


class InsightSlate(BaseModel):
    """Ranked list of candidate findings. Internal artifact, never shipped as-is."""

    insights: list[Insight] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Narrative (Chapter 1 + Chapter 7)
# ---------------------------------------------------------------------------


class BigIdea(BaseModel):
    """One declarative sentence stating point of view, stakes, and grammar.

    Duarte's three criteria (cited by Knaflic, ch. 1):
    (1) articulates a unique point of view;
    (2) conveys what is at stake;
    (3) is a complete sentence.
    """

    sentence: str = Field(
        ...,
        description="One complete sentence stating POV and stakes.",
    )
    stakes: str = Field(
        default="",
        description="What is at stake for the audience if they act or do not.",
    )

    @field_validator("sentence")
    @classmethod
    def _one_complete_sentence(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 20:
            raise ValueError(
                "big_idea.sentence must be a complete declarative sentence; "
                "labels like 'Q3 revenue' are rejected"
            )
        # Very light structural check: must look like a sentence, not a label.
        if " " not in text:
            raise ValueError("big_idea.sentence must be more than a single word")
        if text.endswith(":"):
            raise ValueError("big_idea.sentence must not end with a colon (that is a label)")
        return text


class StoryboardScene(BaseModel):
    """One low-fidelity scene in the storyboard.

    Scenes are defined by their takeaway, not their chart. The visual comes
    later.
    """

    index: int = Field(..., ge=0)
    intent: str = Field(
        ...,
        description="Takeaway this scene is meant to deliver. Not a chart spec.",
    )
    role: Literal["setup", "tension", "evidence", "resolution"] = Field(
        ...,
        description="Where this scene sits in the setup → tension → resolution arc.",
    )
    notes: str = ""


NarrativeOrder = Literal["chronological", "lead_with_ending"]


class Storyboard(BaseModel):
    """Scene-by-scene outline of the narrative, built before any chart.

    The system enforces:
    - setup → tension → resolution arc (tension is required; flat narratives rejected)
    - minimum scenes: enough to carry the argument, no more
    - a deliberate narrative order choice (chronological vs. lead-with-ending)
    """

    scenes: list[StoryboardScene]
    narrative_order: NarrativeOrder
    rationale: str = Field(
        default="",
        description="Why this narrative order was chosen for this audience.",
    )

    @field_validator("scenes")
    @classmethod
    def _has_tension_and_resolution(
        cls, scenes: list[StoryboardScene]
    ) -> list[StoryboardScene]:
        if len(scenes) < 2:
            raise ValueError("storyboard needs at least 2 scenes")
        roles = {s.role for s in scenes}
        if "tension" not in roles:
            raise ValueError(
                "storyboard must contain at least one 'tension' scene. A "
                "narrative where everything is fine is not a story."
            )
        if "resolution" not in roles:
            raise ValueError(
                "storyboard must end with a 'resolution' scene carrying the "
                "call to action."
            )
        return scenes


# ---------------------------------------------------------------------------
# Visual design (Chapters 2-5)
# ---------------------------------------------------------------------------


ChartFamily = Literal[
    "simple_text",
    "table",
    "heatmap",
    "line",
    "slopegraph",
    "bar_vertical",
    "bar_horizontal",
    "stacked_bar_vertical",
    "stacked_bar_horizontal",
    "stacked_bar_100",
    "waterfall",
    "scatter",
    "square_area",
]


_BANNED_CHART_FAMILIES = {"pie", "donut", "3d", "dual_axis"}


class VisualSpec(BaseModel):
    """Per-chart design rationale attached to a scene and, optionally, a chart_id.

    The Visual Designer Agent produces one `VisualSpec` per scene. The
    `chart_id` is filled in later when the scene is rendered by the existing
    `generate_charts` tool, so this model links the storyboard to the actual
    chart registry.
    """

    scene_index: int
    relationship: RelationshipType
    chart_family: ChartFamily
    focal_element: str = Field(
        ...,
        description=(
            "The one thing the audience should see first. Grey everything else."
        ),
    )
    action_title: str = Field(
        ...,
        description=(
            "Sentence title stating the takeaway, not a descriptive label. "
            "'Support tickets doubled — we need to staff up' not 'Ticket volume'."
        ),
    )
    deemphasize: str = Field(
        default="",
        description="Elements pushed to grey / appendix / lower visual weight.",
    )
    annotations: list[str] = Field(
        default_factory=list,
        description="On-chart callouts (inflection points, external factors, nuances).",
    )
    justification: str = Field(
        default="",
        description=(
            "Required when chart_family deviates from the relationship default. "
            "Empty is allowed for default mappings."
        ),
    )
    chart_id: str | None = Field(
        default=None,
        description="Chart registry id (from generate_charts) once rendered.",
    )

    @field_validator("chart_family")
    @classmethod
    def _not_banned(cls, value: str) -> str:
        if value in _BANNED_CHART_FAMILIES:
            raise ValueError(
                f"chart_family '{value}' is banned: pies/donuts hide magnitudes, "
                f"3D distorts, dual-axis implies false relationships"
            )
        return value

    @field_validator("action_title")
    @classmethod
    def _is_sentence_not_label(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 15:
            raise ValueError(
                "action_title must be a sentence stating the takeaway, not a label"
            )
        if text.endswith(":"):
            raise ValueError("action_title must not end with a colon (that is a label)")
        return text


# ---------------------------------------------------------------------------
# Critique (Chapter 7)
# ---------------------------------------------------------------------------


ClarityTestName = Literal[
    "title_only_readthrough",
    "per_scene_reinforcement",
    "reverse_storyboard",
    "fresh_eye_simulation",
    "emphasis_alignment",
    "chart_type_audit",
    "action_title_audit",
    "assumption_surfacing",
]


class ClarityCheck(BaseModel):
    test: ClarityTestName
    passed: bool
    finding: str = Field(
        default="",
        description="What the check observed. Required when passed=False.",
    )
    fix: str = Field(
        default="",
        description="Concrete remediation. Required when passed=False.",
    )


class ReviewReport(BaseModel):
    """Output of the Critic Agent. On any failure, the orchestrator loops back
    to the earliest failing stage rather than silently fixing and continuing."""

    checks: list[ClarityCheck]
    assumptions_surfaced: list[str] = Field(default_factory=list)
    weak_evidence: list[str] = Field(default_factory=list)
    alternative_interpretations: list[str] = Field(default_factory=list)
    ready_for_handoff: bool = False
    blocking_issues: list[str] = Field(default_factory=list)

    def summarize(self) -> str:
        lines = [
            f"Ready for handoff: {self.ready_for_handoff}",
            f"Checks: {sum(1 for c in self.checks if c.passed)}/{len(self.checks)} passed",
        ]
        for check in self.checks:
            marker = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{marker}] {check.test}: {check.finding or 'ok'}")
        if self.blocking_issues:
            lines.append("Blocking issues:")
            lines.extend(f"  - {issue}" for issue in self.blocking_issues)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session snapshot (what the state module exposes)
# ---------------------------------------------------------------------------


StorytellerPhase = Literal[
    "idle",
    "context",
    "explore",
    "narrative",
    "storyboard",
    "visual_design",
    "write",
    "critique",
    "accessibility",
    "handoff",
]


class StorytellerSnapshot(BaseModel):
    """A read-only view of the storyteller session state, returned by tools
    so the caller can inspect current phase and gate status."""

    active: bool
    phase: StorytellerPhase
    context_brief: ContextBrief | None = None
    big_idea: BigIdea | None = None
    storyboard: Storyboard | None = None
    visual_specs: list[VisualSpec] = Field(default_factory=list)
    review_report: ReviewReport | None = None
    next_step: str = ""
