# Memory and Resume — Cerebro MCP

How Cerebro remembers things across crashes, restarts, and conversations.

This document is the canonical reference for everything that gets persisted by
the MCP server: where it lives, how it gets written, how it gets read back,
and how the resume machinery surfaces it to the agent on the next interaction.

> **TL;DR.** Cerebro has **four** persistence layers: a SQLite event log
> (`.cerebro/cerebro_state.db`), durable JSON state for research/QBR projects
> (`.cerebro/research_projects/...`), DuckDB sandboxes for what-if simulations
> (`.cerebro/sandboxes/...`), and per-process in-memory state for storyteller
> and session counters. Phase 3 added the event log and the WorkflowRegistry
> resume handlers; before that, only the JSON state existed.

---

## Table of contents

1. [Why this exists](#why-this-exists)
2. [The four memory layers](#the-four-memory-layers)
3. [The SQLite event log](#the-sqlite-event-log)
4. [Event kinds and payload schemas](#event-kinds-and-payload-schemas)
5. [The WorkflowRegistry and resume handlers](#the-workflowregistry-and-resume-handlers)
6. [How writes flow through tools](#how-writes-flow-through-tools)
7. [How resume is computed](#how-resume-is-computed)
8. [Multi-tenant identity (owner column)](#multi-tenant-identity-owner-column)
9. [Lifecycle and cleanup](#lifecycle-and-cleanup)
10. [Failure modes the design protects against](#failure-modes-the-design-protects-against)
11. [Failure modes still outside cerebro's control](#failure-modes-still-outside-cerebros-control)

---

## Why this exists

Before Phase 3, a Cerebro session that crashed mid-flight lost everything that
wasn't already in the file-based research store: every `execute_query`, every
intermediate observation, every chart the agent had committed to but not yet
persisted. The 14-minute Gnosis Pay research session that died in
`session_20260426_200535` was the canonical example — 17 steps in, the LLM
client gave up, and the next conversation started from scratch.

Phase 3 introduces a separate **event log** that records every meaningful
state transition the agent makes. It's append-only, crash-safe, and small.
On the next interaction, the agent can call `list_resumable_workflows` and
`recompute_workflow_resume_hint` to ask the registry "what was I doing, and
where do I pick up?". The registry walks the event log and returns a
structured `ResumeOutcome` with a concrete next call to make.

The event log doesn't replace the existing storage; it sits alongside it. The
JSON research store is still the source of truth for the project's actual
content (hypothesis, evidence, findings, markdown plans). The event log is
the chronological record of what happened, optimised for cheap reads and
narrow updates.

---

## The four memory layers

```
┌───────────────────────────────────────────────────────────────────────┐
│ Cerebro MCP — Persistence layers                                      │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. Workflow event log    .cerebro/cerebro_state.db   (SQLite, WAL)   │
│     ─ workflows + events + gates tables                               │
│     ─ Append-only, monotonic seq, crash-safe                          │
│     ─ Owner column for multi-tenant filtering                         │
│                                                                       │
│  2. Research / QBR project store    .cerebro/research_projects/<id>/  │
│     ─ project.json, evidence.json, memory.json,                       │
│       findings.json, peer_review.json                                 │
│     ─ Durable, atomic JSON writes                                     │
│     ─ Source of truth for hypothesis / scope / phase plans / content  │
│                                                                       │
│  3. Sandbox snapshots    .cerebro/sandboxes/<id>/snapshot.parquet     │
│     ─ DuckDB :memory: instances mounted from parquet                  │
│     ─ Used for "what-if" simulations (mmm_simulator etc.)             │
│     ─ TTL-evicted; not durable across server restart                  │
│                                                                       │
│  4. In-memory singletons (process lifetime only)                      │
│     ─ storyteller_state.StorytellerState                              │
│     ─ tools/session_state.SessionState (discovery counters etc.)      │
│     ─ runtime_state (ssl_trust, current_agent_role)                   │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

| Layer | Format | Survives kill -9? | Survives box restart? | Used for |
|---|---|---|---|---|
| Event log | SQLite WAL | ✅ yes | ✅ yes | Crash recovery, resume hints, audit trail |
| Research store | JSON files | ✅ yes (atomic writes) | ✅ yes | Authoritative project state |
| Sandbox snapshots | parquet + DuckDB | ✅ parquet survives | ❌ DuckDB connection dies | Counterfactual simulations |
| In-memory state | Python dicts | ❌ no | ❌ no | Live storyteller phase, session counters |

---

## The SQLite event log

**File:** `.cerebro/cerebro_state.db` (path configurable via `EVENT_STORE_PATH`).

**Pragmas:** `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`. The
synchronous-NORMAL choice is critical for performance — `synchronous=FULL`
would halve the append rate and is unnecessary in a workflow event log
context.

### Schema

```sql
CREATE TABLE workflows (
    id            TEXT PRIMARY KEY,             -- e.g. "research_rp_009c4ade8d6e"
    kind          TEXT NOT NULL,                -- "research_project" | "quarterly_review" | "storyteller_session"
    status        TEXT NOT NULL,                -- "running" | "waiting_gate" | "completed" | "failed" | "orphaned"
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,                -- bumped by every event append
    metadata_json TEXT NOT NULL DEFAULT '{}',
    owner         TEXT                          -- SHA-256 hash of caller identity, NULL for legacy
);

CREATE INDEX idx_workflows_owner_status ON workflows(owner, status);

CREATE TABLE events (
    workflow_id        TEXT NOT NULL,
    seq                INTEGER NOT NULL,        -- monotonic per-workflow
    kind               TEXT NOT NULL,           -- e.g. "phase_planned", "query_executed"
    payload_json       BLOB NOT NULL,
    ts                 REAL NOT NULL,
    payload_compressed INTEGER NOT NULL DEFAULT 0,    -- gzip flag
    PRIMARY KEY (workflow_id, seq),
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE INDEX idx_events_workflow_seq ON events(workflow_id, seq);

CREATE TABLE gates (
    workflow_id  TEXT NOT NULL,
    gate_name    TEXT NOT NULL,                 -- e.g. "verification:verification", "peer_review"
    status       TEXT NOT NULL,                 -- "pending" | "ready" | "passed" | "failed"
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL,
    PRIMARY KEY (workflow_id, gate_name)
);
```

### Why these design choices

- **Per-workflow seq, not global seq** — lets concurrent workflows write
  without contention beyond SQLite's writer lock; replay only needs to
  reconstruct one workflow at a time.
- **`payload_json` as BLOB with gzip flag** — payloads >4 KB (LLM message
  histories in particular) are gzipped before insert. Saves disk and keeps
  read performance flat.
- **`updated_at` bumped on every append** — gives the registry a cheap way
  to filter "stale" workflows for the orphan sweep without scanning events.
  Subtlety: this means writing a `workflow_resume_hint` event itself
  refreshes `updated_at`, which is why `list_resumable_workflows` defaults
  to `min_idle_seconds=0` (otherwise the boot sweep's hints would hide
  the workflows they were written for).
- **Two parallel APIs**: async `EventStore` (aiosqlite) and sync
  `event_store_sync` (stdlib sqlite3). Same file, same schema. The sync API
  is for sync MCP tools; the async API is for the parallel-fan-out runner
  and the resume handlers.

### Self-bootstrapping

The sync path (`event_store_sync._connect`) creates the schema on first
connection if absent. So you can delete `cerebro_state.db` at any time and
the next tool call will recreate it cleanly.

---

## Event kinds and payload schemas

Three workflow kinds, each with their own event vocabulary. All events
carry `kind`, `seq`, `ts`, and a workflow-specific `payload`.

### Universal events

| Kind | When | Payload |
|---|---|---|
| `workflow_started` | Workflow row first created | `{project_id?, hypothesis?, scope?}` (varies by kind) |
| `workflow_resume_hint` | Registry computes a resume outcome | `{kind, action, summary, resume_hint, unfinished_llm_call_count}` |
| `llm_call_started` / `llm_call_completed` / `llm_call_failed` | An agent runner brackets each LLM call | `LLMCallEvent` (subtask_name, call_id, system_prompt, full message history, tool_schemas, response, elapsed_seconds) |

### `research_project` events

| Kind | When | Payload |
|---|---|---|
| `phase_planned` | `plan_research_phase(phase, plan_markdown)` | `{phase, plan_preview}` |
| `phase_completed` | `execute_research_phase` advances | `{phase, advanced_to}` |
| `verification_completed` | `verify_research_phase` runs | `{phase, passed, summary_preview}` |
| `peer_review_recorded` | `record_peer_review` lands | `{status, summary_preview}` |
| `report_published` | `publish_research_report` succeeds | `{report_id, title}` |
| **`query_executed`** | `execute_query(... research_project_id=)` | `{sql_preview, sql_full_len, database, row_count, elapsed_seconds, evidence_title, artifact_ref_id, error_class}` |
| **`memory_recorded`** | `record_research_memory` | `{memory_id, kind, statement_preview, statement_full_len, confidence}` |
| **`finding_recorded`** | `record_research_finding` | `{finding_id, title, confidence, evidence_count}` |
| **`evidence_attached`** | `attach_research_evidence` / `capture_schema_snapshot` | `{kind, ref_id, phase, title}` |

The bottom four (bold) are the **Step 1 expansion** (2026-04-30). Before
they shipped, only phase-level transitions were captured — so if an agent
called `record_research_memory` 50 times mid-phase, the event log only
showed the phase entry. The Step 1 expansion fixed that.

### `quarterly_review` events

| Kind | When | Payload |
|---|---|---|
| `workflow_started` | `open_quarterly_review` | `{project_id, quarter, hypothesis, scope}` |
| `evidence_attached` | `save_quarterly_analysis` | `{kind, ref_id, quarter}` |
| `report_published` | `publish_quarterly_review` | `{report_id, title}` |
| **`note_recorded`** | `record_quarterly_note` | `{kind ("observation"\|"priority"\|"action"), statement_preview, statement_full_len}` |

### `storyteller_session` events

| Kind | When | Payload |
|---|---|---|
| `workflow_started` | `storyteller_start_session` | `{session_id}` |
| `phase_advanced` | Any state-machine forward move | `{from, to}` |
| `gate_failed` | Clarity / accessibility check rolls state back | `{gate, blocking_phase, reason}` |
| `handoff_completed` | `storyteller_generate_story_report` succeeds | `{report_id, style}` |
| **`context_brief_recorded`** | `storyteller_record_context_brief` | `{audience, mechanism, required_action}` |
| **`big_idea_recorded`** | `storyteller_record_big_idea` | `{sentence, stakes}` (verbatim ≤500 chars) |
| **`storyboard_recorded`** | `storyteller_record_storyboard` | `{scene_count, narrative_order, rationale_preview}` |
| **`visual_spec_recorded`** | `storyteller_record_visual_spec` (one per scene) | `{scene_index, chart_family, relationship, action_title}` |
| **`final_story_recorded`** | `storyteller_record_final_story` | `{title, content_length}` |

### Payload size budgets

Every long-form text in a payload is truncated to a cap so the event log
stays compact. The truncation cap is captured alongside (e.g.
`statement_preview` paired with `statement_full_len`) so the resume hint
can tell the agent "this preview is 800 of 4,200 chars; pull the full
version from the JSON store if you need it."

| Field | Cap |
|---|---|
| `sql_preview` | 1500 chars |
| `statement_preview` (memory) | 800 chars |
| `statement_preview` (QBR note) | 600 chars |
| `plan_preview` | 500 chars |
| `summary_preview` (verification, peer review) | 500 chars |
| `title` (finding, evidence) | 300 chars |
| `audience` (storyteller) | 200 chars |
| `sentence` (big_idea — verbatim, no truncation marker) | 500 chars |

---

## The WorkflowRegistry and resume handlers

`workflow_registry.py` defines a registry that maps each workflow `kind` to a
**resume handler function**. Handlers are pure: `(workflow_id, workflow_row,
events) → ResumeOutcome`.

### `ResumeOutcome` shape

```python
@dataclass
class ResumeOutcome:
    workflow_id: str
    kind: str
    action: str               # "ready_to_resume" | "complete" | "failed" | "orphan" | "no_handler"
    summary: str              # one-line human-readable
    resume_hint: dict         # kind-specific structured payload — see below
    unfinished_llm_calls: list[LLMCallEvent]  # surfaced from llm_call_started without a matching completed
```

### Action vocabulary

| Action | Meaning | Status side-effect |
|---|---|---|
| `ready_to_resume` | Workflow has more work; hint describes next step | none |
| `complete` | Terminal success detected (e.g. `report_published`) | row → `completed` |
| `failed` | Terminal failure (e.g. `peer_review_recorded` with status=rejected) | row → `failed` |
| `orphan` | Stale beyond TTL OR handler can't make sense of state | row → `orphaned` |
| `no_handler` | `kind` has no registered handler | row → `orphaned` |

### Registered handlers

Three are registered today; new workflow kinds are added incrementally by
calling `default_workflow_registry().register(kind, handler)` in
`bootstrap.init_event_store_async`.

| Kind | Handler module | What it surfaces in the hint |
|---|---|---|
| `research_project` | `research_resume.py` | `current_phase`, `completed_phases`, `next_action`, `next_action_args`, gates, `work` block (queries / memories / findings / evidence) |
| `quarterly_review` | `quarterly_review_resume.py` | `quarter`, `evidence_count`, `next_action`, `notes_by_kind`, `recent_notes` |
| `storyteller_session` | `storyteller_resume.py` | `current_phase`, `next_action`, `content` block (audience / big_idea_sentence / scene_count / visual_specs_recorded) |

---

## How writes flow through tools

Two paths, depending on whether the tool is sync or async.

### Sync tool path (most research / QBR / storyteller tools)

```
agent calls @mcp.tool() def some_tool(...)
  ↓
tool body runs (validates, mutates research_store / sandbox / etc.)
  ↓
tool calls a `record_*` helper from event_store_sync.py
  ↓
helper opens fresh sqlite3 connection, applies WAL+NORMAL pragmas,
  begins IMMEDIATE transaction, computes seq via SELECT MAX(seq)+1,
  inserts event, commits
  ↓
tool returns success to agent
```

Event-log writes are wrapped in try/except in every `*_safe` helper. If the
event log fails (file unwritable, schema corruption, anything), the helper
logs an error and returns `False` / `None` — **the underlying tool always
succeeds**. Event-log writes are observability, never correctness.

### Async tool path (workflow_resume tools, parallel runner)

```
agent calls @mcp.tool() async def list_resumable_workflows(...)
  ↓
tool awaits EventStore methods directly (aiosqlite)
  ↓
EventStore opens connection-per-call, applies pragmas, runs query
  ↓
returns markdown summary
```

The async path is used wherever we're already in an asyncio context
(FastMCP serves tools in an event loop). It avoids `asyncio.run()` inside
running loops, which was a bug we hit in the registry MCP tools
(2026-04-27 regression).

### Per-workflow append serialization

Multiple concurrent appends against the same workflow can race on
`SELECT MAX(seq) + 1`. The async `EventStore` uses an `asyncio.Lock` per
workflow_id to serialize. The sync `event_store_sync.append_event_safe`
uses `BEGIN IMMEDIATE` transactions which take a write lock, so SQLite
itself serializes.

---

## How resume is computed

Two trigger points:

### Trigger 1: bootstrap-time sweep

On every server start, `bootstrap.init_event_store_async()`:

1. Initializes the event store schema (idempotent).
2. Registers all known resume handlers.
3. Calls `registry.resume_all_running(max_age_seconds=24h)` — finds every
   workflow in `running` / `waiting_gate` last touched more than 24 h ago.
4. For each, dispatches to the registered handler, gets a `ResumeOutcome`,
   appends a `workflow_resume_hint` event, flips the workflow status if
   the outcome is `complete` / `failed` / `orphan`.

Why 24 h: too short and we'd false-positive on workflows the user is
actively working on; too long and stale workflows clutter the agent's
view. 24 h is the threshold that captures the "Claude conversation died
overnight" case without disturbing in-flight work.

### Trigger 2: agent-on-demand

Three MCP tools:

- `list_resumable_workflows(min_idle_seconds=0)` — returns all running /
  waiting_gate workflows with their most recent `workflow_resume_hint`
  event (if any).
- `get_workflow_resume_hint(workflow_id)` — fetches the latest hint event
  for a specific workflow. Doesn't recompute.
- `recompute_workflow_resume_hint(workflow_id)` — re-runs the registered
  handler, appends a fresh hint, flips status if terminal. Use this when
  the workflow has progressed since the bootstrap-time scan.

The registered handler is the same code the boot sweep uses. There's no
"two paths" here — `recompute` and the sweep call exactly the same
function with the same inputs.

### Inside a handler

Each handler walks the event stream once, in order, and folds it into a
structured state:

```python
async def resume_research_project(workflow_id, workflow_row, events):
    project_id = _project_id_from_workflow(workflow_row)
    kinds = [ev["kind"] for ev in events]

    # Terminal: published?
    if "report_published" in kinds:
        return ResumeOutcome(action=ACTION_COMPLETE, ...)

    verification_gate, peer_review_gate = _scan_gates(events)
    if peer_review_gate == "failed":
        return ResumeOutcome(action=ACTION_FAILED, ...)

    completed, current_phase = _scan_phases(events)
    next_action, next_args = _next_action_for_phase(current_phase, completed, project_id)
    work = _scan_work(events)        # query/memory/finding/evidence summary
    unfinished = find_unfinished_llm_calls(events)

    return ResumeOutcome(
        action=ACTION_READY_TO_RESUME,
        summary=f"Project {project_id}: ready to resume at phase {current_phase!r}. ...",
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
```

The handler is a pure function over events — no I/O, no LLM calls, no
ClickHouse. That's what makes it safe to run in the bootstrap path before
the server has even opened its transport.

---

## Multi-tenant identity (owner column)

### What it is

Every workflow row has an optional `owner TEXT` column populated with a
SHA-256 hash of the caller's identifier. Plaintext identifiers never
persist — `identity.set_current_owner("alice@gnosis.io")` hashes at the
boundary, the contextvar holds only the hex digest.

### Sources of identity

| Transport | Source | When set |
|---|---|---|
| stdio | `CEREBRO_OWNER` env var | Once at server boot, in `server.py:main()` |
| SSE | `X-Cerebro-Owner` HTTP header | Per request, in `BearerAuthMiddleware.__call__` (try/finally with `Token.reset()`) |

If neither is set, the contextvar stays `None`, all workflows write
`owner=NULL`, and the read filter treats NULL as legacy / visible to
everyone (single-tenant fallback).

### Optional salt

Set `CEREBRO_OWNER_HASH_SALT` to make hashes deployment-specific — useful
if you ever share or back up `cerebro_state.db` and don't want hashes to
be cross-referenceable against a known list of emails.

### How filters work

`EventStore.list_workflows(owner=, include_unowned=True)` builds:

```sql
WHERE (owner = ? OR (include_unowned AND owner IS NULL))
```

So a caller with hash `H` sees their own workflows plus any `NULL`-owned
ones. To enforce strict isolation (no NULL fall-through), pass
`include_unowned=False`.

`get_workflow(workflow_id, requesting_owner=H)` returns `None` for rows
owned by anyone other than `H` (or NULL). Treats "not yours" the same as
"not found" so callers don't have to distinguish.

### Trust model

The identity is **self-attested unless an upstream auth proxy verifies
it**. Cerebro doesn't validate the header — that's the proxy's job. In a
single-token shared SSE deployment, anyone with the token can claim any
owner, so this layer provides separation but not security against malice.
For a real authz model, add JWT claims verification at the middleware
layer (out of scope today).

---

## Lifecycle and cleanup

### Event log

- **Bounded by workflow count, not session count.** Each workflow gets ~5–
  100 events typically.
- **No automatic retention/cleanup yet.** The DB grows indefinitely. At ~50
  events × gzip × 100 workflows / day, expect ~MBs/year. Good for now;
  schedule a vacuum + age-based archival when it becomes a real cost.
- **Manual cleanup**: `rm .cerebro/cerebro_state.db*` — schema recreates
  on next boot, all workflow history lost.

### Sandbox snapshots

- **TTL eviction** at `SANDBOX_TTL_SECONDS` (default 30 min idle).
- **LRU eviction** when active sandbox count exceeds
  `SANDBOX_MAX_CONCURRENT` (default 4).
- **atexit teardown** on graceful shutdown — closes DuckDB, unlinks
  parquet, removes workspace dir.
- **Crash recovery** for sandboxes: parquet files survive, but the
  in-memory DuckDB state is lost. Re-create the sandbox on the next call.

### Research / QBR JSON store

- **Atomic writes** via `_write_json_atomic` — writes to a temp file then
  renames. No partial-state corruption on `kill -9`.
- **No automatic cleanup**. Project directories stay forever unless
  manually deleted. A completed project is identical to an in-flight one
  on disk — you have to consult the workflow status in `cerebro_state.db`
  to know.

### Storyteller in-memory state

- **Process-lifetime only.** Restart wipes it.
- **One active session per process** (the singleton holds at most one).
  Calling `storyteller_start_session` again clears any prior session.
- **Layer A migration (current)**: events captured to event log for
  observability. State machine itself stays in-memory.
- **Layer B (deferred)**: would make the state durable by reading current
  phase from the event log on bootstrap. Not yet shipped.

---

## Failure modes the design protects against

| Failure | Outcome | Where Phase 3 helps |
|---|---|---|
| Server `kill -9` mid-call | Tool call atomically committed or not | SQLite WAL + atomic JSON writes |
| Server `kill -9` mid-research | Workflow row + completed phase events survive | Boot sweep marks orphans, recompute_hint shows where we stopped |
| Anthropic 529 / rate limit | LLM call interrupted; tool already returned | `unfinished_llm_calls` surfaces the abandoned call (when wrapped by an agent runner that emits `llm_call_*` events) |
| Concurrent appends from `asyncio.gather` | UNIQUE constraint race | Per-workflow `asyncio.Lock` serializes |
| DB file deleted between calls | Schema vanished | `event_store_sync._connect` self-bootstraps schema on next call |
| Owner column added to existing DB | Schema mismatch | No migration — delete and recreate; this is local observability state |
| Resume handler raises | Bootstrap should still succeed | Registry catches, converts to `failed` outcome, logs |
| Wrong handler kind / missing handler | Workflow looks live forever | Falls back to `orphan` action, status flipped |

---

## Failure modes still outside cerebro's control

The Phase 3 + Step 1 work doesn't fix these, and it can't:

| Failure | What's lost | Why cerebro can't help |
|---|---|---|
| **Claude Code wipes the conversation buffer** | Agent's working understanding, narrative thread | Conversation lives in the LLM client's process; cerebro only sees the tool calls |
| Agent runs `execute_query` *without* `research_project_id` | Query not recorded in any workflow | Cerebro can't tell which active workflow a free-form query belongs to |
| Agent forgets to call `record_research_memory` | Insight stays in conversation only | We can't intercept thoughts — only tool calls |
| Network hiccup loses an in-flight tool call before MCP returns | Tool call never reaches the server | Standard MCP retry territory; not our layer |

The right answers for those:

1. **Conversation-buffer loss** → Claude Code support / configuration
   issue. Persona prompts can mitigate by encouraging frequent
   `record_research_memory` checkpoints, but ultimately the agent has to
   participate.
2. **`research_project_id` not threaded** → persona + tool-description
   nudges. The current `execute_query` docstring says "use
   `research_project_id` when in a research project"; reinforcing this
   in `cerebro_dispatcher.md` would help.
3. **Forgotten checkpoints** → opt-in habit for the agent. A future
   `record_session_snapshot(narrative)` tool the agent calls between
   insights would close this gap; deferred until needed.

---

## Inspecting the live state

```bash
# Schema sanity check
sqlite3 .cerebro/cerebro_state.db ".schema"

# All workflows by status
sqlite3 .cerebro/cerebro_state.db \
  "SELECT kind, status, count(*) FROM workflows GROUP BY kind, status"

# Most recent events on a specific workflow
sqlite3 .cerebro/cerebro_state.db "
  SELECT seq, kind, datetime(ts,'unixepoch') AS ts
  FROM events WHERE workflow_id = 'research_rp_xxx'
  ORDER BY seq
"

# What the resume hint would say (without writing one)
python -c "
import asyncio
from cerebro_mcp.workflow_registry import default_workflow_registry
from cerebro_mcp.research_resume import install_research_resume_handler
install_research_resume_handler()
async def main():
    out = await default_workflow_registry().resume('research_rp_xxx')
    print(out.summary)
    print(out.resume_hint)
asyncio.run(main())
"

# Owner distribution (multi-tenant audit)
sqlite3 .cerebro/cerebro_state.db \
  "SELECT substr(owner, 1, 12) AS owner_prefix, count(*)
   FROM workflows GROUP BY owner_prefix ORDER BY 2 DESC"
```

---

## See also

- [`docs/phase3_resumable_workflows.md`](phase3_resumable_workflows.md) —
  the original Phase 3 sprint write-up (this doc supersedes the
  conceptual portions; phase3_resumable_workflows.md remains as the
  shipping log).
- [`docs/phase2_simulation_sandbox.md`](phase2_simulation_sandbox.md) —
  DuckDB + parquet sandbox layer (Phase 2).
- [`docs/MCP_USAGE_GUIDE.md`](MCP_USAGE_GUIDE.md) — how to use the MCP
  end-to-end with examples.
- [`docs/security.md`](security.md) — query validation, audit log.
- [`docs/observability.md`](observability.md) — Prometheus metrics.
