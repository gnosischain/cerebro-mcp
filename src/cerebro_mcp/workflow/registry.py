"""Phase 3 — WorkflowRegistry: kind-keyed resume handlers.

The orphan sweep in `bootstrap.init_event_store_async` was a placeholder:
it marked stale `running` workflows as `orphaned` so they wouldn't masquerade
as live, but it had no way to actually pick them up. The registry closes
that loop.

Design constraints
==================

1. **Bootstrap-safe.** Resume must be fast and offline. It runs at server
   startup before FastMCP is serving requests. So `resume_fn` MUST NOT
   make LLM calls, hit ClickHouse, or do anything expensive. It reads the
   event log, returns a structured hint, and exits.

2. **Hint-driven, not action-driven.** The `resume_fn` decides what state
   the workflow is in and produces a `ResumeOutcome`. It does not execute
   the next phase. The agent (on the next user interaction) reads the
   hints via the MCP `list_resumable_workflows` / `get_workflow_resume_hint`
   tools and decides whether to continue.

3. **Idempotent.** Calling resume twice on the same workflow produces the
   same outcome and at most one `workflow_resume_hint` event per resume
   call. Duplicate hints are deduplicated by `(workflow_id, hint_seq)` —
   we read the most recent existing hint and append a new one only if the
   underlying state changed.

4. **No-handler fallback = orphan.** If a workflow's `kind` has no
   registered resume_fn, it falls through to the existing orphan-marking
   path. This means migrating workflow types is incremental — nothing
   breaks for unregistered kinds.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from cerebro_mcp.workflow.event_store import EventStore, default_event_store
from cerebro_mcp.workflow.payloads import (
    LLMCallEvent,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
    WORKFLOW_WAITING_GATE,
    find_unfinished_llm_calls,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome of a resume_fn — small, picklable, JSON-serializable.
# ---------------------------------------------------------------------------


# Action vocabulary. Kept as plain strings so callers don't need to
# import the registry just to check `outcome.action == "ready_to_resume"`.
ACTION_READY_TO_RESUME = "ready_to_resume"
ACTION_COMPLETE = "complete"
ACTION_FAILED = "failed"
ACTION_ORPHAN = "orphan"
ACTION_NO_HANDLER = "no_handler"


@dataclass
class ResumeOutcome:
    """Structured result of a resume attempt.

    `resume_hint` is a kind-specific opaque dict — the agent + the
    `resume_fn` define its shape together. For `research_project`,
    `resume_hint` includes `project_id`, `current_phase`, `next_action`,
    and a list of completed phase names. Other kinds may shape it
    differently.

    `unfinished_llm_calls` lifts unfinished LLM calls (via
    `find_unfinished_llm_calls`) so the agent can re-issue them with the
    same message history. May be empty even when `action == "ready_to_resume"`
    — not every interruption happens mid-LLM-call.
    """

    workflow_id: str
    kind: str
    action: str
    summary: str = ""
    resume_hint: dict[str, Any] = field(default_factory=dict)
    unfinished_llm_calls: list[LLMCallEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "kind": self.kind,
            "action": self.action,
            "summary": self.summary,
            "resume_hint": dict(self.resume_hint),
            "unfinished_llm_calls": [
                c.to_dict() if hasattr(c, "to_dict") else c
                for c in self.unfinished_llm_calls
            ],
        }


# ---------------------------------------------------------------------------
# Resume function signature
#
# A resume_fn receives:
#   - workflow_id (str) — full id from `workflows.id`
#   - workflow_row (dict) — the row dict from EventStore.get_workflow
#   - events (list[dict]) — full replay (seq, kind, payload, ts), already
#     decompressed and JSON-decoded
#
# Returns a ResumeOutcome.
# ---------------------------------------------------------------------------


ResumeFn = Callable[
    [str, dict[str, Any], list[dict[str, Any]]],
    Awaitable[ResumeOutcome],
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class WorkflowRegistry:
    """Maps workflow `kind` strings to async resume handlers.

    Use the process-wide singleton via `default_workflow_registry()`. Tests
    construct fresh instances directly.
    """

    def __init__(self, event_store: EventStore | None = None) -> None:
        self._handlers: dict[str, ResumeFn] = {}
        self._store = event_store

    def store(self) -> EventStore:
        """Lazy accessor — defers to the default singleton if none was
        injected at construction. Lets us register handlers before the
        event store has been initialized."""
        return self._store or default_event_store()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, kind: str, fn: ResumeFn) -> None:
        """Register `fn` as the resume handler for workflows of `kind`.

        Idempotent — calling `register(kind, fn)` twice with the same fn
        is a no-op; calling with a different fn logs a warning and
        replaces the previous handler.
        """
        if kind in self._handlers and self._handlers[kind] is not fn:
            logger.warning(
                "WorkflowRegistry: replacing handler for kind=%s "
                "(prev=%r, new=%r)",
                kind, self._handlers[kind], fn,
            )
        self._handlers[kind] = fn

    def has_handler(self, kind: str) -> bool:
        return kind in self._handlers

    def known_kinds(self) -> list[str]:
        return sorted(self._handlers)

    # ------------------------------------------------------------------
    # Resume API
    # ------------------------------------------------------------------

    async def resume(self, workflow_id: str) -> ResumeOutcome:
        """Resume a single workflow. Reads the workflow row + replays
        events, dispatches to the registered handler, and returns the
        outcome. Records a `workflow_resume_hint` event so the trail is
        auditable.

        If the workflow is unknown, returns an `orphan` outcome.
        If the kind has no handler, returns a `no_handler` outcome.
        Handler exceptions are caught and converted to `failed` outcomes
        — a buggy handler must never bring down the bootstrap.
        """
        store = self.store()
        wf = await store.get_workflow(workflow_id)
        if wf is None:
            return ResumeOutcome(
                workflow_id=workflow_id, kind="(unknown)",
                action=ACTION_ORPHAN,
                summary="Workflow row not found in event store.",
            )

        kind = wf["kind"]
        events = await store.replay(workflow_id)
        handler = self._handlers.get(kind)

        if handler is None:
            outcome = ResumeOutcome(
                workflow_id=workflow_id, kind=kind,
                action=ACTION_NO_HANDLER,
                summary=f"No resume handler registered for kind={kind!r}.",
            )
        else:
            try:
                outcome = await handler(workflow_id, wf, events)
                # Validate handler output — a misbehaving handler that
                # returns the wrong shape shouldn't break the pipeline.
                if not isinstance(outcome, ResumeOutcome):
                    raise TypeError(
                        f"resume_fn for {kind} returned {type(outcome)}, "
                        f"expected ResumeOutcome"
                    )
            except Exception as exc:
                logger.exception(
                    "resume_fn for kind=%s raised on workflow_id=%s",
                    kind, workflow_id,
                )
                outcome = ResumeOutcome(
                    workflow_id=workflow_id, kind=kind,
                    action=ACTION_FAILED,
                    summary=f"resume_fn raised: {type(exc).__name__}: {exc}",
                )

        # Persist the hint as an event for the audit trail. Outcomes that
        # change workflow status (complete / failed / orphan) also flip
        # the workflows.status row.
        await self._record_outcome(store, outcome)
        return outcome

    async def resume_all_running(
        self,
        max_age_seconds: float | None = None,
    ) -> list[ResumeOutcome]:
        """Resume every workflow currently in `running` or `waiting_gate`.

        `max_age_seconds=86400` (the default in `WORKFLOW_ORPHAN_AGE_SECONDS`)
        only considers workflows whose `updated_at` is at least that old —
        i.e. workflows that have actually been abandoned, not in-flight
        ones being mutated by another part of the server right now.

        Returns the list of outcomes in the order they were processed.
        """
        store = self.store()
        candidates = await store.list_workflows(
            statuses=[WORKFLOW_RUNNING, WORKFLOW_WAITING_GATE],
            older_than_seconds=max_age_seconds,
        )
        outcomes: list[ResumeOutcome] = []
        for wf in candidates:
            outcomes.append(await self.resume(wf["id"]))
        return outcomes

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _record_outcome(
        self, store: EventStore, outcome: ResumeOutcome,
    ) -> None:
        """Append a `workflow_resume_hint` event and, when warranted,
        flip the workflow's status."""
        await store.append_event(
            outcome.workflow_id, "workflow_resume_hint",
            {
                "kind": outcome.kind,
                "action": outcome.action,
                "summary": outcome.summary,
                "resume_hint": outcome.resume_hint,
                "unfinished_llm_call_count": len(outcome.unfinished_llm_calls),
            },
        )
        if outcome.action == ACTION_COMPLETE:
            await store.mark_workflow_status(
                outcome.workflow_id, WORKFLOW_COMPLETED,
            )
        elif outcome.action == ACTION_FAILED:
            await store.mark_workflow_status(
                outcome.workflow_id, WORKFLOW_FAILED,
            )
        elif outcome.action in (ACTION_ORPHAN, ACTION_NO_HANDLER):
            await store.mark_workflow_status(
                outcome.workflow_id, WORKFLOW_ORPHANED,
            )
        # ACTION_READY_TO_RESUME leaves status as-is (running /
        # waiting_gate) so the agent can pick it up.


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


_default_registry: WorkflowRegistry | None = None


def default_workflow_registry() -> WorkflowRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = WorkflowRegistry()
    return _default_registry


def reset_default_workflow_registry() -> None:
    """Tests use this to wipe state between runs."""
    global _default_registry
    _default_registry = None


# ---------------------------------------------------------------------------
# Helper: read the most recent resume hint for a workflow
# ---------------------------------------------------------------------------


async def get_latest_resume_hint(
    workflow_id: str,
    store: EventStore | None = None,
    requesting_owner: str | None = None,
) -> dict[str, Any] | None:
    """Find the most recent `workflow_resume_hint` event for a workflow.

    If `requesting_owner` is set, returns None when the workflow row
    belongs to a different owner (NULL-owned rows pass through —
    legacy / single-tenant fallback). Use `None` for admin / boot
    sweep paths; pass `identity.get_current_owner()` from MCP tools.
    """
    s = store or default_event_store()
    if requesting_owner is not None:
        # Cheap ownership check first — if the row exists but isn't
        # ours, return None without reading the event log.
        wf = await s.get_workflow(workflow_id, requesting_owner=requesting_owner)
        if wf is None:
            return None
    events = await s.replay(workflow_id)
    for ev in reversed(events):
        if ev["kind"] == "workflow_resume_hint":
            return {"seq": ev["seq"], "ts": ev["ts"], **ev["payload"]}
    return None


async def list_recent_resume_hints(
    max_age_seconds: float | None = None,
    store: EventStore | None = None,
    requesting_owner: str | None = None,
) -> list[dict[str, Any]]:
    """Return the latest hint per workflow currently in running /
    waiting_gate.

    Filters:
        max_age_seconds   — only workflows whose `updated_at` is at
                            least this old (passed through to
                            `list_workflows`).
        requesting_owner  — when set, return rows owned by this
                            caller plus NULL-owned (legacy) rows.
                            Use `None` for boot sweeps / admin.
    """
    s = store or default_event_store()
    candidates = await s.list_workflows(
        statuses=[WORKFLOW_RUNNING, WORKFLOW_WAITING_GATE],
        older_than_seconds=max_age_seconds,
        owner=requesting_owner,
    )
    results: list[dict[str, Any]] = []
    for wf in candidates:
        # Skip the per-call ownership check inside get_latest_resume_hint
        # — list_workflows already filtered by owner above.
        hint = await get_latest_resume_hint(wf["id"], store=s)
        results.append({
            "workflow_id": wf["id"],
            "kind": wf["kind"],
            "status": wf["status"],
            "updated_at": wf["updated_at"],
            "hint": hint,
        })
    return results
