"""Phase 3: synchronous event-log API for sync MCP tools.

The async `EventStore` (aiosqlite) is correct for parallel-fan-out
workflow code that already runs on the asyncio event loop. The research
MCP tools (`tools/research.py`) are *synchronous* — they call into
`ResearchStore` (file-based) without ever entering an event loop. We
need a thin sync interface that writes to the **same SQLite file** and
**same schema** so the two coexist.

Design choices:

- Uses stdlib `sqlite3` (no aiosqlite import). The schema bootstrap is
  still handled by `EventStore.init()` at server startup, so we can
  assume the tables exist when this module is called.
- Every method opens a fresh connection, applies WAL/synchronous
  pragmas, runs the operation in a single transaction with
  `BEGIN IMMEDIATE` so concurrent writers serialize at the SQL level
  (no Python-side lock required across processes / threads).
- Failures NEVER raise. Event-log writes are observability — a SQLite
  hiccup must not break a research-workflow MCP tool. All exceptions
  are caught and logged.
"""

from __future__ import annotations

import gzip
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from cerebro_mcp.config import settings
from cerebro_mcp.workflow.payloads import (
    GATE_FAILED,
    GATE_PASSED,
    GATE_PENDING,
    GATE_READY,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
    WORKFLOW_WAITING_GATE,
    serialize_payload,
)

logger = logging.getLogger(__name__)


_VALID_WORKFLOW_STATUSES = frozenset(
    {WORKFLOW_RUNNING, WORKFLOW_WAITING_GATE, WORKFLOW_COMPLETED,
     WORKFLOW_FAILED, WORKFLOW_ORPHANED}
)
_VALID_GATE_STATUSES = frozenset(
    {GATE_PENDING, GATE_READY, GATE_PASSED, GATE_FAILED}
)


# Schema bootstrap — duplicated from `event_store._SCHEMA` so the sync path
# can self-initialize without importing aiosqlite. Kept literally identical
# to the async version; the async path runs the same DDL via `executescript`.
# `cerebro_state.db` is purely local observability state. If the schema
# changes, operators delete the file (`rm .cerebro/cerebro_state.db*`)
# and let it recreate on next boot — no in-place migrations.
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS workflows (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    owner         TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflows_owner_status
    ON workflows(owner, status);

CREATE TABLE IF NOT EXISTS events (
    workflow_id        TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    kind               TEXT NOT NULL,
    payload_json       BLOB NOT NULL,
    ts                 REAL NOT NULL,
    payload_compressed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (workflow_id, seq),
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE INDEX IF NOT EXISTS idx_events_workflow_seq
    ON events(workflow_id, seq);

CREATE TABLE IF NOT EXISTS gates (
    workflow_id  TEXT NOT NULL,
    gate_name    TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL,
    PRIMARY KEY (workflow_id, gate_name)
);
"""

# Cache the path we last bootstrapped against so we don't run CREATE TABLE
# IF NOT EXISTS on every single connection (cheap but not free — adds ~1ms).
# When the path changes (tests / db deletion), the cache miss re-runs DDL.
_bootstrapped_for_path: str | None = None


def _safe_path() -> Path:
    return Path(settings.EVENT_STORE_PATH)


def _connect() -> sqlite3.Connection:
    """Open a sync sqlite3 connection with WAL+NORMAL pragmas and the
    schema guaranteed-present.

    Self-bootstrapping is the difference vs the async `EventStore._connect`:
    the async path assumes `init()` has already run; the sync path can be
    reached at any time (e.g. when a sync MCP tool runs before the async
    server boot is complete, or after the operator deleted the db file
    mid-flight). Running `CREATE TABLE IF NOT EXISTS` on a one-time
    bootstrap-per-path cache keeps the sync path working regardless of
    init ordering at zero steady-state cost.
    """
    global _bootstrapped_for_path
    p = _safe_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    # If the file was deleted out from under us, force a re-bootstrap.
    path_str = str(p)
    if not p.exists():
        _bootstrapped_for_path = None

    conn = sqlite3.connect(path_str, isolation_level=None, timeout=30.0)
    # Match EventStore async pragmas: WAL journal + NORMAL synchronous.
    # journal_mode=WAL persists at the database level; synchronous is
    # connection-scoped so we set it on every open.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if _bootstrapped_for_path != path_str:
        # CREATE TABLE IF NOT EXISTS is idempotent — safe even if the
        # async init also ran.
        conn.executescript(_SCHEMA_DDL)
        _bootstrapped_for_path = path_str

    return conn


def _reset_bootstrap_cache() -> None:
    """Tests use this to force schema re-bootstrap when they swap
    `EVENT_STORE_PATH` between runs."""
    global _bootstrapped_for_path
    _bootstrapped_for_path = None


def create_workflow_safe(
    workflow_id: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
    owner: str | None = None,
) -> bool:
    """Insert a workflow row. Returns True on success, False if the row
    already exists OR the write failed (logged). Never raises — callers
    should treat event-log writes as fire-and-forget.

    `owner` defaults to whatever `identity.get_current_owner()` returns
    at call time when the caller doesn't explicitly pass one. Domain
    helpers in this module (`record_research_started` etc.) lean on
    this default; tests can pass an explicit `owner=` to bypass.
    """
    if owner is None:
        try:
            from cerebro_mcp.runtime.identity import get_current_owner
            owner = get_current_owner()
        except Exception:
            # Identity module is optional infrastructure — never let
            # an import failure here break the workflow write.
            owner = None
    now = time.time()
    meta_json = json.dumps(metadata or {}, default=str)
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO workflows (id, kind, status, created_at, "
                    "updated_at, metadata_json, owner) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (workflow_id, kind, WORKFLOW_RUNNING, now, now,
                     meta_json, owner),
                )
                conn.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                # Row already exists — that's fine on retry / restart.
                return False
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception:
        logger.exception(
            "event-log create_workflow failed (workflow_id=%s)", workflow_id
        )
        return False


def mark_workflow_status_safe(workflow_id: str, status: str) -> bool:
    """Update workflow status. Returns True on success, False on failure."""
    if status not in _VALID_WORKFLOW_STATUSES:
        logger.warning("event-log: rejected invalid workflow status %r", status)
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), workflow_id),
            )
            return True
    except Exception:
        logger.exception(
            "event-log mark_workflow_status failed (workflow_id=%s)",
            workflow_id,
        )
        return False


def append_event_safe(
    workflow_id: str,
    kind: str,
    payload: Any,
) -> int | None:
    """Append an event and bump the workflow's updated_at. Returns the
    new seq, or None on failure. Compresses payload with gzip if its
    serialized size exceeds the configured threshold.

    Wrapped in `BEGIN IMMEDIATE` so the SELECT-MAX + INSERT pair is
    atomic across writers. SQLite's writer-serialization handles
    concurrent appends safely.
    """
    body = serialize_payload(payload).encode("utf-8")
    threshold = getattr(
        settings, "EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES", 4096
    )
    compressed = 0
    if len(body) > threshold:
        body = gzip.compress(body, compresslevel=6)
        compressed = 1
    ts = time.time()
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events "
                    "WHERE workflow_id = ?",
                    (workflow_id,),
                )
                row = cur.fetchone()
                seq = (row[0] if row and row[0] is not None else 0) + 1
                conn.execute(
                    "INSERT INTO events (workflow_id, seq, kind, "
                    "payload_json, ts, payload_compressed) "
                    "VALUES (?,?,?,?,?,?)",
                    (workflow_id, seq, kind, body, ts, compressed),
                )
                conn.execute(
                    "UPDATE workflows SET updated_at = ? WHERE id = ?",
                    (ts, workflow_id),
                )
                conn.execute("COMMIT")
                return seq
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception:
        logger.exception(
            "event-log append_event failed (workflow_id=%s, kind=%s)",
            workflow_id, kind,
        )
        return None


def set_gate_safe(
    workflow_id: str,
    gate_name: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Upsert a gate. Returns True on success, False on failure or
    invalid status."""
    if status not in _VALID_GATE_STATUSES:
        logger.warning("event-log: rejected invalid gate status %r", status)
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO gates (workflow_id, gate_name, status, "
                "payload_json, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(workflow_id, gate_name) DO UPDATE SET "
                "status = excluded.status, "
                "payload_json = excluded.payload_json, "
                "updated_at = excluded.updated_at",
                (workflow_id, gate_name, status,
                 json.dumps(payload or {}, default=str), time.time()),
            )
            return True
    except Exception:
        logger.exception(
            "event-log set_gate failed (workflow_id=%s, gate=%s)",
            workflow_id, gate_name,
        )
        return False


# ---------------------------------------------------------------------------
# Research-workflow specific helpers — these are the integration points the
# research MCP tools call. They wrap the generic safe writers with the
# domain-specific event kinds and payload shapes so call-sites stay terse.
# ---------------------------------------------------------------------------


def workflow_id_for_research(project_id: str) -> str:
    """Stable mapping from `ResearchProjectState.project_id` to event-log
    `workflow_id`. Keeps the namespace unambiguous (a project_id and a
    workflow_id should never collide if both happen to use UUIDs)."""
    return f"research_{project_id}"


def record_research_started(
    project_id: str, hypothesis: str, scope: str,
) -> None:
    wid = workflow_id_for_research(project_id)
    create_workflow_safe(wid, "research_project",
                         {"project_id": project_id,
                          "hypothesis": hypothesis,
                          "scope": scope})
    append_event_safe(wid, "workflow_started",
                      {"project_id": project_id,
                       "hypothesis": hypothesis,
                       "scope": scope})


def record_research_phase_planned(
    project_id: str, phase: str, plan_markdown: str,
) -> None:
    append_event_safe(
        workflow_id_for_research(project_id),
        "phase_planned",
        {"phase": phase,
         # Cap plan size in the event payload to keep events lean — the
         # full text is already in research_store.json.
         "plan_preview": (plan_markdown or "")[:500]},
    )


def record_research_phase_completed(
    project_id: str, phase: str, advanced_to: str | None,
) -> None:
    append_event_safe(
        workflow_id_for_research(project_id),
        "phase_completed",
        {"phase": phase, "advanced_to": advanced_to},
    )


def record_research_verification(
    project_id: str, phase: str, passed: bool, summary: str,
) -> None:
    """Record verification + the matching gate flip."""
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "verification_completed",
                      {"phase": phase, "passed": passed,
                       "summary_preview": (summary or "")[:500]})
    set_gate_safe(
        wid,
        f"verification:{phase}",
        GATE_PASSED if passed else GATE_FAILED,
        {"summary_preview": (summary or "")[:500]},
    )


def record_research_peer_review(
    project_id: str, status: str, summary: str = "",
) -> None:
    """Reviewer landed a verdict (`approved` / `changes_requested` / etc).

    Maps reviewer outcome to the canonical gate status. `approved` →
    `passed`, anything else → `failed`. The verbatim verdict is in the
    event payload for replay.
    """
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "peer_review_recorded",
                      {"status": status,
                       "summary_preview": (summary or "")[:500]})
    gate_status = GATE_PASSED if status == "approved" else GATE_FAILED
    set_gate_safe(wid, "peer_review", gate_status, {"verdict": status})


def record_research_published(
    project_id: str, report_id: str, title: str,
) -> None:
    wid = workflow_id_for_research(project_id)
    append_event_safe(wid, "report_published",
                      {"report_id": report_id, "title": title})
    mark_workflow_status_safe(wid, WORKFLOW_COMPLETED)


# ---------------------------------------------------------------------------
# Research-workflow work events (Step 1 expansion — beyond phase transitions)
#
# Phase 3 originally instrumented only PHASE-LEVEL transitions (start, plan,
# execute, verify, peer_review, publish). The actual analytical work — every
# `execute_query`, every `record_research_memory`, every finding, every
# evidence attachment — bypassed the event log. Result: when a Claude session
# crashed mid-research, the event log only carried `workflow_started` and
# resume hints couldn't surface the agent's actual progress (queries tried,
# observations recorded).
#
# These helpers close that gap. Each one captures a small, lossy summary of
# the work item — never the full payload, so a 100-step research session
# stays cheap to log + replay.
# ---------------------------------------------------------------------------


def record_research_query_executed(
    project_id: str,
    sql: str,
    database: str,
    row_count: int,
    elapsed_seconds: float,
    evidence_title: str = "",
    artifact_ref_id: str | None = None,
    error_class: str | None = None,
) -> None:
    """Append a `query_executed` event for an analytical query the agent
    ran inside an active research project.

    `sql` is truncated to 1.5 KB so the event log stays compact even when
    the agent runs hundreds of long queries. The full SQL is already in
    the on-disk session log JSON if a forensic replay is ever needed.
    `evidence_title` is the agent's short narrative for *why* it ran
    this query — gold for resume.

    `error_class` is set on failed queries (e.g. "ILLEGAL_AGGREGATION",
    "MEMORY_LIMIT_EXCEEDED") so resume can surface "3 queries failed
    with column hallucinations — don't retry them blindly".
    """
    append_event_safe(
        workflow_id_for_research(project_id),
        "query_executed",
        {
            "sql_preview": (sql or "")[:1500],
            "sql_full_len": len(sql or ""),
            "database": database,
            "row_count": row_count,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "evidence_title": evidence_title or None,
            "artifact_ref_id": artifact_ref_id,
            "error_class": error_class,
        },
    )


def record_research_memory_recorded(
    project_id: str, memory_id: str, kind: str,
    statement: str, confidence: float,
) -> None:
    """Append a `memory_recorded` event when an observation lands in
    `research_store.memory.json`. Statement is truncated to 800 chars
    so the event log can carry the gist without bloat — the full text
    is already on disk."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "memory_recorded",
        {
            "memory_id": memory_id,
            "kind": kind,
            "statement_preview": (statement or "")[:800],
            "statement_full_len": len(statement or ""),
            "confidence": confidence,
        },
    )


def record_research_finding_recorded(
    project_id: str, finding_id: str, title: str,
    confidence: float, evidence_count: int,
) -> None:
    """Append a `finding_recorded` event when a conclusion lands in
    `research_store.findings.json`. We don't copy the full conclusion
    text — the resume hint only needs the title + confidence to give
    the agent context."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "finding_recorded",
        {
            "finding_id": finding_id,
            "title": (title or "")[:300],
            "confidence": confidence,
            "evidence_count": evidence_count,
        },
    )


def record_research_evidence_attached(
    project_id: str, kind: str, ref_id: str, phase: str,
    title: str = "",
) -> None:
    """Append an `evidence_attached` event when a query/chart/report/
    schema artifact is wired to a research project. Phase is captured so
    the resume handler can tell, e.g., that 3 execution-phase queries
    are attached but verification is still missing evidence."""
    append_event_safe(
        workflow_id_for_research(project_id),
        "evidence_attached",
        {
            "kind": kind,
            "ref_id": ref_id,
            "phase": phase,
            "title": (title or "")[:300],
        },
    )


# ---------------------------------------------------------------------------
# Quarterly review domain helpers (Sprint 2)
#
# QBRs share `ResearchStore` + research's `PHASE_ORDER`, but they auto-advance
# phases (no explicit plan/execute calls) and can hold analyses from multiple
# quarters in one project. We treat them as a distinct workflow `kind` so the
# resume handler can reason about them differently from research projects
# (no peer-review gate, no `failed` action — just `ready_to_resume` until
# `report_published`).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Storyteller session domain helpers (Sprint 3 — observability layer)
#
# Storyteller's state machine stays in-memory; we only record events for
# observability + resume-hint generation. The 10-phase machine has explicit
# gates and rollback semantics (clarity check failure rolls back to the
# earliest failing phase). Both `phase_advanced` and `gate_failed` are
# represented in the event log.
# ---------------------------------------------------------------------------


def workflow_id_for_storyteller(session_id: str) -> str:
    return f"storyteller_{session_id}"


def record_storyteller_session_started(session_id: str) -> None:
    wid = workflow_id_for_storyteller(session_id)
    create_workflow_safe(
        wid, "storyteller_session", {"session_id": session_id},
    )
    append_event_safe(
        wid, "workflow_started", {"session_id": session_id},
    )


def record_storyteller_phase_advanced(
    session_id: str, from_phase: str, to_phase: str,
) -> None:
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "phase_advanced",
        {"from": from_phase, "to": to_phase},
    )


def record_storyteller_gate_failed(
    session_id: str, gate: str, blocking_phase: str, reason: str = "",
) -> None:
    """A clarity / accessibility check failed; storyteller rolled back
    to `blocking_phase`. Resume handler treats this differently from a
    plain phase_advanced — multiple unresolved gate_failed events
    promote a workflow to `action: failed`."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "gate_failed",
        {"gate": gate, "blocking_phase": blocking_phase, "reason": reason},
    )


def record_storyteller_handoff_completed(
    session_id: str, report_id: str, style: str,
) -> None:
    wid = workflow_id_for_storyteller(session_id)
    append_event_safe(wid, "handoff_completed",
                      {"report_id": report_id, "style": style})
    mark_workflow_status_safe(wid, WORKFLOW_COMPLETED)


# ---------------------------------------------------------------------------
# Storyteller content events (Step 1 expansion)
#
# Phase events tell us WHICH phase the agent reached. Content events tell us
# WHAT was actually recorded so a fresh Claude session sees the story shape:
# audience, big idea sentence, scene count, visual specs, final-story title.
# Each event captures only the gist (a short preview / structured summary)
# — full payloads stay in `storyteller_state`, this is for resume hints.
# ---------------------------------------------------------------------------


def record_storyteller_context_brief_recorded(
    session_id: str,
    audience: str,
    mechanism: str,
    required_action: str,
) -> None:
    """Capture audience / mechanism / required_action when the brief is
    first stored. Resume hint surfaces the audience so the agent picks
    up the right tone."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "context_brief_recorded",
        {
            "audience": (audience or "")[:200],
            "mechanism": mechanism,
            "required_action": (required_action or "")[:300],
        },
    )


def record_storyteller_big_idea_recorded(
    session_id: str, sentence: str, stakes: str,
) -> None:
    """Capture the big-idea sentence verbatim — short enough that we
    don't truncate."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "big_idea_recorded",
        {
            "sentence": (sentence or "")[:500],
            "stakes": (stakes or "")[:300],
        },
    )


def record_storyteller_storyboard_recorded(
    session_id: str, scene_count: int, narrative_order: str,
    rationale: str = "",
) -> None:
    """Capture scene count + narrative order. Lets resume tell the agent
    'storyboard has 5 scenes; you've drafted visual specs for 3'."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "storyboard_recorded",
        {
            "scene_count": scene_count,
            "narrative_order": narrative_order,
            "rationale_preview": (rationale or "")[:300],
        },
    )


def record_storyteller_visual_spec_recorded(
    session_id: str, scene_index: int, chart_family: str,
    relationship: str, action_title: str,
) -> None:
    """One event per scene as visual specs are filled in. Lets resume
    say 'visual specs recorded for scenes [1, 2, 3] of 5; pick up at
    scene 4'."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "visual_spec_recorded",
        {
            "scene_index": scene_index,
            "chart_family": chart_family,
            "relationship": relationship,
            "action_title": (action_title or "")[:300],
        },
    )


def record_storyteller_final_story_recorded(
    session_id: str, title: str, content_length: int,
) -> None:
    """Capture title + length of the final story when the agent commits
    it. Length is a quick sanity-check signal for resume."""
    append_event_safe(
        workflow_id_for_storyteller(session_id),
        "final_story_recorded",
        {
            "title": (title or "")[:300],
            "content_length": content_length,
        },
    )


