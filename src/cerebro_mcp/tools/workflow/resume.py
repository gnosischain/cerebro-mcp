"""MCP tools for surfacing WorkflowRegistry resume hints to agents.

Bootstrap runs the registry sweep at server start and writes a
`workflow_resume_hint` event for every still-running workflow it touched.
These tools expose that data so an analyst agent can:

  - See which workflows are mid-flight on the next user interaction
    (`list_resumable_workflows`)
  - Pull the structured resume hint for a specific workflow
    (`get_workflow_resume_hint`)
  - Re-run the resume scan on demand for a single workflow
    (`recompute_workflow_resume_hint`) — useful when a workflow has
    progressed since the bootstrap-time scan and the hint is stale.

The tools are read-only (apart from the recompute tool, which only
appends a new hint event). They never make LLM calls or hit ClickHouse;
the agent decides whether to act on the hint.
"""

from __future__ import annotations

import json
import logging

from cerebro_mcp.runtime.identity import get_current_owner
from cerebro_mcp.runtime.tool_output import truncate_response
from cerebro_mcp.workflow.registry import (
    default_workflow_registry,
    get_latest_resume_hint,
    list_recent_resume_hints,
)

logger = logging.getLogger(__name__)


# These tools are `async def` because they call into the async EventStore
# API (aiosqlite). The earlier sync wrappers used `asyncio.run(...)`, which
# crashes inside FastMCP's running event loop with
# `RuntimeError: asyncio.run() cannot be called from a running event loop`.
# FastMCP awaits async tool handlers correctly, so async is the simplest
# correct path.


def register_workflow_resume_tools(mcp):
    @mcp.tool()
    async def list_resumable_workflows(min_idle_seconds: int = 0) -> str:
        """List workflows currently running or waiting on a gate, with
        the most recent resume hint for each.

        Use this on the next user interaction after a server restart to
        find research projects (or other workflow types) that were
        abandoned mid-flight. Each entry includes the workflow_id, kind,
        status, the most recent `updated_at`, and the structured
        `resume_hint` payload — the agent decides whether to continue.

        Args:
            min_idle_seconds: optional filter — only show workflows whose
                `updated_at` is at least this old. Default 0 (show every
                running / waiting_gate workflow regardless of age).
                Use a positive value (e.g. 3600) to find workflows that
                have been specifically stalled for more than an hour.

                NB: the bootstrap-time auto-resume sweep uses 24h as its
                cutoff for *deciding whether to write a hint*, but the
                hint-writing itself bumps the workflow's `updated_at`.
                That's why the default here is 0 — otherwise the agent
                would never see workflows the boot sweep just flagged
                (their `updated_at` would be "now", filtered out).
                Live regression observed 2026-04-27.

        Returns:
            Markdown summary listing each workflow + hint.
        """
        try:
            entries = await list_recent_resume_hints(
                max_age_seconds=min_idle_seconds if min_idle_seconds > 0 else None,
                requesting_owner=get_current_owner(),
            )
        except Exception as e:
            logger.exception("list_resumable_workflows failed")
            return f"Error: {e}"

        if not entries:
            return (
                "No resumable workflows found "
                f"(checked workflows older than {max_age_seconds}s)."
            )

        lines = [f"Found {len(entries)} resumable workflow(s):\n"]
        for ent in entries:
            wid = ent["workflow_id"]
            kind = ent["kind"]
            status = ent["status"]
            hint = ent.get("hint") or {}
            action = hint.get("action", "(no hint yet)")
            summary = hint.get("summary", "")
            lines.append(f"- **{wid}**  ({kind}, status={status})")
            lines.append(f"  - Action: `{action}`")
            if summary:
                lines.append(f"  - {summary}")
            rh = hint.get("resume_hint") or {}
            if rh:
                lines.append(f"  - Resume hint: `{json.dumps(rh, default=str)[:300]}`")
        return truncate_response("\n".join(lines))

    @mcp.tool()
    async def get_workflow_resume_hint(workflow_id: str) -> str:
        """Return the most recent resume hint for a specific workflow.

        Use this when you have a workflow_id (e.g. from
        `list_resumable_workflows`) and want to see the full hint payload
        without the listing-level summary truncation.

        Args:
            workflow_id: full event-store workflow id, e.g.
                `research_rp_a1b2c3d4`.

        Returns:
            JSON-formatted hint with action, summary, structured
            resume_hint dict, and unfinished_llm_call_count.
        """
        try:
            hint = await get_latest_resume_hint(
                workflow_id,
                requesting_owner=get_current_owner(),
            )
        except Exception as e:
            logger.exception("get_workflow_resume_hint failed")
            return f"Error: {e}"
        if hint is None:
            return f"No resume hint recorded for workflow `{workflow_id}`."
        return f"```json\n{json.dumps(hint, indent=2, default=str)}\n```"

    @mcp.tool()
    async def recompute_workflow_resume_hint(workflow_id: str) -> str:
        """Re-run the resume scan for a single workflow and append a new
        hint event. Use this when a workflow has progressed since the
        bootstrap-time scan and the previous hint is stale.

        Args:
            workflow_id: full event-store workflow id.

        Returns:
            JSON of the new ResumeOutcome.
        """
        try:
            registry = default_workflow_registry()
            # Ownership check before triggering registry side effects.
            # An attacker with a known workflow_id but no ownership
            # could otherwise force status flips (registry resume can
            # mark `complete` / `failed` / `orphan`). We don't reveal
            # whether the row exists vs is owned by someone else —
            # both surface as "Error: not found".
            requester = get_current_owner()
            if requester is not None:
                wf = await registry.store().get_workflow(
                    workflow_id, requesting_owner=requester,
                )
                if wf is None:
                    return f"Error: workflow {workflow_id} not found."
            outcome = await registry.resume(workflow_id)
        except Exception as e:
            logger.exception("recompute_workflow_resume_hint failed")
            return f"Error: {e}"
        return f"```json\n{json.dumps(outcome.to_dict(), indent=2, default=str)}\n```"
