"""Thread-safe, process-global state for the Storyteller mode.

The storyteller is an opt-in workflow. This module owns the gates that make
the workflow enforceable: context before analysis, big idea before charts,
storyboard before visuals, review before handoff. Standard-mode tools
(generate_charts, generate_report) are *not* affected; they continue to read
from `cerebro_mcp.tools.governance.session_state.state`.

The storyteller state is deliberately separate from `SessionState` so that:
- Standard mode remains zero-impact if the storyteller never starts.
- Storyteller session resets do not clobber discovery/exploration tracking.
- Both states can coexist for users who explore first, then write the story.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from cerebro_mcp.models.storyteller import (
    BigIdea,
    ContextBrief,
    ReviewReport,
    Storyboard,
    StorytellerPhase,
    StorytellerSnapshot,
    VisualSpec,
)


# Phase ordering used for gate checks and next-step hints.
_PHASE_ORDER: list[StorytellerPhase] = [
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


def _phase_index(phase: StorytellerPhase) -> int:
    return _PHASE_ORDER.index(phase)


@dataclass
class StorytellerState:
    """Owns the live artifacts of a single storyteller session.

    Only one storyteller session is active per process at a time. Starting a
    new session clears previous artifacts. The caller drives the workflow:
    each tool call records an artifact and advances the phase cursor.
    """

    active: bool = False
    phase: StorytellerPhase = "idle"

    context_brief: ContextBrief | None = None
    insight_slate_notes: str = ""
    big_idea: BigIdea | None = None
    storyboard: Storyboard | None = None
    visual_specs: list[VisualSpec] = field(default_factory=list)
    final_story_title: str = ""
    final_story_markdown: str = ""
    review_report: ReviewReport | None = None
    accessibility_passed: bool = False

    lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Lifecycle ────────────────────────────────────────────────────

    def start_session(self) -> StorytellerSnapshot:
        """Begin a new storyteller session, clearing any prior state."""
        with self.lock:
            self._clear_unlocked()
            self.active = True
            self.phase = "context"
            return self._snapshot_unlocked(
                next_step=(
                    "Record a context brief with `storyteller_record_context_brief`. "
                    "Audience must be specific; required action must be articulable."
                )
            )

    def end_session(self) -> StorytellerSnapshot:
        with self.lock:
            self._clear_unlocked()
            return self._snapshot_unlocked(next_step="Session ended.")

    def _clear_unlocked(self) -> None:
        self.active = False
        self.phase = "idle"
        self.context_brief = None
        self.insight_slate_notes = ""
        self.big_idea = None
        self.storyboard = None
        self.visual_specs = []
        self.final_story_title = ""
        self.final_story_markdown = ""
        self.review_report = None
        self.accessibility_passed = False

    # ── Recording methods (each one advances the phase) ─────────────

    def record_context_brief(self, brief: ContextBrief) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            self.context_brief = brief
            # Exploration is optional and happens in the normal Cerebro flow;
            # we jump directly to narrative as the next gate.
            self.phase = "narrative"
            return self._snapshot_unlocked(
                next_step=(
                    "Explore the data with the normal Cerebro tools as needed, "
                    "then record the big idea with `storyteller_record_big_idea`."
                )
            )

    def record_big_idea(self, idea: BigIdea) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if self.context_brief is None:
                raise RuntimeError(
                    "Gate: context_brief must be recorded before big_idea. "
                    "Call `storyteller_record_context_brief` first."
                )
            self.big_idea = idea
            self.phase = "storyboard"
            return self._snapshot_unlocked(
                next_step=(
                    "Build a storyboard with setup → tension → resolution. "
                    "Use `storyteller_record_storyboard`."
                )
            )

    def record_storyboard(self, storyboard: Storyboard) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if self.big_idea is None:
                raise RuntimeError(
                    "Gate: big_idea must be recorded before storyboard. "
                    "Call `storyteller_record_big_idea` first."
                )
            self.storyboard = storyboard
            self.phase = "visual_design"
            return self._snapshot_unlocked(
                next_step=(
                    "Design visuals per scene with "
                    "`storyteller_record_visual_spec`. One spec per storyboard "
                    "scene. Relationship-first; grey everything non-focal."
                )
            )

    def record_visual_spec(self, spec: VisualSpec) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if self.storyboard is None:
                raise RuntimeError(
                    "Gate: storyboard must be recorded before visual specs. "
                    "Call `storyteller_record_storyboard` first."
                )
            if not any(
                scene.index == spec.scene_index
                for scene in self.storyboard.scenes
            ):
                raise ValueError(
                    f"visual_spec.scene_index={spec.scene_index} does not "
                    f"match any storyboard scene"
                )
            # Replace existing spec for the same scene, if present.
            self.visual_specs = [
                s for s in self.visual_specs if s.scene_index != spec.scene_index
            ]
            self.visual_specs.append(spec)
            self.visual_specs.sort(key=lambda s: s.scene_index)

            if len(self.visual_specs) >= len(self.storyboard.scenes):
                self.phase = "write"
                next_step = (
                    "All scenes have visuals. Assemble the final story with "
                    "`storyteller_record_final_story` (action titles, "
                    "annotations, prose for the chosen medium)."
                )
            else:
                remaining = [
                    scene.index
                    for scene in self.storyboard.scenes
                    if not any(s.scene_index == scene.index for s in self.visual_specs)
                ]
                next_step = (
                    f"Visual recorded for scene {spec.scene_index}. "
                    f"Remaining scenes: {remaining}."
                )
            return self._snapshot_unlocked(next_step=next_step)

    def record_final_story(
        self,
        title: str,
        content_markdown: str,
    ) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if self.storyboard is None:
                raise RuntimeError(
                    "Gate: storyboard must exist before the final story"
                )
            if len(self.visual_specs) < len(self.storyboard.scenes):
                raise RuntimeError(
                    f"Gate: all {len(self.storyboard.scenes)} storyboard "
                    f"scenes need a visual_spec before writing the final "
                    f"story (have {len(self.visual_specs)})"
                )
            if not title.strip():
                raise ValueError("final_story title cannot be empty")
            if not content_markdown.strip():
                raise ValueError("final_story content_markdown cannot be empty")
            self.final_story_title = title.strip()
            self.final_story_markdown = content_markdown
            self.phase = "critique"
            return self._snapshot_unlocked(
                next_step=(
                    "Run clarity checks with `storyteller_run_clarity_checks` "
                    "(title-only readthrough, reverse storyboard, fresh-eye "
                    "review, action-title audit)."
                )
            )

    def record_review(self, report: ReviewReport) -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if not self.final_story_markdown:
                raise RuntimeError(
                    "Gate: final_story must exist before review"
                )
            self.review_report = report
            if report.ready_for_handoff:
                self.phase = "accessibility"
                next_step = (
                    "Review passed. Run accessibility and tone pass with "
                    "`storyteller_record_accessibility_pass`."
                )
            else:
                # Roll back to the earliest failing stage.
                blocking = report.blocking_issues
                earliest = _earliest_failing_phase(blocking) or "write"
                self.phase = earliest
                next_step = (
                    f"Review found {len(blocking)} blocking issue(s). "
                    f"Loop back to '{earliest}' and fix before retrying."
                )
            return self._snapshot_unlocked(next_step=next_step)

    def record_accessibility_pass(self, passed: bool, notes: str = "") -> StorytellerSnapshot:
        with self.lock:
            self._require_active_unlocked()
            if self.review_report is None or not self.review_report.ready_for_handoff:
                raise RuntimeError(
                    "Gate: clarity review must pass before accessibility"
                )
            self.accessibility_passed = passed
            if passed:
                self.phase = "handoff"
                next_step = (
                    "Accessibility passed. Hand off with "
                    "`storyteller_generate_story_report` to render the final artifact."
                )
            else:
                self.phase = "write"
                next_step = (
                    f"Accessibility failed: {notes or 'see notes'}. Loop back "
                    f"to write/design and fix before retrying."
                )
            return self._snapshot_unlocked(next_step=next_step)

    # ── Gate checks ─────────────────────────────────────────────────

    def require_ready_for_handoff(self) -> None:
        """Raises RuntimeError unless every upstream gate has passed."""
        with self.lock:
            self._require_active_unlocked()
            if self.context_brief is None:
                raise RuntimeError("Gate: context_brief missing")
            if self.big_idea is None:
                raise RuntimeError("Gate: big_idea missing")
            if self.storyboard is None:
                raise RuntimeError("Gate: storyboard missing")
            if len(self.visual_specs) < len(self.storyboard.scenes):
                raise RuntimeError(
                    "Gate: not all storyboard scenes have a visual_spec"
                )
            if not self.final_story_markdown:
                raise RuntimeError("Gate: final_story missing")
            if self.review_report is None or not self.review_report.ready_for_handoff:
                raise RuntimeError("Gate: clarity review has not passed")
            if not self.accessibility_passed:
                raise RuntimeError("Gate: accessibility pass not recorded")

    # ── Introspection ───────────────────────────────────────────────

    def snapshot(self) -> StorytellerSnapshot:
        with self.lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self, next_step: str = "") -> StorytellerSnapshot:
        return StorytellerSnapshot(
            active=self.active,
            phase=self.phase,
            context_brief=self.context_brief,
            big_idea=self.big_idea,
            storyboard=self.storyboard,
            visual_specs=list(self.visual_specs),
            review_report=self.review_report,
            next_step=next_step or _default_next_step(self.phase),
        )

    def _require_active_unlocked(self) -> None:
        if not self.active:
            raise RuntimeError(
                "Storyteller session is not active. Call "
                "`storyteller_start_session` to begin."
            )

    # ── Durability ──────────────────────────────────────────────────
    #
    # This state is in-process and volatile. The existing
    # `record_storyteller_*_recorded` events are resume HINTS, not artifacts:
    # `storyboard_recorded` carries a scene COUNT, `visual_spec_recorded` a
    # chart family, and `final_story_recorded` a content LENGTH — the story
    # markdown itself was never written anywhere. So a pipeline that had
    # passed every gate could not be reconstructed after a restart, and a
    # single stuck gate made the finished work unreachable.
    #
    # These two methods carry the artifacts themselves. Payloads above
    # EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES are gzipped by the event
    # store, which is what keeps a long story cheap to persist.

    def to_payload(self) -> dict:
        """Full serializable snapshot, artifacts included."""
        with self.lock:
            return self._to_payload_unlocked()

    def _to_payload_unlocked(self) -> dict:
        def dump(model):
            return model.model_dump(mode="json") if model is not None else None

        return {
            "active": self.active,
            "phase": self.phase,
            "context_brief": dump(self.context_brief),
            "insight_slate_notes": self.insight_slate_notes,
            "big_idea": dump(self.big_idea),
            "storyboard": dump(self.storyboard),
            "visual_specs": [dump(v) for v in self.visual_specs],
            "final_story_title": self.final_story_title,
            "final_story_markdown": self.final_story_markdown,
            "review_report": dump(self.review_report),
            "accessibility_passed": self.accessibility_passed,
        }

    def restore_from_payload(self, payload: dict) -> None:
        """Rehydrate from `to_payload`. Replaces all current state."""
        with self.lock:
            self.active = bool(payload.get("active", False))
            self.phase = payload.get("phase", "idle")
            self.insight_slate_notes = payload.get("insight_slate_notes", "") or ""
            self.final_story_title = payload.get("final_story_title", "") or ""
            self.final_story_markdown = (
                payload.get("final_story_markdown", "") or ""
            )
            self.accessibility_passed = bool(
                payload.get("accessibility_passed", False)
            )

            def load(model_cls, raw):
                return model_cls.model_validate(raw) if raw else None

            self.context_brief = load(ContextBrief, payload.get("context_brief"))
            self.big_idea = load(BigIdea, payload.get("big_idea"))
            self.storyboard = load(Storyboard, payload.get("storyboard"))
            self.review_report = load(ReviewReport, payload.get("review_report"))
            self.visual_specs = [
                VisualSpec.model_validate(v)
                for v in (payload.get("visual_specs") or [])
                if v
            ]


def _default_next_step(phase: StorytellerPhase) -> str:
    return {
        "idle": "Call `storyteller_start_session` to begin.",
        "context": "Record a context brief with `storyteller_record_context_brief`.",
        "explore": "Explore data with standard Cerebro tools, then record the big idea.",
        "narrative": "Record the big idea with `storyteller_record_big_idea`.",
        "storyboard": "Record the storyboard with `storyteller_record_storyboard`.",
        "visual_design": "Record visual specs with `storyteller_record_visual_spec`.",
        "write": "Assemble the final story with `storyteller_record_final_story`.",
        "critique": "Run clarity checks with `storyteller_run_clarity_checks`.",
        "accessibility": "Run accessibility pass with `storyteller_record_accessibility_pass`.",
        "handoff": "Render the final artifact with `storyteller_generate_story_report`.",
    }[phase]


def _earliest_failing_phase(blocking_issues: list[str]) -> StorytellerPhase | None:
    """Map blocking-issue tags to the earliest phase that must be re-run.

    Phase tags recognised in blocking_issues (case-insensitive substrings):
      'context', 'big_idea', 'storyboard', 'visual', 'write', 'title'
    """
    if not blocking_issues:
        return None
    joined = " ".join(blocking_issues).lower()
    if "context" in joined:
        return "context"
    if "big idea" in joined or "big_idea" in joined:
        return "narrative"
    if "storyboard" in joined:
        return "storyboard"
    if "visual" in joined or "chart" in joined:
        return "visual_design"
    return "write"


# Process-global singleton, separate from the standard SessionState.
storyteller_state = StorytellerState()
