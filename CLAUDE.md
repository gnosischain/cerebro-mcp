# Cerebro MCP — Project Instructions

**Changing this repo's code?** The canonical guide is [AGENTS.md](AGENTS.md) — read it
first, and run `get_cerebro_change_context(paths=...)` before editing. Mistake classes
this repo has already paid for live in
[`src/cerebro_mcp/prompts/lessons/INDEX.md`](src/cerebro_mcp/prompts/lessons/INDEX.md)
and are searchable with `search_cerebro_knowledge(query)`.

@AGENTS.md

Scoped guides (read the one for the directory you touch):
`src/cerebro_mcp/tools/visualization/AGENTS.md`,
`src/cerebro_mcp/tools/visualization/queries/AGENTS.md`,
`ui/src/mini-apps/AGENTS.md`, `benchmarks/AGENTS.md`.
Vendor-neutral workflows: `docs/workflows/`.

---

Everything below is the **analyst runtime** contract — how to answer questions with
the platform, not how to modify it.

## Start here: Cerebro Dispatcher

For any non-trivial user request (anything beyond a single scalar question, "open report 3", or an explicit specialist invocation), start by calling:

    get_agent_persona("cerebro_dispatcher")

The dispatcher classifies intent, runs `preflight_analytics_request`, picks the specialist chain, and emits a **dispatch manifest**. All subsequent workflow rules in this file (Report Workflow, MMM Workflow, Data Query SOP) are subordinate to that manifest — if the dispatcher says `mmm_causal_reviewer` must PASS before `generate_report`, that's binding.

Skip the dispatcher only for:
- Trivial turns ("hi", "thanks", "list reports", "open report 3")
- Explicit specialist invocations by the user ("use mmm_analyst on DEXes")
- Follow-up turns inside an already-dispatched workflow — the manifest from turn 1 still applies

The dispatcher is a router only. It does not query the DB, write SQL, or produce analysis. It names *who* should act and *in what order*.

## Domain specialists with dedicated data planes

Three personas own data planes that are OUTSIDE the semantic registry / dbt catalog — dbt discovery (`find`, `search_models`, `discover_models`) returns only noise for their topics, so they skip it by design:

- **`cow_analyst`** — CoW Protocol internals (solvers, batch auctions, order lifecycle, settlements, surplus) over the `cow_db` raw indexer database. Visuals default to the gate-free `open_cow_explorer` mini-app; numbers come from `describe_table` + `execute_query` on `cow_db`.
- **`dao_governance_analyst`** — Snapshot signaling + Discourse forum over `governance_db`, plus Snapshot **delegation** for the `gnosis.eth` space over the `rpc_log_indexer` DelegateRegistry plane (view `v_delegate_events_gnosis`, mainnet + Gnosis Chain). Every `governance_db` read requires `FINAL` (ReplacingMergeTree, daily re-inserts); the delegation view is canonical and queried **without** FINAL. Quorum vocabulary is met/missed/unspecified — never pass/fail. Delegation is last-write-wins per `(chain_id, delegator)` (reduce/count per chain); delegated voting power = Snapshot's realized `vp_by_strategy` (voted delegates, snapshot-time), never balance reconstruction — and **never read by fixed array index**: `vp_by_strategy` is positional against each proposal's OWN strategy list, which `gnosis.eth` has rewritten three times (lengths 2/4/5, with the chain order inverting between the 4- and 5-slot layouts). Resolve the delegation slots by strategy name+network from `snapshot_proposals`; NULL, never 0, where no realized figure exists. Visuals default to `open_governance` (Delegations tab).
- **`chain_state_analyst`** — point-in-time on-chain reads (current balances, proxy implementations, storage slots, bytecode identity) via the gate-exempt `contract_*` / `rpc_scan_*` tools. No preflight, no discovery ceremony. Escalates to `chain_forensics` for incident, historical, or reconciliation-grade work.

Gate accommodation (enforced in code): `describe_table` on a **curated raw database** (`CURATED_RAW_DATABASES` in `config.py`: `cow_db`, `governance_db`, `rpc_log_indexer`, plus the RPC scratch DB when scans are enabled) counts as both discovery and lineage for the chart gates — one `find(mode="chart")` + one in-domain `describe_table` opens `quick_chart`/`generate_charts`; a full report needs three in-domain describes. dbt-plane behavior is unchanged.

## Reporting tiers — pick `mode=` deliberately

There is one report tool (`generate_report`) but **four effective tiers**, selected by the `mode=` argument you pass to `preflight_analytics_request`. Mode is the single decisive switch between the heavy and light pipelines — get it right at preflight and the rest of the workflow follows automatically.

| Tier | When to use | Preflight call | What runs |
|---|---|---|---|
| `quick_answer` | Scalar lookups: "current X", "how many Y", "latest Z". | None — call `execute_query` / `query_metrics` directly. | No chart, no report. |
| `single_chart` | "Plot/show/chart X", a single metric, no narrative needed. | `preflight_analytics_request(query, mode="chart")` — or just start with `discover_models` (it auto-routes). | 1–2 charts via `generate_charts` (or `quick_chart` for a one-off). In this tier the chart tools **render the visualization themselves** (inline UI + an Open link) — present the result and STOP; do **not** call `generate_report`. Only build a report if the user then explicitly asks for one. |
| `lite_report` | "How is sector X doing", 2–5 charts, light narrative. Most sector-performance questions live here. | `preflight_analytics_request(query, mode="answer")` | 2–5 charts presented **inline** + a short written answer. `generate_report` is **not** called — only `full_report` produces a report artifact. |
| `full_report` | Analytical deep-dive: ≥3 charts, statistical depth, dimensional breakdown, correlations. **The only tier that calls `generate_report`.** | `preflight_analytics_request(query, mode="report")` | Full contract active (Fast Path SOP below): composition shortfalls are disclosed in the artifact, the four SQL-discipline rules block. |

**Default to the lightest tier that actually answers the question.** A "how did the bridge sector do last quarter?" question is a `lite_report`, not a `full_report`. Only escalate to `full_report` when the analysis itself requires statistical depth, multi-axis correlation, or ≥3 distinct chart types.

`generate_report` produces a report **only** when the request was routed as an explicit report (`mode="report"`). This is enforced in `check_report_preconditions` (in `src/cerebro_mcp/tools/governance/session_state.py`): when `semantic_mode_last` is `"answer"` or `"chart"`, it **hard-blocks** `generate_report` (controlled by `REPORT_REQUIRES_EXPLICIT_MODE`, default on) and tells the model to present the chart(s) inline and STOP. So a plain "show me / plot X" ask (mode `chart`/`answer`) cannot be escalated into a report artifact — prose guidance alone did not hold, so the gate enforces it. Set `REPORT_REQUIRES_EXPLICIT_MODE=false` to restore the legacy behavior where answer/chart mode auto-builds a lightweight report.

The `chart` / `answer` tiers also lighten the **chart** gate (`check_chart_preconditions`, same file): they require only `MIN_MODELS_DETAILED_LITE` (default 1) model-detail lookups instead of the full-report `MIN_MODELS_DETAILED` (default 3). And when several chart preconditions are unmet, the gate now returns **all** of them in one message, so preflight + discovery + lineage + schema are satisfied in a single follow-up batch rather than one tool round-trip per gate.

Three friction-killers to rely on (all enforced in code, not prose):

- **Discovery auto-routes.** A first-touch `discover_models` / `search_models` call no longer bounces with "call `find` first" — the gate runs the semantic routing itself, records it (as an answer-mode `find`), and lets discovery proceed. No prior `find`/`preflight` needed just to discover.
- **`find` OR `preflight` satisfies the chart gate.** They record identical route/mode data, so calling both is never necessary. Only the **report** tier still requires an explicit `preflight_analytics_request(query, mode="report")`.
- **Chart tools deliver a model-inline payload — YOU render the charts.** In `chart`/`answer` mode, `generate_charts` / `quick_chart` return an assistant-facing block that says `RENDER THESE CHARTS INLINE` plus, per chart, its data (`x` + `series`), `chart_type`, `source_model`, and **`sql`**, plus the cerebro `palette_dark`/`palette_light` and `font`. **Draw the charts inline in your reply yourself** (Claude renders model-authored visuals inline; the server UI panel does NOT mount in Claude Desktop / claude.ai — an open client bug, ext-apps #671), one chart per spec, styled with the palette + a mono font, and put each chart's `sql` in a collapsible block beneath it labeled with the source model. Keep prose to a one-line takeaway per chart, then STOP. The response also carries an `[Open Report](file://…)` link to the full-fidelity native report (renders in a browser). Setting `MCP_UI_INLINE_ENABLED=true` re-enables the server-rendered inline panel for hosts that support it (e.g. Claude Code); it is off by default so Desktop shows no broken panel.

## Report Workflow (CRITICAL)

**Decide first: chart request or report request?** A plain "show me / plot / chart / graph X"
ask is a CHART request — produce the chart(s) and **STOP**. Do **not** call `generate_report`,
and there is no minimum chart count. A trend / time-series question ("X over time", "weekly Y
by Z") is a chart request, **not** automatically a report. Build a report only when the user
explicitly asks for a report, dashboard, deep-dive, write-up, or written analysis.

For a **chart request**:
1. Produce the chart(s) with `generate_charts` (batch) — or `quick_chart` for a single one-off plot.
2. Present them and stop. The charts are the deliverable.

When the user **explicitly asks for a report / dashboard / analysis**:

1. Use `generate_charts` (batch) with ALL chart specs in ONE call (minimum 3 charts)
2. Do NOT use individual `generate_chart` calls for reports — use the batch tool
3. Use `generate_report` to assemble the final interactive report
4. The report renders as a native UI in GUI clients; opens in browser for terminal clients
5. After the report is generated, ask if the user wants conversion to docx/pdf/pptx
6. If yes, use Claude Code's built-in file skills to convert

**Report enforcement gates (generate_report will REJECT without):**
- At least 1 chart with `series_field` or pie/treemap/heatmap/sankey type (dimensional breakdown)
- At least 1 scatter/heatmap chart OR correlation query (relational analysis)
- At least 1 statistical query (quantiles/stddev/corr)
- At least 2 exploratory queries
- **Quality-discipline checks** (each rule independently toggleable in `settings.py`):
    - **stock_flow_discipline** — rejects `SUM(tvl_usd|balance|supply|cumulative_*)` over a date range without a point-in-time constraint. TVL etc. are stock measures; aggregating over time is meaningless. Suggested fix: `argMax(col, date)` or `WHERE date = (SELECT max(date) ...)` or use the canonical snapshot model.
    - **residual_bucket_disclosure** — rejects `WHERE label != ''` / `WHERE col IS NOT NULL` filters that exclude residual buckets without acknowledging the exclusion in the chart title/subtitle/description.
    - **stationarity_on_correlations** — rejects `corr(x, y)` over a series with a `date` / `month` / `week` column unless the SQL or chart metadata mentions stationarity, first-differencing, Spearman, ADF, cointegration, or `lagInFrame`.
    - **aggregator_volume_dedup** — rejects `SUM(volume_usd)` over `fct_execution_pools_daily` / `fct_execution_trades_by_protocol_daily` / `fct_execution_trades_by_token_daily` without a deduplication CTE or first-hop-only acknowledgment.
    - **discovered_model_coverage** — rejects reports where any model returned by `search_models` / `discover_models` / `discover_metrics` was not subsequently queried (`execute_query` / `start_query`) or explored (`get_model_details`) or excluded via `record_model_exclusion(name, reason)`. The discovered set is shared across model and metric discovery — sloppy `discover_metrics` floods the gate identically. **Use the batch helpers below; calling singular `record_model_exclusion` per model is the slowest possible path.**
- All ten design principles (0–9) are documented in [`src/cerebro_mcp/prompts/agents/_shared_quality_rules.md`](src/cerebro_mcp/prompts/agents/_shared_quality_rules.md). Every analysis persona references this file at the top of its operational rules.

**Batch / scope-shortcut exclusion tools** (use ONE of these; not the singular `record_model_exclusion` in a loop):
- `record_model_exclusion_batch(model_names, reason)` — explicit list, one call.
- `exclude_models_by_prefix(prefix, reason)` — sweep a name prefix (e.g. `api_execution_circles_v2_`).
- `exclude_module(module, reason)` — sweep a dbt module (`circles`, `bridges`, `p2p`, …).
- `exclude_all_discovered_except(keep, reason)` — inverse: keep listed names, exclude every other discovered model.

The coverage gate accepts these as equivalent to per-model exclusion. Prefer `exclude_module` / `exclude_models_by_prefix` for broad sweeps, `exclude_all_discovered_except` when only a handful of discovered models are in scope, and `record_model_exclusion_batch` for explicit lists.

**Telemetry**: gate evaluations and discovered-model coverage are recorded as Prometheus counters (`cerebro_quality_gate_evaluations_total`, `cerebro_quality_report_generations_total`, `cerebro_discovered_model_coverage_total`). The `quality_metrics` MCP tool returns a markdown summary of in-process counts; for long-window analysis scrape `/metrics`.

**After `generate_report` or `open_report` succeeds:**
- ALWAYS include the file:// link from the tool response in your reply
- Do NOT repeat the markdown content or {{chart:CHART_ID}} placeholders as text
- Summarize key insights and ask about format conversion
- SQL queries are embedded in the report UI (click `</>` on each chart card)

Use `list_reports()` and `open_report(id)` to reopen past reports. The list
includes both dashboard reports and research-essay reports (`kind` column).

### Picking a report tool

All tools share the chart pipeline and enforcement gates. Pick based on the
artifact the user is asking for:

- **`generate_report`** — analytical dashboard. Dense charts, grids, KPIs,
  short commentary between chart groups. Default choice.
- **`generate_research_report`** — long-form research essay in the
  Anthropic-style layout. Use when the user asks for a whitepaper, research
  report, research essay, narrative analysis, thesis piece, or "long-form"
  article with an argument. Requires `deck` (sub-headline, ≤240 chars) and
  `key_takeaways` (3–6 items). Supports extra markdown directives:
  `{{figure:CHART_ID caption="..." source="..."}}`, `{{pullquote}}`,
  `{{callout kind=...}}`, `{{sidebar title="..."}}`, and `[^fnid]` footnotes.
  Standard `{{chart:ID}}` and `{{grid:N}}` still work inline.
- **`generate_case_study_report`** — scrollytelling layout modeled on
  Anthropic customer-story pages. Use when the deliverable is a marketing
  case study, customer story, growth pitch, or narrative-first investor
  update — i.e. persuasion with scroll-triggered visuals, NOT a whitepaper
  or dashboard. Requires `deck` and `key_points` (3–6). Supports extra
  markdown directives:
  `{{scene chart="..." side="left|right"}} ... {{/scene}}` (sticky visual +
  scrolling narrative), `{{step chart="..." state="..."}} ... {{/step}}`
  inside a scene (stepped chart beats), `{{reveal}} - bullet ... {{/reveal}}`
  (progressive bullet reveal), `{{image src="..." caption="..."
  full_bleed=true}}` (full-bleed imagery), `{{cta label="..." href="..."}}`.
  Optional structured `hero_image`, `hero_chart_id`, and `cta` args.
- **`storyteller_generate_story_report`** — final step of the storyteller
  pipeline (memos, decision briefs, pitches, investor updates, narrative-first
  deliverables).
    - Default `style="research"` → research-essay layout.
    - `style="scrollytelling"` → case-study / growth-pitch layout. Natural
      fit when the context-brief mechanism is pitch / customer-story /
      investor-update. Maps big idea → deck, storyboard scene intents →
      key_points, context-brief mechanism → category.
    - `style="dashboard"` → standard dashboard layout (back-compat).

**Key takeaways formatting:**
- Key takeaways / summary sections in reports MUST use a 3-column table:

| Takeaway | Evidence | Why it matters |
|----------|----------|----------------|
| ... | ... | ... |

Do NOT use bullet lists for key takeaways. Always use the table format above.

## Data Query SOP

0. PLANE CHECK — pick the data plane before discovering anything:

| Question shape | Plane |
|---|---|
| Historical aggregate / trend / USD-valued | dbt models via `execute_query` (steps 1-7 below) |
| Current or pinned-block state, ONE address | `contract_call_function` (one RPC round-trip) |
| Current or pinned-block state across MANY addresses; storage slots; bytecode/proxy identity; native-value traces; events no dbt model decodes; windows too recent for dbt | `rpc_scan_*` tools (needs `RPC_SCAN_ENABLED`) → results land in a `scratch.rpc_*` ClickHouse table → continue with `execute_query` joins against dbt models. Pin anchor blocks first via `rpc_find_block(kind="timestamp")`. Address sets ≤500 inline, else `address_sql` (a dbt model or a previous scan's scratch table works). Count scratch tables with `uniqExact`/`FINAL`. |
| "What did tx X do" | `contract_decode_transaction_input` / `contract_decode_receipt_logs` / `rpc_trace_transaction` |

Never re-scan the chain to re-aggregate — scan once, then aggregate in SQL. See [docs/rpc/rpc_scan_overview.md](docs/rpc/rpc_scan_overview.md).

1. DISCOVER: `search_models` — find models across ALL tiers (api_*, fct_*, int_*), not just the first match. **Pass `module=` and a tight `query=`; the default `limit=50` is a ceiling, not a target — drop to 10–15 for focused tasks.** Every model returned counts toward the discovered-model coverage gate.
2. EXPLORE: `get_model_details` for top 3-5 models — map lineage, identify all dimensions (token, action, segment)
3. VERIFY: `describe_table` for exact column names
4. EDA: Quick distribution check — `quantiles`, `stddevPop`, `min/max`, `count` to assess data shape and outliers
5. QUERY: `execute_query` with date filters, LIMIT, and statistical functions (medians over means). Include correlation queries (corr/covarPop/simpleLinearRegression).
6. VISUALIZE: `generate_charts` (batch) — all charts in ONE call. Include dimensional breakdowns (series_field) and scatter/heatmap charts.
7. REPORT: `generate_report` with {{chart:CHART_ID}} placeholders

### Fast Path: minimum-cost full report

A clean run of the **full_report** tier hits all gates with O(1) tool calls per gate plus one sweep call for coverage. Target shape:

1. **Preflight (1 call):** `preflight_analytics_request(query, mode="report")`. Resets the analysis cycle.
2. **Narrow discovery (1–2 calls):** `search_models(query=…, module=…, limit=15)`. The discovered set is the only gate that scales with N — keep it small.
3. **Lineage (3 calls):** `get_model_details` on the 3 models you will actually use → satisfies `MIN_MODELS_DETAILED`.
4. **Verify (1 call):** `describe_table` on the primary fact table → satisfies `MIN_TABLES_VERIFIED`.
5. **EDA (≥2 calls):** `execute_query` for distribution + at least one statistical query (`quantiles` / `stddev` / `corr`) → satisfies `MIN_EXPLORATORY_QUERIES`, `MIN_STATISTICAL_QUERIES`, and feeds `REQUIRE_RELATIONAL_CHART`.
6. **Coverage sweep (1 call — pick ONE):**
   - `exclude_module(module, reason="…")`
   - `exclude_models_by_prefix(prefix, reason="…")`
   - `exclude_all_discovered_except(keep=[…], reason="…")`
   - `record_model_exclusion_batch([…], reason="…")`

   Use singular `record_model_exclusion` only for genuine one-off cases. Calling it in a loop is the slowest path possible — the batch tools satisfy the gate in one round-trip.
7. **Charts (1 call):** `generate_charts([...])` — must include ≥1 chart with `series_field` (or pie/treemap/heatmap/sankey), ≥1 scatter/heatmap (or correlation already done in EDA), and ≥3 charts total. Wrap any KPIs in `{{grid:3}}`.
8. **Report (1 call):** `generate_report(...)`.

For `single_chart` / `lite_report` tiers, skip steps 6–7's enforcement; only step 1 (with `mode="chart"` or `"answer"`) and step 7 (≥1 chart) are required.

## MMM Workflow (sector contribution / ROI analysis)

Use this workflow when a user asks for contribution attribution, ROI across incentive programs, budget optimisation, or "which emissions / rewards actually drove TVL / volume / users".

1. `get_agent_persona("mmm_analyst")` — adopt the MMM SOP. The analyst runs: spine-fill → multicollinearity check → baseline extraction → adstock + response-curve fit (both concave and Hill; pick lower MAE) → contribution decomposition → SQL bootstrap for credibility intervals.
2. **Orchestration handoff (explicit, no inter-agent calls).** Synthesize `mmm_analyst`'s output into a markdown DAG table (nodes = variables, edges = hypothesized causation, flags = co-launched / confounded pairs). THEN call `get_agent_persona("mmm_causal_reviewer")` and pass that table verbatim in the next user message. The reviewer returns a pass/fail verdict.
3. **Do NOT call `generate_report` until the verdict is PASS.** On BLOCK, apply the reviewer's prescribed fixes (intervention, segmentation, or front-door variable) and re-submit.
4. If the user asks "what should we do next?": `get_agent_persona("mmm_simulator")`, passing the fitted `(β, r, λ, current_spend, baseline_kpi)` per media. The simulator bounds shifts at ±30% per period and returns marginal-ROI + reallocation charts.

Required charts in the final MMM report (on top of the standard `generate_report` gates):
- Contribution stacked-area over time (series_field = media)
- Spend vs. effectiveness share (grouped bar)
- Response curve per media (scatter + fitted line)
- Adstock decay (bar, per media, showing λ)
- Causal-review table (from `mmm_causal_reviewer`)

## MTA Workflow (user-journey attribution)

Use when the user asks for touchpoint attribution, conversion paths, or "which app actions precede topup / swap / claim".

1. `get_agent_persona("mta_analyst")` — adopt the MTA SOP.
2. **Discovery is mandatory every run.** Run `search_models` / `discover_models`, then `describe_table` on every model used. The persona's "context examples" are illustrative and not a contract.
3. Build a runtime mapping (user, timestamp, touchpoint, conversion columns + identity grain) from `describe_table` output.
4. Volume gates: <30 conversions → descriptive only; 30–499 → rule-based + funnel; ≥500 → Markov + Shapley proxy allowed. Default lookback = 30 days; sweep 7/14/30/60 when volume permits.
5. Hand numerical claims to `statistical_reviewer`.

MTA output is **observational**. No causal claim is allowed unless paired with MMM PASS, an experiment, or a named quasi-experimental design.

See [docs/measurement/mta_overview.md](docs/measurement/mta_overview.md) and [docs/measurement/identity_grain.md](docs/measurement/identity_grain.md) for the conceptual framing.

## Unified MMM + MTA Workflow

Use when the user asks to combine MMM and MTA — e.g. "attribute MMM-measured lift across observed user journeys" or "calibrate our MTA shares against the MMM lift estimate".

1. `get_agent_persona("mmm_analyst")` and run the MMM workflow (steps above).
2. Submit the DAG to `mmm_causal_reviewer`. **Only after PASS** proceed to step 3.
3. `get_agent_persona("mta_analyst")` and run the MTA workflow.
4. `get_agent_persona("unified_causal_reviewer")` and pass BOTH the MMM artifact AND the MTA artifact in the next user message. The reviewer runs eight checks (MMM-gate, conversion consistency, incrementality bound, coverage, leakage, identity grain, selection bias, method stability) and returns PASS / BLOCK with calibration applied: `calibrated_credit_i = raw_mta_credit_i × MMM_lift / Σ raw_credit`.
5. **Do NOT call `generate_report` until the unified verdict is PASS.** On BLOCK, apply the prescribed fix and resubmit.
6. Optional prescription: `get_agent_persona("unified_allocator")` for bounded micro / tactical recommendations. Inherits the ±30%/period cap from `mmm_simulator`. Refuses to run without `unified_causal_reviewer` PASS.

The unified report MUST disclose the `unexplained / untracked` residual — the portion of MMM-estimated lift no observed touchpoint can claim. Omitting it overstates the explanatory power of the touchpoint set.

See [docs/measurement/unified_measurement.md](docs/measurement/unified_measurement.md) for the calibration formula, [docs/measurement/causal_review.md](docs/measurement/causal_review.md) for what each gate enforces, and [docs/measurement/examples/unified_pay_subsidy.md](docs/measurement/examples/unified_pay_subsidy.md) for an end-to-end worked example.
