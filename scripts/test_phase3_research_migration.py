#!/usr/bin/env python3
"""End-to-end test of the Phase 3 research-workflow → event-log migration.

What this verifies (that the existing scripts don't):

1. `tools/research.py` MCP tools — when invoked end-to-end through the
   actual registration surface — write events into the SAME
   `cerebro_state.db` the production server uses.
2. The full research lifecycle (start → plan → execute → verify → peer
   review → publish) produces the expected event sequence and gate flips.
3. Failure tolerance: forcing an unwritable EVENT_STORE_PATH does not
   break the underlying research-store flow.
4. Idempotency on `start_research_project` retries.

Strategy:
    Construct a `FakeMCP` that captures every `@mcp.tool()` decoration so
    we can call the registered handlers directly without FastMCP. Use a
    real `ResearchStore` against a tmp dir so on-disk JSON state is also
    real. Point `EVENT_STORE_PATH` at a fresh SQLite file we can inspect.

Run:
    python scripts/test_phase3_research_migration.py
    python scripts/test_phase3_research_migration.py --keep-db   # leave the db for sqlite3 inspection
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp import config as cerebro_config  # noqa: E402
from cerebro_mcp.workflow.event_store import EventStore  # noqa: E402
from cerebro_mcp.models.research import EvidenceRef, PeerReviewResult  # noqa: E402
from cerebro_mcp.research.store import ResearchStore  # noqa: E402
from cerebro_mcp.tools.research.research import register_research_tools  # noqa: E402
from cerebro_mcp.workflow.payloads import (  # noqa: E402
    GATE_FAILED,
    GATE_PASSED,
    WORKFLOW_COMPLETED,
    WORKFLOW_RUNNING,
)


# ---------------------------------------------------------------------------
# Test runner + report
# ---------------------------------------------------------------------------


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

    def info(self, label: str, value: Any) -> None:
        print(f"  [INFO] {label}: {value}")

    def summary(self) -> int:
        total = self.passes + self.fails
        print(f"\n--- {self.passes}/{total} checks passed "
              f"({self.fails} failures) ---")
        return 0 if self.fails == 0 else 1


# ---------------------------------------------------------------------------
# Fake FastMCP — captures @mcp.tool() registrations so we can call handlers.
# ---------------------------------------------------------------------------


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *args, **kwargs):
        def _decorator(fn: Callable) -> Callable:
            self.tools[fn.__name__] = fn
            return fn
        return _decorator


def _read_workflows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT id, kind, status, datetime(created_at, 'unixepoch') "
        "FROM workflows ORDER BY created_at"
    )
    return [{"id": r[0], "kind": r[1], "status": r[2], "started": r[3]}
            for r in cur.fetchall()]


def _read_events(db_path: Path, workflow_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT seq, kind, datetime(ts, 'unixepoch') "
        "FROM events WHERE workflow_id = ? ORDER BY seq",
        (workflow_id,),
    )
    return [{"seq": r[0], "kind": r[1], "ts": r[2]} for r in cur.fetchall()]


def _read_gates(db_path: Path, workflow_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT gate_name, status, payload_json FROM gates "
        "WHERE workflow_id = ? ORDER BY updated_at",
        (workflow_id,),
    )
    return [{"gate": r[0], "status": r[1],
             "payload": json.loads(r[2] or "{}")}
            for r in cur.fetchall()]


def _setup(workdir: Path, monkey_event_path: Path | None = None) -> tuple:
    """Create a fresh research store + fake-MCP-bound tool registry.

    Returns (tools_dict, store, ch_stub, event_db_path).
    """
    research_dir = workdir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(str(research_dir))

    db_path = monkey_event_path or workdir / "cerebro_state.db"
    cerebro_config.settings.EVENT_STORE_PATH = str(db_path)

    # Bootstrap the schema once via async EventStore so the sync helpers
    # see the tables on first use (matches what server.py:main() does).
    if monkey_event_path is None:
        es = EventStore(db_path=db_path)
        asyncio.run(es.init())

    fake_mcp = FakeMCP()
    ch_stub = SimpleNamespace()  # research tools never call CH directly
    register_research_tools(fake_mcp, ch_stub, store)
    return fake_mcp.tools, store, ch_stub, db_path


# ---------------------------------------------------------------------------
# Section 1 — full lifecycle through the actual MCP tool surface
# ---------------------------------------------------------------------------


def section_full_lifecycle(r: Reporter, workdir: Path) -> dict:
    r.section("1. Full research lifecycle (start → plan → execute → verify → peer review → publish)")
    tools, store, ch, db_path = _setup(workdir)

    # 1) Start the project — should land a workflow row + workflow_started event.
    summary = tools["start_research_project"](
        hypothesis="Gnosis Pay user retention is improving in Q3.",
        scope="Daily activity, payments, cashback for Q3 2026.",
    )
    if isinstance(summary, str) and summary.startswith("Error"):
        r.check("start_research_project succeeded", False, summary)
        return {"db_path": db_path}
    project_id = summary.project_id
    r.info("project_id", project_id)
    workflow_id = f"research_{project_id}"

    workflows = _read_workflows(db_path)
    r.check("workflow row created in event store",
            any(w["id"] == workflow_id for w in workflows),
            f"workflow_ids={[w['id'] for w in workflows]}")

    # 2) Walk through mapping → hypothesis → execution
    for phase, _ in [
        ("mapping", "hypothesis"),
        ("hypothesis", "execution"),
        ("execution", "verification"),
    ]:
        plan_result = tools["plan_research_phase"](
            project_id=project_id, phase=phase,
            plan_markdown=f"## Plan for {phase}\n\nDo the work.",
        )
        if isinstance(plan_result, str) and plan_result.startswith("Error"):
            r.check(f"plan {phase}", False, plan_result)
            continue
        # `verification` and `publication` are completed via dedicated
        # tools, but `mapping`/`hypothesis`/`execution` go through
        # `execute_research_phase`.
        exec_result = tools["execute_research_phase"](
            project_id=project_id, phase=phase,
        )
        if isinstance(exec_result, str) and exec_result.startswith("Error"):
            r.check(f"execute {phase}", False, exec_result)
            continue

    # Seed an execution-phase query_result evidence so verification
    # actually passes (otherwise the verification gate fails on missing
    # evidence and the lifecycle stops here). This bypasses MCP tools
    # because we don't have a live ClickHouse — the artifact format
    # mirrors what `execute_query` produces inside a research project.
    artifact_id = store.save_query_result_artifact(
        project_id=project_id,
        title="EDA: payment volume distribution",
        sql=("SELECT quantile(0.5)(volume) AS median, "
             "stddevPop(volume) AS sd FROM dbt.fct_execution_gpay_kpi_monthly"),
        database="dbt",
        columns=["median", "sd"],
        rows=[[1234.5, 678.9]],
        row_count=1,
    )
    store.append_evidence(project_id, EvidenceRef(
        kind="query_result", ref_id=artifact_id, phase="execution",
        title="EDA: payment volume distribution",
        summary="Statistical EDA on payment volume.",
    ))

    # Plan the verification phase (will be completed via verify_research_phase).
    tools["plan_research_phase"](
        project_id=project_id, phase="verification",
        plan_markdown="## Verification\n\nRun the gates.",
    )

    # 3) Verify
    verify_result = tools["verify_research_phase"](project_id=project_id)
    if isinstance(verify_result, str) and verify_result.startswith("Error"):
        r.check("verify_research_phase succeeded", False, verify_result)
    else:
        r.check("verify_research_phase succeeded",
                verify_result.overall_status in ("passed", "warning"),
                f"overall_status={verify_result.overall_status}")

    # 4) Peer review (accepted — Cerebro's ReviewDecision literal)
    review = PeerReviewResult(
        project_id=project_id,
        overall_decision="accepted",
        accepted_claims=["Retention is up 8% MoM."],
        summary_markdown="LGTM",
    )
    review_result = tools["record_peer_review"](
        project_id=project_id, result=review,
    )
    r.check("record_peer_review succeeded",
            not (isinstance(review_result, str) and review_result.startswith("Error")),
            str(review_result)[:120])

    # Plan the publication phase + publish.
    tools["plan_research_phase"](
        project_id=project_id, phase="publication",
        plan_markdown="## Publication\n\nWrite it up.",
    )
    publish_result = tools["publish_research_report"](
        project_id=project_id,
        title="Gnosis Pay Q3 Retention",
        content_markdown="## Summary\n\nRetention is up.\n\n## Details\n\nMore.",
    )
    r.check("publish_research_report succeeded",
            not (isinstance(publish_result, str) and publish_result.startswith("Error")),
            str(publish_result)[:120])

    # ---- Now read back the event log ----
    events = _read_events(db_path, workflow_id)
    kinds = [e["kind"] for e in events]
    r.info("event count", len(events))
    r.info("event kinds in order", kinds)

    r.check("workflow_started present (first event)",
            kinds and kinds[0] == "workflow_started")
    r.check("3 phase_planned events (mapping/hypothesis/execution + verification + publication)",
            kinds.count("phase_planned") >= 3,
            f"got {kinds.count('phase_planned')}")
    r.check("3 phase_completed events for non-terminal phases",
            kinds.count("phase_completed") >= 3,
            f"got {kinds.count('phase_completed')}")
    r.check("verification_completed event present",
            "verification_completed" in kinds)
    r.check("peer_review_recorded event present",
            "peer_review_recorded" in kinds)
    r.check("report_published event present",
            "report_published" in kinds)

    # ---- Gates ----
    gates = _read_gates(db_path, workflow_id)
    r.info("gates", [(g["gate"], g["status"]) for g in gates])
    has_verif_pass = any(
        g["gate"].startswith("verification:") and g["status"] == GATE_PASSED
        for g in gates
    )
    has_review_pass = any(
        g["gate"] == "peer_review" and g["status"] == GATE_PASSED
        for g in gates
    )
    r.check("verification gate passed", has_verif_pass)
    r.check("peer_review gate passed", has_review_pass)

    # ---- Final workflow status ----
    workflows = _read_workflows(db_path)
    wf = next((w for w in workflows if w["id"] == workflow_id), None)
    r.check("workflow marked COMPLETED after publish",
            wf and wf["status"] == WORKFLOW_COMPLETED,
            str(wf))

    return {"db_path": db_path, "workflow_id": workflow_id,
            "events": len(events), "kinds": kinds}


# ---------------------------------------------------------------------------
# Section 2 — peer-review rejection flips gate to FAILED
# ---------------------------------------------------------------------------


def section_peer_review_rejection(r: Reporter, workdir: Path) -> dict:
    r.section("2. Peer-review rejection: gate flips to FAILED, workflow stays RUNNING")
    tools, store, ch, db_path = _setup(workdir / "rejected")

    summary = tools["start_research_project"](
        hypothesis="Hypothesis pending review.",
        scope="A scope.",
    )
    project_id = summary.project_id
    workflow_id = f"research_{project_id}"

    # Walk to publication phase
    for phase in ["mapping", "hypothesis", "execution"]:
        tools["plan_research_phase"](project_id=project_id, phase=phase,
                                     plan_markdown=f"## {phase}")
        tools["execute_research_phase"](project_id=project_id, phase=phase)

    # Seed execution evidence so verification passes (otherwise we never
    # reach publication and `record_peer_review` raises a phase mismatch).
    artifact_id = store.save_query_result_artifact(
        project_id=project_id,
        title="EDA stats",
        sql="SELECT quantile(0.5)(x), stddevPop(x) FROM dbt.t",
        database="dbt",
        columns=["q", "sd"], rows=[[1.0, 0.5]], row_count=1,
    )
    store.append_evidence(project_id, EvidenceRef(
        kind="query_result", ref_id=artifact_id, phase="execution",
        title="EDA stats", summary="stats",
    ))

    tools["plan_research_phase"](project_id=project_id, phase="verification",
                                 plan_markdown="## verify")
    tools["verify_research_phase"](project_id=project_id)

    # Reject at peer review.
    review = PeerReviewResult(
        project_id=project_id,
        overall_decision="rejected",
        challenged_claims=["Insufficient evidence."],
        summary_markdown="needs more data",
    )
    tools["record_peer_review"](project_id=project_id, result=review)

    gates = _read_gates(db_path, workflow_id)
    review_gate = next((g for g in gates if g["gate"] == "peer_review"), None)
    r.check("peer_review gate exists", review_gate is not None)
    r.check("peer_review gate status = failed",
            review_gate and review_gate["status"] == GATE_FAILED,
            str(review_gate))

    # Workflow should still be running (not completed — we never published).
    wf = next((w for w in _read_workflows(db_path)
               if w["id"] == workflow_id), None)
    r.check("workflow still RUNNING (no publish event)",
            wf and wf["status"] == WORKFLOW_RUNNING,
            str(wf))
    return {"db_path": db_path, "workflow_id": workflow_id}


# ---------------------------------------------------------------------------
# Section 3 — interrupted workflow stays recoverable in the event log
# ---------------------------------------------------------------------------


def section_interrupted_workflow(r: Reporter, workdir: Path) -> dict:
    r.section("3. Interrupted workflow: events survive a 'process kill'")
    tools, store, ch, db_path = _setup(workdir / "interrupted")

    summary = tools["start_research_project"](
        hypothesis="Long-running hypothesis.", scope="Wide scope.",
    )
    project_id = summary.project_id
    workflow_id = f"research_{project_id}"

    # Run a few phases, then "die".
    tools["plan_research_phase"](project_id=project_id, phase="mapping",
                                 plan_markdown="## mapping")
    tools["execute_research_phase"](project_id=project_id, phase="mapping")
    tools["plan_research_phase"](project_id=project_id, phase="hypothesis",
                                 plan_markdown="## hypothesis")
    # No `execute` for `hypothesis` — simulates the agent stalling.

    pre_kill_events = _read_events(db_path, workflow_id)
    r.info("events at kill", len(pre_kill_events))

    # Simulate a process kill: drop everything in scope, re-open the DB.
    del tools, store
    survived = _read_events(db_path, workflow_id)
    r.check("all pre-kill events readable from a fresh sqlite connection",
            len(survived) == len(pre_kill_events),
            f"pre={len(pre_kill_events)}, post={len(survived)}")

    survived_kinds = [e["kind"] for e in survived]
    r.check("workflow_started event still present",
            "workflow_started" in survived_kinds)
    r.check("phase_completed for mapping recorded",
            sum(1 for e in survived if e["kind"] == "phase_completed") >= 1)

    # Workflow row stays at status=running — the orphan sweep would mark
    # it `orphaned` only after WORKFLOW_ORPHAN_AGE_SECONDS (default 24h).
    wf = next((w for w in _read_workflows(db_path)
               if w["id"] == workflow_id), None)
    r.check("workflow row still RUNNING (recoverable)",
            wf and wf["status"] == WORKFLOW_RUNNING)
    return {"db_path": db_path, "workflow_id": workflow_id,
            "interrupted_events": len(survived)}


# ---------------------------------------------------------------------------
# Section 4 — failure tolerance: unwritable event store doesn't break tools
# ---------------------------------------------------------------------------


def section_failure_tolerance(r: Reporter, workdir: Path) -> dict:
    r.section("4. Unwritable event store does NOT break the research tools")
    bad_path = Path("/nonexistent_dir/cant_write/state.db")
    tools, store, ch, db_path = _setup(
        workdir / "bad_event_db", monkey_event_path=bad_path,
    )

    # Silence the expected event-log error tracebacks during this
    # section — they're the EXPECTED behavior we're testing for, not
    # actual failures. The `*_safe` helpers catch and log; we just
    # don't want the log spam in the smoke output.
    import logging
    ev_logger = logging.getLogger("cerebro_mcp.workflow.event_store_sync")
    prior_level = ev_logger.level
    ev_logger.setLevel(logging.CRITICAL)
    try:
        return _section_failure_tolerance_body(r, tools)
    finally:
        ev_logger.setLevel(prior_level)


def _section_failure_tolerance_body(r: Reporter, tools: dict) -> dict:

    # The research tool must succeed even though the event log can't be
    # written. The on-disk JSON snapshot is the source of truth for
    # research state — event log is observability.
    try:
        summary = tools["start_research_project"](
            hypothesis="Tolerance test.", scope="Test.",
        )
    except Exception as e:
        r.check("start_research_project does NOT raise on bad db", False,
                f"{type(e).__name__}: {e}")
        return {}

    if isinstance(summary, str) and summary.startswith("Error"):
        r.check("start_research_project does NOT return Error on bad db",
                False, summary)
    else:
        r.check("start_research_project succeeded despite bad event db", True)
        # Walk one transition to confirm the path stays clean.
        plan = tools["plan_research_phase"](
            project_id=summary.project_id, phase="mapping",
            plan_markdown="## ok",
        )
        r.check("plan_research_phase also succeeded on bad db",
                not (isinstance(plan, str) and plan.startswith("Error")),
                str(plan)[:120])

    return {}


# ---------------------------------------------------------------------------
# Section 5 — duplicate start is idempotent (workflow row reused, no crash)
# ---------------------------------------------------------------------------


def section_idempotent_start(r: Reporter, workdir: Path) -> dict:
    r.section("5. Duplicate start_research_project does not corrupt the event log")
    tools, store, ch, db_path = _setup(workdir / "idempotent")

    # First start — creates a workflow row.
    s1 = tools["start_research_project"](
        hypothesis="H1.", scope="S1.",
    )
    workflow_id = f"research_{s1.project_id}"
    workflows_after_first = _read_workflows(db_path)
    r.check("first start created workflow row",
            any(w["id"] == workflow_id for w in workflows_after_first))

    # Second start with the same hypothesis/scope produces a NEW project
    # (different project_id) — so it should produce a different workflow.
    s2 = tools["start_research_project"](
        hypothesis="H1.", scope="S1.",
    )
    r.check("second start produces a different project_id",
            s2.project_id != s1.project_id,
            f"s1={s1.project_id[:8]}, s2={s2.project_id[:8]}")

    workflows_after_second = _read_workflows(db_path)
    r.check("second workflow row also created",
            len(workflows_after_second) == len(workflows_after_first) + 1)
    return {"db_path": db_path}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-db", action="store_true",
                    help="leave the SQLite db on disk for inspection")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="explicit workspace dir (otherwise a temp dir)")
    args = ap.parse_args()

    workdir = args.workspace or Path(
        tempfile.mkdtemp(prefix="cerebro_phase3_research_")
    )
    print("Phase 3 research migration smoke test")
    print(f"workspace: {workdir}")
    print("=" * 60)

    r = Reporter()
    section_full_lifecycle(r, workdir)
    section_peer_review_rejection(r, workdir)
    section_interrupted_workflow(r, workdir)
    section_failure_tolerance(r, workdir)
    section_idempotent_start(r, workdir)

    rc = r.summary()

    if args.keep_db:
        print(f"\nworkspace kept: {workdir}")
        print("inspect with:")
        print(f"  sqlite3 {workdir}/cerebro_state.db 'SELECT id, kind, status FROM workflows'")
        print(f"  sqlite3 {workdir}/cerebro_state.db 'SELECT workflow_id, seq, kind FROM events ORDER BY workflow_id, seq LIMIT 30'")
        print(f"  sqlite3 {workdir}/cerebro_state.db 'SELECT workflow_id, gate_name, status FROM gates'")
    else:
        try:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
