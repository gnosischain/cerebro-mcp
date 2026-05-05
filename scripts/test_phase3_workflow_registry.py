#!/usr/bin/env python3
"""Phase 3 — WorkflowRegistry end-to-end smoke.

Demonstrates the auto-resume flow that closes the original "lost
14-minute report" failure mode:

  1. A research workflow runs partway through, then "the process dies"
     (we simulate by closing references and reopening a fresh store on
     the same SQLite file).
  2. The bootstrap path runs the registry sweep — finds the still-running
     workflow, dispatches to the research resume handler, computes a
     ResumeOutcome, and writes a `workflow_resume_hint` event to the log.
  3. The list/get MCP tool helpers (`list_recent_resume_hints` /
     `get_latest_resume_hint`) read that hint back — what an agent would
     do on the next user interaction.
  4. The agent uses the hint's `next_action` and `next_action_args` to
     pick up at the right phase.

Five sections cover all the resume action types:

  A. Fresh project at "mapping" → ready_to_resume, next_action=plan
  B. Mid-flight project mid-execution → ready_to_resume at execution
  C. Workflow with peer review rejected → failed, audit trail recorded
  D. Workflow that already published → complete, status flipped
  E. Unknown kind (no registered handler) → orphaned

Usage:
    python scripts/test_phase3_workflow_registry.py
    python scripts/test_phase3_workflow_registry.py --keep-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.event_store import EventStore  # noqa: E402
from cerebro_mcp.research_resume import install_research_resume_handler  # noqa: E402
from cerebro_mcp.workflow_payloads import (  # noqa: E402
    EVENT_LLM_CALL_STARTED,
    LLMCallEvent,
    LLMTurn,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
)
from cerebro_mcp.workflow_registry import (  # noqa: E402
    ACTION_COMPLETE,
    ACTION_FAILED,
    ACTION_NO_HANDLER,
    ACTION_READY_TO_RESUME,
    default_workflow_registry,
    get_latest_resume_hint,
    list_recent_resume_hints,
    reset_default_workflow_registry,
)


class Reporter:
    def __init__(self) -> None:
        self.passes = 0
        self.fails = 0

    def section(self, name: str) -> None:
        print(f"\n=== {name} ===")

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        if ok:
            self.passes += 1
        else:
            self.fails += 1

    def info(self, label: str, value) -> None:
        print(f"  [INFO] {label}: {value}")

    def summary(self) -> int:
        total = self.passes + self.fails
        print(f"\n--- {self.passes}/{total} checks passed "
              f"({self.fails} failures) ---")
        return 0 if self.fails == 0 else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_store(db_path: Path) -> EventStore:
    """Build an EventStore pointing at `db_path` AND wire the process-wide
    singletons (settings.EVENT_STORE_PATH, default_event_store) to use it
    so the registry — which constructs via the singleton — reads/writes
    the same file."""
    from cerebro_mcp import config as cerebro_config
    cerebro_config.settings.EVENT_STORE_PATH = str(db_path)

    from cerebro_mcp import event_store as event_store_mod
    s = EventStore(db_path=db_path)
    await s.init()
    event_store_mod._default_store = s
    return s


async def _seed_research_project(
    store: EventStore, project_id: str, *, scenario: str,
) -> str:
    """Seed a research workflow with a specific event pattern.

    `scenario` selects which sub-state we want:
      - "fresh"        — workflow_started only
      - "mid_execution"— through phase_planned("execution") then died
      - "rejected"     — peer_review_recorded with status=rejected
      - "published"    — full lifecycle through report_published
      - "interrupted_llm" — workflow_started + an unfinished LLM call
    """
    workflow_id = f"research_{project_id}"
    await store.create_workflow(
        workflow_id, "research_project",
        metadata={"project_id": project_id,
                  "hypothesis": "h", "scope": "s"},
    )

    if scenario == "fresh":
        await store.append_event(workflow_id, "workflow_started",
                                 {"project_id": project_id})

    elif scenario == "mid_execution":
        await store.append_event(workflow_id, "workflow_started",
                                 {"project_id": project_id})
        for phase, advance_to in [
            ("mapping", "hypothesis"),
            ("hypothesis", "execution"),
        ]:
            await store.append_event(workflow_id, "phase_planned",
                                     {"phase": phase})
            await store.append_event(workflow_id, "phase_completed",
                                     {"phase": phase, "advanced_to": advance_to})
        await store.append_event(workflow_id, "phase_planned",
                                 {"phase": "execution"})
        # NB: no phase_completed for execution — that's where it died.

    elif scenario == "rejected":
        await store.append_event(workflow_id, "workflow_started",
                                 {"project_id": project_id})
        await store.append_event(workflow_id, "peer_review_recorded",
                                 {"status": "rejected",
                                  "summary_preview": "stats too thin"})

    elif scenario == "published":
        await store.append_event(workflow_id, "workflow_started",
                                 {"project_id": project_id})
        await store.append_event(workflow_id, "report_published",
                                 {"report_id": "rep_done",
                                  "title": "Final"})

    elif scenario == "interrupted_llm":
        await store.append_event(workflow_id, "workflow_started",
                                 {"project_id": project_id})
        call = LLMCallEvent(
            subtask_name="defi_analyst", call_id="call_001",
            system_prompt="cerebro persona…",
            messages=[LLMTurn(role="user",
                              content=[{"type": "text",
                                        "text": "Bridge TVL?"}])],
            tool_schemas=[{"name": "execute_query"}],
        )
        await store.append_event(workflow_id, EVENT_LLM_CALL_STARTED, call)
        # No completion → unfinished.

    else:
        raise ValueError(f"unknown scenario {scenario!r}")

    return workflow_id


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


async def section_fresh(r: Reporter, db_path: Path) -> None:
    r.section("A. Fresh project — registry resumes at 'mapping'")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    wid = await _seed_research_project(store, "p_fresh", scenario="fresh")
    outcome = await registry.resume(wid)

    r.check("action = ready_to_resume",
            outcome.action == ACTION_READY_TO_RESUME,
            f"got {outcome.action}")
    r.check("hint includes project_id",
            outcome.resume_hint.get("project_id") == "p_fresh")
    r.check("current_phase = mapping",
            outcome.resume_hint.get("current_phase") == "mapping")
    r.check("next_action = plan_research_phase",
            outcome.resume_hint.get("next_action") == "plan_research_phase")
    # Hint event was recorded.
    events = await store.replay(wid)
    r.check("workflow_resume_hint event recorded",
            any(e["kind"] == "workflow_resume_hint" for e in events))


async def section_mid_execution(r: Reporter, db_path: Path) -> None:
    r.section("B. Mid-execution — registry knows to resume at 'execution'")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    wid = await _seed_research_project(store, "p_mid", scenario="mid_execution")
    outcome = await registry.resume(wid)

    r.check("action = ready_to_resume",
            outcome.action == ACTION_READY_TO_RESUME)
    r.check("current_phase = execution",
            outcome.resume_hint.get("current_phase") == "execution",
            f"got {outcome.resume_hint.get('current_phase')}")
    r.check("completed_phases includes mapping + hypothesis",
            set(["mapping", "hypothesis"]).issubset(
                set(outcome.resume_hint.get("completed_phases") or [])))


async def section_published(r: Reporter, db_path: Path) -> None:
    r.section("C. Published workflow — registry returns 'complete' and flips status")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    wid = await _seed_research_project(store, "p_done", scenario="published")
    outcome = await registry.resume(wid)
    r.check("action = complete", outcome.action == ACTION_COMPLETE)

    wf = await store.get_workflow(wid)
    r.check("workflow row marked COMPLETED",
            wf["status"] == WORKFLOW_COMPLETED, str(wf["status"]))


async def section_rejected(r: Reporter, db_path: Path) -> None:
    r.section("D. Peer review rejected — registry returns 'failed' and flips status")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    wid = await _seed_research_project(store, "p_rej", scenario="rejected")
    outcome = await registry.resume(wid)
    r.check("action = failed", outcome.action == ACTION_FAILED)

    wf = await store.get_workflow(wid)
    r.check("workflow row marked FAILED",
            wf["status"] == WORKFLOW_FAILED, str(wf["status"]))


async def section_unknown_kind(r: Reporter, db_path: Path) -> None:
    r.section("E. Unknown kind — registry falls back to 'orphan'")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    # Make a workflow whose `kind` we never registered a handler for.
    await store.create_workflow("wid_storyteller_xyz", "storyteller_session")
    await store.append_event("wid_storyteller_xyz", "phase_started",
                             {"phase": "context"})

    outcome = await registry.resume("wid_storyteller_xyz")
    r.check("action = no_handler",
            outcome.action == ACTION_NO_HANDLER,
            f"got {outcome.action}")

    wf = await store.get_workflow("wid_storyteller_xyz")
    r.check("workflow row marked ORPHANED",
            wf["status"] == WORKFLOW_ORPHANED, str(wf["status"]))


async def section_unfinished_llm_call(r: Reporter, db_path: Path) -> None:
    r.section("F. Unfinished LLM call surfaces in the resume hint")
    store = await _fresh_store(db_path)
    install_research_resume_handler()
    registry = default_workflow_registry()

    wid = await _seed_research_project(
        store, "p_llm", scenario="interrupted_llm",
    )
    outcome = await registry.resume(wid)
    r.check("action = ready_to_resume",
            outcome.action == ACTION_READY_TO_RESUME)
    r.check("1 unfinished LLM call surfaced",
            len(outcome.unfinished_llm_calls) == 1,
            f"got {len(outcome.unfinished_llm_calls)}")
    r.check("unfinished call has full message history",
            outcome.unfinished_llm_calls
            and outcome.unfinished_llm_calls[0].messages
            and outcome.unfinished_llm_calls[0].messages[0].content[0]["text"]
                == "Bridge TVL?")


async def section_list_recent_hints(r: Reporter, db_path: Path) -> None:
    r.section("G. list_recent_resume_hints / get_latest_resume_hint MCP-tool helpers")
    store = await _fresh_store(db_path)
    # Force the helpers to read from the same store our test sections wrote to
    # by making sure default_event_store() points at this file.
    from cerebro_mcp import event_store as event_store_mod
    event_store_mod._default_store = store

    entries = await list_recent_resume_hints(store=store)
    r.info("running workflows visible to list helper", len(entries))
    # Sections A, B, F left workflows in `running`; C/D/E flipped to
    # completed/failed/orphaned. So we expect exactly 3 surfacing here.
    r.check("3 running workflows surfaced (A, B, F)",
            len(entries) == 3, f"got {len(entries)}")

    # Pick one and verify the latest hint reads back end-to-end.
    if entries:
        sample = entries[0]
        hint = await get_latest_resume_hint(sample["workflow_id"], store=store)
        r.check("get_latest_resume_hint returns a hint",
                hint is not None and "action" in hint,
                f"got {hint}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-db", action="store_true",
                    help="leave the SQLite db on disk for inspection")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="cerebro_phase3_registry_"))
    db_path = workdir / "state.db"
    print("Phase 3 WorkflowRegistry smoke test")
    print(f"workspace: {workdir}")
    print(f"sqlite db: {db_path}")
    print("=" * 60)

    # Reset the registry singleton so each section starts clean (the
    # research handler is idempotent so re-installing in each section
    # is a no-op).
    reset_default_workflow_registry()

    r = Reporter()
    await section_fresh(r, db_path)
    await section_mid_execution(r, db_path)
    await section_published(r, db_path)
    await section_rejected(r, db_path)
    await section_unknown_kind(r, db_path)
    await section_unfinished_llm_call(r, db_path)
    await section_list_recent_hints(r, db_path)

    rc = r.summary()

    if args.keep_db:
        print(f"\nworkspace kept: {workdir}")
        print("inspect with:")
        print(f"  sqlite3 {db_path} 'SELECT id, kind, status FROM workflows'")
        print(f"  sqlite3 {db_path} 'SELECT workflow_id, kind, COUNT(*) FROM events GROUP BY workflow_id, kind'")
    else:
        try:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
