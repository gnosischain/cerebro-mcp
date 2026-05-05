"""Resume handler for `storyteller_session` workflows.

Storyteller's state machine is in-memory (`storyteller_state` singleton)
and the migration is observability-only — events go to the event log,
state itself isn't durable. So "resume" here means: tell the agent
which phase the storyteller was in when something went wrong, and
whether a clarity / accessibility gate is currently failing.

State recovered from events
===========================

We walk `phase_advanced` and `gate_failed` events to reconstruct the
last known phase. Events the migration emits:

  - `workflow_started`         — session start
  - `phase_advanced(from, to)` — state machine moved forward
  - `gate_failed(gate, blocking_phase)` — clarity / accessibility check
    failed, state machine rolled back to `blocking_phase`
  - `handoff_completed`        — terminal success

Decision rules
==============

1. `handoff_completed` event present → `action: complete`.
2. The latest `gate_failed` is *unresolved* (no subsequent
   `phase_advanced` past the rolled-back phase) → `action: failed` with
   the gate name in the summary so the agent can re-run the right step.
3. Otherwise → `action: ready_to_resume`. Hint includes the current
   phase and the storyteller MCP tool to call next.

next_action mapping
===================

We map the current phase to the storyteller MCP tool the agent should
call next. This deliberately matches the storyteller_state machine's
expectations — calling the wrong tool for a given phase will raise on
the next user turn.

  context        → storyteller_record_context_brief
  narrative      → storyteller_record_big_idea
  storyboard     → storyteller_record_storyboard
  visual_design  → storyteller_record_visual_spec
  write          → storyteller_record_final_story
  critique       → storyteller_run_clarity_checks
  accessibility  → storyteller_record_accessibility_pass
  handoff        → storyteller_generate_story_report

`idle` and `explore` (if encountered) fall back to a generic "resume the
session manually" hint — neither phase has a dedicated next-step tool.
"""

from __future__ import annotations

import logging
from typing import Any

from cerebro_mcp.workflow_payloads import find_unfinished_llm_calls
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_FAILED,
    ACTION_READY_TO_RESUME,
    ResumeOutcome,
)

logger = logging.getLogger(__name__)


_NEXT_ACTION_BY_PHASE: dict[str, str] = {
    "context":       "storyteller_record_context_brief",
    "narrative":     "storyteller_record_big_idea",
    "storyboard":    "storyteller_record_storyboard",
    "visual_design": "storyteller_record_visual_spec",
    "write":         "storyteller_record_final_story",
    "critique":      "storyteller_run_clarity_checks",
    "accessibility": "storyteller_record_accessibility_pass",
    "handoff":       "storyteller_generate_story_report",
}


def _session_id_from_workflow(workflow_row: dict[str, Any]) -> str:
    meta = workflow_row.get("metadata") or {}
    sid = meta.get("session_id")
    if sid:
        return sid
    wid = workflow_row.get("id", "")
    return wid[len("storyteller_"):] if wid.startswith("storyteller_") else wid


def _scan_content(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk events for storyteller content recordings.

    Captures the latest brief (audience / mechanism), big_idea sentence,
    storyboard scene count, set of visual_spec scene indices completed,
    and final_story title + length. The resume hint folds these into a
    `content` block so a fresh Claude session sees the story shape, not
    just the phase name.
    """
    audience: str | None = None
    mechanism: str | None = None
    big_idea_sentence: str | None = None
    storyboard_scene_count = 0
    visual_spec_scenes: list[int] = []
    visual_spec_chart_families: dict[str, int] = {}
    final_story_title: str | None = None
    final_story_length = 0

    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "context_brief_recorded":
            audience = payload.get("audience") or audience
            mechanism = payload.get("mechanism") or mechanism
        elif kind == "big_idea_recorded":
            big_idea_sentence = payload.get("sentence") or big_idea_sentence
        elif kind == "storyboard_recorded":
            storyboard_scene_count = int(payload.get("scene_count") or 0)
        elif kind == "visual_spec_recorded":
            idx = payload.get("scene_index")
            if idx is not None and idx not in visual_spec_scenes:
                visual_spec_scenes.append(int(idx))
            cf = payload.get("chart_family")
            if cf:
                visual_spec_chart_families[cf] = (
                    visual_spec_chart_families.get(cf, 0) + 1
                )
        elif kind == "final_story_recorded":
            final_story_title = payload.get("title") or final_story_title
            final_story_length = int(payload.get("content_length") or 0)

    return {
        "audience": audience,
        "mechanism": mechanism,
        "big_idea_sentence": big_idea_sentence,
        "storyboard_scene_count": storyboard_scene_count,
        "visual_specs_recorded": sorted(visual_spec_scenes),
        "visual_spec_chart_families": visual_spec_chart_families or None,
        "final_story_title": final_story_title,
        "final_story_length": final_story_length,
    }


def _scan_state(events: list[dict[str, Any]]) -> tuple[str, str | None, str | None]:
    """Walk events. Returns:
      - current_phase (the latest known phase; defaults to "context")
      - last_unresolved_gate_failed_gate_name (or None)
      - last_unresolved_gate_failed_blocking_phase (or None)

    A `gate_failed` is "unresolved" when the latest `phase_advanced` AFTER
    it has not moved past the rolled-back `blocking_phase`. We don't have
    the canonical phase_order here, so we use a simple "any forward
    movement after the gate_failed clears it" heuristic — the agent only
    sees the gate_failed signal when work is genuinely stalled.
    """
    current_phase = "context"
    pending_gate: dict[str, Any] | None = None
    cleared_after_gate = False

    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "phase_advanced":
            to_phase = payload.get("to") or current_phase
            current_phase = to_phase
            if pending_gate is not None:
                cleared_after_gate = True
        elif kind == "gate_failed":
            blocking = payload.get("blocking_phase") or current_phase
            current_phase = blocking
            pending_gate = {
                "gate": payload.get("gate") or "unknown_gate",
                "blocking_phase": blocking,
            }
            cleared_after_gate = False

    if pending_gate is not None and cleared_after_gate:
        # The gate failure was followed by progress past the block —
        # treat as resolved.
        return current_phase, None, None
    if pending_gate is not None:
        return (current_phase,
                pending_gate["gate"],
                pending_gate["blocking_phase"])
    return current_phase, None, None


async def resume_storyteller_session(
    workflow_id: str,
    workflow_row: dict[str, Any],
    events: list[dict[str, Any]],
) -> ResumeOutcome:
    """Resume handler for `kind = "storyteller_session"`.

    Pure function over `events`; no I/O.
    """
    session_id = _session_id_from_workflow(workflow_row)
    kinds = [ev.get("kind") for ev in events]

    if "handoff_completed" in kinds:
        return ResumeOutcome(
            workflow_id=workflow_id, kind="storyteller_session",
            action=ACTION_COMPLETE,
            summary=(
                f"Storyteller session {session_id}: handoff completed."
            ),
            resume_hint={"session_id": session_id},
        )

    current_phase, failed_gate, blocking_phase = _scan_state(events)
    content = _scan_content(events)

    if failed_gate is not None:
        return ResumeOutcome(
            workflow_id=workflow_id, kind="storyteller_session",
            action=ACTION_FAILED,
            summary=(
                f"Storyteller session {session_id}: {failed_gate} gate "
                f"failed, rolled back to {blocking_phase!r}. Agent must "
                "re-run the failing step before the workflow can advance."
            ),
            resume_hint={
                "session_id": session_id,
                "current_phase": current_phase,
                "failed_gate": failed_gate,
                "blocking_phase": blocking_phase,
                "next_action": _NEXT_ACTION_BY_PHASE.get(
                    current_phase, "storyteller_status",
                ),
                "content": content,
            },
            unfinished_llm_calls=find_unfinished_llm_calls(events),
        )

    next_action = _NEXT_ACTION_BY_PHASE.get(current_phase, "storyteller_status")
    summary_parts = [
        f"Storyteller session {session_id}: ready to resume at phase "
        f"{current_phase!r}."
    ]
    if content["big_idea_sentence"]:
        summary_parts.append("big_idea recorded.")
    if content["storyboard_scene_count"]:
        n_specs = len(content["visual_specs_recorded"])
        summary_parts.append(
            f"storyboard has {content['storyboard_scene_count']} scenes "
            f"({n_specs} visual specs done)."
        )
    if content["final_story_title"]:
        summary_parts.append(
            f"final story drafted ({content['final_story_length']} chars)."
        )

    return ResumeOutcome(
        workflow_id=workflow_id, kind="storyteller_session",
        action=ACTION_READY_TO_RESUME,
        summary=" ".join(summary_parts),
        resume_hint={
            "session_id": session_id,
            "current_phase": current_phase,
            "next_action": next_action,
            "next_action_args": {},
            "content": content,
        },
        unfinished_llm_calls=find_unfinished_llm_calls(events),
    )


def install_storyteller_resume_handler() -> None:
    """Register `resume_storyteller_session` for the
    `storyteller_session` kind. Called from
    `bootstrap.init_event_store_async`. Idempotent.
    """
    from cerebro_mcp.workflow_registry import default_workflow_registry
    default_workflow_registry().register(
        "storyteller_session", resume_storyteller_session,
    )
