#!/usr/bin/env python3
"""Phase 3 Step 1 end-to-end smoke.

Drives a research workflow through the FULL real flow — phase transitions
plus the new work-tool events (query/memory/finding/evidence) — then
"crashes" (closes the store, reopens on the same SQLite file) and verifies
the resume hint surfaces real progress.

Sections:
    A. Start project + plan/execute mapping (existing phase events)
    B. Execute several queries via record_research_query_executed:
       successful + 2 failed (column hallucination + memory limit)
    C. Record an observation (memory_recorded)
    D. Attach evidence (evidence_attached)
    E. Record a finding (finding_recorded)
    F. "Process kill" — drop refs, reopen store
    G. Recompute resume hint, verify it carries:
         - 4 queries (2 failed with their error classes)
         - 1 memory entry with the actual statement preview
         - 1 finding with title + confidence
         - 2 evidence items grouped by phase
         - phase=hypothesis (mapping completed)
         - next_action = plan_research_phase for hypothesis
    H. Continue post-resume — record one more memory, recompute, verify
       the new memory shows up + the prior progress is still there

Run:
    python scripts/test_phase3_step1_e2e.py
    python scripts/test_phase3_step1_e2e.py --keep-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp import config as cerebro_config
from cerebro_mcp import event_store_sync as ev
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.research_resume import (
    install_research_resume_handler,
    resume_research_project,
)
from cerebro_mcp.workflow_registry import (
    ACTION_READY_TO_RESUME,
    default_workflow_registry,
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
# Sections
# ---------------------------------------------------------------------------


PROJECT_ID = "p_marketplace_e2e"


def section_a_start_and_phase(r: Reporter) -> None:
    r.section("A. Start project + plan/execute mapping")
    ev.record_research_started(
        PROJECT_ID,
        hypothesis="Marketplace shows distinct user-behavior waves",
        scope="Q1 2026 Gnosis App marketplace, Mixpanel funnel",
    )
    ev.record_research_phase_planned(
        PROJECT_ID, "mapping",
        "## Mapping plan\n\nDiscover marketplace + funnel models.",
    )
    ev.record_research_phase_completed(
        PROJECT_ID, "mapping", advanced_to="hypothesis",
    )
    r.check("phase events recorded", True)


def section_b_queries(r: Reporter) -> None:
    r.section("B. Execute queries (2 succeed, 2 fail with different error classes)")

    ev.record_research_query_executed(
        PROJECT_ID,
        sql="SELECT count(*) AS rows FROM dbt.fct_execution_gnosis_app_marketplace_buys_daily",
        database="dbt", row_count=1, elapsed_seconds=0.142,
        evidence_title="EDA: marketplace daily row count",
        artifact_ref_id="qry_aaa111",
    )
    ev.record_research_query_executed(
        PROJECT_ID,
        sql="SELECT day, sum(buys) AS buys FROM dbt.fct_execution_gnosis_app_marketplace_buys_daily GROUP BY day ORDER BY day",
        database="dbt", row_count=92, elapsed_seconds=0.487,
        evidence_title="Daily buys time-series",
        artifact_ref_id="qry_bbb222",
    )
    # Hallucinated column — what the agent did 4× yesterday
    ev.record_research_query_executed(
        PROJECT_ID,
        sql="SELECT n_buys FROM dbt.fct_execution_gnosis_app_marketplace_offers_latest",
        database="dbt", row_count=0, elapsed_seconds=0.0,
        evidence_title="(failed) per-offer buys",
        error_class="clickhouse_code_47",
    )
    # CH memory limit
    ev.record_research_query_executed(
        PROJECT_ID,
        sql="SELECT * FROM dbt.huge_join LIMIT 100000",
        database="dbt", row_count=0, elapsed_seconds=8.2,
        evidence_title="(failed) full marketplace dump",
        error_class="clickhouse_code_241",
    )
    r.check("4 query_executed events appended", True)


def section_c_memory(r: Reporter) -> None:
    r.section("C. Record observation (memory_recorded)")
    ev.record_research_memory_recorded(
        PROJECT_ID,
        memory_id="mem_marketplace_waves",
        kind="observation",
        statement=(
            "The marketplace has TWO distinct activity waves: (1) Jan 15 – "
            "Feb 25, 2026: Fileverse Shop Payments dominates with low-ticket "
            "(256 CRC) volume, drives ~80% of all buys; (2) Feb 25 – Mar 22, "
            "2026: premium offers (Gnosis cap, Ocean Bottle) launch with "
            "high-ticket items (1,500–3,500 CRC) but small buyer counts."
        ),
        confidence=0.95,
    )
    r.check("memory_recorded event appended", True)


def section_d_evidence(r: Reporter) -> None:
    r.section("D. Attach evidence (2 items at execution phase)")
    ev.record_research_evidence_attached(
        PROJECT_ID, kind="query_result", ref_id="qry_aaa111",
        phase="execution", title="EDA: marketplace daily row count",
    )
    ev.record_research_evidence_attached(
        PROJECT_ID, kind="query_result", ref_id="qry_bbb222",
        phase="execution", title="Daily buys time-series",
    )
    r.check("2 evidence_attached events appended", True)


def section_e_finding(r: Reporter) -> None:
    r.section("E. Record a finding")
    ev.record_research_finding_recorded(
        PROJECT_ID,
        finding_id="find_dropoff_at_modal",
        title="Marketplace funnel drops 90% at modal-open step",
        confidence=0.85,
        evidence_count=2,
    )
    r.check("finding_recorded event appended", True)


async def section_f_crash_and_resume(r: Reporter, db_path: Path) -> None:
    r.section("F. Simulated process kill — close + reopen the store")
    # The sync helpers don't hold a long-lived connection — every call
    # opens + closes its own. So the crash sim is "drop the bootstrap
    # cache, reopen, replay events".
    ev._reset_bootstrap_cache()

    s2 = EventStore(db_path=db_path)
    workflow_id = ev.workflow_id_for_research(PROJECT_ID)
    events = await s2.replay(workflow_id)
    r.info("events recovered after reopen", len(events))
    kinds = [e["kind"] for e in events]
    r.check("workflow_started survived",
            "workflow_started" in kinds)
    r.check("4 query_executed survived",
            kinds.count("query_executed") == 4)
    r.check("memory_recorded survived",
            kinds.count("memory_recorded") == 1)
    r.check("finding_recorded survived",
            kinds.count("finding_recorded") == 1)
    r.check("2 evidence_attached survived",
            kinds.count("evidence_attached") == 2)
    r.check("phase events survived",
            kinds.count("phase_planned") >= 1
            and kinds.count("phase_completed") >= 1)


async def section_g_resume_hint(r: Reporter, db_path: Path) -> None:
    r.section("G. Resume hint surfaces real progress")
    s = EventStore(db_path=db_path)
    workflow_id = ev.workflow_id_for_research(PROJECT_ID)
    wf = await s.get_workflow(workflow_id)
    events = await s.replay(workflow_id)
    out = await resume_research_project(workflow_id, wf, events)

    r.check("action = ready_to_resume",
            out.action == ACTION_READY_TO_RESUME, out.action)
    r.check("current_phase = hypothesis (mapping completed)",
            out.resume_hint["current_phase"] == "hypothesis")
    r.check("next_action = plan_research_phase",
            out.resume_hint["next_action"] == "plan_research_phase")

    work = out.resume_hint["work"]
    r.info("work summary", json.dumps(work, indent=2))
    r.check("queries_run = 4", work["queries_run"] == 4)
    r.check("queries_failed = 2", work["queries_failed"] == 2)
    r.check("error_classes captures both CH errors",
            set(work["query_error_classes"] or {}) ==
            {"clickhouse_code_47", "clickhouse_code_241"})
    r.check("memory_count = 1", work["memory_count"] == 1)
    r.check("recent_memories preserves the actual observation",
            "TWO distinct activity waves" in
            (work["recent_memories"][0]["statement_preview"] or ""))
    r.check("finding_count = 1", work["finding_count"] == 1)
    r.check("recent_findings has the title",
            "drops 90%" in (work["recent_findings"][0]["title"] or ""))
    r.check("evidence_count = 2", work["evidence_count"] == 2)
    r.check("evidence_by_phase recorded for execution",
            (work["evidence_by_phase"] or {}).get("execution") == 2)
    r.check("recent_evidence_titles populated",
            work["recent_evidence_titles"]
            and len(work["recent_evidence_titles"]) == 2)
    r.check("summary mentions queries+memories+evidence",
            all(s in out.summary for s in
                ["4 queries run", "2 failed",
                 "1 memory entries", "2 evidence items"]))


async def section_h_continue_post_resume(r: Reporter, db_path: Path) -> None:
    r.section("H. Continue post-resume: append one more memory, re-check hint")
    ev.record_research_memory_recorded(
        PROJECT_ID,
        memory_id="mem_funnel_followup",
        kind="hypothesis",
        statement=(
            "If the modal-open step drops 90% of users, fixing the modal "
            "load time should be the highest-leverage growth experiment."
        ),
        confidence=0.7,
    )

    s = EventStore(db_path=db_path)
    workflow_id = ev.workflow_id_for_research(PROJECT_ID)
    wf = await s.get_workflow(workflow_id)
    events = await s.replay(workflow_id)
    out = await resume_research_project(workflow_id, wf, events)

    work = out.resume_hint["work"]
    r.check("memory_count = 2 after follow-up",
            work["memory_count"] == 2)
    r.check("recent_memories[-1] is the new follow-up",
            "highest-leverage growth experiment" in
            (work["recent_memories"][-1]["statement_preview"] or ""))
    # Old memory still in recent_memories (we keep last 3).
    r.check("original observation still surfaced",
            any("TWO distinct activity waves" in
                (m["statement_preview"] or "")
                for m in work["recent_memories"]))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-db", action="store_true",
                    help="leave the SQLite db on disk for inspection")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="cerebro_phase3_step1_e2e_"))
    db_path = workdir / "state.db"
    cerebro_config.settings.EVENT_STORE_PATH = str(db_path)
    ev._reset_bootstrap_cache()
    # Make sure the registry singleton uses our DB.
    reset_default_workflow_registry()
    install_research_resume_handler()

    print("Phase 3 Step 1 end-to-end smoke")
    print(f"workspace: {workdir}")
    print(f"sqlite db: {db_path}")
    print("=" * 60)

    r = Reporter()
    section_a_start_and_phase(r)
    section_b_queries(r)
    section_c_memory(r)
    section_d_evidence(r)
    section_e_finding(r)
    await section_f_crash_and_resume(r, db_path)
    await section_g_resume_hint(r, db_path)
    await section_h_continue_post_resume(r, db_path)

    rc = r.summary()

    if args.keep_db:
        print(f"\nworkspace kept: {workdir}")
        print(f"  sqlite3 {db_path} 'SELECT seq, kind FROM events ORDER BY workflow_id, seq'")
    else:
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
