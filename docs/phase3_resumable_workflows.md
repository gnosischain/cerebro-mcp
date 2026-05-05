# Phase 3 — Resumable Gated Workflows + Parallel Dispatcher

**Status:** shipped (Sprint 3 of the next-gen architecture plan).
**Goal:** stop losing 14-minute multi-agent runs to a single Anthropic 529
or network blip, and steer the dispatcher toward parallel fan-out where
the task is decomposable (per Google's "Science of Scaling Agent Systems"
findings).

This phase is the answer to a real failure mode observed in
`.cerebro/logs`: a Gnosis Pay research report ran for 14.6 minutes,
completed 17 of ~50 expected steps, then stalled when the LLM client hit
a turn limit. Three subsequent retry attempts produced empty report
shells — each one starting from scratch because nothing remembered the
9 steps of progress.

After Phase 3, every workflow phase, sub-task, and LLM call is recorded
in a SQLite event log. A retry replays the events instead of re-running
the queries, and unfinished LLM calls can be re-issued with the exact
message history they had when interrupted.

---

## What was implemented

### 1. SQLite event log (`event_store.py`)

`aiosqlite`-backed, WAL mode, `synchronous=NORMAL` — ms-class commits
that survive process kills. Three tables:

```sql
workflows(id, kind, status, created_at, updated_at, metadata_json)
events(workflow_id, seq, kind, payload_json, ts, payload_compressed)
gates(workflow_id, gate_name, status, payload_json, updated_at)
```

`events` is append-only with monotonic per-workflow `seq`. A workflow's
state is the fold of every event from `seq=1`. `gates` is denormalized
for cheap reviewer-status reads (which gate is ready / passed / failed).

Public API on `EventStore`:

- `init()` — create schema, set WAL/synchronous pragmas. Idempotent.
- `create_workflow(id, kind, metadata)` / `mark_workflow_status(id, status)`.
- `append_event(workflow_id, kind, payload)` → returns new seq.
- `replay(workflow_id)` → list of `{seq, kind, payload, ts}`.
- `set_gate(workflow_id, gate_name, status, payload)` (upsert).
- `list_workflows(statuses=…, older_than_seconds=…)` — used by orphan sweep.

**Compression**: payloads larger than `EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES`
(default 4 KB) are gzipped before insert. LLM message-history payloads
can be 10–100 KB per turn; without compression a long workflow's event
log would balloon.

**Singleton**: `default_event_store()` returns a process-wide instance
constructed from `settings.EVENT_STORE_PATH`.

### 2. LLM payload contract (`workflow_payloads.py`)

For replay to work, `llm_call_started` events MUST carry the *exact*
conversation that produced them — system prompt, full message history,
tool schemas. Without that, replay can only restart agents from scratch.

```python
@dataclass
class LLMTurn:
    role: str           # "user" | "assistant" | "tool"
    content: list[dict] # provider-shaped content blocks
    model: str
    stop_reason: str | None

@dataclass
class LLMCallEvent:
    subtask_name: str
    call_id: str             # caller-chosen, unique within workflow
    system_prompt: str
    messages: list[LLMTurn]  # full history sent in this call
    tool_schemas: list[dict]
    response: LLMTurn | None
    elapsed_seconds: float | None
    error: str | None
```

Both dataclasses round-trip via `to_dict()` / `from_dict()`; nothing in
the payloads imports the Anthropic SDK.

`find_unfinished_llm_calls(events)` walks an event stream and returns
the `LLMCallEvent`s that have a `started` but no matching `completed` or
`failed`. **Resume strategy**: re-issue exactly these calls with the
recorded `messages`. The replay finishes the work that was in flight
when the process died; downstream phases proceed normally.

### 3. Workflow runner (`workflow_runner.py`)

Two helpers — both write events at every transition:

- **`run_parallel_phase(workflow_id, phase_name, subtasks, gate_name)`** —
  uses `asyncio.gather(..., return_exceptions=True)` so one sub-task
  failing doesn't kill peers. A semaphore caps concurrency at
  `settings.WORKFLOW_MAX_PARALLEL` (default 8). On any failure: gate
  marked `failed`, workflow marked `failed`, helper raises after every
  sub-task records its result.
- **`run_sequential_phase(workflow_id, phase_name, steps)`** — runs
  steps one at a time, short-circuits on first failure. Used for math
  chains where step N depends on step N-1's numeric output. The scaling
  paper shows fan-out hurts on these.

`SubTask(name, coro)` wraps a no-argument async callable returning a
JSON-serializable dict. `begin_workflow(id, kind, metadata)` creates the
workflow row + emits `workflow_started`.

### 4. Bootstrap orphan sweep (`bootstrap.py`)

On server startup, `init_event_store_sync()` opens the event store and
marks workflows in `running` / `waiting_gate` state with `updated_at`
older than `WORKFLOW_ORPHAN_AGE_SECONDS` (default 24 h) as `orphaned`.
This stops a leftover from a previous crash from looking like a live
workflow forever.

It does **not** auto-resume yet — that requires a `WorkflowRegistry`
mapping `kind → resume_fn`, which lands incrementally as individual
workflow types (research, storyteller, mmm) are migrated to the
event-log model. Marking orphan is the safety net; auto-resume is the
follow-up.

Wired into `server.py:main()` after manifest loading; failures are
logged but non-fatal so workflows that don't use the event log still
boot cleanly.

### 5. Dispatcher persona rewrite (`prompts/agents/cerebro_dispatcher.md`)

New top-level section "Architecture selection (binding — Phase 3)"
encodes the scaling-paper routing rules:

| Decomposable | Sequential depth | Architecture | `parallelism` | Example |
|---|---|---|---|---|
| no  | high | Single specialist       | `single`     | "stddev of TVL over 30d" |
| no  | low  | Single specialist       | `single`     | "current bridge TVL" |
| yes | low  | Centralized parallel    | `parallel`   | "Q3 review: network + tokenomics + bridge" |
| yes | high | Centralized sequential  | `sequential` | "MMM contribution → causal review → simulation" |

**Independent (no-reviewer) parallel is forbidden.** The Google paper
measured 17.2× error amplification on uncoordinated parallel agents vs
4.4× with a validating orchestrator. Cerebro's reviewer agents
(`statistical_reviewer`, `mmm_causal_reviewer`, `reality_checker`) are
mandatory in any `parallel` plan.

The dispatch manifest gained a `Parallelism: <single | parallel | sequential>`
line so reviewers and downstream callers can see the routing decision
explicitly.

### 6. Configuration

```python
EVENT_STORE_PATH: str = ".cerebro/cerebro_state.db"
WORKFLOW_ORPHAN_AGE_SECONDS: int = 24 * 60 * 60        # 24 h
EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES: int = 4096  # 4 KB
WORKFLOW_MAX_PARALLEL: int = 8
```

All env-overridable.

### 7. Dependencies

```toml
"aiosqlite>=0.20"
```

Pure-Python; uses the stdlib `sqlite3` under the hood.

### 8. Tests (`tests/test_phase3_workflows.py`)

22 tests, all async, all use `tmp_path` for isolated SQLite files. Cover:

- **EventStore basics** (8): create + get, append + replay, event_count,
  status mutations, invalid-status rejection, gate set + get, gate
  upsert, list-by-status.
- **Compression** (1): >100-byte payload compressed and round-trips.
- **Crash recovery** (1): close + reopen a fresh store on the same file,
  events still readable.
- **LLM payloads** (3): roundtrip, find_unfinished surfaces an open
  call, failed call clears the unfinished marker.
- **Parallel runner** (4): all-succeed, one-failure-marks-workflow-failed,
  events recorded, max_parallel bound is honored (semaphore enforces
  serial behavior at bound=1).
- **Sequential runner** (2): basic chain, short-circuits at first failure.
- **Orphan detection** (2): stale running workflow surfaces, completed
  workflows skipped.

### 9. Smoke script (`scripts/test_phase3_workflows.py`)

End-to-end demonstration with no external services. 7 sections covering
the same surface as the unit tests but in a more narrative form — useful
as a "is this thing actually wired up?" check after deployment.

```bash
python scripts/test_phase3_workflows.py
```

---

## What Phase 3 changes for an end-to-end run

Before Phase 3, the gpay research report log showed:

- 14.6 min wall, 1 sec tool work
- LLM client hit a turn limit, agent stalled
- 3 subsequent retries each produced empty report shells (no progress retained)
- Net: zero charts, zero queries, zero artifacts

After Phase 3, the same failure pattern produces:

- The first run records every step in `cerebro_state.db`. When the LLM
  client gives up, the workflow row is in `running` state with the
  partial event stream intact.
- A retry calls `EventStore.replay(workflow_id)` to reconstruct the
  in-memory state of the agent (already-completed sub-tasks, already-
  passed gates). It re-issues only the unfinished LLM calls
  (`find_unfinished_llm_calls`).
- The 9 ClickHouse queries that succeeded in the first run are NOT
  re-run. The agent picks up at step 10.
- If 24 h passes with no progress, the next server boot marks the
  workflow `orphaned` — no longer pretending to be live.

This is incremental: any specific workflow type (research, storyteller,
mmm) needs to call `begin_workflow` + `append_event` + `set_gate` for
its phases to benefit. The infrastructure is in place; migration of
existing flows is per-workflow follow-up.

---

## Trust model — single-tenant

Cerebro-MCP assumes a **single trust domain per server**. Every MCP
client that authenticates (stdio, or SSE with the bearer token from
`MCP_AUTH_TOKEN`) has full access to:

- Every workflow row in `cerebro_state.db`, regardless of who created it.
- Every event in `events` — including the full LLM message history
  recorded as `llm_call_started` payloads (system prompt, user
  questions, tool schemas).
- Every gate state, plus the ability to flip them via
  `recompute_workflow_resume_hint`.
- Every research project on disk (`.cerebro/research_projects/`).

The MCP tools do **NOT** check ownership. There is no per-user
filtering of `list_resumable_workflows`, `get_workflow_resume_hint`, or
any other read path. There is no per-user gate on `record_peer_review`,
`publish_research_report`, `verify_research_phase`, etc.

### Deployment shapes this is fine for

- **stdio** (Claude Code / Cursor / a single human via local IDE) —
  one OS user, one process, OS file permissions on `.cerebro/` are the
  boundary.
- **SSE with one shared bearer token** — the token represents the
  service, not individual humans. Sharing the token = sharing the
  state.

### Deployment shapes this is NOT fine for

- **Multiple humans sharing one MCP server with one token.** Anyone on
  the team can read everyone else's research hypotheses, queries, and
  LLM transcripts.
- **Per-user tokens without an authorization model.** Adding distinct
  tokens for distinct humans does NOT add isolation — the tools still
  see every workflow.
- **Exposing the SQLite db (or `cerebro_state.db` paths) to other
  processes** that bypass the MCP layer.

If your deployment crosses any of those lines, you need an authorization
layer: at minimum an `owner` column on `workflows` populated from an
authenticated identity, with read/write filters in `EventStore` and
`event_store_sync`. That work is **not** included in Phase 3 — flag it
as a separate sprint if multi-user isolation becomes a requirement.

## Operational characteristics

| Property | Value | Notes |
|---|---|---|
| Backing store | local SQLite file | `EVENT_STORE_PATH` (default `.cerebro/cerebro_state.db`) |
| Concurrent writers | safe (WAL) | Multiple FastMCP requests can write simultaneously |
| Crash safety | journal_mode=WAL, synchronous=NORMAL | Survives `kill -9`; not OS crash |
| Replay cost | O(events) | A 200-event workflow replays in <50 ms |
| Compression | gzip on payloads >4 KB | Set `EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES=0` to disable |
| Parallelism cap | `WORKFLOW_MAX_PARALLEL` (default 8) | Global; bounded by semaphore in `run_parallel_phase` |
| Orphan threshold | 24 h | `WORKFLOW_ORPHAN_AGE_SECONDS`; only `running` / `waiting_gate` rows are touched |
| Auto-resume | NOT YET | Orphan marking is the safety net; `WorkflowRegistry` for kind-keyed resume is the follow-up |

---

## What was deliberately NOT done

- **No auto-resume.** Orphan marking only. Auto-resume requires a
  `WorkflowRegistry` mapping each workflow `kind` to a `resume_fn`,
  which lands per-workflow as we migrate research / storyteller / mmm
  to use the event log. Explicit so partial migrations don't try to
  resume into code paths that aren't yet event-aware.
- **No multi-process write coordination.** SQLite WAL handles concurrent
  *reads* and serial *writes* on a single host fine. If cerebro-mcp
  ever runs as multiple replica processes hitting the same db file,
  we'd want a shared store (Postgres) — out of scope for this phase.
- **No event-log retention/cleanup.** The db grows indefinitely. Once
  workflows mature, a periodic vacuum + age-based archival is the next
  step. Not urgent — at ~50 events/workflow + gzip, a hundred workflows
  per day is ~MBs/year.
- **No Anthropic-specific replay machinery.** The `LLMCallEvent`
  dataclass is provider-shaped (Anthropic content blocks) but does not
  call the SDK. The actual re-issue is in the agent runner that owns
  the workflow — out of scope here.
- **Persona rewrites are dispatcher-only.** Existing analyst personas
  don't yet know about the event log. Adding `await store.append_event`
  calls inside each analyst's flow is part of per-workflow migration.

---

## Files touched

| File | Change |
|---|---|
| `pyproject.toml` | +`aiosqlite>=0.20` |
| `src/cerebro_mcp/event_store.py` | **new** — `EventStore` |
| `src/cerebro_mcp/workflow_payloads.py` | **new** — `LLMTurn`, `LLMCallEvent`, kind constants, `find_unfinished_llm_calls` |
| `src/cerebro_mcp/workflow_runner.py` | **new** — `SubTask`, `begin_workflow`, `run_parallel_phase`, `run_sequential_phase` |
| `src/cerebro_mcp/config.py` | +`EVENT_STORE_PATH`, +`WORKFLOW_ORPHAN_AGE_SECONDS`, +`EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES`, +`WORKFLOW_MAX_PARALLEL` |
| `src/cerebro_mcp/bootstrap.py` | +`init_event_store_async`, +`init_event_store_sync` |
| `src/cerebro_mcp/server.py` | event-store bootstrap call in `main()` |
| `src/cerebro_mcp/prompts/agents/cerebro_dispatcher.md` | +"Architecture selection" section, +`Parallelism` field in manifest |
| `tests/test_phase3_workflows.py` | **new** — 22 tests |
| `scripts/test_phase3_workflows.py` | **new** — smoke script (7 sections) |
| `docs/phase3_resumable_workflows.md` | **new** — this document |

---

## Reproducing the tests

```bash
python -m pytest tests/test_phase3_workflows.py -v
python -m pytest tests/ -q       # full suite — Phase 1 + 2 + 3 should be green
python scripts/test_phase3_workflows.py
```

---

## Phase 3 migration: `tools/research.py` (shipped 2026-04-27)

After the initial Phase 3 ship, the dispatcher persona was bypassed
entirely on the first live test (zero `cerebro_dispatcher` invocations
in 263 steps) because the user prompts named specialists explicitly,
which triggers the project's "explicit specialist invocation" exemption.
This left the event log empty of real workflow data and the architecture-
selection rules unenforced.

The follow-up migration wires the **research workflow** — the most
common multi-phase analyst flow — directly into the event log so every
research project produces a replayable trace independent of whether the
dispatcher is consulted.

### Sync API: `event_store_sync.py`

The async `EventStore` (aiosqlite) is correct for parallel-fan-out
runners that already live on the asyncio event loop. The research MCP
tools are synchronous (`def`, not `async def`), so calling async APIs
from them would force every tool through `asyncio.run` and pay
event-loop startup per call.

`event_store_sync.py` is a stdlib-`sqlite3` sibling that writes to the
**same DB file** and **same schema** as the async store. Both paths
coexist; aiosqlite handles `EventStore` callers, `sqlite3` handles
`event_store_sync` callers. Concurrency is safe because:

- Both libraries respect SQLite's writer-serialization at the file lock
  level.
- The SELECT-MAX + INSERT pair in `append_event_safe` runs inside
  `BEGIN IMMEDIATE` so concurrent appends serialize without a Python-side
  lock.
- WAL mode + `synchronous=NORMAL` are set on every connection
  (synchronous is connection-scoped).

All `*_safe` functions follow a strict no-raise contract — exceptions
are caught and logged. Event-log writes are observability; a SQLite
hiccup must never break the underlying research-workflow operation.

### Domain helpers

The integration points the research MCP tools call:

| Helper | Called from | Event kind | Gate effect |
|---|---|---|---|
| `record_research_started` | `start_research_project` | `workflow_started` | creates workflow row, status=running |
| `record_research_phase_planned` | `plan_research_phase` | `phase_planned` | none |
| `record_research_phase_completed` | `execute_research_phase` | `phase_completed` | none |
| `record_research_verification` | `verify_research_phase` | `verification_completed` | flips `verification:<phase>` to passed/failed |
| `record_research_peer_review` | `record_peer_review` | `peer_review_recorded` | flips `peer_review` to passed/failed |
| `record_research_published` | `publish_research_report` | `report_published` | marks workflow status=completed |

`workflow_id_for_research(project_id)` namespaces research workflows as
`research_<project_id>` so a UUID collision between project IDs and
arbitrary workflow IDs is impossible.

### Live behavior after migration

A research session that starts with `start_research_project(...)` and
walks through plan → execute → verify → peer review → publish now
populates the event log with ~7-15 events plus 2 gates. The on-disk
JSON snapshots in `research_store.py` remain the authoritative
denormalized state; the event log is the **chronological log** that
replay reads.

After a process kill mid-research:

1. The workflow row stays at `status=running` (or `waiting_gate` if it
   was at the verification step).
2. On server restart, `init_event_store_sync()` runs the orphan sweep
   — workflows untouched for 24h are marked `orphaned` (default
   `WORKFLOW_ORPHAN_AGE_SECONDS`).
3. Within the 24h window the workflow row stays `running`. A retry of
   the user's request hits the same `project_id` (deterministic via
   `start_research_project`'s hash inputs) → `record_research_started`
   detects the duplicate (returns False, doesn't crash) → subsequent
   phase calls append events normally.

### Tests (`tests/test_phase3_research_migration.py`)

11 tests covering:

- **Lifecycle events**: started → planned → completed → verified →
  peer-review → published, in order.
- **Gate flips**: verification pass/fail, peer review approved/rejected.
- **Workflow status**: `running` → `completed` after publish.
- **Failure tolerance**: db unwritable, invalid gate status, duplicate
  workflow_id — none raise.

Run:

```bash
python -m pytest tests/test_phase3_research_migration.py -v
```

### Files touched in this migration

| File | Change |
|---|---|
| `src/cerebro_mcp/event_store_sync.py` | **new** — sync sqlite3 API + research domain helpers |
| `src/cerebro_mcp/tools/research.py` | event-log calls in 6 tools (start, plan, execute, verify, peer review, publish) |
| `tests/test_phase3_research_migration.py` | **new** — 11 tests |
| `docs/phase3_resumable_workflows.md` | this section |

### How to verify it's working in production

After running any research workflow against the live MCP server:

```bash
sqlite3 .cerebro/cerebro_state.db <<EOF
.headers on
SELECT id, kind, status, created_at FROM workflows ORDER BY created_at DESC LIMIT 5;
SELECT workflow_id, seq, kind, ts FROM events ORDER BY ts DESC LIMIT 20;
SELECT workflow_id, gate_name, status FROM gates ORDER BY updated_at DESC LIMIT 10;
EOF
```

If `workflows` is non-empty after a `start_research_project` call, the
migration is wired correctly. If it stays empty, check the server logs
for `event-log create_workflow failed` — likely a permissions issue on
the `EVENT_STORE_PATH`.

---

## WorkflowRegistry — auto-resume on bootstrap (shipped 2026-04-27)

After the research migration, the third Phase 3 piece closes the loop:
the bootstrap path no longer just *marks orphans* — it dispatches every
stale `running` / `waiting_gate` workflow to a registered resume handler
that produces a structured `ResumeOutcome` with a hint the agent can act
on at the next user interaction.

### Design constraints

- **Bootstrap-safe.** Resume runs at server startup before FastMCP
  serves traffic. Handlers MUST be fast and offline — no LLM calls, no
  ClickHouse, no network. They read events and return a hint. The
  agent decides whether to act.
- **Hint-driven, not action-driven.** The `resume_fn` shape is:
  `(workflow_id, workflow_row, events) → ResumeOutcome`. It produces
  *structured advice*, not side effects. The actual resumption happens
  on the next user turn, when the agent reads the hint via
  `list_resumable_workflows` / `get_workflow_resume_hint`.
- **Idempotent.** Calling resume twice on the same workflow produces
  the same outcome and writes a fresh `workflow_resume_hint` event each
  call (latest-wins semantics for the MCP read helpers).
- **No-handler fallback = orphan.** A workflow whose `kind` has no
  registered handler falls through to the existing `mark_orphaned`
  path. Migrating new workflow types is incremental — nothing breaks
  for unregistered kinds.

### Five outcome types

| `action` | Meaning | Status flip |
|---|---|---|
| `ready_to_resume` | Workflow has more work; hint contains `next_action`, `next_action_args` | Status preserved (running / waiting_gate) |
| `complete` | Terminal success detected (e.g. `report_published` event found) | → `completed` |
| `failed` | Terminal failure detected (e.g. peer review rejected) | → `failed` |
| `orphan` | Stale beyond TTL OR handler can't make sense of state | → `orphaned` |
| `no_handler` | Kind not registered | → `orphaned` |

### `WorkflowRegistry` API

**File:** `src/cerebro_mcp/workflow_registry.py`

```python
@dataclass
class ResumeOutcome:
    workflow_id: str
    kind: str
    action: str                # one of ACTION_*
    summary: str
    resume_hint: dict          # opaque, kind-specific
    unfinished_llm_calls: list[LLMCallEvent]


class WorkflowRegistry:
    def register(self, kind: str, fn: ResumeFn) -> None
    def has_handler(self, kind: str) -> bool
    def known_kinds(self) -> list[str]

    async def resume(self, workflow_id: str) -> ResumeOutcome
    async def resume_all_running(self, max_age_seconds=...) -> list[ResumeOutcome]
```

`ResumeFn` is `(workflow_id, workflow_row, events) → Awaitable[ResumeOutcome]`.

The registry validates handler output (rejects non-`ResumeOutcome`
returns), catches handler exceptions and converts them to `failed`
outcomes (a buggy handler must never break bootstrap), and writes a
`workflow_resume_hint` event for every outcome.

### `research_project` resume handler

**File:** `src/cerebro_mcp/research_resume.py`

Pure-function state machine over the event stream. Decisions:

1. `report_published` event present → `complete`.
2. `peer_review_recorded` event with `status=rejected` → `failed`.
3. Otherwise → `ready_to_resume`. Compute current phase by walking
   `phase_planned` / `phase_completed` events; emit
   `next_action` + `next_action_args` so the agent knows the exact MCP
   call to make.

Resume hint shape:

```python
{
    "project_id":       "rp_xxx",
    "current_phase":    "mapping" | "hypothesis" | "execution" |
                        "verification" | "publication",
    "completed_phases": ["mapping", "hypothesis", ...],
    "next_action":      "plan_research_phase" | "execute_research_phase" |
                        "verify_research_phase" | "record_peer_review" |
                        "publish_research_report",
    "next_action_args": { "project_id": ..., "phase": ... },
    "verification_gate": "passed" | "failed" | None,
    "peer_review_gate":  "passed" | "failed" | None,
}
```

`unfinished_llm_calls` lifts unfinished `llm_call_started` events (via
`find_unfinished_llm_calls`) so the agent can re-issue them with the
recorded message history.

### Bootstrap integration

`init_event_store_async` (in `src/cerebro_mcp/bootstrap.py`) now:

1. Initializes the SQLite schema (idempotent).
2. Registers all known resume handlers (`install_research_resume_handler`).
3. Calls `registry.resume_all_running(max_age_seconds=WORKFLOW_ORPHAN_AGE_SECONDS)`.
4. Returns a counter dict: `{ready_to_resume, complete, failed, orphaned, no_handler}`.

The server logs a one-line summary on boot when any sweep results are
non-zero:

```
WARNING Workflow resume sweep on startup: ready_to_resume=2, complete=1
```

### MCP tools (the agent-facing surface)

**File:** `src/cerebro_mcp/tools/workflow_resume.py`

Three new tools:

- **`list_resumable_workflows(max_age_seconds=86400)`** — markdown list
  of every running / waiting_gate workflow, with the most recent hint
  for each. The agent uses this as the first call after a server
  restart to find abandoned work.
- **`get_workflow_resume_hint(workflow_id)`** — JSON payload of the
  latest hint for a specific workflow. Used when the agent has a
  `workflow_id` and wants the full hint without listing-level
  truncation.
- **`recompute_workflow_resume_hint(workflow_id)`** — re-runs the
  resume handler on demand and appends a fresh hint event. Use this
  when a workflow has progressed since the bootstrap-time scan.

All three are read-only (apart from `recompute`, which only appends a
hint event); none make LLM calls or hit ClickHouse.

### Example end-to-end flow

```
[server boot]
  init_event_store_async runs:
    workflow `research_proj_xyz`, kind=research_project, status=running
    -> resume_research_project decides: ready_to_resume at phase "execution"
    -> appends {kind:"workflow_resume_hint", payload:{...}}
    -> status preserved as `running`

[next user interaction]
  user asks: "Resume the project I started yesterday."
  agent calls list_resumable_workflows()
    -> sees research_proj_xyz, current_phase=execution
  agent calls get_workflow_resume_hint("research_proj_xyz")
    -> reads {next_action:"execute_research_phase", next_action_args:{...}}
  agent calls execute_research_phase(project_id="proj_xyz", phase="execution")
    -> normal phase transition; phase_completed event appended;
       new workflow_resume_hint event written by recompute (or natural advance)
```

The 14-minute lost-report failure mode from the original `.cerebro/logs`
session is now structurally impossible: every phase that completed in
the first run is in the event log, and the resume handler tells the
agent exactly where to pick up. No re-running of completed ClickHouse
queries; no re-issuing of completed LLM calls.

### Tests + smoke

| File | Coverage |
|---|---|
| `tests/test_phase3_workflow_registry.py` | 21 tests: registry mechanics (8), research handler (7), idempotent install (1), the rest |
| `scripts/test_phase3_workflow_registry.py` | 7-section smoke covering all 5 action paths + unfinished LLM call surfacing + MCP-tool helpers |

Run:

```bash
python -m pytest tests/test_phase3_workflow_registry.py -v
python scripts/test_phase3_workflow_registry.py
python scripts/test_phase3_workflow_registry.py --keep-db   # for sqlite3 inspection
```

### Files touched

| File | Change |
|---|---|
| `src/cerebro_mcp/workflow_registry.py` | **new** — `WorkflowRegistry`, `ResumeOutcome`, `ACTION_*` |
| `src/cerebro_mcp/research_resume.py` | **new** — research-project resume handler + installer |
| `src/cerebro_mcp/tools/workflow_resume.py` | **new** — 3 MCP tools |
| `src/cerebro_mcp/bootstrap.py` | replaced orphan-only sweep with registry-driven resume sweep |
| `src/cerebro_mcp/server.py` | wired `register_workflow_resume_tools`, log resume-sweep summary on boot |
| `tests/test_phase3_workflow_registry.py` | **new** — 21 tests |
| `scripts/test_phase3_workflow_registry.py` | **new** — 7-section smoke |
| `docs/phase3_resumable_workflows.md` | this section |

---

## What's next

- **Migrate `storyteller_state.py`** — second real consumer of the event
  log. Once migrated, register a `storyteller_session` handler so
  `list_resumable_workflows` shows storyteller flows too.
- **Migrate `tools/quarterly_review.py`** — already PHASE_ORDER-shaped,
  small lift; reuses `event_store_sync` helpers.
- **Active resume from the agent layer** — currently the agent reads
  hints; a follow-up could have it auto-call `next_action` /
  `next_action_args` when the user says "resume" without further prompting.
- **Phase 4** — non-blocking event loop (`ProcessPoolExecutor` for
  CPU-bound work like manifest parse + BM25 corpus build).
