"""Resume handler for `quarterly_review` workflows.

QBRs auto-advance phases (no explicit plan/execute calls) and have no
peer-review gate, so the state machine is simpler than research's.

Decision rules
==============

1. `report_published` event present → `action: complete`.
2. Otherwise → `action: ready_to_resume`. The hint's `next_action` is:
   - `save_quarterly_analysis` if the QBR has 0 evidence_attached events
     yet — the user opened the QBR but hasn't attached charts yet.
   - `save_quarterly_analysis` if there are some evidence events but
     the agent might want to attach more before publishing.
   - `publish_quarterly_review` if there are evidence events AND a
     reasonable chunk of work has been done (heuristic: ≥3 evidence
     events). Resume is advisory; the agent decides whether to act.

There is intentionally no `failed` action — QBRs don't have a gate that
can fail. They either complete or stay open indefinitely (e.g. the
analyst is taking weeks to attach analyses).

Resume hint shape
=================

    {
      "project_id":      "<rp_xxx>",
      "quarter":         "<latest quarter the QBR is tracking>",
      "evidence_count":  <int>,
      "next_action":     "save_quarterly_analysis" | "publish_quarterly_review",
      "next_action_args": { ... },
    }
"""

from __future__ import annotations

import logging
from typing import Any

from cerebro_mcp.workflow_payloads import find_unfinished_llm_calls
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_READY_TO_RESUME,
    ResumeOutcome,
)

logger = logging.getLogger(__name__)


# Threshold above which the resume hint prefers "publish" over "attach more".
# Chosen to match the typical QBR shape (~3-5 charts per family). Below this
# the agent is told to keep attaching; at or above, it's invited to publish.
_PUBLISH_NUDGE_EVIDENCE_THRESHOLD = 3


def _project_id_from_workflow(workflow_row: dict[str, Any]) -> str:
    meta = workflow_row.get("metadata") or {}
    pid = meta.get("project_id")
    if pid:
        return pid
    wid = workflow_row.get("id", "")
    return wid[len("quarterly_"):] if wid.startswith("quarterly_") else wid


def _quarter_from_events(events: list[dict[str, Any]]) -> str | None:
    """Find the most recent quarter referenced in the event stream.
    Falls back to the workflow's metadata `quarter` field if no event
    carried one."""
    quarter = None
    for ev in events:
        payload = ev.get("payload") or {}
        q = payload.get("quarter")
        if q:
            quarter = q
    return quarter


async def resume_quarterly_review(
    workflow_id: str,
    workflow_row: dict[str, Any],
    events: list[dict[str, Any]],
) -> ResumeOutcome:
    """Resume handler for `kind = "quarterly_review"`.

    Pure function over `events`; no I/O. Safe to call from bootstrap.
    """
    project_id = _project_id_from_workflow(workflow_row)
    kinds = [ev.get("kind") for ev in events]

    # Terminal: published. Workflow row gets flipped to `completed` by
    # the registry's `_record_outcome`.
    if "report_published" in kinds:
        return ResumeOutcome(
            workflow_id=workflow_id, kind="quarterly_review",
            action=ACTION_COMPLETE,
            summary=f"QBR {project_id}: report already published.",
            resume_hint={"project_id": project_id},
        )

    evidence_count = sum(1 for k in kinds if k == "evidence_attached")
    quarter = (
        _quarter_from_events(events)
        or (workflow_row.get("metadata") or {}).get("quarter")
    )

    # Step 1 expansion — surface QBR notes (observation / priority /
    # action) so resume sees what the agent has already written down,
    # not just how many charts are attached.
    notes_by_kind: dict[str, int] = {}
    recent_notes: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") == "note_recorded":
            payload = ev.get("payload") or {}
            k = payload.get("kind") or "unknown"
            notes_by_kind[k] = notes_by_kind.get(k, 0) + 1
            recent_notes.append({
                "kind": k,
                "statement_preview": payload.get("statement_preview"),
            })
    recent_notes = recent_notes[-5:]  # last 5

    if evidence_count >= _PUBLISH_NUDGE_EVIDENCE_THRESHOLD:
        next_action = "publish_quarterly_review"
        next_args: dict[str, Any] = {"project_id": project_id}
        summary_tail = (
            f"{evidence_count} evidence item(s) attached — ready to publish "
            f"or keep adding analyses."
        )
    else:
        next_action = "save_quarterly_analysis"
        next_args = {"project_id": project_id}
        summary_tail = (
            f"{evidence_count} evidence item(s) so far. Attach more "
            f"analyses or publish when ready."
        )

    summary_parts = [
        f"QBR {project_id} (quarter={quarter or 'unknown'}): {summary_tail}"
    ]
    if notes_by_kind:
        summary_parts.append(
            "Notes: "
            + ", ".join(f"{n} {k}" for k, n in sorted(notes_by_kind.items()))
            + "."
        )

    return ResumeOutcome(
        workflow_id=workflow_id, kind="quarterly_review",
        action=ACTION_READY_TO_RESUME,
        summary=" ".join(summary_parts),
        resume_hint={
            "project_id": project_id,
            "quarter": quarter,
            "evidence_count": evidence_count,
            "next_action": next_action,
            "next_action_args": next_args,
            "notes_by_kind": notes_by_kind or None,
            "recent_notes": recent_notes or None,
        },
        unfinished_llm_calls=find_unfinished_llm_calls(events),
    )


def install_quarterly_review_resume_handler() -> None:
    """Register `resume_quarterly_review` for the `quarterly_review` kind.

    Called from `bootstrap.init_event_store_async` next to the existing
    `install_research_resume_handler()` call. Idempotent.
    """
    from cerebro_mcp.workflow_registry import default_workflow_registry
    default_workflow_registry().register(
        "quarterly_review", resume_quarterly_review,
    )
