# Cerebro MCP — Project Instructions

## Start here: Cerebro Dispatcher

For any non-trivial user request (anything beyond a single scalar question, "open report 3", or an explicit specialist invocation), start by calling:

    get_agent_persona("cerebro_dispatcher")

The dispatcher classifies intent, runs `preflight_analytics_request`, picks the specialist chain, and emits a **dispatch manifest**. All subsequent workflow rules in this file (Report Workflow, MMM Workflow, Data Query SOP) are subordinate to that manifest — if the dispatcher says `mmm_causal_reviewer` must PASS before `generate_report`, that's binding.

Skip the dispatcher only for:
- Trivial turns ("hi", "thanks", "list reports", "open report 3")
- Explicit specialist invocations by the user ("use mmm_analyst on DEXes")
- Follow-up turns inside an already-dispatched workflow — the manifest from turn 1 still applies

The dispatcher is a router only. It does not query the DB, write SQL, or produce analysis. It names *who* should act and *in what order*.

## Report Workflow (CRITICAL)

When a user asks for a report, trends, or visual analysis using cerebro:

1. Use `generate_charts` (batch) with ALL chart specs in ONE call (minimum 3 charts)
2. Do NOT use individual `generate_chart` calls for reports — use the batch tool
3. Use `generate_report` to assemble the final interactive report
4. The report renders as a native UI in GUI clients; opens in browser for terminal clients
5. After the report is generated, ask if the user wants conversion to docx/pdf/pptx
6. If yes, use Claude Code's built-in file skills to convert

Never skip the `generate_charts` -> `generate_report` pipeline.

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
    - **discovered_model_coverage** — rejects reports where any model returned by `search_models` / `discover_models` was not subsequently queried (`execute_query` / `start_query`) or explored (`get_model_details`) or excluded with a stated reason via `record_model_exclusion(name, reason)`.
- All eight design principles are documented in [`src/cerebro_mcp/prompts/agents/_shared_quality_rules.md`](src/cerebro_mcp/prompts/agents/_shared_quality_rules.md). Every analysis persona references this file at the top of its operational rules.

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

1. DISCOVER: `search_models` — find models across ALL tiers (api_*, fct_*, int_*), not just the first match
2. EXPLORE: `get_model_details` for top 3-5 models — map lineage, identify all dimensions (token, action, segment)
3. VERIFY: `describe_table` for exact column names
4. EDA: Quick distribution check — `quantiles`, `stddevPop`, `min/max`, `count` to assess data shape and outliers
5. QUERY: `execute_query` with date filters, LIMIT, and statistical functions (medians over means). Include correlation queries (corr/covarPop/simpleLinearRegression).
6. VISUALIZE: `generate_charts` (batch) — all charts in ONE call. Include dimensional breakdowns (series_field) and scatter/heatmap charts.
7. REPORT: `generate_report` with {{chart:CHART_ID}} placeholders

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
