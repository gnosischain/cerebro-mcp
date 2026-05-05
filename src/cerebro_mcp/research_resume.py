"""Resume handler for `research_project` workflows.

Inspects the event stream of a research workflow and produces a
structured `ResumeOutcome` describing where the agent left off.
The handler does NOT make LLM calls or hit ClickHouse — it must be
fast enough to run in the bootstrap path.

Decision rules
==============

1. If the last meaningful event is `report_published` → action=`complete`.
2. If `peer_review_recorded` event has verdict `rejected` → action=`failed`.
3. If `verification_completed` event exists with `passed=False` and no
   subsequent retry → action=`failed`.
4. Otherwise → action=`ready_to_resume`. Compute the current phase by
   scanning forward through `phase_planned` / `phase_completed` events;
   the resume hint tells the agent the next call to make.

Resume hint shape
=================

    {
      "project_id":       "<rp_xxx>",
      "current_phase":    "mapping" | "hypothesis" | "execution" |
                          "verification" | "publication",
      "completed_phases": ["mapping", "hypothesis", ...],
      "next_action":      "plan_research_phase" | "execute_research_phase" |
                          "verify_research_phase" | "record_peer_review" |
                          "publish_research_report",
      "next_action_args": { ... },
      "verification_gate": "passed" | "failed" | "pending" | None,
      "peer_review_gate":  "passed" | "failed" | "pending" | None,
    }

The `next_action` and `next_action_args` are explicit so the agent can
construct the call without re-deriving state. `verification_gate` and
`peer_review_gate` mirror what the SQLite `gates` table holds for this
workflow.
"""

from __future__ import annotations

import logging
from typing import Any

from cerebro_mcp.event_store import default_event_store
from cerebro_mcp.workflow_payloads import (
    GATE_FAILED,
    GATE_PASSED,
    find_unfinished_llm_calls,
)
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_FAILED,
    ACTION_READY_TO_RESUME,
    ResumeOutcome,
)

logger = logging.getLogger(__name__)


# Canonical phase order — matches `research_models.PhaseName` literal.
_PHASE_ORDER = (
    "mapping",
    "hypothesis",
    "execution",
    "verification",
    "publication",
)


def _project_id_from_workflow(workflow_row: dict[str, Any]) -> str:
    """Workflow ids are `research_<project_id>`; metadata also carries
    `project_id`. Prefer metadata, fall back to the id stripped of the
    prefix."""
    meta = workflow_row.get("metadata") or {}
    pid = meta.get("project_id")
    if pid:
        return pid
    wid = workflow_row.get("id", "")
    return wid[len("research_"):] if wid.startswith("research_") else wid


def _scan_phases(events: list[dict[str, Any]]) -> tuple[list[str], str]:
    """Walk events to determine which phases have completed and what the
    current phase is.

    `completed`: every phase that emitted a `phase_completed` event
    (in order of completion).
    `current`: the phase that has been planned but not completed, OR
    the next phase after the latest completion. Defaults to "mapping"
    if nothing is recorded.
    """
    completed: list[str] = []
    last_planned: str | None = None
    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "phase_completed":
            phase = payload.get("phase")
            if phase and phase not in completed:
                completed.append(phase)
        elif kind == "phase_planned":
            last_planned = payload.get("phase") or last_planned

    if last_planned and last_planned not in completed:
        # Phase was planned but never completed — that's where work stopped.
        return completed, last_planned

    if completed:
        last_completed = completed[-1]
        idx = _PHASE_ORDER.index(last_completed) if last_completed in _PHASE_ORDER else -1
        if idx >= 0 and idx + 1 < len(_PHASE_ORDER):
            return completed, _PHASE_ORDER[idx + 1]
        return completed, last_completed

    return completed, "mapping"


def _scan_work(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk events to summarize the agent's actual analytical work.

    Step 1 expansion captures `query_executed`, `memory_recorded`,
    `finding_recorded`, `evidence_attached` events. The resume handler
    folds them into a concise dict for the hint payload so the agent
    on a fresh Claude session sees real progress (not just phase
    boundaries).

    Memory and finding *previews* are bounded — top 3 most recent of
    each — so the hint stays under a sensible byte budget. Counts are
    full.
    """
    queries_run = 0
    queries_failed = 0
    error_classes: dict[str, int] = {}
    memories: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    evidence_count = 0
    evidence_by_phase: dict[str, int] = {}
    last_evidence_titles: list[str] = []

    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "query_executed":
            queries_run += 1
            err = payload.get("error_class")
            if err:
                queries_failed += 1
                error_classes[err] = error_classes.get(err, 0) + 1
        elif kind == "memory_recorded":
            memories.append({
                "memory_id": payload.get("memory_id"),
                "kind": payload.get("kind"),
                "statement_preview": payload.get("statement_preview"),
                "confidence": payload.get("confidence"),
            })
        elif kind == "finding_recorded":
            findings.append({
                "finding_id": payload.get("finding_id"),
                "title": payload.get("title"),
                "confidence": payload.get("confidence"),
                "evidence_count": payload.get("evidence_count"),
            })
        elif kind == "evidence_attached":
            evidence_count += 1
            phase = payload.get("phase") or "unknown"
            evidence_by_phase[phase] = evidence_by_phase.get(phase, 0) + 1
            title = payload.get("title")
            if title:
                last_evidence_titles.append(title)
                last_evidence_titles = last_evidence_titles[-5:]

    return {
        "queries_run": queries_run,
        "queries_failed": queries_failed,
        "query_error_classes": error_classes or None,
        "memory_count": len(memories),
        "recent_memories": memories[-3:],     # most recent 3
        "finding_count": len(findings),
        "recent_findings": findings[-3:],
        "evidence_count": evidence_count,
        "evidence_by_phase": evidence_by_phase or None,
        "recent_evidence_titles": last_evidence_titles or None,
    }


def _scan_gates(events: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Walk events for verification + peer-review gate signals.

    Returns `(verification_gate, peer_review_gate)`. Each is one of
    `"passed"` / `"failed"` / `None`.
    """
    verif = None
    review = None
    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "verification_completed":
            verif = "passed" if payload.get("passed") else "failed"
        elif kind == "peer_review_recorded":
            status = (payload.get("status") or "").lower()
            review = "passed" if status not in ("rejected",) else "failed"
    return verif, review


def _next_action_for_phase(
    phase: str,
    completed: list[str],
    project_id: str,
) -> tuple[str, dict[str, Any]]:
    """Map (current phase, completed set) → MCP tool call to make next.

    Logic mirrors `tools/research.py`:
      mapping/hypothesis/execution: plan → execute
      verification: plan → verify_research_phase
      publication: plan → record_peer_review (if not done) → publish_research_report
    """
    if phase in ("mapping", "hypothesis", "execution"):
        # If the phase is in `completed`, the agent has already done plan+execute
        # — but we shouldn't be here in that case (scan_phases filtered it).
        # Default: tell the agent to call plan_research_phase next.
        return "plan_research_phase", {
            "project_id": project_id, "phase": phase,
        }
    if phase == "verification":
        return "verify_research_phase", {"project_id": project_id}
    if phase == "publication":
        return "record_peer_review", {"project_id": project_id}
    # Unknown phase — fall back to a no-op hint.
    return "plan_research_phase", {
        "project_id": project_id, "phase": phase,
    }


async def resume_research_project(
    workflow_id: str,
    workflow_row: dict[str, Any],
    events: list[dict[str, Any]],
) -> ResumeOutcome:
    """Resume handler for `kind = "research_project"`.

    Pure function over `events`; no I/O. Safe to call from bootstrap.
    """
    project_id = _project_id_from_workflow(workflow_row)
    kinds = [ev.get("kind") for ev in events]

    # Terminal states first.
    if "report_published" in kinds:
        return ResumeOutcome(
            workflow_id=workflow_id, kind="research_project",
            action=ACTION_COMPLETE,
            summary=f"Project {project_id}: report already published.",
            resume_hint={"project_id": project_id},
        )

    verification_gate, peer_review_gate = _scan_gates(events)

    if peer_review_gate == "failed":
        return ResumeOutcome(
            workflow_id=workflow_id, kind="research_project",
            action=ACTION_FAILED,
            summary=(
                f"Project {project_id}: peer review rejected. "
                "Workflow cannot be auto-resumed without human input."
            ),
            resume_hint={
                "project_id": project_id,
                "verification_gate": verification_gate,
                "peer_review_gate": peer_review_gate,
            },
        )

    completed, current_phase = _scan_phases(events)
    next_action, next_args = _next_action_for_phase(
        current_phase, completed, project_id,
    )
    unfinished = find_unfinished_llm_calls(events)
    work = _scan_work(events)

    summary_lines = [
        f"Project {project_id}: ready to resume at phase {current_phase!r}.",
        f"Completed phases: {completed or '(none)'}.",
    ]
    if work["queries_run"]:
        summary_lines.append(
            f"{work['queries_run']} queries run "
            f"({work['queries_failed']} failed)."
        )
    if work["memory_count"]:
        summary_lines.append(f"{work['memory_count']} memory entries.")
    if work["finding_count"]:
        summary_lines.append(f"{work['finding_count']} findings.")
    if work["evidence_count"]:
        summary_lines.append(f"{work['evidence_count']} evidence items.")
    if unfinished:
        summary_lines.append(
            f"{len(unfinished)} unfinished LLM call(s) — re-issue with the "
            "captured message history before advancing."
        )
    if verification_gate == "failed":
        summary_lines.append(
            "Verification gate FAILED — re-run after seeding execution evidence."
        )

    return ResumeOutcome(
        workflow_id=workflow_id, kind="research_project",
        action=ACTION_READY_TO_RESUME,
        summary=" ".join(summary_lines),
        resume_hint={
            "project_id": project_id,
            "current_phase": current_phase,
            "completed_phases": completed,
            "next_action": next_action,
            "next_action_args": next_args,
            "verification_gate": verification_gate,
            "peer_review_gate": peer_review_gate,
            "work": work,
        },
        unfinished_llm_calls=unfinished,
    )


def install_research_resume_handler() -> None:
    """Register `resume_research_project` for the `research_project` kind.

    Called from `bootstrap.py` at server startup. Idempotent."""
    from cerebro_mcp.workflow_registry import default_workflow_registry
    default_workflow_registry().register(
        "research_project", resume_research_project,
    )
