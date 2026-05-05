# Cerebro MCP — Usage Guide

A comprehensive guide to using the Cerebro MCP server: setup, tools, workflows,
recipes, best practices, and recovery patterns.

> Companion docs:
> - [`memory_and_resume.md`](memory_and_resume.md) — how the event log + resume registry work
> - [`phase1_hybrid_search.md`](phase1_hybrid_search.md) — model search internals
> - [`phase2_simulation_sandbox.md`](phase2_simulation_sandbox.md) — DuckDB sandbox
> - [`phase3_resumable_workflows.md`](phase3_resumable_workflows.md) — workflow registry
> - [`observability.md`](observability.md) — Prometheus metrics
> - [`security.md`](security.md) — owner identity, hashing, secrets

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [MCP Connection Setup](#2-mcp-connection-setup)
3. [Mental Model: The Dispatcher Pattern](#3-mental-model-the-dispatcher-pattern)
4. [Tool Reference by Category](#4-tool-reference-by-category)
5. [Workflow Recipes](#5-workflow-recipes)
6. [Resume & Recovery](#6-resume--recovery)
7. [Multi-Tenant Setup](#7-multi-tenant-setup)
8. [Best Practices](#8-best-practices)
9. [Common Pitfalls](#9-common-pitfalls)
10. [Tips, Tricks, Power Patterns](#10-tips-tricks-power-patterns)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Quick Start

The 30-second tour. Everything else in this guide elaborates on this.

```text
1. get_agent_persona("cerebro_dispatcher")        # always start here for non-trivial tasks
2. preflight_analytics_request(...)               # dispatcher emits this — gates the request
3. search_models / discover_models                # find dbt models across api_*, fct_*, int_*
4. get_model_details / describe_table             # exact columns + lineage
5. start_query / execute_query                    # SQL with date filter + LIMIT
6. generate_charts(specs=[...])                   # BATCH — minimum 3 charts, single call
7. generate_report(markdown="...")                # assembles the dashboard
   → returns file:// link. Show it. Don't paste markdown back.
```

For research projects, QBRs, or storyteller sessions, swap step 7 for the
appropriate `publish_*` / `storyteller_generate_story_report` tool.

---

## 2. MCP Connection Setup

### 2.1 Local stdio (default)

Add this to `~/.config/claude/claude_desktop_config.json` (or your IDE's MCP
config):

```json
{
  "mcpServers": {
    "cerebro-dev": {
      "command": "uv",
      "args": [
        "--directory", "/Users/you/Documents/Gnosis/repos/cerebro-mcp",
        "run", "cerebro-mcp"
      ],
      "env": {
        "CEREBRO_OWNER": "hugo@gnosis.io",
        "CEREBRO_OWNER_HASH_SALT": "rotate-me-quarterly",
        "CLICKHOUSE_HOST": "...",
        "CLICKHOUSE_USER": "...",
        "CLICKHOUSE_PASSWORD": "..."
      }
    }
  }
}
```

`CEREBRO_OWNER` tags every workflow / event / report with a SHA-256 hash of
your identity (salted by `CEREBRO_OWNER_HASH_SALT`). See [§7](#7-multi-tenant-setup).

### 2.2 SSE / remote

For team deployments where the MCP runs as a service:

```json
{
  "mcpServers": {
    "cerebro": {
      "url": "https://cerebro.internal/sse",
      "headers": { "X-Cerebro-Owner": "hugo@gnosis.io" }
    }
  }
}
```

The header is hashed server-side using the same salt rules.

### 2.3 Verifying the connection

```text
system_status()                   # health check + version
list_custom_tools()               # what's available in this build
get_help()                        # top-level navigation
```

---

## 3. Mental Model: The Dispatcher Pattern

The dispatcher is the front door. **Always start here for non-trivial requests.**

```text
get_agent_persona("cerebro_dispatcher")
```

The dispatcher:

1. Classifies intent (analytics report? MMM? research? case study?)
2. Runs `preflight_analytics_request` to gate the request
3. Names the specialist chain (e.g. `mmm_analyst → mmm_causal_reviewer → generate_report`)
4. Emits a **dispatch manifest** binding all subsequent steps

**Skip the dispatcher only for:**
- Trivial turns ("hi", "list reports", "open report 3")
- Explicit specialist invocation ("use mmm_analyst on DEXes")
- Follow-up turns inside an already-dispatched workflow

The dispatcher is a router. It does not query the DB or write SQL.

---

## 4. Tool Reference by Category

### 4.1 Discovery & schema

| Tool | Purpose |
|------|---------|
| `search_models(query)` | Hybrid BM25 + semantic search over dbt manifest. Returns api_*, fct_*, int_* tiers. |
| `discover_models(filters)` | Filtered listing — by tier, tag, or domain. |
| `get_model_details(name)` | Lineage (upstream + downstream), columns, tests, freshness. |
| `describe_table(table)` | Exact CH column types — use before writing SQL. |
| `get_relevant_columns(query, table)` | BM25-ranked column subset for wide tables. |
| `search_models_by_address(addr)` | Reverse lookup: which models reference this address? |
| `list_databases / list_tables` | Raw CH catalog. |
| `get_downstream_impact / get_upstream_lineage` | DAG navigation. |

### 4.2 Query execution

| Tool | When to use |
|------|------|
| `execute_query(sql)` | Synchronous, < 30s queries. Default for EDA. |
| `start_query(sql)` + `get_query_results(id)` | Long-running queries. Returns immediately, poll later. |
| `query_metrics(metric, dims, range)` | Pre-defined metric semantics; safer than raw SQL. |
| `run_saved_query(name)` | Replay a `save_query`'d SQL. |
| `explain_query(sql)` / `explain_metric_query(...)` | CH EXPLAIN — check the plan before running heavy queries. |
| `verify_numbers(claim, sql)` | Cross-check a numerical claim against fresh SQL. |
| `get_clickhouse_query_rules()` | The CH dialect cheat sheet (always-load before complex SQL). |

### 4.3 Visualization & reporting

| Tool | Purpose |
|------|---------|
| `generate_charts(specs=[...])` | **Batch** chart generator. Always ≥ 3 charts in one call. |
| `generate_chart(spec)` | Single chart — only for one-off scratch plots. |
| `generate_metric_charts` / `quick_metric_chart` | Charts driven by metric names instead of raw SQL. |
| `generate_report(markdown)` | Dashboard layout. Standard for analytical reports. |
| `generate_research_report(...)` | Long-form research essay. Requires `deck` + `key_takeaways`. |
| `generate_case_study_report(...)` | Scrollytelling layout for marketing / customer stories. |
| `export_report(id, format)` | docx / pdf / pptx conversion. |
| `list_reports() / open_report(id)` | Reopen past reports. |

**Rule:** `generate_report` enforces gates (≥1 dimensional breakdown, ≥1 scatter/heatmap, ≥1 statistical query, ≥2 exploratory queries, plus quality-discipline checks). See `CLAUDE.md` for the full list.

### 4.4 Research workflow

| Tool | Purpose |
|------|---------|
| `start_research_project(...)` | Creates project + first research workflow. |
| `plan_research_phase` / `execute_research_phase` / `verify_research_phase` | The plan-execute-verify loop per phase. |
| `record_research_finding(...)` | Persists a finding (auto-emits `finding_recorded` event). |
| `record_research_memory(...)` | Persists a memory note. |
| `attach_research_evidence(...)` | Binds a chart/query to a finding. |
| `prepare_peer_review` / `record_peer_review` | Peer review gate. |
| `publish_research_report(...)` | Terminal step → flips workflow to `complete`. |
| `get_research_project / get_research_findings / get_research_memory / get_research_evidence` | Read-side accessors. |

### 4.5 Quarterly Business Review (QBR)

| Tool | Purpose |
|------|---------|
| `open_quarterly_review(quarter)` | Creates the QBR workflow. |
| `update_quarterly_review_focus(...)` | Pin the QBR to specific projects/themes. |
| `save_quarterly_analysis(...)` | Attaches a chart/query as evidence (emits `evidence_attached`). |
| `record_quarterly_note(kind, statement)` | observation / priority / action notes. |
| `add_quarterly_analysis_template(...)` | Reusable QBR templates. |
| `publish_quarterly_review(project_id)` | Terminal step. |

### 4.6 Storyteller (narrative pipeline)

| Tool | Purpose |
|------|---------|
| `storyteller_start_session(...)` | Begins narrative-first deliverable (memo / pitch / brief). |
| `storyteller_record_context_brief(audience, mechanism, required_action)` | Phase 1: who's reading, what action. |
| `storyteller_record_big_idea(sentence, stakes)` | Phase 2: one-sentence thesis. |
| `storyteller_record_storyboard(scene_count, narrative_order, rationale)` | Phase 3: scene plan. |
| `storyteller_record_visual_spec(scene_index, chart_family, ...)` | Phase 4: per-scene chart design. |
| `storyteller_record_final_story(title, content_length)` | Phase 5: drafted prose. |
| `storyteller_run_clarity_checks` / `storyteller_record_accessibility_pass` | Gates. |
| `storyteller_generate_story_report(style="research"|"scrollytelling"|"dashboard")` | Terminal — emits the report. |
| `storyteller_status` / `storyteller_end_session` | Inspection / cleanup. |

### 4.7 Mini-apps (live UI surfaces)

These open interactive panels in the GUI:

| Tool | Surface |
|------|---------|
| `open_portfolio` / `load_portfolio_address` / `navigate_portfolio_relation` | Portfolio explorer |
| `open_token_explorer` / `load_token_explorer_token` | Per-token deep dive |
| `open_graph_explorer` / `expand_graph_explorer_node` | On-chain graph |
| `open_metric_lab` / `load_metric_lab_metric` / `update_metric_lab_chart` | Metric experimentation |
| `open_yield_opportunities` / `load_yield_opportunity` / `run_yield_simulation` | Yield analysis |
| `open_quarterly_review` / `update_quarterly_review_focus` | QBR shell |
| `open_report` | Past reports |

### 4.8 Simulation sandbox (DuckDB + Parquet)

| Tool | Purpose |
|------|---------|
| `create_simulation_sandbox(name, source_models)` | Spin up a DuckDB+Parquet copy of CH data. |
| `query_sandbox(name, sql)` | Free-form what-if queries (no production write). |
| `list_sandboxes() / destroy_sandbox(name)` | Lifecycle. |

See [`phase2_simulation_sandbox.md`](phase2_simulation_sandbox.md).

### 4.9 Resume & state inspection

| Tool | Purpose |
|------|---------|
| `list_resumable_workflows(min_idle_seconds=0)` | Workflows the registry thinks are resumable. |
| `get_workflow_resume_hint(workflow_id)` | Latest hint payload (work / content / notes blocks). |
| `recompute_workflow_resume_hint(workflow_id)` | Force a re-scan. |

See [`memory_and_resume.md`](memory_and_resume.md) for event-log internals.

### 4.10 Other

- `set_thinking_mode(level)` — gives the model a longer scratchpad.
- `log_reasoning / get_reasoning_log` — durable agent reasoning.
- `quality_metrics` — Prometheus counter snapshot.
- `get_performance_stats` — query latency dist.
- `capture_schema_snapshot(tables)` — pin schema for a research run.

---

## 5. Workflow Recipes

### 5.1 "Show me a report on X" (standard analytical request)

```text
1. get_agent_persona("cerebro_dispatcher")
2. preflight_analytics_request(question="...", domain=...)
3. search_models("X")                          # don't stop at first match
4. get_model_details(top 3-5 models)
5. describe_table(chosen tables)
6. start_query(EDA: quantiles, stddevPop, count)   # quick distribution
7. execute_query(...)                              # main queries with LIMIT + dates
   - include ≥1 statistical query
   - include ≥1 correlation query (corr / simpleLinearRegression)
8. generate_charts(specs=[≥3 charts])              # BATCH
   - ≥1 with series_field (or pie/treemap/heatmap/sankey)
   - ≥1 scatter or heatmap
9. generate_report(markdown="""
   ... {{chart:CHART_ID_1}} ...
   ## Key Takeaways
   | Takeaway | Evidence | Why it matters |
   |---|---|---|
   | ... | ... | ... |
   """)
10. Show file:// link. Ask about docx/pdf/pptx export.
```

### 5.2 Long-form research project

```text
1. start_research_project(question, hypothesis)
   → returns research_project_id (rp_xxx)
2. For each phase:
   a. plan_research_phase(rp_xxx, phase_name)
   b. execute_research_phase(rp_xxx, phase_name)
      - inside: search_models / execute_query / generate_charts
      - record_research_finding(rp_xxx, ...)        # emits finding_recorded
      - record_research_memory(rp_xxx, ...)         # emits memory_recorded
      - attach_research_evidence(rp_xxx, chart_id)  # emits evidence_attached
   c. verify_research_phase(rp_xxx, phase_name)
3. prepare_peer_review(rp_xxx)
4. record_peer_review(rp_xxx, verdict="pass")       # OR "fail" — gate
5. publish_research_report(rp_xxx, ...)             # terminal
```

If the chat dies mid-phase: see [§6](#6-resume--recovery).

### 5.3 Quarterly Business Review

```text
1. open_quarterly_review(quarter="2026Q1")
   → returns project_id (rp_xxx) + UI panel
2. update_quarterly_review_focus(project_id, themes=[...])
3. Loop:
   - execute_query / generate_charts
   - save_quarterly_analysis(project_id, chart_id, commentary)
   - record_quarterly_note(project_id, kind="observation"|"priority"|"action", statement="...")
4. publish_quarterly_review(project_id)
```

QBRs auto-advance — there's no explicit plan/execute/verify loop and no peer-review gate.

### 5.4 Storyteller (memo / pitch / brief)

```text
1. storyteller_start_session(session_id="sess_xxx", deliverable_kind="memo")
2. storyteller_record_context_brief(
     audience="VPs and engineering leadership",
     mechanism="memo",                    # or decision_brief / pitch / customer_story / investor_update
     required_action="approve Q1 budget"
   )
3. storyteller_record_big_idea(
     sentence="Q3 retention is up 8% MoM but driven entirely by a single onboarding cohort.",
     stakes="Without diversifying, growth stalls in Q4."
   )
4. storyteller_record_storyboard(scene_count=5, narrative_order="chronological", rationale="...")
5. For each scene:
     storyteller_record_visual_spec(scene_index=i, chart_family="line", relationship="trend",
                                    action_title="...")
6. storyteller_record_final_story(title="...", content_length=N)
7. storyteller_run_clarity_checks()
8. storyteller_record_accessibility_pass()
9. storyteller_generate_story_report(
     style="research" | "scrollytelling" | "dashboard"
   )
```

`mechanism=pitch|customer_story|investor_update` → `style="scrollytelling"` is the natural default.

### 5.5 MMM (sector contribution / ROI)

```text
1. get_agent_persona("mmm_analyst")
   - runs: spine-fill → multicollinearity → baseline → adstock+response curve →
           contribution decomposition → SQL bootstrap CIs
2. Synthesize a markdown DAG table (vars, edges, co-launched flags)
3. get_agent_persona("mmm_causal_reviewer")
   → pass DAG table verbatim
4. If verdict=BLOCK: apply prescribed fix, re-submit
5. If verdict=PASS: generate_report(...)
   - REQUIRED charts: contribution stacked-area, spend-vs-effectiveness,
     response curve, adstock decay, causal-review table
6. Optionally: get_agent_persona("mmm_simulator") with (β, r, λ, current_spend, baseline_kpi)
```

**Do not call `generate_report` until reviewer verdict = PASS.**

### 5.6 What-if simulation

```text
1. create_simulation_sandbox(
     name="reorg_q4",
     source_models=["fct_execution_pools_daily", "dim_token"]
   )
2. query_sandbox("reorg_q4", "SELECT ... FROM fct_execution_pools_daily WHERE ...")
3. Iterate freely — sandbox is isolated DuckDB + Parquet
4. destroy_sandbox("reorg_q4")  # when done
```

---

## 6. Resume & Recovery

### 6.1 The crash recovery flow

If the chat dies, the agent loses context, or you `/clear`:

```text
1. list_resumable_workflows()
   → returns [{workflow_id, kind, last_event_at, action, summary}, ...]
2. get_workflow_resume_hint(workflow_id)
   → full hint with work/content/notes blocks + next_action
3. Call the next_action tool with next_action_args
```

Example hint payload (research):

```json
{
  "research_project_id": "rp_abc",
  "current_phase": "execute",
  "next_action": "execute_research_phase",
  "next_action_args": {"research_project_id": "rp_abc", "phase": "execute"},
  "work": {
    "queries_run": 12,
    "queries_failed": 1,
    "error_classes": {"clickhouse_code_47": 1},
    "recent_memories": [...],
    "recent_findings": [...],
    "evidence_by_phase": {"execute": 3}
  }
}
```

### 6.2 Force a re-scan

If a workflow's hint looks stale:

```text
recompute_workflow_resume_hint(workflow_id)
```

This re-runs the registered handler over the full event log.

### 6.3 What survives a chat wipe

- Everything in `cerebro_state.db`: workflows, events, gates, hints
- All findings, memories, evidence, notes, content recordings
- All generated reports (file:// paths)

**What does NOT survive:**
- Claude's in-conversation reasoning
- Untyped scratch ("I was thinking...") that wasn't saved via `record_research_memory` or `log_reasoning`

**Lesson:** save early, save often. Prefer `record_*` calls over inline prose.

---

## 7. Multi-Tenant Setup

### 7.1 Identity model

Set `CEREBRO_OWNER` (env, header, or session var) to any string identifying the user. The MCP hashes it with `CEREBRO_OWNER_HASH_SALT` (SHA-256) and stamps every workflow / event / report with the hash.

```text
CEREBRO_OWNER          = "hugo@gnosis.io"
CEREBRO_OWNER_HASH_SALT = "rotate-quarterly-2026Q2"
→ owner_hash           = "9f4a...e21c"   (32-byte hex)
```

### 7.2 Isolation guarantees

- `list_resumable_workflows` filters by `owner_hash` of the calling identity
- `get_workflow_resume_hint` enforces ownership; cross-tenant lookups raise
- `list_reports` only returns reports the caller authored
- Event log queries always filter by owner

### 7.3 Rotating the salt

Rotating invalidates all stored hashes (since they were salted with the old value). Treat it as a hard tenant reset — old workflows become unreachable.

For ongoing privacy without losing history: **don't rotate**. Only rotate after a credential leak or compliance event.

---

## 8. Best Practices

### 8.1 Discovery discipline

- Run `search_models` once per major entity in the question. The catalog has more models than the dispatcher names — exhaust it before querying.
- For each candidate, call `get_model_details`. If you skip a candidate, call `record_model_exclusion(name, reason)` — `generate_report` enforces this.
- Use `get_relevant_columns(query, table)` for wide tables (50+ columns). Pull only what you need.

### 8.2 Query hygiene

- Always include a date filter — even `WHERE date >= today() - 90`.
- Always include `LIMIT` for EDA. Drop it only for charts that need full data.
- Use `argMax(col, date)` for stock measures (TVL, balance, supply). Never `SUM(stock_col)` over a range.
- Use `quantile(0.5)` (median) before `avg` — Gnosis Chain data is heavy-tailed.
- For correlations over time: first-difference or use `lagInFrame`. Plain `corr()` over levels is non-stationary.
- Deduplicate aggregator volume — `fct_execution_pools_daily` etc. need a CTE or first-hop-only filter to avoid double-counting.

### 8.3 Reporting

- **One `generate_charts` batch call** with all charts. Multiple single calls slow the run and break gate counting.
- Minimum 3 charts. Include dimensional breakdown + scatter/heatmap + statistical summary.
- Key takeaways = 3-column markdown table (Takeaway / Evidence / Why it matters). Never bullets.
- After `generate_report`, return the file:// link. Do not paste markdown back to the user.
- Acknowledge residual buckets in chart subtitle when filtering `WHERE label != ''`.

### 8.4 Workflow durability

- Save findings the moment you have them — don't wait until "the end."
- Use `record_research_memory` for working notes. They survive chat wipes; inline prose doesn't.
- For long sessions: call `record_research_memory` every ~5 minutes of substantive work.

### 8.5 Performance

- Prefer `start_query` + `get_query_results` over `execute_query` for queries > 10s — async lets you continue working.
- Use `explain_query` before running heavy SQL.
- Sandboxes are cheap — spin up a DuckDB sandbox for any iterative what-if.

---

## 9. Common Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Querying `execution.logs` directly for transfers | Slow, wrong amounts | Use `fct_token_transfers` or related dbt models |
| `SUM(tvl_usd)` over date range | `stock_flow_discipline` gate rejects | Use `argMax(tvl_usd, date)` |
| Plain `corr(x,y)` over time series | `stationarity_on_correlations` gate rejects | First-difference, Spearman, or note ADF |
| Single `generate_chart` calls | Slow, gate counts wrong | One `generate_charts(specs=[...])` batch |
| Pasting markdown back as a reply | User sees raw `{{chart:ID}}` | Show only the file:// link + summary |
| Hardcoding columns without `describe_table` | `Code 47` UNKNOWN_IDENTIFIER | `describe_table` first |
| Stopping search at first model match | Missed dimensions | Exhaust `search_models`, log exclusions |
| Calling `generate_report` before MMM reviewer PASS | Causal review missing | Wait for reviewer verdict |
| Saving findings only at the end | Lost on chat wipe | Save immediately, every memory/finding |
| `min_idle_seconds=86400` filtering out fresh wf | Resume list empty after sweep | Use default 0 |
| Forgetting `research_project_id` on charts | Evidence not linked | Always pass it |

---

## 10. Tips, Tricks, Power Patterns

### 10.1 Inspect raw state

Read-only sqlite of the event log:

```bash
sqlite3 ~/.cerebro/cerebro_state.db
.tables
SELECT id, kind, status, updated_at FROM workflows ORDER BY updated_at DESC LIMIT 10;
SELECT seq, kind, json_extract(payload, '$.preview') FROM events
  WHERE workflow_id = 'research_rp_abc' ORDER BY seq;
```

### 10.2 Re-open a buried report

```text
list_reports()                    # see all, filter by kind
open_report(id=42)                # returns file:// link
```

### 10.3 Hot-swap a chart in a published report

Reports embed SQL — click `</>` in the UI, copy the SQL, modify, then:

```text
generate_chart(spec={...})        # new chart_id
# Then re-emit the same report with the new placeholder
```

### 10.4 Use the metric layer when available

`query_metrics` / `quick_metric_chart` are safer than raw SQL — semantics are pre-validated, no risk of column hallucination.

### 10.5 Pin schemas for research

```text
capture_schema_snapshot(research_project_id, tables=[...])
```

Locks the schema for the project, so column drift later doesn't invalidate findings.

### 10.6 The "trust but verify" pattern

After a numerical claim:

```text
verify_numbers(claim="DEX volume grew 12% MoM in Q1", sql="SELECT ...")
```

Returns a pass/fail + the actual number.

### 10.7 Use thinking mode for hard reasoning

```text
set_thinking_mode("high")
```

Gives the model a longer scratchpad — useful before complex MMM or causal-review steps.

---

## 11. Troubleshooting

### `manifest_hash_mismatch`

Known platform bug. Fall back to raw SQL via `execute_query`. The metric layer will recover after the next dbt build.

### `Workflow not found` on resume

The workflow exists but was created under a different `CEREBRO_OWNER`. Check `owner_hash` in `cerebro_state.db`. Cross-tenant access is blocked by design.

### `aiosqlite: threads can only be started once`

Stale connection in the pool. Restart the MCP server (kill the process; the supervisor respawns).

### Charts render but report rejects them

Check the gate output in the error. Most common: missing `series_field` (no dimensional breakdown), or no scatter/heatmap. Add a chart that satisfies the gate.

### Resume hint shows old next_action

```text
recompute_workflow_resume_hint(workflow_id)
```

Forces a fresh scan over the full event log.

### "Chat cleared completely on crash"

This is upstream of cerebro (Claude conversation buffer). The cerebro event log still has everything — use [§6 Resume](#6-resume--recovery) to recover. Save memories more frequently to minimize what's lost in-between.

---

## See Also

- [`memory_and_resume.md`](memory_and_resume.md) — Event log internals, schema, resume registry
- [`phase1_hybrid_search.md`](phase1_hybrid_search.md) — BM25 + RRF model search
- [`phase2_simulation_sandbox.md`](phase2_simulation_sandbox.md) — DuckDB sandboxes
- [`phase3_resumable_workflows.md`](phase3_resumable_workflows.md) — Workflow registry design
- [`security.md`](security.md) — Owner identity, hashing, secrets
- [`observability.md`](observability.md) — Prometheus metrics
- [`MINI_APPS.md`](MINI_APPS.md) — Mini-app surface reference
- Project root `CLAUDE.md` — Authoritative agent rules
