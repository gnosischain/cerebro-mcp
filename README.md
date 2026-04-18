# Cerebro MCP

![Cerebro MCP](img/header-banner.png)

Model Context Protocol server for Gnosis Chain analytics on top of ClickHouse and dbt.

Cerebro MCP exposes query, schema, visualization, report, and long-horizon research tools to MCP hosts such as Claude Desktop, Claude Code, and VS Code. It is designed for three distinct usage patterns:

1. Quick exploratory analysis and factual lookups
2. Interactive chart and report generation
3. Durable, phase-driven research projects

This README is intentionally implementation-oriented. It describes what the server actually does today, how the MCP flows work, and which tools to use for each job.

---

## What Cerebro MCP Actually Is

Cerebro MCP is a FastMCP server with:

- ClickHouse access for read-only SQL
- dbt manifest search and lineage lookup
- interactive chart and report generation
- reasoning/tracing utilities
- durable research project storage
- parameterized custom query tools loaded from YAML config
- AI dashboard tab scaffolding for metrics-dashboard
- number verification tool that checks arithmetic and cross-references before reporting
- MCP prompts and resources that guide clients, but do not run automatically on their own
- security audit layer with tool risk classification, suspicious-call detection, and append-only JSONL logging
- six React + ECharts **mini-apps** (Report, Token Explorer, Metric Lab, Portfolio, Yield Opportunities, Graph Explorer) served as single-file HTML via `ui://cerebro/<app>` resources — see [`docs/MINI_APPS.md`](docs/MINI_APPS.md) for the full tour
- mini-app infrastructure for interactive in-chat views (Metric Lab, Token Explorer)

Important distinction:

- Tools execute logic on the server
- Prompts return guidance text to the client
- Resources expose static reference material
- The server does not run an internal autonomous LLM loop

If a client wants to use personas or peer review, it must explicitly call the prompt or tool flow that supports it.

---

## How The MCP Works

At runtime the system looks like this:

1. Your MCP host connects over `stdio` or `SSE`
2. The host asks the model to solve a task
3. The model chooses tools, prompts, and resources from this server
4. The server executes ClickHouse/dbt/report/research logic and returns structured data
5. For report tools, MCP App compatible clients can render the returned `structuredContent` inline

Core runtime components:

- `clickhouse_client.py`: shared execution pipeline, limit enforcement, JSON-safe normalization, schema cache
- `tools/query.py`: sync SQL execution
- `tools/query_async.py`: async query jobs with paginated result previews
- `tools/schema.py`: table listing and schema inspection
- `tools/dbt.py`: model search and lineage/context lookup
- `tools/visualization.py`: chart registry, report rendering, report persistence
- `tools/research.py`: durable research projects, evidence, verification, peer review, publication
- `tools/mini_apps.py`: mini-app view infrastructure, app-only tool visibility filter
- `tools/metric_lab.py`: interactive Metric Lab views
- `tools/token_explorer.py`: interactive Token Explorer views
- `security.py`: tool risk classification, suspicious-call flagging, append-only JSONL security audit log
- `observability.py`: Prometheus metrics, structured JSON logging, security counters

Artifacts created by the server:

- saved reports on disk
- saved query SQL snippets
- async query result pages
- research projects and evidence snapshots
- reasoning traces
- security audit logs (JSONL, daily rotation)

---

## Choose The Right Workflow

| Workflow | Use it for | Core tools | Output |
|---|---|---|---|
| Semantic analytics | governed metrics, multi-hop dimension reachability, explainable metric SQL, research evidence with provenance | `discover_metrics`, `get_metric_details`, `explain_metric_query`, `query_metrics` | semantic query result, compiled SQL, retry traces, semantic provenance |
| Quick exploration | answering a direct question, checking a metric, testing a hypothesis quickly | `discover_models`, `describe_table`, `execute_query`, `explain_query`, `get_sample_data` | markdown summary plus structured query payload |
| Report workflow | visual analysis, KPI packs, weekly or monthly summaries, chart-heavy deliverables | `discover_models`, `describe_table`, `execute_query`, `generate_charts`, `generate_report` | interactive report artifact plus saved HTML |
| Research workflow | multi-step investigations that need memory, evidence, review, and publication | `start_research_project`, phase tools, evidence tools, verification, peer review, `publish_research_report` | durable research project plus report artifact |
| Storyteller workflow | decision-oriented narratives, executive briefs, stakeholder memos, recommendation artifacts where an audience must be moved to action | `storyteller_start_session`, `storyteller_record_*`, `storyteller_run_clarity_checks`, `storyteller_generate_story_report` | gated, narrative-first report artifact with action titles, focal-point design, and adversarial clarity review |
| Custom query tools | common domain questions with known parameters | `get_validator_balance_history`, `get_token_transfers_for_address`, etc. | parameterized query result |
| Dashboard scaffolding | creating new dashboard tabs from semantic metrics | `discover_dashboard_metrics`, `scaffold_dashboard_tab` | JS query files + YAML config |
| Number verification | any computed numbers before reporting to user | `verify_numbers` | PASS/MISMATCH verdict |

Use quick exploration when you do not need charts or durable state. Use reports when the deliverable is a visual artifact. Use research when the work spans multiple phases or needs explicit evidence and review.

---

## End-To-End Workflows

### 1. Exploratory Chat

Use this when the user asks something like:

- "How many transactions were there yesterday?"
- "What tables cover validator activity?"
- "Compare bridge inflows over the last 7 days"

Recommended sequence:

1. Find the right data source
   - Prefer `discover_models(query, detail_top_n=5)` as the first step
   - Use `search_models` + `get_model_details` only when you want broader manual triage
2. Verify the actual schema
   - Call `describe_table` before writing SQL
3. Run a bounded query
   - Use `execute_query`
   - Use `explain_query` first if you are unsure about the SQL shape
4. If the query is large or slow
   - Use `start_query`
   - Poll with `get_query_results`

Minimal pattern:

```text
discover_models("bridge volume daily", detail_top_n=5)
describe_table("api_bridges_volume_daily", database="dbt")
execute_query("SELECT dt, volume_usd FROM dbt.api_bridges_volume_daily WHERE dt >= today() - 7 ORDER BY dt", database="dbt", max_rows=30)
```

What to expect from `execute_query`:

- structured payload with `columns`, `rows`, `row_count`, `rows_returned`, `warnings`
- `summary_markdown` for a human-readable table
- automatic row and payload truncation to protect MCP/LLM context

When to use async instead:

- expected result set is large
- query may take longer than a normal chat turn
- you want paginated result retrieval

Async flow:

```text
start_query(...)
get_query_results(query_id)
get_query_results(query_id, page_token="...")
```

Important behavior:

- async jobs are in-process, not permanent
- restarting the server loses pending/finished async job state
- completed async jobs are cleaned up after a short retention window

### 1b. Semantic Analytics Workflow

Use this when the user is really asking for a governed metric question, for example:

- "Show transaction count by sector over the last 30 days"
- "Which dimensions can I slice validator rewards by?"
- "Explain how this metric query was planned"

Recommended sequence:

1. Discover the metric surface
   - call `discover_metrics(query, module=..., limit=...)`
2. Inspect the resolved metric
   - call `get_metric_details(metric_name)`
3. Explain before execution when the path is non-trivial
   - call `explain_metric_query(...)`
4. Execute through the semantic layer
   - call `query_metrics(...)`
5. If semantic execution is unavailable or unsupported
   - surface the returned fallback reason
   - move to raw SQL with `describe_table`, `get_model_details`, `execute_query`, and `get_clickhouse_query_rules`

Important behavior:

- semantic execution is deployment-gated by `SEMANTIC_ENABLED`
- when semantic is enabled but artifacts are stale or unavailable, semantic tools stay registered but execution returns graceful unavailable guidance
- `query_metrics` performs one deterministic server-side repair retry for known-safe ClickHouse errors
- raw `execute_query` remains agent-guided rather than auto-healed by the server

### 1c. Custom Parameterized Queries

Use this when the user asks common domain questions with known parameters:

- "What is validator 12345's balance history?"
- "Show me GNO transfers for 0x..."
- "What are the bridge flows for USDC?"

These are pre-built, peer-reviewed SQL templates. The LLM passes typed parameters; ClickHouse handles binding natively. Zero SQL hallucination risk.

Recommended sequence:

1. Check available tools
   - Call `list_custom_tools` to see available parameterized tools
2. Call the matching tool directly
   - e.g. `get_validator_balance_history(validator_index=12345, start_date="2025-01-01")`

Example tools:

```text
get_validator_balance_history(validator_index=12345, start_date="2025-01-01")
get_token_transfers_for_address(address="0x...", token_symbol="GNO")
get_bridge_flows_by_token(token="USDC", start_date="2025-06-01")
get_gpay_wallet_activity(wallet_address="0x...")
```

Custom tools use ClickHouse native parameter binding (`{param:Type}`) and are immune to SQL injection. When no custom tool exists, fall back to the raw SQL workflow.

### 1d. Number Verification

The `verify_numbers` tool catches computation errors before numbers reach the user. The agent must show its work — the formula it used and the component values — so the tool can independently verify the math.

When to call: before reporting any computed number (sums, nets, percentages, totals).

How it works:

1. Agent computes numbers from query results
2. Agent calls `verify_numbers` with structured claims:
   - `label`: what the number represents
   - `value`: the computed number
   - `formula`: the arithmetic used (e.g., "received - sent")
   - `components`: the named values (e.g., {"received": 9352.5, "sent": 9002.9})
   - `check_query`: optional SQL against an independent model for cross-reference
3. Tool verifies: does received - sent actually equal the claimed value?
4. Tool runs check_query if provided and compares
5. Returns PASS or MISMATCH with specific error details

Example catching a real bug:

The agent computed transfers and claimed net inflow = 1360 GNO:

```text
verify_numbers('[{"label": "net GNO inflow", "value": 1360.5, "formula": "received - sent", "components": {"received": 9352.5, "sent": 9002.9}}]')
```

Result: ARITHMETIC FAIL — 9352.5 - 9002.9 = 349.6, NOT 1360.5 (diff: 289.2%). The agent must correct to 349.6 before presenting to the user.

### 2. Report Workflow

Use this when the user wants:

- a weekly or monthly summary
- charts, trends, dashboards, or visual comparisons
- a report artifact that can be reopened or exported

Recommended sequence:

1. Discover relevant models
   - Prefer `discover_models(query, detail_top_n=5)`
2. Verify a real table with `describe_table`
3. Run exploratory queries with `execute_query`
4. Build charts
   - Preferred: `generate_charts`
   - Secondary: `generate_chart`
   - For a one-off plot with no gates: `quick_chart`
5. Write markdown with chart placeholders
6. Call `generate_report`
7. Optionally use `open_report`, `list_reports`, or `export_report`

Preferred report chart flow:

```text
generate_charts([
  {...},
  {...},
  {...}
])
generate_report(title="Gnosis Chain Weekly Report", content_markdown="...")
```

#### Which chart tool to use

| Tool | Use when | Notes |
|---|---|---|
| `quick_chart` | one-off exploratory plot | bypasses report/discovery gates |
| `generate_chart` | you need one chart at a time | gated; useful for a single additional chart |
| `generate_charts` | you are building a real report | preferred; creates multiple charts in one call |

#### Report markdown syntax

The report renderer understands:

- `## Heading` for major sections
- `{{chart:chart_id}}` to place a chart
- `{{grid:N}} ... {{/grid}}` to group charts into a grid row

Example:

```markdown
## Executive Summary

{{grid:3}}
{{chart:chart_1}}
{{chart:chart_2}}
{{chart:chart_3}}
{{/grid}}

Key takeaways from the week.

## Activity Trend

{{chart:chart_4}}

## Breakdown

{{grid:2}}
{{chart:chart_5}}
{{chart:chart_6}}
{{/grid}}
```

#### What is actually enforced vs recommended

Hard gates for chart creation:

- `generate_chart` and `generate_charts` require:
  - at least one discovery call (`search_models` or `discover_models`)
  - at least `MIN_MODELS_DETAILED` model detail explorations
  - at least `MIN_TABLES_VERIFIED` table schema checks

Hard gates for report creation:

- at least `MIN_CHARTS_FOR_REPORT` registered charts
- at least one trend or breakdown chart
- at least `MIN_EXPLORATORY_QUERIES` prior query executions
- at least one dimensional breakdown
- at least one relational analysis signal:
  - a scatter or heatmap chart, or
  - a correlation query

Soft warnings only:

- lack of statistical queries
- lack of explicit correlation/regression queries
- shallow exploration before charting
- no scatter chart in a larger report

Layout enforcement:

- if the report contains two or more `numberDisplay` charts and no `{{grid:N}}` block, report generation is rejected
- other layout guidance is recommended through prompts and personas, but not hard-blocked by the renderer

This matters because older documentation may imply stricter layout or persona enforcement than the server actually performs.

#### Report outputs

`generate_report` does three things:

- saves a standalone HTML report to disk
- returns `structuredContent` for MCP App capable clients
- returns a text summary containing the report ID and reopen/export hints

Useful report tools after creation:

- `open_report(report_id)` to reopen a saved report
- `list_reports()` to browse saved reports
- `export_report(report_id)` to get a file path or SSE download URL

### 3. Research Workflow

Use this when the user wants:

- a deep-dive investigation
- a multi-turn, multi-phase analysis
- durable memory and evidence
- explicit verification and peer review

The research system is MCP-native and client-orchestrated. It is not an autonomous background scientist. The assistant must explicitly drive the project through its phases.

Research phases:

1. `mapping`
2. `hypothesis`
3. `execution`
4. `verification`
5. `publication`

Recommended sequence:

```text
start_research_project(...)
plan_research_phase(..., phase="mapping")
execute_research_phase(..., phase="mapping")
capture_schema_snapshot(...)
record_research_memory(...)

plan_research_phase(..., phase="hypothesis")
execute_research_phase(..., phase="hypothesis")

plan_research_phase(..., phase="execution")
execute_research_phase(..., phase="execution")
execute_query(..., research_project_id=..., persist_result=True)
attach_research_evidence(...)
record_research_finding(...)

verify_research_phase(...)
prepare_peer_review(...)
conduct_research_peer_review(...)
record_peer_review(...)
publish_research_report(...)
```

#### Research project behavior

`get_research_project(project_id)` returns a compact summary only:

- current phase
- status
- evidence count
- memory count
- findings count
- peer review status
- artifact count

For detail, use the paginated retrieval tools:

- `get_research_memory`
- `get_research_evidence`
- `get_research_findings`

#### Research evidence rules

Durable evidence kinds:

- `query_result`
- `semantic_query_result`
- `schema_snapshot`
- `chart`
- `report`

Important:

- `save_query` is not evidence
- `save_query` stores reusable SQL only
- durable sync query evidence must come from `execute_query(..., persist_result=True)`

When you run:

```text
execute_query(
  sql=...,
  research_project_id="rp_...",
  persist_result=True,
  evidence_title="Daily bridge volume",
  persist_max_rows=10000
)
```

the server does two separate things:

- returns a tool-budgeted preview payload to the model
- stores a larger durable result artifact on disk and returns `result_ref_id`

You can then attach that result to the project:

```text
attach_research_evidence(
  project_id="rp_...",
  kind="query_result",
  ref_id="qry_...",
  phase="execution"
)
```

#### Research memory vs findings

Use `record_research_memory` for reusable domain facts:

- decimals and units
- caveats about tables
- join constraints
- protocol-specific notes

Use `record_research_finding` for project-specific claims:

- "Bridge inflows increased after proposal X"
- "Median validator count remained stable"

#### Verification and peer review

`verify_research_phase` is a structural check. It verifies things like:

- execution evidence exists
- statistical depth exists in the evidence set
- evidence references resolve cleanly

If verification fails, publication stays blocked.

Peer review is explicitly client-driven:

1. call `prepare_peer_review`
2. feed the packet into `conduct_research_peer_review(packet_json)`
3. call `record_peer_review`

The review result is structured and stored as `PeerReviewResult`.

#### Publishing research

`publish_research_report` reuses the normal report renderer but applies research-phase gating instead of report-mode session gating.

Use it after:

- verification passes
- peer review is recorded
- peer review decision is not `rejected`

### 4. Dashboard Tab Factory

Use this when you need to create new dashboard tabs in the metrics-dashboard Vite application.

Recommended sequence:

1. Discover available metrics
   - Call `discover_dashboard_metrics(module="bridges")` to browse api_* models
2. Build a blueprint
   - Construct a DashboardBlueprint JSON with tab config and query specs
3. Preview changes
   - Call `scaffold_dashboard_tab(blueprint_json)` with `dry_run: true`
4. Apply changes
   - Call again with `dry_run: false` to write JS query files and merge YAML config

The scaffold tool generates JS query files in `metrics-dashboard/src/queries/` and merges tab config into the appropriate YAML dashboard file. It is idempotent: re-running updates existing files rather than creating duplicates.

Important: The LLM never writes React/JSX directly. Only the scaffold tool generates UI code from structured JSON blueprints validated by Pydantic.

### 5. Storyteller Workflow

Use this when the user explicitly asks for a **story, narrative, executive brief, decision memo, stakeholder pitch, or recommendation artifact** — anything where an audience must be moved to action rather than just shown the data. Do **not** auto-upgrade a standard report request into a storyteller run; if the user asks for "a report" and the scope is ambiguous, ask which mode they want.

The storyteller is an opt-in, multi-agent pipeline grounded in Cole Nussbaumer Knaflic's *Storytelling with Data* (Wiley, 2015). It sits alongside the standard report workflow and leaves `generate_report` untouched. Standard mode remains the default for dashboards, KPI packs, trend reports, and ad-hoc analysis.

#### Why it exists

Standard report mode answers "show me the data." Storyteller mode answers "convince this specific audience to take this specific action." The two have different shapes:

- Standard mode pushes charts as fast as the discovery/EDA gates allow.
- Storyteller mode refuses to touch data until it has a named audience, a required action, and a one-sentence big idea with stakes.

If the user has not named a decision-maker and a concrete ask, the storyteller pauses and asks — guessing the audience is the single most common failure mode in decision communication.

#### Agent pipeline

The storyteller is structured as seven cooperating personas coordinated by an orchestrator. Each persona has a narrow contract, produces a specific artifact, and hands off to the next. Gates are enforced in code (`storyteller_state.py`), not by convention — skipping a gate raises `RuntimeError`.

```text
┌────────────────────┐
│    Orchestrator    │  Mode selection + gate enforcement (no content)
└─────────┬──────────┘
          │
┌─────────▼──────────┐
│   Context Agent    │  audience, required action, mechanism, tone
└─────────┬──────────┘  produces: ContextBrief
          │
┌─────────▼──────────┐
│      Explorer      │  runs through standard Cerebro tools
└─────────┬──────────┘  produces: candidate findings (feed, never shipped)
          │
┌─────────▼──────────┐
│  Narrative Agent   │  one-sentence big idea with stakes
└─────────┬──────────┘  produces: BigIdea, Storyboard (setup → tension → resolution)
          │
┌─────────▼──────────┐
│  Visual Designer   │  relationship-first chart choice, one focal point per scene
└─────────┬──────────┘  produces: VisualSpec per scene (action titles, de-emphasis)
          │
┌─────────▼──────────┐
│       Writer       │  action titles, annotations, prose, medium adaptation
└─────────┬──────────┘  produces: final_story markdown with chart placeholders
          │
┌─────────▼──────────┐
│       Critic       │  adversarial, cold-reader review (four clarity tests)
└─────────┬──────────┘  produces: ReviewReport (ready_for_handoff + blocking_issues)
          │
┌─────────▼──────────┐
│  Accessibility &   │  colorblind palette, contrast, language, tone match
│    Tone Agent      │
└─────────┬──────────┘  produces: pass/fail verdict
          │
┌─────────▼──────────┐
│      Handoff       │  storyteller_generate_story_report
└────────────────────┘  renders via the existing generate_report pipeline
```

Each arrow is a hard gate. On critic failure, the pipeline loops back to the earliest phase implicated in the blocking issues (a failed reverse storyboard sends work back to Narrative, not Writer).

Why seven personas and not one monolith like `analytics_reporter`? The storyteller is an **artifact-handoff pipeline** — each phase produces a typed Pydantic artifact the next phase consumes as its entire input. Persona separation matches real information boundaries: the Critic must read the work cold, without the Writer's "make it pretty" bias. The analytics reporter is a **state-accumulation pipeline** where later phases need the full upstream history, so monolithic is correct there. Different shapes, different layouts.

#### Workflow

```text
1. storyteller_start_session
2. get_agent_persona("storyteller_orchestrator") and ("storyteller_context")
3. Collect audience, required action, mechanism, tone, background, biases
4. storyteller_record_context_brief(...)         # Gate 1: context
5. Explore data with standard Cerebro tools (discover_models, execute_query, etc.)
6. get_agent_persona("storyteller_narrative")
7. storyteller_record_big_idea(sentence, stakes) # Gate 2: big idea
8. storyteller_record_storyboard(scenes, ...)    # Gate 3: storyboard
9. get_agent_persona("storyteller_visual_designer")
10. storyteller_record_visual_spec(scene_index, ...)   # one per scene
    generate_charts([...])                             # render via standard tool
    storyteller_record_visual_spec(..., chart_id=...)  # attach chart_id
11. get_agent_persona("storyteller_writer")
12. storyteller_record_final_story(title, content_markdown)  # Gate 4: final story
13. get_agent_persona("storyteller_critic")
14. storyteller_run_clarity_checks(checks=[...])  # Gate 5: clarity review
    # On failure: loop back to earliest failing phase
15. get_agent_persona("storyteller_accessibility")
16. storyteller_record_accessibility_pass(passed, notes)  # Gate 6: accessibility
17. storyteller_generate_story_report()          # renders via generate_report
```

Minimal pattern the calling LLM follows:

```text
storyteller_start_session()
storyteller_record_context_brief(
  audience="Q2 budget committee",
  required_action="Approve $250k to continue the summer pilot next year",
  mechanism="memo",
  tone="recommendation"
)
# ... normal Cerebro discovery, EDA, correlation queries ...
storyteller_record_big_idea(
  sentence="The pilot lifted perception of science by 28 points, so the committee should fund a full-year rollout."
)
storyteller_record_storyboard(
  scenes=[
    {"index": 0, "intent": "Set up the pilot's goal", "role": "setup"},
    {"index": 1, "intent": "Show the pre-pilot baseline gap", "role": "tension"},
    {"index": 2, "intent": "Show the 28-point lift", "role": "evidence"},
    {"index": 3, "intent": "Ask for full-year funding", "role": "resolution"},
  ],
  narrative_order="lead_with_ending"
)
# ... record one visual_spec per scene; render charts via generate_charts ...
storyteller_record_final_story(title, content_markdown)
storyteller_run_clarity_checks(checks=[...])
storyteller_record_accessibility_pass(passed=True)
storyteller_generate_story_report()
```

#### What the gates enforce

- **Context gate.** Vague audiences like "stakeholders" or "leadership" are rejected at the Pydantic layer. Required actions shorter than 10 characters are rejected. Mechanism must be one of `live_presentation`, `slide_deck_leave_behind`, `emailed_deck`, `memo`, `brief`, `dashboard_excerpt`, `script`.
- **Big idea gate.** Must be a complete declarative sentence with point of view and stakes. Labels ("Q3 revenue"), single words, and strings ending in a colon are rejected.
- **Storyboard gate.** Must contain at least one `tension` scene and at least one `resolution` scene. Flat "everything is fine" narratives are rejected.
- **Visual spec gate.** `pie`, `donut`, `3d`, and `dual_axis` chart families are rejected. Action titles must be sentences, not labels. Every scene index must match a scene in the recorded storyboard.
- **Clarity review gate.** `ready_for_handoff` is `True` only if every clarity check passes. Failures drop the pipeline back to the earliest failing phase, tagged via the `blocking_issues` list.
- **Accessibility gate.** Hard failures (colorblind-hostile encoding, unreadable contrast) block handoff. Soft failures warn but do not block.

All gates are code, not convention. The relevant modules are:

- `src/cerebro_mcp/storyteller_models.py` — Pydantic contracts and validators
- `src/cerebro_mcp/storyteller_state.py` — phase cursor and gate enforcement
- `src/cerebro_mcp/tools/storyteller.py` — 11 MCP tools

#### Relationship to standard mode

The storyteller does **not** replace the standard report pipeline. Both modes coexist:

- Standard mode (default): `discover_models` → `execute_query` → `generate_charts` → `generate_report`. Unchanged.
- Storyteller mode (opt-in): wraps the standard pipeline with gated persona handoffs. `storyteller_generate_story_report` renders the final artifact through the same `create_report_artifact` used by `generate_report`, but bypasses the standard quality gate because the storyteller has its own.

The standard session state (`search_models_count`, `explored_models`, chart registry) is preserved across a storyteller run so users can continue exploring after the story is rendered.

Do not auto-upgrade. If the user asks "give me a report on bridge activity", ask whether they want a standard report or a decision-oriented story.

---

## Tool Guide

### Query And Schema Tools

| Tool | Use when | Notes |
|---|---|---|
| `discover_models` | best first step for analysis | search plus top model expansion in one call |
| `search_models` | broader search/triage | returns search list only |
| `get_model_details` | you already know the model name | full lineage, SQL, columns, dependencies |
| `list_tables` | browsing raw databases | paginated |
| `describe_table` | verifying exact ClickHouse schema | should happen before writing SQL |
| `get_sample_data` | checking real row shape quickly | lightweight shape inspection |
| `execute_query` | sync bounded SQL preview | structured output, truncation, optional research snapshotting |
| `explain_query` | checking query plan without running the full query | useful for troubleshooting |
| `start_query` / `get_query_results` | long-running or wider queries | paginated async result preview |

### Visualization And Report Tools

| Tool | Use when | Notes |
|---|---|---|
| `quick_chart` | one fast chart | bypasses workflow gates |
| `generate_chart` | one gated chart | okay for single additions |
| `generate_charts` | report chart batches | preferred for report workflows |
| `list_charts` | inspecting the current chart registry | charts are session-scoped |
| `generate_report` | final report assembly | consumes chart placeholders |
| `open_report` | reopen a saved report | works with saved report IDs |
| `list_reports` | browse disk reports | persistent across sessions if the directory persists |
| `export_report` | get a shareable file path or URL | SSE deployments can return HTTP download URLs |

### Research Tools

| Tool | Use when | Notes |
|---|---|---|
| `start_research_project` | start a durable investigation | creates project state on disk |
| `get_research_project` | check project status | compact summary only |
| `plan_research_phase` | lock the plan for the current phase | phase must be current and pending |
| `execute_research_phase` | mark the phase complete and advance | deterministic state transition only |
| `attach_research_evidence` | link an artifact into the project | validates the ref first |
| `capture_schema_snapshot` | make schema inspection durable | good for mapping phase |
| `record_research_memory` | save reusable domain knowledge | backed by evidence refs |
| `record_research_finding` | save a project claim | backed by evidence refs |
| `get_research_memory` | browse stored memory | paginated |
| `get_research_evidence` | browse evidence | paginated, optional phase filter |
| `get_research_findings` | browse findings | paginated |
| `verify_research_phase` | run structural validation | publication gate |
| `prepare_peer_review` | build the review packet | client then applies review prompt |
| `record_peer_review` | persist the review result | publication remains blocked on rejection |
| `publish_research_report` | generate the final research artifact | requires verification and peer review |

### Metadata And Utility Tools

| Tool | Use when | Notes |
|---|---|---|
| `list_databases` | inspect available ClickHouse databases | includes descriptions |
| `system_status` | debug server state | ClickHouse, manifest, docs, cache, tracing, transport |
| `resolve_address` | map names to addresses or labels | backed by `dune_labels` |
| `get_token_metadata` | verify token decimals and metadata | useful before interpreting amounts |
| `search_models_by_address` | find dbt models around a contract | contract-centric analysis |
| `search_docs` / `get_doc_chunk` | query platform docs | supplementary context |
| `get_platform_constants` | chain constants and fixed references | useful before querying raw execution data |
| `save_query` / `run_saved_query` / `list_saved_queries` | reusable SQL snippets | not evidence storage |

### Semantic Tools

| Tool | Use when | Notes |
|---|---|---|
| `discover_metrics` | first step for governed metric questions | scores canonical names, synonyms, docs keywords, and module hints |
| `get_metric_details` | you already know the metric name | shows root model, allowed dimensions, supported grains, docs, and reachability hints |
| `explain_metric_query` | you want the plan without execution | returns planner mode, selected/rejected paths, warnings, and compiled SQL |
| `query_metrics` | execute governed metrics with semantic provenance | uses the semantic planner, ClickHouse compiler, and one safe retry loop |
| `get_clickhouse_query_rules` | raw SQL fallback help | only registered when a valid vendored ClickHouse bundle is present |

### Custom Query Tools

| Tool | Use when | Notes |
|---|---|---|
| `list_custom_tools` | discovering available parameterized tools | shows all tools from custom_tools.yaml |
| *(dynamically registered)* | common domain questions with specific parameters | parameterized SQL, no raw query needed |

Custom tools are defined in `custom_tools.yaml` and registered at startup when `CUSTOM_TOOLS_ENABLED=True`. Each tool maps to a pre-built SQL template with typed parameters.

### Dashboard Builder Tools

| Tool | Use when | Notes |
|---|---|---|
| `discover_dashboard_metrics` | browsing api_* models for dashboard building | returns chart type suggestions and column info |
| `scaffold_dashboard_tab` | generating JS + YAML for a new dashboard tab | idempotent, supports dry_run mode |

Dashboard tools are registered when `DASHBOARD_BUILDER_ENABLED=True` and `METRICS_DASHBOARD_PATH` is set.

### Verification Tools

| Tool | Use when | Notes |
|---|---|---|
| `verify_numbers` | before reporting any computed numbers to the user | checks arithmetic and optionally cross-references via check_query |

The agent provides its computation logic (formula + component values) and the tool independently verifies the math. If a `check_query` is provided, it also cross-references against an independent data source.

### Storyteller Tools

Opt-in, gated pipeline for decision-oriented narratives. See the Storyteller Workflow section above for the full agent pipeline. Do not auto-upgrade a standard report request into a storyteller run.

| Tool | Use when | Notes |
|---|---|---|
| `storyteller_start_session` | user explicitly asks for a story, memo, executive brief, pitch, or recommendation | clears prior storyteller state; standard SessionState is untouched |
| `storyteller_status` | checking current phase and gate status mid-workflow | read-only snapshot |
| `storyteller_end_session` | abandoning a session | clears artifacts |
| `storyteller_record_context_brief` | after collecting audience, action, mechanism, tone | **Gate 1.** Rejects vague audiences and un-articulable actions at the Pydantic layer |
| `storyteller_record_big_idea` | after writing one declarative sentence with stakes | **Gate 2.** Rejects labels and single-word "ideas" |
| `storyteller_record_storyboard` | after mapping scenes to setup → tension → resolution | **Gate 3.** Rejects storyboards with no tension or no resolution |
| `storyteller_record_visual_spec` | one call per storyboard scene | **Gate 4.** Rejects pie/donut/3D/dual-axis; rejects descriptive titles; rejects scene indexes not in the storyboard |
| `storyteller_record_final_story` | after assembling markdown with `{{chart:CHART_ID}}` placeholders | **Gate 5.** Requires every scene to have a visual spec |
| `storyteller_run_clarity_checks` | after running title-only readthrough, reverse storyboard, fresh-eye, and audits | **Gate 6.** `ready_for_handoff=True` only if all checks pass; failures loop back to earliest failing phase |
| `storyteller_record_accessibility_pass` | after colorblind, contrast, language, and tone checks | **Gate 7.** Hard failures block handoff |
| `storyteller_generate_story_report` | after every gate has passed | renders through the existing `create_report_artifact`; standard session state is preserved |

The seven persona briefs are loaded via `get_agent_persona(role)` with roles `storyteller_orchestrator`, `storyteller_context`, `storyteller_narrative`, `storyteller_visual_designer`, `storyteller_writer`, `storyteller_critic`, `storyteller_accessibility`. Each persona has a narrow contract and is adopted only for its phase.

---

## Prompts, Personas, And Resources

Prompts are guidance text returned by the MCP server. They do not execute analysis by themselves.

Prompts available today:

- `getting_started`
- `analyze_data(topic)`
- `explore_protocol(protocol)`
- `write_query(question, database="dbt")`
- `report(period, topics, focus)`
- `adopt_persona_analytics_reporter`
- `adopt_persona_gnosis_research_analyst`
- `adopt_persona_ui_designer`
- `adopt_persona_reality_checker`
- `conduct_research_peer_review(packet_json)`

Personas are guidance, not automation:

- `analytics_reporter`: analysis and EDA discipline (monolithic — state-accumulation pipeline)
- `gnosis_research_analyst`: semantic-first research workflow and evidence discipline
- `ui_designer`: chart selection and report composition
- `reality_checker`: QA and adversarial review rules
- `storyteller_orchestrator`: storyteller mode selection and gate enforcement
- `storyteller_context`: audience, required action, mechanism, tone
- `storyteller_narrative`: big idea and storyboard construction
- `storyteller_visual_designer`: relationship-first chart choice, focal-point design, declutter rules
- `storyteller_writer`: action titles, annotations, medium adaptation
- `storyteller_critic`: adversarial clarity review (title-only readthrough, reverse storyboard, fresh-eye)
- `storyteller_accessibility`: colorblind palette, contrast, language, tone match

The seven storyteller personas are split by design — each is adopted for exactly one phase of an artifact-handoff pipeline. See the Storyteller Workflow section for why.

Resources expose stable reference material:

- platform overview
- ClickHouse SQL guide
- chain parameters
- address directory
- metric definitions
- query cookbook
- `gnosis://semantic-model/{name}`
- `gnosis://semantic-metric/{name}`
- `gnosis://semantic-relationship/{name}`
- `gnosis://semantic-module/{module}`
- `gnosis://semantic-graph-overview`

If a client wants to use a persona, it should explicitly load the prompt or call `get_agent_persona`.

---

## Reports, Charts, And Artifact Lifetime

This part is easy to misunderstand, so the distinctions matter.

### Chart registry

- charts are stored in memory
- chart IDs are session-scoped
- chart registry entries are pruned after a TTL
- if the server restarts, chart IDs are lost

Use charts when you are actively building a report in the current session.

### Reports

- reports are saved as standalone HTML on disk
- reports can be reopened later by report ID
- in SSE mode, reports can be exposed through `/reports/{id}`
- `export_report` can return a local path or a download URL

Use reports for durable visual deliverables.

### Saved queries

- saved queries store SQL and metadata
- they do not store the executed result set
- they are useful for reuse, not as evidence snapshots

### Research evidence snapshots

- persisted sync query results are saved inside the research project
- these are the right artifact for durable SQL evidence
- they are created by `execute_query(..., persist_result=True)`

---

## Running Cerebro MCP

### Prerequisites

- Python 3.10+
- Node.js 20+ for building the bundled report UI
- ClickHouse credentials for the Gnosis analytics warehouse

### Local Development Setup

```bash
git clone https://github.com/gnosischain/cerebro-mcp.git
cd cerebro-mcp

cp .env.example .env
```

For local development, the code default is already a writable local path: `.cerebro/research_projects`.

Recommended local overrides in `.env`:

```dotenv
CLICKHOUSE_PASSWORD=your_password_here
CEREBRO_RESEARCH_DIR=.cerebro/research_projects
THINKING_LOG_DIR=.cerebro/logs
ASYNC_RESULT_DIR=.cerebro/query_results
SEMANTIC_ENABLED=False
```

Then build and install:

```bash
make install
```

What `make install` does:

- builds the report UI bundle
- copies it into `src/cerebro_mcp/static/report.html`
- installs the Python package in editable mode

### Enable Semantic Mode

Semantic is opt-in at deployment time. The single control surface is:

```dotenv
SEMANTIC_ENABLED=True
```

When semantic is enabled, the server also needs access to:

- `manifest.json`
- `catalog.json`
- `semantic_registry.json`
- `semantic_docs_index.json`

The default hosted URLs already point at the published `dbt-cerebro` artifacts. For local or private deployments, switch to filesystem paths or alternate URLs:

```dotenv
DBT_MANIFEST_PATH=/absolute/path/to/manifest.json
DBT_CATALOG_PATH=/absolute/path/to/catalog.json
SEMANTIC_REGISTRY_PATH=/absolute/path/to/semantic_registry.json
SEMANTIC_DOCS_INDEX_PATH=/absolute/path/to/semantic_docs_index.json
```

Semantic runtime states are intentionally narrow:

1. `SEMANTIC_ENABLED=False`
   - semantic tools and semantic resources are not registered
2. `SEMANTIC_ENABLED=True` with healthy artifacts
   - semantic tools and resources are registered and executable
3. `SEMANTIC_ENABLED=True` with stale or unavailable artifacts
   - semantic tools and resources stay registered
   - semantic discovery and docs still work when possible
   - semantic execution returns a graceful unavailable or fallback response instead of silently switching to raw SQL

### Run With stdio

This is the default mode for local desktop MCP hosts:

```bash
cerebro-mcp
```

### Run With SSE

Use this for remote or browser-accessed MCP clients:

```bash
export MCP_AUTH_TOKEN=replace_me
cerebro-mcp --sse
```

SSE behavior:

- binds to `FASTMCP_HOST` / `FASTMCP_PORT`
- requires `Authorization: Bearer <token>` by default
- `/health` is public
- `/reports/{id}` accepts bearer auth or `?token=...`

To disable auth for local testing only:

```dotenv
ALLOW_INSECURE_REMOTE_TRANSPORT=True
```

### Run With Docker

Build:

```bash
docker build -t cerebro-mcp .
```

Run:

```bash
docker run \
  --env-file .env \
  -p 8000:8000 \
  -v "$(pwd)/data:/data" \
  cerebro-mcp
```

Important for Docker:

- the image uses `/data` for persistent storage
- mounted `/data` must be writable by container user `uid 1000`
- reports, saved queries, logs, and research projects all live under `/data` in the image defaults
- if you use local semantic artifact paths instead of published URLs, mount those files into the container and point the corresponding `*_PATH` settings at the mounted location

---

## Connecting MCP Clients

### Claude Desktop

If `cerebro-mcp` is on your `PATH`:

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "cerebro-mcp",
      "env": {
        "CLICKHOUSE_PASSWORD": "your_password",
        "CEREBRO_RESEARCH_DIR": ".cerebro/research_projects"
      }
    }
  }
}
```

### Claude Code

Using `uv` from a checked-out repo:

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

### VS Code

```json
{
  "servers": {
    "cerebro": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cerebro-mcp", "run", "cerebro-mcp"]
    }
  }
}
```

### Remote SSE Client

```json
{
  "mcpServers": {
    "cerebro": {
      "url": "https://mcp.analytics.gnosis.io/sse",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

---

## Deployment Notes

### Health Endpoint

`/health` performs a real ClickHouse connectivity check and returns:

- `200` when ClickHouse is reachable
- `503` when ClickHouse is not reachable
- `clickhouse_version`
- `ssl_trust_injected`

### Report Download Endpoint

In SSE mode:

```text
GET /reports/{report_id}
```

Behavior:

- accepts full UUID or unique short prefix
- returns standalone HTML
- supports bearer header or `?token=...`

### Manifest And Docs Loading

At startup the server tries to load:

- the dbt manifest
- the dbt catalog
- the semantic registry when semantic is enabled
- the semantic docs index when semantic is enabled
- the external docs index

If these fail, dbt and docs-assisted capabilities degrade, but the server can still start.

Semantic-specific behavior:

- semantic loading only happens in `main()`, not at import time
- when the semantic registry hash is stale against the manifest or catalog, semantic execution is disabled but semantic discovery/docs can remain available
- semantic snapshot reloads happen off-thread and the runtime swaps to the new snapshot under a lock

Important for stdio clients:

- the MCP transport expects JSON-RPC on `stdout` only
- any plain-text startup logging sent to `stdout` will break the connection
- Cerebro sends startup diagnostics through logging/stderr instead

### Vendoring ClickHouse agent-skills

`get_clickhouse_query_rules` is intentionally strict. It is registered only when a valid vendored bundle exists under `CLICKHOUSE_AGENT_SKILLS_PATH`.

The canonical sync flow is:

```bash
python scripts/sync_clickhouse_skills.py /path/to/local/agent-skills-checkout --ref <pinned_commit>
```

The sync script copies only the required upstream content:

- `skills/clickhouse-best-practices/**`
- `LICENSE`
- `NOTICE`
- a local `bundle_manifest.json` containing the pinned source ref and deterministic compiled rules path

The implementation environment can stay offline as long as the operator provides a local checkout of the upstream repo. The server will not register `get_clickhouse_query_rules` from a partially copied directory.

---

## Configuration

All settings are environment variables or `.env` values.

### dbt and semantic artifacts

| Variable | Default | Description |
|---|---|---|
| `DBT_MANIFEST_URL` | published `dbt-cerebro` manifest URL | remote manifest source; takes precedence over local path |
| `DBT_MANIFEST_PATH` | empty | local manifest fallback |
| `DBT_CATALOG_URL` | published `dbt-cerebro` catalog URL | remote catalog source; takes precedence over local path |
| `DBT_CATALOG_PATH` | empty | local catalog fallback |
| `DOCS_SEARCH_INDEX_URL` | published docs search URL | external platform docs index |
| `DOCS_SEARCH_INDEX_PATH` | empty | local docs index fallback |
| `DOCS_REFRESH_INTERVAL_SECONDS` | `3600` | docs index refresh cadence |
| `SEMANTIC_ENABLED` | `False` | deployment-level semantic switch; the only semantic on/off control |
| `SEMANTIC_REGISTRY_URL` | published `dbt-cerebro` registry URL | remote semantic registry source |
| `SEMANTIC_REGISTRY_PATH` | empty | local semantic registry fallback |
| `SEMANTIC_DOCS_INDEX_URL` | published `dbt-cerebro` semantic docs URL | remote semantic docs index source |
| `SEMANTIC_DOCS_INDEX_PATH` | empty | local semantic docs index fallback |
| `SEMANTIC_REFRESH_INTERVAL_SECONDS` | `300` | semantic snapshot refresh cadence |
| `CLICKHOUSE_AGENT_SKILLS_PATH` | `src/cerebro_mcp/static/clickhouse_agent_skills` | vendored ClickHouse rules bundle root |

### Dashboard builder and custom tools

| Variable | Default | Description |
|---|---|---|
| `DASHBOARD_BUILDER_ENABLED` | `False` | enable dashboard scaffolding tools |
| `METRICS_DASHBOARD_PATH` | empty | absolute path to metrics-dashboard repo root |
| `CUSTOM_TOOLS_ENABLED` | `False` | enable YAML-defined parameterized query tools |
| `CUSTOM_TOOLS_PATH` | empty | path to custom_tools.yaml config file |

### ClickHouse and SQL limits

| Variable | Default | Description |
|---|---|---|
| `CLICKHOUSE_HOST` | `ujt1j3jrk0.eu-central-1.aws.clickhouse.cloud` | ClickHouse server |
| `CLICKHOUSE_PORT` | `8443` | ClickHouse port |
| `CLICKHOUSE_USER` | `default` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | empty | ClickHouse password |
| `CLICKHOUSE_SECURE` | `True` | Use TLS |
| `CLICKHOUSE_VERIFY` | `True` | Verify TLS certificates |
| `CLICKHOUSE_CONNECT_TIMEOUT` | `30` | connect timeout |
| `CLICKHOUSE_SEND_RECEIVE_TIMEOUT` | `300` | socket timeout |
| `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` | `30` | server-side execution timeout |
| `QUERY_TIMEOUT_SECONDS` | `30` | deprecated fallback |
| `MAX_ROWS` | `10000` | max query result rows for internal execution |
| `MAX_QUERY_LENGTH` | `10000` | max accepted SQL length |

### Tool response budgets

| Variable | Default | Description |
|---|---|---|
| `TOOL_RESULT_MAX_ROWS` | `200` | max rows returned to sync tool consumers |
| `TOOL_RESULT_MAX_CHARS` | `40000` | max serialized sync tool payload size |
| `TOOL_SUMMARY_BUDGET_RATIO` | `0.9` | fraction reserved for `summary_markdown` |
| `TOOL_RESPONSE_MAX_CHARS` | `40000` | legacy fallback if `TOOL_RESULT_MAX_CHARS` is unset |

### Async query storage

| Variable | Default | Description |
|---|---|---|
| `ASYNC_RESULT_PAGE_SIZE` | `200` | async result preview page size |
| `ASYNC_RESULT_MEMORY_THRESHOLD_BYTES` | `5000000` | spill large async results to disk |
| `ASYNC_RESULT_DIR` | `.cerebro/query_results` | async result storage |

### Research storage

| Variable | Default | Description |
|---|---|---|
| `CEREBRO_RESEARCH_DIR` | `.cerebro/research_projects` | durable research project root for local/dev; Docker overrides to `/data/research_projects` |
| `RESEARCH_PAGE_SIZE_DEFAULT` | `20` | default page size for research listings |
| `RESEARCH_PAGE_SIZE_MAX` | `100` | max page size for research listings |

### Reports, tracing, and transport

| Variable | Default | Description |
|---|---|---|
| `CEREBRO_REPORT_DIR` | code fallback `~/.cerebro/reports`, Docker override `/data/reports` | report storage |
| `CEREBRO_SAVED_QUERIES_DIR` | tool fallback `~/.cerebro-mcp`, Docker override `/data/saved-queries` | saved SQL snippets |
| `THINKING_LOG_DIR` | `.cerebro/logs` | reasoning trace directory |
| `THINKING_ALWAYS_ON` | `True` | auto capture tool calls |
| `THINKING_LOG_RETENTION_DAYS` | `30` | trace retention |
| `REPORT_BASE_URL` | empty | URL prefix for exported reports |
| `MCP_AUTH_TOKEN` | empty | required for SSE unless insecure mode is enabled |
| `ALLOW_INSECURE_REMOTE_TRANSPORT` | `False` | disable SSE auth for local testing |
| `FASTMCP_HOST` | `0.0.0.0` | SSE bind host |
| `FASTMCP_PORT` | `8000` | SSE bind port |

### Security and audit

| Variable | Default | Description |
|---|---|---|
| `MCP_SECURITY_POLICY_MODE` | `log_only` | security policy mode; future: `warn`, `enforce` |
| `MCP_SECURITY_LOG_DIR` | `.cerebro/security_audit` | directory for daily JSONL security audit logs |
| `MCP_EXPECTED_MANIFEST_SHA256` | empty | optional manifest pin; empty disables hash verification |

### Chart and report gates

| Variable | Default | Description |
|---|---|---|
| `ENFORCE_CHART_PRECONDITIONS` | `True` | enable chart/report gates |
| `MIN_MODELS_DETAILED` | `3` | model detail explorations required before gated charting |
| `MIN_TABLES_VERIFIED` | `1` | tables to verify before gated charting |
| `MIN_CHARTS_FOR_REPORT` | `3` | minimum registered charts before report creation |
| `MIN_EXPLORATORY_QUERIES` | `2` | minimum prior query executions before report creation |
| `REQUIRE_DIMENSIONAL_BREAKDOWN` | `True` | require a dimensional breakdown in report flow |
| `REQUIRE_RELATIONAL_CHART` | `True` | require relational analysis in report flow |

Use `system_status()` to inspect the live resolved configuration, cache sizes, transport mode, and ClickHouse connectivity.

### Observability

Cerebro MCP exposes comprehensive Prometheus metrics and structured JSON logs. A ready-to-import Grafana dashboard is provided at `grafana/cerebro-mcp-observability.json`.

The dashboard covers 9 sections:

1. **Overview** — replicas, pods, restarts, images
2. **HTTP / SSE** — request rate, p95 latency, 4xx/5xx errors
3. **MCP Internals** — MCP request/tool call rates, latency, top failing tools
4. **Security Audit** — suspicious calls, high-risk tool calls, app-only calls, report auth denials
5. **Tool Usage Details** — top tools by volume, call distribution, error rates, p99 latency
6. **Semantic Layer** — semantic state, registry stats, query attempts, route decisions, planner failures
7. **ClickHouse** — query rate, latency, errors, rows returned
8. **Pod Resources** — CPU, memory, network, throttling, restarts
9. **Logs** — structured logs, tool calls, security audit events, artifact reloads

Prometheus metrics include:

- `cerebro_http_*`, `cerebro_mcp_*` — HTTP and MCP protocol metrics
- `cerebro_clickhouse_*` — ClickHouse query metrics
- `cerebro_security_*` — security audit counters (high-risk calls, suspicious flags, app-only calls)
- `cerebro_report_token_auth_total` — report endpoint auth events
- `semantic_*` — semantic layer metrics (query attempts, planner failures, repairs, fallbacks, snapshot age)

Low-cardinality metrics track tool calls, planner failures, retries, fallbacks, and security events by tool name, risk class, and transport. High-cardinality details (resolved metrics, SQL hash, ClickHouse error text) remain in structured logs and reasoning traces.

See [docs/observability.md](docs/observability.md) for the full metric catalog, Grafana setup, and structured log event reference.

---

## Safety And Guardrails

The server enforces the following at the execution layer:

- read-only SQL only
- blocked DDL and DML
- blocked `FORMAT`, `SETTINGS`, and external reader functions such as `s3(...)`, `url(...)`, `remote(...)`
- database allowlist
- identifier validation
- forced result capping even when a query already contains `LIMIT`
- JSON-safe normalization for ClickHouse values
- best-effort OS trust-store injection for TLS
- custom tools use ClickHouse native parameter binding — immune to SQL injection
- custom tool SQL templates are peer-reviewed and static; the LLM only provides parameter values
- dashboard scaffold tool validates blueprints via Pydantic before any file writes
- `verify_numbers` tool enforces arithmetic verification before numerical claims reach the user
- tool risk classification: every tool is assigned a risk class (`read_only`, `server_state_write`, `workspace_write`, `subprocess`, `app_only`)
- suspicious-call detection: flags app-only tool invocations, workspace writes over SSE, and unknown tools
- append-only security audit log: JSONL file per day with redacted arguments, SHA-256 hashes, risk class, and suspicious flags
- security layer is observation-only (`log_only` mode) — never blocks tool execution

See [docs/security.md](docs/security.md) for the full security architecture, risk registry, and audit event schema.

Recommended operational hardening:

- use a least-privilege ClickHouse user
- keep `CLICKHOUSE_VERIFY=True`
- require `MCP_AUTH_TOKEN` on remote SSE
- monitor `cerebro_security_suspicious_calls_total` in Grafana for anomalous tool invocations

Saved-query reminder:

- `save_query` is for reusable SQL
- it is not evidence storage
- use research snapshots when you need durable query evidence

---

## Typical User Requests And What To Do

### "What data is available for X?"

Use:

1. `discover_models`
2. `get_model_details` if needed
3. `describe_table`

### "Give me the number or trend"

Use:

1. `discover_metrics` and `query_metrics` first if the request maps cleanly to a governed metric
2. otherwise `discover_models`
3. `describe_table`
4. `execute_query`

If semantic execution is unavailable or unsupported, follow the returned fallback reason and use raw SQL.

If the result is large or slow:

1. `start_query`
2. `get_query_results`

### "Make me a chart"

Use:

- `quick_chart` for a one-off plot
- `generate_chart` if you are already in report mode and only need one chart

### "Make me a report"

Use:

1. `discover_models`
2. `describe_table`
3. `execute_query` for EDA and support queries
4. `generate_charts`
5. `generate_report`

### "Investigate this over several steps"

Use the research workflow:

1. `start_research_project`
2. phase planning and execution
3. persisted evidence capture
4. verification
5. peer review
6. publication

### "Write me an executive brief / decision memo / stakeholder pitch"

Use the storyteller workflow when the user explicitly asks for a story, narrative, memo, brief, pitch, or recommendation artifact. Do **not** auto-upgrade a standard report request. If the scope is ambiguous ("give me a report on X"), ask which mode the user wants.

1. `storyteller_start_session`
2. Adopt `get_agent_persona("storyteller_orchestrator")` and `("storyteller_context")`
3. Collect audience, required action, mechanism, tone from the user
4. `storyteller_record_context_brief(...)` — rejects vague audiences
5. Run normal Cerebro discovery and EDA to gather evidence
6. `storyteller_record_big_idea(sentence, stakes)` — one declarative sentence
7. `storyteller_record_storyboard(scenes, narrative_order)` — setup → tension → resolution
8. For each scene: `storyteller_record_visual_spec(...)` then `generate_charts` then attach `chart_id`
9. `storyteller_record_final_story(title, content_markdown)`
10. `storyteller_run_clarity_checks(checks=[...])` — loops back on failure
11. `storyteller_record_accessibility_pass(passed, notes)`
12. `storyteller_generate_story_report()`

---

## Project Structure

```text
cerebro-mcp/
├── src/cerebro_mcp/
│   ├── server.py
│   ├── bootstrap.py
│   ├── config.py
│   ├── clickhouse_client.py
│   ├── artifact_loader.py
│   ├── catalog_loader.py
│   ├── tool_models.py
│   ├── tool_output.py
│   ├── safety.py
│   ├── manifest_loader.py
│   ├── docs_loader.py
│   ├── semantic_loader.py
│   ├── semantic_models.py
│   ├── semantic_index.py
│   ├── semantic_graph.py
│   ├── semantic_planner.py
│   ├── semantic_sql_compiler.py
│   ├── dashboard_models.py
│   ├── custom_tool_models.py
│   ├── research_models.py
│   ├── research_store.py
│   ├── research_workflow.py
│   ├── storyteller_models.py
│   ├── storyteller_state.py
│   ├── tools/
│   │   ├── query.py
│   │   ├── query_async.py
│   │   ├── schema.py
│   │   ├── dbt.py
│   │   ├── metadata.py
│   │   ├── saved_queries.py
│   │   ├── visualization.py
│   │   ├── research.py
│   │   ├── session_state.py
│   │   ├── reasoning.py
│   │   ├── agents.py
│   │   ├── dashboard_builder.py
│   │   ├── custom_queries.py
│   │   ├── cross_check.py
│   │   ├── storyteller.py
│   │   ├── semantic.py
│   │   ├── mini_apps.py
│   │   ├── metric_lab.py
│   │   └── token_explorer.py
│   ├── security.py
│   ├── mini_app_cache.py
│   ├── mini_app_models.py
│   ├── prompts/
│   ├── resources/
│   └── static/
├── custom_tools.yaml
├── docs/
│   ├── MINI_APPS.md
│   ├── security.md
│   └── observability.md
├── grafana/
│   └── cerebro-mcp-observability.json
├── scripts/
├── ui/
├── tests/
├── Dockerfile
├── Makefile
└── .env.example
```

---

## Development

```bash
make build-ui
make install
make dev
pytest -v
```

Useful local checks:

```bash
python -m compileall src tests
cerebro-mcp
cerebro-mcp --sse
python scripts/sync_clickhouse_skills.py /path/to/local/agent-skills-checkout --ref <pinned_commit>
```

MCP Inspector example:

```bash
uv run mcp dev src/cerebro_mcp/server.py
```

### Local Semantic Testing Against `dbt-cerebro`

You can test semantic MCP features locally before publishing any artifacts remotely.

1. Build fresh artifacts in `dbt-cerebro`:

```bash
cd /path/to/dbt-cerebro
dbt docs generate
python scripts/semantic/build_registry.py --validate --target-dir target
python scripts/semantic/build_semantic_docs.py --target-dir target
```

2. Point `cerebro-mcp` at the local artifact paths and clear the remote URLs:

```bash
SEMANTIC_ENABLED=true
DBT_MANIFEST_URL=
DBT_CATALOG_URL=
SEMANTIC_REGISTRY_URL=
SEMANTIC_DOCS_INDEX_URL=

DBT_MANIFEST_PATH=/absolute/path/to/dbt-cerebro/target/manifest.json
DBT_CATALOG_PATH=/absolute/path/to/dbt-cerebro/target/catalog.json
SEMANTIC_REGISTRY_PATH=/absolute/path/to/dbt-cerebro/target/semantic_registry.json
SEMANTIC_DOCS_INDEX_PATH=/absolute/path/to/dbt-cerebro/target/semantic_docs_index.json
```

3. Start the server and validate semantic behavior:

```bash
cerebro-mcp
```

Recommended checks:

- `discover_metrics`
- `get_metric_details`
- `explain_metric_query`
- `query_metrics`

Runtime behavior:

- `SEMANTIC_ENABLED=false`: semantic tools and resources are not registered
- `SEMANTIC_ENABLED=true` with healthy local artifacts: semantic tools are registered and executable
- `SEMANTIC_ENABLED=true` with stale or mismatched artifacts: semantic tools remain available, but execution returns graceful unavailable or coverage-gap guidance

### Approved-Only Semantic Execution

`cerebro-mcp` now treats semantic execution as approved-only:

- approved metrics can be discovered and executed
- candidate metrics remain visible in the registry/docs layer but are not executable
- when semantic coverage is missing, MCP returns a structured semantic coverage gap and falls back to the raw SQL path instead of silently mixing candidate assets into execution

Semantic resources now prefer the generated semantic page bodies referenced by `semantic_docs_index.json`, with JSON fallback only when a page body is unavailable.

---

## Dependencies

| Package | Purpose |
|---|---|
| `mcp[cli]` | FastMCP server and MCP protocol support |
| `clickhouse-connect` | ClickHouse client |
| `pyarrow` | Arrow fetch path for ClickHouse |
| `pydantic-settings` | environment-backed settings |
| `python-dotenv` | `.env` loading |
| `requests` | manifest/docs HTTP fetching |
| `truststore` | OS trust-store TLS integration |
| `PyYAML` | YAML parsing for dashboard configs and custom tool definitions |

Frontend stack:

- React
- ECharts
- Tailwind
- `@modelcontextprotocol/ext-apps`

---

## License

See [LICENSE](LICENSE).
